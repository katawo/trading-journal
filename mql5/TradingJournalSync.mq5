#property strict
#property version   "5.00"

// Must match trading_journal/domain/models.py's MT5PositionExport.schema_version
// and the versions listed in SUPPORTED_SCHEMA_VERSIONS (application/import_mt5.py).
#define TRADING_JOURNAL_SCHEMA_VERSION 5
#define TRADING_JOURNAL_LIVE_SCHEMA_VERSION 1
#define TRADING_JOURNAL_MONEY_DIGITS 8
#define TRADING_JOURNAL_HTTP_TIMEOUT_MS 3000
#define TRADING_JOURNAL_REMOTE_BATCH_SIZE 100

// Relative to MT5 Common Files. Each account receives its own CSV filename.
input string CommonFilesSubfolder = "trading_journal";
input int InpSafetyExportSeconds = 60;
input int InpLiveExportSeconds = 10;
// Optional, additive: pushes the same completed positions to a remote journal
// backend over HTTPS, in addition to the local CSV above (see
// /home/thang/.claude/plans/which-free-server-platform-whimsical-sky.md).
// Leave BackendUrl empty to keep the EA exactly as before. BackendUrl must be
// whitelisted once in Tools > Options > Expert Advisors > "Allow WebRequest
// for listed URL" - MT5 gives no programmatic way to do this.
input string BackendUrl = "";
input string ApiToken = "";

int    g_last_export_count=-1;
string g_last_export_time="";
int    g_last_live_export_count=-1;
string g_last_live_export_time="";
ulong  g_last_completed_export_tick=0;
ulong  g_last_live_export_tick=0;
bool   g_completed_dirty=true;
bool   g_live_dirty=true;
int    g_completed_remote_failures=0;
int    g_live_remote_failures=0;
ulong  g_completed_remote_retry_tick=0;
ulong  g_live_remote_retry_tick=0;
string g_completed_remote_status="waiting";
string g_live_remote_status="waiting";
ulong  g_ledger_tickets[];
long   g_ledger_times[];
double g_ledger_cash_flows[];
double g_ledger_balances_before[];

string ExportFileName()
  {
   return CommonFilesSubfolder+"\\"+(string)AccountInfoInteger(ACCOUNT_LOGIN)+"_positions.csv";
  }

string LiveExportFileName()
  {
   return CommonFilesSubfolder+"\\"+(string)AccountInfoInteger(ACCOUNT_LOGIN)+"_open_positions.csv";
  }

int ExportIntervalSeconds()
  {
   return (InpSafetyExportSeconds<1 ? 1 : InpSafetyExportSeconds);
  }

int LiveExportIntervalSeconds()
  {
   return (InpLiveExportSeconds<1 ? 1 : InpLiveExportSeconds);
  }

string AckLedgerFileName()
  {
   return CommonFilesSubfolder+"\\"+(string)AccountInfoInteger(ACCOUNT_LOGIN)+"_backend_acked.txt";
  }

string JsonEscape(const string value)
  {
   string result=value;
   StringReplace(result,"\\","\\\\");
   StringReplace(result,"\"","\\\"");
   StringReplace(result,"\n"," ");
   StringReplace(result,"\r"," ");
   return result;
  }

string ServerTime(datetime value)
  {
   string formatted=TimeToString(value,TIME_DATE|TIME_SECONDS);
   StringReplace(formatted,".","-");
   StringReplace(formatted," ","T");
   return formatted;
  }

int ServerUtcOffsetMinutes()
  {
   datetime server_time=TimeTradeServer();
   datetime utc_time=TimeGMT();
   if(server_time==0 || utc_time==0)
      return 0;
   return (int)MathRound((double)(server_time-utc_time)/60.0);
  }

string DealReasonText(const long reason)
  {
   if(reason==DEAL_REASON_SL) return "stop_loss";
   if(reason==DEAL_REASON_TP) return "take_profit";
   if(reason==DEAL_REASON_SO) return "stop_out";
   if(reason==DEAL_REASON_EXPERT) return "expert";
   if(reason==DEAL_REASON_MOBILE) return "mobile";
   if(reason==DEAL_REASON_WEB) return "web";
   if(reason==DEAL_REASON_CLIENT) return "client";
   return "other";
  }

string OptionalNumber(const double value,const int digits)
  {
   return value>0.0 ? DoubleToString(value,digits) : "";
  }

int SymbolDigits(const string symbol)
  {
   long digits=0;
   return SymbolInfoInteger(symbol,SYMBOL_DIGITS,digits) ? (int)digits : _Digits;
  }

int VolumeDigits(const string symbol)
  {
   double step=SymbolInfoDouble(symbol,SYMBOL_VOLUME_STEP);
   if(step<=0.0)
      return 2;
   for(int digits=0;digits<=8;digits++)
      if(MathAbs(step-NormalizeDouble(step,digits))<0.0000000001)
         return digits;
   return 8;
  }

bool ContainsPosition(const ulong &positions[],const ulong position_id)
  {
   for(int index=0;index<ArraySize(positions);index++)
      if(positions[index]==position_id)
         return true;
   return false;
  }

bool IsPositionIdentifierOpen(const ulong position_id)
  {
   for(int index=0;index<PositionsTotal();index++)
     {
      ulong ticket=PositionGetTicket(index);
      if(ticket>0 && PositionSelectByTicket(ticket) && (ulong)PositionGetInteger(POSITION_IDENTIFIER)==position_id)
         return true;
     }
   return false;
  }

bool ContainsText(const string &values[],const string value)
  {
   for(int index=0;index<ArraySize(values);index++)
      if(values[index]==value)
         return true;
   return false;
  }

