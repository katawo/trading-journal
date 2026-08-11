#property script_show_inputs
#property strict

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

bool ContainsPosition(const ulong &positions[],const ulong position_id)
  {
   for(int index=0;index<ArraySize(positions);index++)
      if(positions[index]==position_id)
         return true;
   return false;
  }

bool ExportPosition(const ulong position_id,const int handle,const double account_balance)
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
   double entry_stop=0.0;
   double entry_target=0.0;
   double close_stop=0.0;
   long entry_magic=0;
   long exit_reason=DEAL_REASON_CLIENT;
   int entry_deal_count=0;

   for(uint index=0;index<HistoryDealsTotal();index++)
     {
      ulong ticket=HistoryDealGetTicket(index);
      long deal_type=HistoryDealGetInteger(ticket,DEAL_TYPE);
      if(deal_type!=DEAL_TYPE_BUY && deal_type!=DEAL_TYPE_SELL)
         continue;

      long entry=HistoryDealGetInteger(ticket,DEAL_ENTRY);
      double volume=HistoryDealGetDouble(ticket,DEAL_VOLUME);
      double price=HistoryDealGetDouble(ticket,DEAL_PRICE);
      double stop=HistoryDealGetDouble(ticket,DEAL_SL);
      double target=HistoryDealGetDouble(ticket,DEAL_TP);
      datetime time=(datetime)HistoryDealGetInteger(ticket,DEAL_TIME);
      symbol=HistoryDealGetString(ticket,DEAL_SYMBOL);
      gross_pnl+=HistoryDealGetDouble(ticket,DEAL_PROFIT);
      commission+=HistoryDealGetDouble(ticket,DEAL_COMMISSION);
      swap+=HistoryDealGetDouble(ticket,DEAL_SWAP);
      fees+=HistoryDealGetDouble(ticket,DEAL_FEE);

      if(entry==DEAL_ENTRY_IN)
        {
         entry_deal_count++;
         entry_volume+=volume;
         entry_notional+=volume*price;
         if(entry_time==0)
           {
            entry_time=time;
            direction=(deal_type==DEAL_TYPE_BUY ? "long" : "short");
            entry_stop=stop;
            entry_target=target;
            entry_magic=HistoryDealGetInteger(ticket,DEAL_MAGIC);
           }
        }
      else if(entry==DEAL_ENTRY_OUT || entry==DEAL_ENTRY_OUT_BY || entry==DEAL_ENTRY_INOUT)
        {
         exit_volume+=volume;
         exit_notional+=volume*price;
         if(time>exit_time)
           {
            exit_time=time;
            close_stop=stop;
            exit_reason=HistoryDealGetInteger(ticket,DEAL_REASON);
           }
        }
     }

   // Only emit positions that are flat. Open positions and non-trading account
   // operations deliberately stay out of the journal import.
   if(entry_volume<=0.0 || exit_volume+0.00000001<entry_volume || entry_time==0 || exit_time==0 || direction=="")
      return false;

   double net_pnl=gross_pnl+commission+swap+fees;
   double entry_price=entry_notional/entry_volume;
   int symbol_digits=SymbolDigits(symbol);
   double initial_risk=0.0;
   double initial_reward=0.0;
   ENUM_ORDER_TYPE order_type=(direction=="long" ? ORDER_TYPE_BUY : ORDER_TYPE_SELL);
   bool valid_stop=(direction=="long" ? entry_stop>0.0 && entry_stop<entry_price : entry_stop>entry_price);
   bool valid_target=(direction=="long" ? entry_target>entry_price : entry_target>0.0 && entry_target<entry_price);
   if(entry_deal_count==1 && valid_stop)
     {
      double calculated=0.0;
      if(OrderCalcProfit(order_type,symbol,entry_volume,entry_price,entry_stop,calculated))
         initial_risk=MathAbs(calculated);
      if(valid_target && OrderCalcProfit(order_type,symbol,entry_volume,entry_price,entry_target,calculated))
         initial_reward=MathAbs(calculated);
     }
   FileWrite(handle,
             3,
             (string)AccountInfoInteger(ACCOUNT_LOGIN),
             AccountInfoString(ACCOUNT_SERVER),
             AccountInfoString(ACCOUNT_CURRENCY),
             (string)position_id,
             symbol,
             direction,
             ServerTime(entry_time),
             ServerTime(exit_time),
             DoubleToString(entry_price,symbol_digits),
             DoubleToString(exit_notional/exit_volume,symbol_digits),
             DoubleToString(entry_volume,2),
             DoubleToString(gross_pnl,2),
             DoubleToString(commission,2),
             DoubleToString(swap,2),
             DoubleToString(fees,2),
             DoubleToString(net_pnl,2),
             OptionalNumber(entry_stop,symbol_digits),
             OptionalNumber(entry_target,symbol_digits),
             OptionalNumber(close_stop,symbol_digits),
             (string)entry_magic,
             entry_deal_count,
             DealReasonText(exit_reason),
             OptionalNumber(initial_risk,2),
             OptionalNumber(initial_reward,2),
             DoubleToString(account_balance,2));
   return true;
  }

void OnStart()
  {
   if(!HistorySelect(0,TimeCurrent()))
     {
      PrintFormat("History selection failed: %d",GetLastError());
      return;
     }

   ulong position_ids[];
   for(uint index=0;index<HistoryDealsTotal();index++)
     {
      ulong ticket=HistoryDealGetTicket(index);
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
   FileWrite(handle,"schema_version","account_login","broker_server","account_currency","position_id","symbol","direction","entry_time","exit_time","entry_price","exit_price","volume","gross_pnl","commission","swap","fees","net_pnl","entry_stop_price","entry_target_price","close_stop_price","entry_magic_number","entry_deal_count","exit_reason","initial_risk_amount","initial_reward_amount","account_balance");

   int exported=0;
   for(int index=0;index<ArraySize(position_ids);index++)
      if(ExportPosition(position_ids[index],handle,account_balance))
         exported++;
   FileClose(handle);

   if(!FileMove(temporary_name,FILE_COMMON,export_name,FILE_COMMON|FILE_REWRITE))
     {
      PrintFormat("Unable to publish export: %d",GetLastError());
      return;
     }
   PrintFormat("Trading Journal export complete: %d completed positions",exported);
  }
