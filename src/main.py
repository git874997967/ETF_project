import pandas as pd
import pandas_ta as ta
import numpy as np
import yfinance as yf
import json
from datetime import datetime

def normalize_columns(df):
   """将 DataFrame 列名统一转换为小写字符串。"""
   if isinstance(df.columns, pd.MultiIndex):
       df.columns = df.columns.get_level_values(0).str.lower()
   else:
       df.columns = [c.lower() if isinstance(c, str) else c for c in df.columns]
   return df


UNDERLYING_MAP = {
   'soxl': 'SOXX',
   'soxs': 'SOXX',
   'tqqq': 'QQQ',
   'sqqq': 'QQQ'
}

def get_underlying_ticker(levered_ticker: str) -> str:
   """返回对应的基础标的"""
   return UNDERLYING_MAP.get(levered_ticker.lower(), levered_ticker)


def compute_gmma_trend(df, fast_emas=[3, 5, 8, 10, 12, 15], slow_emas=[30, 35, 40, 45, 50, 60]):
   """计算 GMMA (Guppy Multiple Moving Average)
   
   短期组 (快速): 用于识别短期趋势反转
   长期组 (缓慢): 用于识别长期趋势方向
   
   返回: Series，值为 'uptrend' / 'downtrend' / 'mixed'
   """
   close = df['close']
   
   # 计算快速和缓慢EMA组
   for p in fast_emas:
       df[f'ema_fast_{p}'] = close.ewm(span=p, adjust=False).mean()
   for p in slow_emas:
       df[f'ema_slow_{p}'] = close.ewm(span=p, adjust=False).mean()
   
   # 快速组平均值
   fast_avg = df[[f'ema_fast_{p}' for p in fast_emas]].mean(axis=1)
   # 缓慢组平均值
   slow_avg = df[[f'ema_slow_{p}' for p in slow_emas]].mean(axis=1)
   
   # 判断趋势：快速组都在缓慢组之上 = 上升趋势
   fast_above_slow = (df[[f'ema_fast_{p}' for p in fast_emas]] > slow_avg.values.reshape(-1, 1)).all(axis=1)
   fast_below_slow = (df[[f'ema_fast_{p}' for p in fast_emas]] < slow_avg.values.reshape(-1, 1)).all(axis=1)
   
   trend = pd.Series('mixed', index=df.index)
   trend[fast_above_slow] = 'uptrend'
   trend[fast_below_slow] = 'downtrend'
   
   return trend, fast_avg, slow_avg