int RemoteBackoffSeconds(const int failures)
  {
   int seconds=10;
   for(int index=1;index<failures && seconds<300;index++)
      seconds=(int)MathMin(seconds*2,300);
   return seconds;
  }

bool RemoteDue(const ulong retry_tick)
  {
   return retry_tick==0 || GetTickCount64()>=retry_tick;
  }

// The backend push has no delivery guarantee beyond its own HTTP response, so
// only a confirmed 2xx marks a position acknowledged here - a dropped
// connection or backend outage simply retries next cycle, and a position
// already on the backend from a prior successful push is never resent.
void LoadAckedIds(string &acked_ids[])
  {
   ArrayResize(acked_ids,0);
   int handle=FileOpen(AckLedgerFileName(),FILE_READ|FILE_TXT|FILE_COMMON|FILE_ANSI,CP_UTF8);
   if(handle==INVALID_HANDLE)
      return;
   while(!FileIsEnding(handle))
     {
      string line=FileReadString(handle);
      if(StringLen(line)>0)
        {
         int size=ArraySize(acked_ids);
         ArrayResize(acked_ids,size+1);
         acked_ids[size]=line;
        }
     }
   FileClose(handle);
  }

bool AppendAckedId(const string position_id)
  {
   int handle=FileOpen(AckLedgerFileName(),FILE_READ|FILE_WRITE|FILE_TXT|FILE_COMMON|FILE_ANSI,CP_UTF8);
   if(handle==INVALID_HANDLE)
      return false;
   FileSeek(handle,0,SEEK_END);
   FileWriteString(handle,(string)position_id+"\n");
   FileClose(handle);
   return true;
  }

// WebRequest is synchronous. Calls are timer-only, bounded, and backed off so
// they can delay this read-only exporter's queue but never any trading EA.
bool PushToBackend(const string json_body)
  {
   string headers="Content-Type: application/json\r\nAuthorization: Bearer "+ApiToken+"\r\n";
   char post_data[];
   StringToCharArray(json_body,post_data,0,StringLen(json_body),CP_UTF8);
   char result[];
   string result_headers;
   ResetLastError();
   int status=WebRequest("POST",BackendUrl,headers,TRADING_JOURNAL_HTTP_TIMEOUT_MS,post_data,result,result_headers);
   if(status==-1)
     {
      PrintFormat("Trading Journal backend push failed to send: %d (is %s whitelisted in Tools > Options > Expert Advisors > Allow WebRequest?)",GetLastError(),BackendUrl);
      return false;
     }
   if(status<200 || status>=300)
     {
      PrintFormat("Trading Journal backend push rejected: HTTP %d",status);
      return false;
     }
   return true;
  }

bool PushPendingPositions(const string &pending_json_rows[],const string &pending_ids[])
  {
   for(int start=0;start<ArraySize(pending_json_rows);start+=TRADING_JOURNAL_REMOTE_BATCH_SIZE)
     {
      int finish=(int)MathMin(start+TRADING_JOURNAL_REMOTE_BATCH_SIZE,ArraySize(pending_json_rows));
      string body="{\"positions\":[";
      for(int index=start;index<finish;index++)
        {
         if(index>start)
            body+=",";
         body+=pending_json_rows[index];
        }
      body+="]}";
      if(!PushToBackend(body))
         return false;
      for(int index=start;index<finish;index++)
         if(!AppendAckedId(pending_ids[index]))
            PrintFormat("Trading Journal could not persist backend acknowledgement for %s",pending_ids[index]);
      PrintFormat("Trading Journal backend push complete: %d position(s)",finish-start);
     }
   return true;
  }

bool LedgerKeyLess(const long left_time,const ulong left_ticket,const long right_time,const ulong right_ticket)
  {
   return left_time<right_time || (left_time==right_time && left_ticket<right_ticket);
  }

void SwapLedgerRows(const int left,const int right)
  {
   ulong ticket=g_ledger_tickets[left];
   g_ledger_tickets[left]=g_ledger_tickets[right];
   g_ledger_tickets[right]=ticket;
   long time_msc=g_ledger_times[left];
   g_ledger_times[left]=g_ledger_times[right];
   g_ledger_times[right]=time_msc;
   double cash=g_ledger_cash_flows[left];
   g_ledger_cash_flows[left]=g_ledger_cash_flows[right];
   g_ledger_cash_flows[right]=cash;
  }

void SortBalanceLedger(const int left,const int right)
  {
   int low=left;
   int high=right;
   int pivot=(left+right)/2;
   long pivot_time=g_ledger_times[pivot];
   ulong pivot_ticket=g_ledger_tickets[pivot];
   while(low<=high)
     {
      while(low<=right && LedgerKeyLess(g_ledger_times[low],g_ledger_tickets[low],pivot_time,pivot_ticket)) low++;
      while(high>=left && LedgerKeyLess(pivot_time,pivot_ticket,g_ledger_times[high],g_ledger_tickets[high])) high--;
      if(low<=high)
        {
         SwapLedgerRows(low,high);
         low++;
         high--;
        }
     }
   if(left<high) SortBalanceLedger(left,high);
   if(low<right) SortBalanceLedger(low,right);
  }

