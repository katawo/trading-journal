#property strict

// Relative to MT5 Common Files. Each account receives its own CSV filename.
input string CommonFilesSubfolder = "trading_journal";
input int InpSafetyExportSeconds = 60;

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

bool ContainsPosition(const ulong &positions[],const ulong position_id)
  {
   for(int index=0;index<ArraySize(positions);index++)
      if(positions[index]==position_id)
         return true;
   return false;
  }

bool ExportPosition(const ulong position_id,const int handle)
  {
   if(!HistorySelectByPosition(position_id))
      return false;

   double entry_volume=0.0;
   double exit_volume=0.0;
   double entry_notional=0.0;
   double exit_notional=0.0;
   double gross_pnl=0.0;
   double commission=0.0;
   double swap=0.0;
   double fees=0.0;
   string symbol="";
   string direction="";
   datetime entry_time=0;
   datetime exit_time=0;

   for(int index=0;index<HistoryDealsTotal();index++)
     {
      ulong ticket=HistoryDealGetTicket((uint)index);
      long deal_type=HistoryDealGetInteger(ticket,DEAL_TYPE);
      if(deal_type!=DEAL_TYPE_BUY && deal_type!=DEAL_TYPE_SELL)
         continue;

      long entry=HistoryDealGetInteger(ticket,DEAL_ENTRY);
      double volume=HistoryDealGetDouble(ticket,DEAL_VOLUME);
      double price=HistoryDealGetDouble(ticket,DEAL_PRICE);
      datetime time=(datetime)HistoryDealGetInteger(ticket,DEAL_TIME);
      symbol=HistoryDealGetString(ticket,DEAL_SYMBOL);
      gross_pnl+=HistoryDealGetDouble(ticket,DEAL_PROFIT);
      commission+=HistoryDealGetDouble(ticket,DEAL_COMMISSION);
      swap+=HistoryDealGetDouble(ticket,DEAL_SWAP);
      fees+=HistoryDealGetDouble(ticket,DEAL_FEE);

      if(entry==DEAL_ENTRY_IN)
        {
         entry_volume+=volume;
         entry_notional+=volume*price;
         if(entry_time==0)
           {
            entry_time=time;
            direction=(deal_type==DEAL_TYPE_BUY ? "long" : "short");
           }
        }
      else if(entry==DEAL_ENTRY_OUT || entry==DEAL_ENTRY_OUT_BY || entry==DEAL_ENTRY_INOUT)
        {
         exit_volume+=volume;
         exit_notional+=volume*price;
         if(time>exit_time)
            exit_time=time;
        }
     }

   // The journal receives completed positions only. Open positions and account
   // operations deliberately remain outside this read-only export.
   if(entry_volume<=0.0 || exit_volume+0.00000001<entry_volume || entry_time==0 || exit_time==0 || direction=="")
      return false;

   double net_pnl=gross_pnl+commission+swap+fees;
   FileWrite(handle,
             1,
             (string)AccountInfoInteger(ACCOUNT_LOGIN),
             AccountInfoString(ACCOUNT_SERVER),
             AccountInfoString(ACCOUNT_CURRENCY),
             (string)position_id,
             symbol,
             direction,
             ServerTime(entry_time),
             ServerTime(exit_time),
             DoubleToString(entry_notional/entry_volume,_Digits),
             DoubleToString(exit_notional/exit_volume,_Digits),
             DoubleToString(entry_volume,2),
             DoubleToString(gross_pnl,2),
             DoubleToString(commission,2),
             DoubleToString(swap,2),
             DoubleToString(fees,2),
             DoubleToString(net_pnl,2));
   return true;
  }

bool ExportCompletedPositions()
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
   FileWrite(handle,"schema_version","account_login","broker_server","account_currency","position_id","symbol","direction","entry_time","exit_time","entry_price","exit_price","volume","gross_pnl","commission","swap","fees","net_pnl");

   int exported=0;
   for(int index=0;index<ArraySize(position_ids);index++)
      if(ExportPosition(position_ids[index],handle))
         exported++;
   FileClose(handle);

   // Do not publish an empty snapshot. The app will simply remain in its
   // waiting state until the account has its first completed position.
   if(exported==0)
     {
      FileDelete(temporary_name,FILE_COMMON);
      return true;
     }

   if(!FileMove(temporary_name,FILE_COMMON,export_name,FILE_COMMON|FILE_REWRITE))
     {
      PrintFormat("Trading Journal cannot publish export: %d",GetLastError());
      return false;
     }
   PrintFormat("Trading Journal export complete: %d completed positions",exported);
   return true;
  }

int OnInit()
  {
   int seconds=(InpSafetyExportSeconds<1 ? 1 : InpSafetyExportSeconds);
   EventSetTimer(seconds);
   ExportCompletedPositions();
   return INIT_SUCCEEDED;
  }

void OnDeinit(const int reason)
  {
   EventKillTimer();
  }

void OnTimer()
  {
   ExportCompletedPositions();
  }

void OnTradeTransaction(const MqlTradeTransaction &transaction,
                        const MqlTradeRequest &request,
                        const MqlTradeResult &result)
  {
   // A deal event can be an entry or a partial close. Exporting a full snapshot
   // keeps the app's completed-position aggregation correct in both cases.
   if(transaction.type==TRADE_TRANSACTION_DEAL_ADD)
      ExportCompletedPositions();
  }