def run_tiered_backtest_ma200(leverage_ticker, start_date="2024-01-01", end_date="2026-02-27"):
   """使用 MA200 的三层濾網策略回測"""
   underlying = get_underlying_ticker(leverage_ticker)
   data_start = pd.to_datetime(start_date) - pd.DateOffset(days=300)
   
   df_u = yf.download(underlying, start=data_start, end=end_date, progress=False)
   if df_u.empty: return None
   df_u = normalize_columns(df_u)
   if 'close' not in df_u.columns: return None

   df_u.ta.rsi(length=14, append=True)
   df_u.ta.bbands(length=20, std=2.0, append=True)
   df_u.ta.sma(length=200, append=True)
   df_u.ta.adx(length=14, append=True)
   df_u = normalize_columns(df_u)

   df_u = df_u.dropna().copy()

   rsi_col, bbl_col, sma_col, adx_col = 'rsi_14', 'bbl_20', 'sma_200', 'adx_14'

   disable_rsi = (df_u[adx_col] > 30) & (df_u[adx_col].diff() > 0)
   cond_oversold = (df_u[rsi_col] < 30) & (~disable_rsi)
   cond_breakout = (df_u['close'] < df_u[bbl_col])
   cond_bull_trend = (df_u['close'] > df_u[sma_col])

   df_u['Signal_Type'] = "WAIT"
   df_u['Suggested_Size'] = 0.0

   mask_strong = cond_oversold & cond_breakout & cond_bull_trend
   df_u.loc[mask_strong, 'Signal_Type'] = "STRONG BUY"
   df_u.loc[mask_strong, 'Suggested_Size'] = 1.0

   mask_spec = cond_oversold & cond_breakout & (~cond_bull_trend)
   df_u.loc[mask_spec, 'Signal_Type'] = "CAUTIOUS BUY"
   df_u.loc[mask_spec, 'Suggested_Size'] = 0.5

   mask_weak = cond_oversold & (~cond_breakout)
   df_u.loc[mask_weak, 'Signal_Type'] = "WEAK BUY"
   df_u.loc[mask_weak, 'Suggested_Size'] = 0.2

   df_l = yf.download(leverage_ticker, start=data_start, end=end_date, progress=False)
   df_l = normalize_columns(df_l)
   df_u = df_u.join(df_l[['close']], how='left', rsuffix='_lev')
   df_u.rename(columns={'close_lev': 'close_levered'}, inplace=True)

   sell_signal = (df_u[rsi_col] > 70)
   df_u['Position'] = np.nan
   df_u.loc[df_u['Suggested_Size'] > 0, 'Position'] = df_u['Suggested_Size']
   df_u.loc[sell_signal, 'Position'] = 0
   df_u['Position'] = df_u['Position'].ffill().shift(1).fillna(0)

   df_u['Market_Ret'] = df_u['close_levered'].pct_change()
   df_u['Strategy_Ret'] = df_u['Position'] * df_u['Market_Ret']
   df_u['Equity'] = (1 + df_u['Strategy_Ret']).cumprod()

   result_df = df_u.loc[start_date:]
   if result_df.empty: return None

   total_ret = result_df['Equity'].iloc[-1] / result_df['Equity'].iloc[0] - 1
   max_dd = ((result_df['Equity'] / result_df['Equity'].cummax() - 1).min())
   
   # 计算信号准确率
   buy_signals = result_df[result_df['Suggested_Size'] > 0]
   if len(buy_signals) > 0:
       signal_accuracy = (buy_signals['Market_Ret'].shift(-1) > 0).sum() / len(buy_signals)
   else:
       signal_accuracy = 0

   return {
       "method": "MA200",
       "ticker": leverage_ticker,
       "Total_Return": total_ret,
       "Max_Drawdown": max_dd,
       "Signal_Accuracy": signal_accuracy,
       "Num_Signals": len(buy_signals),
       "Latest_Equity": result_df['Equity'].iloc[-1]
   }


def run_tiered_backtest_gmma(leverage_ticker, start_date="2024-01-01", end_date="2026-02-27"):
   """使用 GMMA 的三层濾網策略回測"""
   underlying = get_underlying_ticker(leverage_ticker)
   data_start = pd.to_datetime(start_date) - pd.DateOffset(days=300)
   
   df_u = yf.download(underlying, start=data_start, end=end_date, progress=False)
   if df_u.empty: return None
   df_u = normalize_columns(df_u)
   if 'close' not in df_u.columns: return None

   df_u.ta.rsi(length=14, append=True)
   df_u.ta.bbands(length=20, std=2.0, append=True)
   df_u.ta.adx(length=14, append=True)
   df_u = normalize_columns(df_u)

   # 添加 GMMA
   gmma_trend, fast_avg, slow_avg = compute_gmma_trend(df_u)
   df_u['gmma_trend'] = gmma_trend
   df_u = df_u.dropna().copy()

   rsi_col, bbl_col, adx_col = 'rsi_14', 'bbl_20', 'adx_14'

   disable_rsi = (df_u[adx_col] > 30) & (df_u[adx_col].diff() > 0)
   cond_oversold = (df_u[rsi_col] < 30) & (~disable_rsi)
   cond_breakout = (df_u['close'] < df_u[bbl_col])
   cond_bull_trend = (df_u['gmma_trend'] == 'uptrend')

   df_u['Signal_Type'] = "WAIT"
   df_u['Suggested_Size'] = 0.0

   mask_strong = cond_oversold & cond_breakout & cond_bull_trend
   df_u.loc[mask_strong, 'Signal_Type'] = "STRONG BUY"
   df_u.loc[mask_strong, 'Suggested_Size'] = 1.0

   mask_spec = cond_oversold & cond_breakout & (~cond_bull_trend)
   df_u.loc[mask_spec, 'Signal_Type'] = "CAUTIOUS BUY"
   df_u.loc[mask_spec, 'Suggested_Size'] = 0.5

   mask_weak = cond_oversold & (~cond_breakout)
   df_u.loc[mask_weak, 'Signal_Type'] = "WEAK BUY"
   df_u.loc[mask_weak, 'Suggested_Size'] = 0.2

   df_l = yf.download(leverage_ticker, start=data_start, end=end_date, progress=False)
   df_l = normalize_columns(df_l)
   df_u = df_u.join(df_l[['close']], how='left', rsuffix='_lev')
   df_u.rename(columns={'close_lev': 'close_levered'}, inplace=True)

   sell_signal = (df_u[rsi_col] > 70)
   df_u['Position'] = np.nan
   df_u.loc[df_u['Suggested_Size'] > 0, 'Position'] = df_u['Suggested_Size']
   df_u.loc[sell_signal, 'Position'] = 0
   df_u['Position'] = df_u['Position'].ffill().shift(1).fillna(0)

   df_u['Market_Ret'] = df_u['close_levered'].pct_change()
   df_u['Strategy_Ret'] = df_u['Position'] * df_u['Market_Ret']
   df_u['Equity'] = (1 + df_u['Strategy_Ret']).cumprod()

   result_df = df_u.loc[start_date:]
   if result_df.empty: return None

   total_ret = result_df['Equity'].iloc[-1] / result_df['Equity'].iloc[0] - 1
   max_dd = ((result_df['Equity'] / result_df['Equity'].cummax() - 1).min())
   
   # 计算信号准确率
   buy_signals = result_df[result_df['Suggested_Size'] > 0]
   if len(buy_signals) > 0:
       signal_accuracy = (buy_signals['Market_Ret'].shift(-1) > 0).sum() / len(buy_signals)
   else:
       signal_accuracy = 0

   return {
       "method": "GMMA",
       "ticker": leverage_ticker,
       "Total_Return": total_ret,
       "Max_Drawdown": max_dd,
       "Signal_Accuracy": signal_accuracy,
       "Num_Signals": len(buy_signals),
       "Latest_Equity": result_df['Equity'].iloc[-1]
   }