void PrepareBalanceLedger(const double current_balance)
  {
   int total=HistoryDealsTotal();
   ArrayResize(g_ledger_tickets,total);
   ArrayResize(g_ledger_times,total);
   ArrayResize(g_ledger_cash_flows,total);
   ArrayResize(g_ledger_balances_before,total);
   int count=0;
   for(int index=0;index<total;index++)
     {
      ulong ticket=HistoryDealGetTicket((uint)index);
      if(ticket==0) continue;
      g_ledger_tickets[count]=ticket;
      g_ledger_times[count]=HistoryDealGetInteger(ticket,DEAL_TIME_MSC);
      g_ledger_cash_flows[count]=HistoryDealGetDouble(ticket,DEAL_PROFIT)+HistoryDealGetDouble(ticket,DEAL_COMMISSION)+HistoryDealGetDouble(ticket,DEAL_SWAP)+HistoryDealGetDouble(ticket,DEAL_FEE);
      count++;
     }
   ArrayResize(g_ledger_tickets,count);
   ArrayResize(g_ledger_times,count);
   ArrayResize(g_ledger_cash_flows,count);
   ArrayResize(g_ledger_balances_before,count);
   if(count>1) SortBalanceLedger(0,count-1);
   double running=current_balance;
   for(int index=count-1;index>=0;index--)
     {
      running-=g_ledger_cash_flows[index];
      g_ledger_balances_before[index]=running;
     }
  }

// Reconstruct balance once per export and look up the first entry in O(log n).
// A same-millisecond competing cash movement remains unknown rather than guessed.
bool PreTradeBalance(const ulong first_ticket,const long first_time,double &balance_before)
  {
   int low=0;
   int high=ArraySize(g_ledger_tickets)-1;
   int found=-1;
   while(low<=high)
     {
      int middle=(low+high)/2;
      if(g_ledger_times[middle]==first_time && g_ledger_tickets[middle]==first_ticket)
        {
         found=middle;
         break;
        }
      if(LedgerKeyLess(g_ledger_times[middle],g_ledger_tickets[middle],first_time,first_ticket)) low=middle+1;
      else high=middle-1;
     }
   if(found<0) return false;
   for(int index=found-1;index>=0 && g_ledger_times[index]==first_time;index--)
      if(MathAbs(g_ledger_cash_flows[index])>0.00000001) return false;
   for(int index=found+1;index<ArraySize(g_ledger_tickets) && g_ledger_times[index]==first_time;index++)
      if(MathAbs(g_ledger_cash_flows[index])>0.00000001) return false;
   balance_before=g_ledger_balances_before[found];
   return true;
  }

struct CompletedPositionRecord
  {
   bool complete;
   string symbol;
   string direction;
   datetime entry_time;
   datetime exit_time;
   ulong first_entry_ticket;
   long first_entry_time_msc;
   double entry_volume;
   double exit_volume;
   double entry_notional;
   double exit_notional;
   double gross_pnl;
   double commission;
   double swap;
   double fees;
   double entry_stop;
   double entry_target;
   double close_stop;
   long entry_magic;
   long exit_reason;
   int entry_deal_count;
  };

void StartCompletedRecord(CompletedPositionRecord &record,const string symbol,const long deal_type,const datetime time,
                          const ulong ticket,const long time_msc,const double volume,const double price,
                          const double stop,const double target,const long magic)
  {
   record.complete=false;
   record.symbol=symbol;
   record.direction=(deal_type==DEAL_TYPE_BUY ? "long" : "short");
   record.entry_time=time;
   record.exit_time=0;
   record.first_entry_ticket=ticket;
   record.first_entry_time_msc=time_msc;
   record.entry_volume=volume;
   record.exit_volume=0.0;
   record.entry_notional=volume*price;
   record.exit_notional=0.0;
   record.gross_pnl=0.0;
   record.commission=0.0;
   record.swap=0.0;
   record.fees=0.0;
   record.entry_stop=stop;
   record.entry_target=target;
   record.close_stop=0.0;
   record.entry_magic=magic;
   record.exit_reason=DEAL_REASON_CLIENT;
   record.entry_deal_count=1;
  }

string CompletedRecordId(const ulong position_id,const int ordinal)
  {
   return ordinal==1 ? (string)position_id : (string)position_id+":"+(string)ordinal;
  }

