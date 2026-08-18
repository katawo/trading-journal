#property script_show_inputs
#property strict
#property version   "5.00"

// Must match trading_journal/domain/models.py's MT5PositionExport.schema_version
// and the versions listed in SUPPORTED_SCHEMA_VERSIONS (application/import_mt5.py).
#define TRADING_JOURNAL_SCHEMA_VERSION 5
#define TRADING_JOURNAL_MONEY_DIGITS 8

input string CommonFilesSubfolder = "trading_journal";

string ExportFileName()
  {
   return CommonFilesSubfolder+"\\"+(string)AccountInfoInteger(ACCOUNT_LOGIN)+"_positions.csv";
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
   if(step<=0.0) return 2;
   for(int digits=0;digits<=8;digits++)
      if(MathAbs(step-NormalizeDouble(step,digits))<0.0000000001) return digits;
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

// Reconstruct the balance immediately before a position's first entry from
// MT5's complete deal ledger. A same-millisecond competing cash movement has
// no reliable order in exported history, so report no value rather than guess.
bool PreTradeBalance(const ulong position_id,const double current_balance,double &balance_before)
  {
   if(!HistorySelect(0,TimeCurrent()))
      return false;
   ulong tickets[];
   long times[];
   double cash_flows[];
   ulong first_ticket=0;
   long first_time=0;
   for(int index=0;index<HistoryDealsTotal();index++)
     {
      ulong ticket=HistoryDealGetTicket((uint)index);
      if(ticket==0)
         continue;
      long time_msc=HistoryDealGetInteger(ticket,DEAL_TIME_MSC);
      long deal_type=HistoryDealGetInteger(ticket,DEAL_TYPE);
      long entry=HistoryDealGetInteger(ticket,DEAL_ENTRY);
      ulong deal_position=(ulong)HistoryDealGetInteger(ticket,DEAL_POSITION_ID);
      if(deal_position==position_id &&
         (deal_type==DEAL_TYPE_BUY || deal_type==DEAL_TYPE_SELL) &&
         entry==DEAL_ENTRY_IN &&
         (first_ticket==0 || time_msc<first_time || (time_msc==first_time && ticket<first_ticket)))
        {
         first_ticket=ticket;
         first_time=time_msc;
        }
      int size=ArraySize(tickets);
      ArrayResize(tickets,size+1);
      ArrayResize(times,size+1);
      ArrayResize(cash_flows,size+1);
      tickets[size]=ticket;
      times[size]=time_msc;
      cash_flows[size]=HistoryDealGetDouble(ticket,DEAL_PROFIT)+HistoryDealGetDouble(ticket,DEAL_COMMISSION)+HistoryDealGetDouble(ticket,DEAL_SWAP)+HistoryDealGetDouble(ticket,DEAL_FEE);
     }
   if(first_ticket==0)
      return false;
   for(int left=0;left<ArraySize(tickets)-1;left++)
      for(int right=left+1;right<ArraySize(tickets);right++)
         if(times[right]<times[left] || (times[right]==times[left] && tickets[right]<tickets[left]))
           {
            ulong ticket=tickets[left]; tickets[left]=tickets[right]; tickets[right]=ticket;
            long time_msc=times[left]; times[left]=times[right]; times[right]=time_msc;
            double cash=cash_flows[left]; cash_flows[left]=cash_flows[right]; cash_flows[right]=cash;
           }
   for(int index=0;index<ArraySize(tickets);index++)
      if(times[index]==first_time && tickets[index]!=first_ticket && MathAbs(cash_flows[index])>0.00000001)
         return false;
   double running=current_balance;
   for(int index=ArraySize(tickets)-1;index>=0;index--)
     {
      if(tickets[index]==first_ticket)
        {
         balance_before=running-cash_flows[index];
         return true;
        }
      running-=cash_flows[index];
     }
   return false;
  }

struct CompletedPositionRecord
  {
   bool complete;
   string symbol;
   string direction;
   datetime entry_time;
   datetime exit_time;
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
                          const double volume,const double price,const double stop,const double target,const long magic)
  {
   record.complete=false;
   record.symbol=symbol;
   record.direction=(deal_type==DEAL_TYPE_BUY ? "long" : "short");
   record.entry_time=time;
   record.exit_time=0;
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

bool WriteCompletedRecord(const CompletedPositionRecord &record,const string exported_id,const int ordinal,
                          const ulong position_id,const int handle,const double account_balance,const int server_utc_offset_minutes)
  {
   double pretrade_balance=0.0;
   bool has_pretrade_balance=ordinal==1 && PreTradeBalance(position_id,account_balance,pretrade_balance);
   if(!record.complete || record.entry_volume<=0.0 || record.exit_volume+0.00000001<record.entry_volume ||
      record.entry_time==0 || record.exit_time==0 || record.direction=="")
      return false;
   double net_pnl=record.gross_pnl+record.commission+record.swap+record.fees;
   double entry_price=record.entry_notional/record.entry_volume;
   int symbol_digits=SymbolDigits(record.symbol);
   int volume_digits=VolumeDigits(record.symbol);
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
             5,
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
   return true;
  }

int ExportPosition(const ulong position_id,const int handle,const double account_balance,const int server_utc_offset_minutes)
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
            StartCompletedRecord(records[active],symbol,deal_type,time,volume,price,stop,target,magic);
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
            StartCompletedRecord(records[active],symbol,deal_type,time,open_volume,price,stop,target,magic);
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
      string exported_id=index==0 ? (string)position_id : (string)position_id+":"+(string)(index+1);
      if(WriteCompletedRecord(records[index],exported_id,index+1,position_id,handle,account_balance,server_utc_offset_minutes)) exported++;
     }
   return exported;
  }

void OnStart()
  {
   if(!HistorySelect(0,TimeCurrent()))
     {
      PrintFormat("History selection failed: %d",GetLastError());
      return;
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
      PrintFormat("Unable to create export: %d",GetLastError());
      return;
     }
   double account_balance=AccountInfoDouble(ACCOUNT_BALANCE);
   int server_utc_offset_minutes=ServerUtcOffsetMinutes();
   FileWrite(handle,"schema_version","account_login","broker_server","account_currency","position_id","symbol","direction","entry_time","exit_time","server_utc_offset_minutes","entry_price","exit_price","volume","gross_pnl","commission","swap","fees","net_pnl","entry_stop_price","entry_target_price","close_stop_price","entry_magic_number","entry_deal_count","exit_reason","initial_risk_amount","initial_reward_amount","account_balance","pretrade_account_balance");

   int exported=0;
   for(int index=0;index<ArraySize(position_ids);index++)
      if(!IsPositionIdentifierOpen(position_ids[index]))
         exported+=ExportPosition(position_ids[index],handle,account_balance,server_utc_offset_minutes);
   FileClose(handle);

   if(!FileMove(temporary_name,FILE_COMMON,export_name,FILE_COMMON|FILE_REWRITE))
     {
      PrintFormat("Unable to publish export: %d",GetLastError());
      return;
     }
   PrintFormat("Trading Journal export complete: %d completed positions",exported);
  }