# === 对比回測 ===
print("=" * 80)
print("MA200 vs GMMA 趋势指标对比回测")
print("=" * 80)

tickers = ['SOXL', 'TQQQ']
results = []

for ticker in tickers:
   print(f"\n【{ticker}】")
   print("-" * 80)
   
   res_ma = run_tiered_backtest_ma200(ticker)
   res_gm = run_tiered_backtest_gmma(ticker)
   
   if res_ma and res_gm:
       # 横向对比
       comparison = pd.DataFrame([res_ma, res_gm])
       print(comparison[['method', 'Total_Return', 'Max_Drawdown', 'Signal_Accuracy', 'Num_Signals']].to_string(index=False))
       
       # 评分
       ma_score = 0
       gm_score = 0
       
       if res_ma['Total_Return'] > res_gm['Total_Return']:
           ma_score += 1
       else:
           gm_score += 1
       
       if res_ma['Max_Drawdown'] > res_gm['Max_Drawdown']:  # 回撤越小越好
           gm_score += 1
       else:
           ma_score += 1
       
       if res_ma['Signal_Accuracy'] > res_gm['Signal_Accuracy']:
           ma_score += 1
       else:
           gm_score += 1
       
       results.append({
           'Ticker': ticker,
           'MA200_Score': ma_score,
           'GMMA_Score': gm_score,
           'Winner': 'MA200' if ma_score > gm_score else 'GMMA' if gm_score > ma_score else 'TIE'
       })

print("\n" + "=" * 80)
print("总体对比结果")
print("=" * 80)
summary_df = pd.DataFrame(results)
print(summary_df.to_string(index=False))

# 最终建议
print("\n" + "=" * 80)
print("📌 建议")
print("=" * 80)
ma_wins = sum(1 for r in results if r['Winner'] == 'MA200')
gm_wins = sum(1 for r in results if r['Winner'] == 'GMMA')

if ma_wins > gm_wins:
   print("✅ 推荐使用 MA200")
   print("原因：更简单、计算快、在回测中表现更稳定。适合实时应用。")
elif gm_wins > ma_wins:
   print("✅ 推荐使用 GMMA")
   print("原因：多EMA组合能更灵敏地捕捉趋势反转，信号准确率更高。")
else:
   print("⚖️  两者势均力敌")
   print("建议根据实际场景选择：")
   print("  - 追求简洁 → MA200")
   print("  - 追求敏感度 → GMMA")