bool WriteCompletedRecord(const CompletedPositionRecord &record,const string exported_id,const int ordinal,
                          const int handle,const double account_balance,const int server_utc_offset_minutes,
                          const string &acked_ids[],string &pending_json_rows[],string &pending_ids[])
  {
   if(!record.complete || record.entry_volume<=0.0 || record.exit_volume+0.00000001<record.entry_volume ||
      record.entry_time==0 || record.exit_time==0 || record.direction=="")
      return false;
   double net_pnl=record.gross_pnl+record.commission+record.swap+record.fees;
   double entry_price=record.entry_notional/record.entry_volume;
   int symbol_digits=SymbolDigits(record.symbol);
   int volume_digits=VolumeDigits(record.symbol);
   double pretrade_balance=0.0;
   bool has_pretrade_balance=ordinal==1 && PreTradeBalance(record.first_entry_ticket,record.first_entry_time_msc,pretrade_balance);
   double initial_risk=0.0;
   double initial_reward=0.0;
   ENUM_ORDER_TYPE order_type=(record.direction=="long" ? ORDER_TYPE_BUY : ORDER_TYPE_SELL);
   bool valid_stop=(record.direction=="long" ? record.entry_stop>0.0 && record.entry_stop<entry_price : record.entry_stop>entry_price);
   bool valid_target=(record.direction=="long" ? record.entry_target>entry_price : record.entry_target>0.0 && record.entry_target<entry_price);
   if(record.entry_deal_count==1 && valid_stop)
     {
      double calculated=0.0;
      if(OrderCalcProfit(order_type,record.symbol,record.entry_volume,entry_price,record.entry_stop,calculated))
         initial_risk=MathAbs(calculated);
      if(valid_target && OrderCalcProfit(order_type,record.symbol,record.entry_volume,entry_price,record.entry_target,calculated))
         initial_reward=MathAbs(calculated);
     }
   FileWrite(handle,
             TRADING_JOURNAL_SCHEMA_VERSION,
             (string)AccountInfoInteger(ACCOUNT_LOGIN),
             AccountInfoString(ACCOUNT_SERVER),
             AccountInfoString(ACCOUNT_CURRENCY),
             exported_id,
             record.symbol,
             record.direction,
             ServerTime(record.entry_time),
             ServerTime(record.exit_time),
             server_utc_offset_minutes,
             DoubleToString(entry_price,symbol_digits),
             DoubleToString(record.exit_notional/record.exit_volume,symbol_digits),
             DoubleToString(record.entry_volume,volume_digits),
             DoubleToString(record.gross_pnl,TRADING_JOURNAL_MONEY_DIGITS),
             DoubleToString(record.commission,TRADING_JOURNAL_MONEY_DIGITS),
             DoubleToString(record.swap,TRADING_JOURNAL_MONEY_DIGITS),
             DoubleToString(record.fees,TRADING_JOURNAL_MONEY_DIGITS),
             DoubleToString(net_pnl,TRADING_JOURNAL_MONEY_DIGITS),
             OptionalNumber(record.entry_stop,symbol_digits),
             OptionalNumber(record.entry_target,symbol_digits),
             OptionalNumber(record.close_stop,symbol_digits),
             (string)record.entry_magic,
             record.entry_deal_count,
             DealReasonText(record.exit_reason),
             OptionalNumber(initial_risk,TRADING_JOURNAL_MONEY_DIGITS),
             OptionalNumber(initial_reward,TRADING_JOURNAL_MONEY_DIGITS),
             DoubleToString(account_balance,TRADING_JOURNAL_MONEY_DIGITS),
             has_pretrade_balance ? DoubleToString(pretrade_balance,TRADING_JOURNAL_MONEY_DIGITS) : "");

   if(StringLen(BackendUrl)>0 && !ContainsText(acked_ids,exported_id))
     {
      string json="{";
      json+="\"schema_version\":"+(string)TRADING_JOURNAL_SCHEMA_VERSION+",";
      json+="\"account_login\":\""+JsonEscape((string)AccountInfoInteger(ACCOUNT_LOGIN))+"\",";
      json+="\"broker_server\":\""+JsonEscape(AccountInfoString(ACCOUNT_SERVER))+"\",";
      json+="\"account_currency\":\""+JsonEscape(AccountInfoString(ACCOUNT_CURRENCY))+"\",";
      json+="\"position_id\":\""+exported_id+"\",";
      json+="\"symbol\":\""+JsonEscape(record.symbol)+"\",";
      json+="\"direction\":\""+record.direction+"\",";
      json+="\"entry_time\":\""+ServerTime(record.entry_time)+"\",";
      json+="\"exit_time\":\""+ServerTime(record.exit_time)+"\",";
      json+="\"server_utc_offset_minutes\":"+(string)server_utc_offset_minutes+",";
      json+="\"entry_price\":\""+DoubleToString(entry_price,symbol_digits)+"\",";
      json+="\"exit_price\":\""+DoubleToString(record.exit_notional/record.exit_volume,symbol_digits)+"\",";
      json+="\"volume\":\""+DoubleToString(record.entry_volume,volume_digits)+"\",";
      json+="\"gross_pnl\":\""+DoubleToString(record.gross_pnl,TRADING_JOURNAL_MONEY_DIGITS)+"\",";
      json+="\"commission\":\""+DoubleToString(record.commission,TRADING_JOURNAL_MONEY_DIGITS)+"\",";
      json+="\"swap\":\""+DoubleToString(record.swap,TRADING_JOURNAL_MONEY_DIGITS)+"\",";
      json+="\"fees\":\""+DoubleToString(record.fees,TRADING_JOURNAL_MONEY_DIGITS)+"\",";
      json+="\"net_pnl\":\""+DoubleToString(net_pnl,TRADING_JOURNAL_MONEY_DIGITS)+"\",";
      json+="\"entry_stop_price\":\""+OptionalNumber(record.entry_stop,symbol_digits)+"\",";
      json+="\"entry_target_price\":\""+OptionalNumber(record.entry_target,symbol_digits)+"\",";
      json+="\"close_stop_price\":\""+OptionalNumber(record.close_stop,symbol_digits)+"\",";
      json+="\"entry_magic_number\":\""+(string)record.entry_magic+"\",";
      json+="\"entry_deal_count\":"+(string)record.entry_deal_count+",";
      json+="\"exit_reason\":\""+DealReasonText(record.exit_reason)+"\",";
      json+="\"initial_risk_amount\":\""+OptionalNumber(initial_risk,TRADING_JOURNAL_MONEY_DIGITS)+"\",";
      json+="\"initial_reward_amount\":\""+OptionalNumber(initial_reward,TRADING_JOURNAL_MONEY_DIGITS)+"\",";
      json+="\"account_balance\":\""+DoubleToString(account_balance,TRADING_JOURNAL_MONEY_DIGITS)+"\",";
      json+="\"pretrade_account_balance\":\""+(has_pretrade_balance?DoubleToString(pretrade_balance,TRADING_JOURNAL_MONEY_DIGITS):"")+"\"";
      json+="}";

      int size=ArraySize(pending_json_rows);
      ArrayResize(pending_json_rows,size+1);
      ArrayResize(pending_ids,size+1);
      pending_json_rows[size]=json;
      pending_ids[size]=exported_id;
     }
   return true;
  }

int ExportPosition(const ulong position_id,const int handle,const double account_balance,const int server_utc_offset_minutes,
                   const string &acked_ids[],string &pending_json_rows[],string &pending_ids[])
  {
   if(!HistorySelectByPosition(position_id)) return 0;
   CompletedPositionRecord records[];
   int active=-1;
   double signed_volume=0.0;
   for(int index=0;index<HistoryDealsTotal();index++)
     {
      ulong ticket=HistoryDealGetTicket((uint)index);
      long deal_type=HistoryDealGetInteger(ticket,DEAL_TYPE);
      if(deal_type!=DEAL_TYPE_BUY && deal_type!=DEAL_TYPE_SELL) continue;
      long entry=HistoryDealGetInteger(ticket,DEAL_ENTRY);
      double volume=HistoryDealGetDouble(ticket,DEAL_VOLUME);
      double price=HistoryDealGetDouble(ticket,DEAL_PRICE);
      double stop=HistoryDealGetDouble(ticket,DEAL_SL);
      double target=HistoryDealGetDouble(ticket,DEAL_TP);
      double profit=HistoryDealGetDouble(ticket,DEAL_PROFIT);
      double commission=HistoryDealGetDouble(ticket,DEAL_COMMISSION);
      double swap=HistoryDealGetDouble(ticket,DEAL_SWAP);
      double fee=HistoryDealGetDouble(ticket,DEAL_FEE);
      datetime time=(datetime)HistoryDealGetInteger(ticket,DEAL_TIME);
      long time_msc=HistoryDealGetInteger(ticket,DEAL_TIME_MSC);
      long reason=HistoryDealGetInteger(ticket,DEAL_REASON);
      long magic=HistoryDealGetInteger(ticket,DEAL_MAGIC);
      string symbol=HistoryDealGetString(ticket,DEAL_SYMBOL);
      double sign=(deal_type==DEAL_TYPE_BUY ? 1.0 : -1.0);

      if(entry==DEAL_ENTRY_IN)
        {
         if(active<0)
           {
            active=ArraySize(records);
            ArrayResize(records,active+1);
            StartCompletedRecord(records[active],symbol,deal_type,time,ticket,time_msc,volume,price,stop,target,magic);
            signed_volume=sign*volume;
           }
         else
           {
            records[active].entry_volume+=volume;
            records[active].entry_notional+=volume*price;
            records[active].entry_deal_count++;
            signed_volume+=sign*volume;
           }
         records[active].gross_pnl+=profit;
         records[active].commission+=commission;
         records[active].swap+=swap;
         records[active].fees+=fee;
         continue;
        }

      if(active<0) continue;
      if(entry==DEAL_ENTRY_INOUT)
        {
         double close_volume=MathAbs(signed_volume);
         double open_volume=MathMax(volume-close_volume,0.0);
         double close_ratio=(volume>0.0 ? close_volume/volume : 1.0);
         records[active].exit_volume+=close_volume;
         records[active].exit_notional+=close_volume*price;
         records[active].gross_pnl+=profit;
         records[active].commission+=commission*close_ratio;
         records[active].swap+=swap;
         records[active].fees+=fee*close_ratio;
         records[active].exit_time=time;
         records[active].close_stop=stop;
         records[active].exit_reason=reason;
         records[active].complete=true;
         active=-1;
         signed_volume=0.0;
         if(open_volume>0.00000001)
           {
            active=ArraySize(records);
            ArrayResize(records,active+1);
            StartCompletedRecord(records[active],symbol,deal_type,time,ticket,time_msc,open_volume,price,stop,target,magic);
            records[active].commission+=commission*(1.0-close_ratio);
            records[active].fees+=fee*(1.0-close_ratio);
            signed_volume=sign*open_volume;
           }
         continue;
        }

      if(entry==DEAL_ENTRY_OUT || entry==DEAL_ENTRY_OUT_BY)
        {
         double close_volume=MathMin(volume,MathAbs(signed_volume));
         records[active].exit_volume+=close_volume;
         records[active].exit_notional+=close_volume*price;
         records[active].gross_pnl+=profit;
         records[active].commission+=commission;
         records[active].swap+=swap;
         records[active].fees+=fee;
         records[active].exit_time=time;
         records[active].close_stop=stop;
         records[active].exit_reason=reason;
         signed_volume+=sign*close_volume;
         if(MathAbs(signed_volume)<=0.00000001)
           {
            records[active].complete=true;
            active=-1;
            signed_volume=0.0;
           }
        }
     }

   int exported=0;
   for(int index=0;index<ArraySize(records);index++)
     {
      string exported_id=CompletedRecordId(position_id,index+1);
      if(WriteCompletedRecord(records[index],exported_id,index+1,handle,account_balance,server_utc_offset_minutes,acked_ids,pending_json_rows,pending_ids))
         exported++;
     }
   return exported;
  }

void ShowStatusComment()
  {
   string backend=StringLen(BackendUrl)==0 ? "off" : StringFormat("closed %s · live %s",g_completed_remote_status,g_live_remote_status);
   Comment(StringFormat("Trading Journal Sync — schema v%d\nClosed: %s · %d positions\nOpen: %s · %d positions\nConnection: %s\nBackend: %s",
                         TRADING_JOURNAL_SCHEMA_VERSION,
                         g_last_export_time,
                         MathMax(g_last_export_count,0),
                         g_last_live_export_time,
                         MathMax(g_last_live_export_count,0),
                         TerminalInfoInteger(TERMINAL_CONNECTED) ? "connected" : "disconnected",
                         backend));
  }

bool ExportCompletedPositions(const bool allow_network_push)
  {
   if(!HistorySelect(0,TimeCurrent()))
     {
      PrintFormat("Trading Journal history selection failed: %d",GetLastError());
      return false;
     }

   ulong position_ids[];
   for(int index=0;index<HistoryDealsTotal();index++)
     {
      ulong ticket=HistoryDealGetTicket((uint)index);
      long deal_type=HistoryDealGetInteger(ticket,DEAL_TYPE);
      long entry=HistoryDealGetInteger(ticket,DEAL_ENTRY);
      ulong position_id=(ulong)HistoryDealGetInteger(ticket,DEAL_POSITION_ID);
      if((deal_type==DEAL_TYPE_BUY || deal_type==DEAL_TYPE_SELL) &&
         (entry==DEAL_ENTRY_OUT || entry==DEAL_ENTRY_OUT_BY || entry==DEAL_ENTRY_INOUT) &&
         position_id>0 && !ContainsPosition(position_ids,position_id))
        {
         int size=ArraySize(position_ids);
         ArrayResize(position_ids,size+1);
         position_ids[size]=position_id;
        }
     }

   string export_name=ExportFileName();
   string temporary_name=export_name+".tmp";
   int handle=FileOpen(temporary_name,FILE_WRITE|FILE_CSV|FILE_ANSI|FILE_COMMON,',',CP_UTF8);
   if(handle==INVALID_HANDLE)
     {
      PrintFormat("Trading Journal cannot create export: %d",GetLastError());
      return false;
     }
   double account_balance=AccountInfoDouble(ACCOUNT_BALANCE);
   int server_utc_offset_minutes=ServerUtcOffsetMinutes();
   FileWrite(handle,"schema_version","account_login","broker_server","account_currency","position_id","symbol","direction","entry_time","exit_time","server_utc_offset_minutes","entry_price","exit_price","volume","gross_pnl","commission","swap","fees","net_pnl","entry_stop_price","entry_target_price","close_stop_price","entry_magic_number","entry_deal_count","exit_reason","initial_risk_amount","initial_reward_amount","account_balance","pretrade_account_balance");

   PrepareBalanceLedger(account_balance);

   string acked_ids[];
   string pending_json_rows[];
   string pending_ids[];
   if(StringLen(BackendUrl)>0)
      LoadAckedIds(acked_ids);

   int exported=0;
   for(int index=0;index<ArraySize(position_ids);index++)
      if(!IsPositionIdentifierOpen(position_ids[index]))
         exported+=ExportPosition(position_ids[index],handle,account_balance,server_utc_offset_minutes,acked_ids,pending_json_rows,pending_ids);
   FileClose(handle);

   if(allow_network_push && StringLen(BackendUrl)>0 && ArraySize(pending_json_rows)>0 && RemoteDue(g_completed_remote_retry_tick))
     {
      if(PushPendingPositions(pending_json_rows,pending_ids))
        {
         g_completed_remote_failures=0;
         g_completed_remote_retry_tick=0;
         g_completed_remote_status="ok";
        }
      else
        {
         g_completed_remote_failures++;
         int delay=RemoteBackoffSeconds(g_completed_remote_failures);
         g_completed_remote_retry_tick=GetTickCount64()+(ulong)delay*1000;
         g_completed_remote_status=StringFormat("retry in %d seconds",delay);
        }
     }

   // Do not publish an empty snapshot. The app will simply remain in its
   // waiting state until the account has its first completed position.
   if(exported==0)
     {
      FileDelete(temporary_name,FILE_COMMON);
      g_last_export_count=0;
      g_last_export_time=ServerTime(TimeCurrent());
      ShowStatusComment();
      return true;
     }
   if(!FileMove(temporary_name,FILE_COMMON,export_name,FILE_COMMON|FILE_REWRITE))
     {
      PrintFormat("Trading Journal cannot publish export: %d",GetLastError());
      return false;
     }
   PrintFormat("Trading Journal export complete: %d completed positions",exported);
   g_last_export_count=exported;
   g_last_export_time=ServerTime(TimeCurrent());
   ShowStatusComment();
   return true;
  }

bool AppendLivePositionJson(const ulong ticket,string &rows[])
  {
   if(!PositionSelectByTicket(ticket))
      return false;
   string symbol=PositionGetString(POSITION_SYMBOL);
   long position_type=PositionGetInteger(POSITION_TYPE);
   string direction=(position_type==POSITION_TYPE_BUY ? "long" : "short");
   double entry_price=PositionGetDouble(POSITION_PRICE_OPEN);
   double current_price=PositionGetDouble(POSITION_PRICE_CURRENT);
   double volume=PositionGetDouble(POSITION_VOLUME);
   double stop=PositionGetDouble(POSITION_SL);
   double target=PositionGetDouble(POSITION_TP);
   ulong position_id=(ulong)PositionGetInteger(POSITION_IDENTIFIER);
   double risk=0.0;
   ENUM_ORDER_TYPE order_type=(direction=="long" ? ORDER_TYPE_BUY : ORDER_TYPE_SELL);
   bool valid_stop=(direction=="long" ? stop>0.0 && stop<current_price : stop>current_price);
   double calculated=0.0;
   if(valid_stop && OrderCalcProfit(order_type,symbol,volume,current_price,stop,calculated))
      risk=MathAbs(calculated);
   int digits=SymbolDigits(symbol);
   int volume_digits=VolumeDigits(symbol);
   int size=ArraySize(rows);
   ArrayResize(rows,size+1);
   string json="{";
   json+="\"schema_version\":"+(string)TRADING_JOURNAL_LIVE_SCHEMA_VERSION+",";
   json+="\"account_login\":\""+JsonEscape((string)AccountInfoInteger(ACCOUNT_LOGIN))+"\",";
   json+="\"broker_server\":\""+JsonEscape(AccountInfoString(ACCOUNT_SERVER))+"\",";
   json+="\"account_currency\":\""+JsonEscape(AccountInfoString(ACCOUNT_CURRENCY))+"\",";
   json+="\"position_id\":\""+(string)position_id+"\",";
   json+="\"symbol\":\""+JsonEscape(symbol)+"\",";
   json+="\"direction\":\""+direction+"\",";
   json+="\"entry_time\":\""+ServerTime((datetime)PositionGetInteger(POSITION_TIME))+"\",";
   json+="\"entry_price\":\""+DoubleToString(entry_price,digits)+"\",";
   json+="\"current_price\":\""+DoubleToString(current_price,digits)+"\",";
   json+="\"volume\":\""+DoubleToString(volume,volume_digits)+"\",";
   json+="\"stop_price\":\""+OptionalNumber(stop,digits)+"\",";
   json+="\"target_price\":\""+OptionalNumber(target,digits)+"\",";
   json+="\"net_unrealized_pnl\":\""+DoubleToString(PositionGetDouble(POSITION_PROFIT)+PositionGetDouble(POSITION_SWAP),TRADING_JOURNAL_MONEY_DIGITS)+"\",";
   // Keep enough precision that a valid small risk is never serialized as
   // 0.00 and rejected by the live-snapshot validator.
   json+="\"risk_to_stop_amount\":\""+OptionalNumber(risk,8)+"\",";
   json+="\"magic_number\":\""+(string)PositionGetInteger(POSITION_MAGIC)+"\"}";
   rows[size]=json;
   return true;
  }

string LiveIngestionUrl()
  {
   string base=BackendUrl;
   while(StringLen(base)>0 && StringSubstr(base,StringLen(base)-1)=="/")
      base=StringSubstr(base,0,StringLen(base)-1);
   string suffix="/ingest";
   if(StringLen(base)>=StringLen(suffix) && StringSubstr(base,StringLen(base)-StringLen(suffix))==suffix)
      base=StringSubstr(base,0,StringLen(base)-StringLen(suffix));
   return base+"/ingest/live-positions";
  }

bool PushLiveSnapshot(const string snapshot_time,const string &rows[])
  {
   if(StringLen(BackendUrl)==0)
      return true;
   string body="{\"snapshot\":{\"schema_version\":"+(string)TRADING_JOURNAL_LIVE_SCHEMA_VERSION+",";
   body+="\"account_login\":\""+JsonEscape((string)AccountInfoInteger(ACCOUNT_LOGIN))+"\",";
   body+="\"broker_server\":\""+JsonEscape(AccountInfoString(ACCOUNT_SERVER))+"\",";
   body+="\"account_currency\":\""+JsonEscape(AccountInfoString(ACCOUNT_CURRENCY))+"\",";
   body+="\"snapshot_time\":\""+snapshot_time+"\",";
   body+="\"export_interval_seconds\":"+(string)LiveExportIntervalSeconds()+",\"positions\":[";
   for(int index=0;index<ArraySize(rows);index++)
     {
      if(index>0) body+=",";
      string row=rows[index];
      string prefix="{";
      if(StringFind(row,prefix)==0) row=StringSubstr(row,1);
      string suffix="}";
      if(StringLen(row)>0 && StringSubstr(row,StringLen(row)-1)==suffix) row=StringSubstr(row,0,StringLen(row)-1);
      body+="{\"snapshot_time\":\""+snapshot_time+"\","+row+"}";
     }
   body+="]}}";
   // Pass only the JSON bytes. WHOLE_ARRAY includes a terminal NUL byte,
   // which standards-compliant JSON servers reject as trailing data.
   char payload[]; StringToCharArray(body,payload,0,StringLen(body),CP_UTF8);
   char response[]; string response_headers;
   string headers="Content-Type: application/json\r\nAuthorization: Bearer "+ApiToken+"\r\n";
   ResetLastError();
   int code=WebRequest("POST",LiveIngestionUrl(),headers,TRADING_JOURNAL_HTTP_TIMEOUT_MS,payload,response,response_headers);
   if(code<200 || code>=300)
     {
      PrintFormat("Trading Journal live snapshot push failed: HTTP %d, error %d",code,GetLastError());
      return false;
     }
   return true;
  }

bool ExportLivePositions(const bool allow_network_push)
  {
   if(!TerminalInfoInteger(TERMINAL_CONNECTED))
     {
      g_live_remote_status="terminal disconnected";
      ShowStatusComment();
      return false;
     }
   // Snapshot freshness is always UTC. Position entry timestamps remain MT5
   // server timestamps and are display-only in the live workspace.
   string snapshot_time=ServerTime(TimeGMT());
   string export_name=LiveExportFileName();
   string temporary_name=export_name+".tmp";
   int handle=FileOpen(temporary_name,FILE_WRITE|FILE_CSV|FILE_ANSI|FILE_COMMON,',',CP_UTF8);
   if(handle==INVALID_HANDLE)
     {
      PrintFormat("Trading Journal cannot create live export: %d",GetLastError());
      return false;
     }
   FileWrite(handle,"record_type","schema_version","account_login","broker_server","account_currency","snapshot_time","export_interval_seconds","position_id","symbol","direction","entry_time","entry_price","current_price","volume","stop_price","target_price","net_unrealized_pnl","risk_to_stop_amount","magic_number");
   FileWrite(handle,"snapshot",TRADING_JOURNAL_LIVE_SCHEMA_VERSION,(string)AccountInfoInteger(ACCOUNT_LOGIN),AccountInfoString(ACCOUNT_SERVER),AccountInfoString(ACCOUNT_CURRENCY),snapshot_time,LiveExportIntervalSeconds(),"","","","","","","","","","","","");
   string json_rows[];
   for(int index=0;index<PositionsTotal();index++)
     {
      ulong ticket=PositionGetTicket(index);
      if(ticket==0 || !PositionSelectByTicket(ticket)) continue;
      string symbol=PositionGetString(POSITION_SYMBOL);
      long position_type=PositionGetInteger(POSITION_TYPE);
      string direction=(position_type==POSITION_TYPE_BUY ? "long" : "short");
      double entry_price=PositionGetDouble(POSITION_PRICE_OPEN);
      double current_price=PositionGetDouble(POSITION_PRICE_CURRENT);
      double volume=PositionGetDouble(POSITION_VOLUME);
      double stop=PositionGetDouble(POSITION_SL);
      double target=PositionGetDouble(POSITION_TP);
      ulong position_id=(ulong)PositionGetInteger(POSITION_IDENTIFIER);
      double risk=0.0, calculated=0.0;
      ENUM_ORDER_TYPE order_type=(direction=="long" ? ORDER_TYPE_BUY : ORDER_TYPE_SELL);
      bool valid_stop=(direction=="long" ? stop>0.0 && stop<current_price : stop>current_price);
      if(valid_stop && OrderCalcProfit(order_type,symbol,volume,current_price,stop,calculated)) risk=MathAbs(calculated);
      int digits=SymbolDigits(symbol);
      int volume_digits=VolumeDigits(symbol);
      FileWrite(handle,"position",TRADING_JOURNAL_LIVE_SCHEMA_VERSION,(string)AccountInfoInteger(ACCOUNT_LOGIN),AccountInfoString(ACCOUNT_SERVER),AccountInfoString(ACCOUNT_CURRENCY),snapshot_time,"",(string)position_id,symbol,direction,ServerTime((datetime)PositionGetInteger(POSITION_TIME)),DoubleToString(entry_price,digits),DoubleToString(current_price,digits),DoubleToString(volume,volume_digits),OptionalNumber(stop,digits),OptionalNumber(target,digits),DoubleToString(PositionGetDouble(POSITION_PROFIT)+PositionGetDouble(POSITION_SWAP),TRADING_JOURNAL_MONEY_DIGITS),OptionalNumber(risk,TRADING_JOURNAL_MONEY_DIGITS),(string)PositionGetInteger(POSITION_MAGIC));
      AppendLivePositionJson(ticket,json_rows);
     }
   FileClose(handle);
   if(!FileMove(temporary_name,FILE_COMMON,export_name,FILE_COMMON|FILE_REWRITE))
     {
      PrintFormat("Trading Journal cannot publish live export: %d",GetLastError());
      return false;
     }
   if(allow_network_push && StringLen(BackendUrl)>0 && RemoteDue(g_live_remote_retry_tick))
     {
      if(PushLiveSnapshot(snapshot_time,json_rows))
        {
         g_live_remote_failures=0;
         g_live_remote_retry_tick=0;
         g_live_remote_status="ok";
        }
      else
        {
         g_live_remote_failures++;
         int delay=RemoteBackoffSeconds(g_live_remote_failures);
         g_live_remote_retry_tick=GetTickCount64()+(ulong)delay*1000;
         g_live_remote_status=StringFormat("retry in %d seconds",delay);
        }
     }
   g_last_live_export_count=ArraySize(json_rows);
   g_last_live_export_time=snapshot_time;
   ShowStatusComment();
   return true;
  }

int OnInit()
  {
   ResetLastError();
   if(!EventSetTimer(1))
     {
      PrintFormat("Trading Journal Sync cannot start its timer: %d",GetLastError());
      return INIT_FAILED;
     }
   PrintFormat("Trading Journal Sync starting — schema version %d%s",TRADING_JOURNAL_SCHEMA_VERSION,
               StringLen(BackendUrl)>0 ? StringFormat(" · backend push to %s enabled",BackendUrl) : "");
   g_completed_dirty=true;
   g_live_dirty=true;
   ShowStatusComment();
   return INIT_SUCCEEDED;
  }

void OnDeinit(const int reason)
  {
   EventKillTimer();
   Comment("");
  }

void OnTimer()
  {
   ulong now=GetTickCount64();
   if(g_live_dirty || g_last_live_export_tick==0 || now-g_last_live_export_tick>=(ulong)LiveExportIntervalSeconds()*1000)
     {
      if(!TerminalInfoInteger(TERMINAL_CONNECTED))
        {
         g_live_remote_status="terminal disconnected";
         g_last_live_export_tick=now;
         g_live_dirty=false;
         ShowStatusComment();
        }
      else if(ExportLivePositions(true))
        {
         g_last_live_export_tick=GetTickCount64();
         g_live_dirty=false;
        }
     }
   if(g_completed_dirty || g_last_completed_export_tick==0 || now-g_last_completed_export_tick>=(ulong)ExportIntervalSeconds()*1000)
      if(ExportCompletedPositions(true))
        {
         g_last_completed_export_tick=GetTickCount64();
         g_completed_dirty=false;
        }
  }

void OnTradeTransaction(const MqlTradeTransaction &transaction,
                        const MqlTradeRequest &request,
                        const MqlTradeResult &result)
  {
   // Keep this event handler constant-time. The one-second timer performs all
   // history scans, file publication, and bounded network work.
   if(transaction.type==TRADE_TRANSACTION_DEAL_ADD)
     {
      g_completed_dirty=true;
      g_live_dirty=true;
     }
  }
