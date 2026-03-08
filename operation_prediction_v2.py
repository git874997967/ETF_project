import pandas as pd
import pandas_ta as ta
import numpy as np
import yfinance as yf
import json
import os
import sys
import random
import statistics
from datetime import timedelta


class StrategyEngine:
    """封装的策略引擎，支持配置父体/子体、参数化阈值、双模逻辑和各种诊断。"""
    def __init__(self, parent_map=None):
        # 默认映射可在外部传入
        self.underlying_map = parent_map or {
            'soxl': 'SOXX',
            'soxs': 'SOXX',
            'tqqq': 'QQQ',
            'sqqq': 'QQQ'
        }

    @staticmethod
    def normalize_columns(df):
        """将 DataFrame 列名统一转换为小写字符串。
        对于 MultiIndex 会先取第一级再小写。
        返回修改后的 DataFrame 以支持链式调用。"""
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0).str.lower()
        else:
            df.columns = [c.lower() if isinstance(c, str) else c for c in df.columns]
        return df

    @staticmethod
    def compute_gmma_trend(df, fast_emas=[3,5,8,10,12,15], slow_emas=[30,35,40,45,50,60]):
        close = df['close']
        for p in fast_emas:
            df[f'ema_fast_{p}'] = close.ewm(span=p, adjust=False).mean()
        for p in slow_emas:
            df[f'ema_slow_{p}'] = close.ewm(span=p, adjust=False).mean()
        slow_avg = df[[f'ema_slow_{p}' for p in slow_emas]].mean(axis=1)
        fast_above_slow = (df[[f'ema_fast_{p}' for p in fast_emas]] > slow_avg.values.reshape(-1,1)).all(axis=1)
        fast_below_slow = (df[[f'ema_fast_{p}' for p in fast_emas]] < slow_avg.values.reshape(-1,1)).all(axis=1)
        df['gmma_slow_avg'] = slow_avg
        trend = pd.Series('mixed', index=df.index)
        trend[fast_above_slow] = 'uptrend'
        trend[fast_below_slow] = 'downtrend'
        return trend

    def get_underlying(self, levered):
        return self.underlying_map.get(levered.lower(), levered)

    @staticmethod
    def load_config(path='config.json'):
        """Load per-ticker configuration from JSON.

        Expected format (partial):
        {
          "per_ticker": {
              "tqqq": {"parent": "QQQ", "rsi": 65, "bbl": 1.14},
              "soxl": {"parent": "SOXX", "rsi": 50, "bbl": 1.00}
          }
        }
        Returns the `per_ticker` dict or None if unavailable.
        """
        if not os.path.exists(path):
            return None
        try:
            with open(path, 'r', encoding='utf-8') as f:
                cfg = json.load(f)
            return cfg.get('per_ticker')
        except Exception:
            return None

    def backtest(self, leverage_ticker, start_date="2024-01-01", end_date="2026-02-27",
                 rsi_threshold=30, bbl_buffer=1.0, strict_gmma=False, vol_filter_pct=None):
        """回测单只杠杆 ETF，并返回详细结果。
        支持双模逻辑：ModeA (轻仓顺势回调) 与 ModeB (重仓深度超卖)
        """
        underlying = self.get_underlying(leverage_ticker)
        data_start = pd.to_datetime(start_date) - pd.DateOffset(days=300)
        df_u = yf.download(underlying, start=data_start, end=end_date, progress=False)
        if df_u.empty: return None
        df_u = self.normalize_columns(df_u)
        if 'close' not in df_u.columns: return None
        # indicators on underlying
        df_u.ta.rsi(length=14, append=True)
        df_u.ta.bbands(length=20, std=2.0, append=True)
        df_u.ta.adx(length=14, append=True)
        df_u.ta.atr(length=14, append=True)
        df_u = self.normalize_columns(df_u)
        df_u['gmma_trend'] = self.compute_gmma_trend(df_u)
        df_u = df_u.dropna().copy()
        rsi_col = 'rsi_14'; bbl_col = 'bbl_20'; adx_col = 'adx_14'; atr_col = 'atr_14'
        disable_rsi = (df_u[adx_col] > 30) & (df_u[adx_col].diff() > 0)
        cond_oversold = (df_u[rsi_col] < rsi_threshold) & (~disable_rsi)
        cond_breakout = (df_u['close'] < df_u[bbl_col] * bbl_buffer)
        atr_pct = df_u[atr_col] / df_u['close'] if atr_col in df_u.columns else pd.Series(0,index=df_u.index)
        cond_vol_ok = (atr_pct < vol_filter_pct) if vol_filter_pct else pd.Series(True,index=df_u.index)
        cond_bull_trend = (df_u['gmma_trend']=='uptrend')
        if strict_gmma:
            cond_bull_trend = cond_bull_trend & (df_u['gmma_slow_avg'].diff() > 0)
        # dual mode
        df_u['Signal_Type']='WAIT'; df_u['Suggested_Size']=0.0
        # ModeA: trend dip
        maskA = cond_oversold & cond_breakout & cond_bull_trend & cond_vol_ok
        df_u.loc[maskA,'Signal_Type']='MODE A BUY (30%)'; df_u.loc[maskA,'Suggested_Size']=0.3
        # ModeB: deep value
        maskB = (df_u[rsi_col] < 30) & cond_breakout & (~cond_bull_trend) & cond_vol_ok
        df_u.loc[maskB,'Signal_Type']='MODE B BUY (70%)'; df_u.loc[maskB,'Suggested_Size']=0.7
        # Sell: RSI>70 or forced exit
        sell = (df_u[rsi_col]>70)
        if 'gmma_slow_avg' in df_u.columns:
            sell = sell | (df_u['close'] < df_u['gmma_slow_avg'])
        df_u['Position']=np.nan
        df_u.loc[df_u['Suggested_Size']>0,'Position']=df_u['Suggested_Size']
        df_u.loc[sell,'Position']=0
        df_u['Position']=df_u['Position'].ffill().shift(1).fillna(0)
        df_l = yf.download(leverage_ticker, start=data_start, end=end_date, progress=False)
        df_l = self.normalize_columns(df_l)
        df_u = df_u.join(df_l[['close']], how='left', rsuffix='_lev')
        df_u.rename(columns={'close_lev':'close_levered'}, inplace=True)
        df_u['Market_Ret']=df_u['close_levered'].pct_change()
        df_u['Strategy_Ret']=df_u['Position']*df_u['Market_Ret']
        df_u['Equity']=(1+df_u['Strategy_Ret']).cumprod()
        result_df = df_u.loc[start_date:]
        if result_df.empty: return None
        total_ret = result_df['Equity'].iloc[-1]/result_df['Equity'].iloc[0]-1
        days=(result_df.index[-1]-result_df.index[0]).days
        years=max(days/365.25,1/365.25)
        apy=(result_df['Equity'].iloc[-1]/result_df['Equity'].iloc[0])**(1.0/years)-1
        signal_counts = result_df['Signal_Type'].value_counts()
        latest = result_df.iloc[-1]
        trend_text = "牛市 (上升趋势)" if latest.get('gmma_trend','mixed')=='uptrend' else "熊市" if latest.get('gmma_trend','mixed')=='downtrend' else "震荡"
        ui={"日期":str(latest.name.date()),"槓杆ETF":leverage_ticker,"基礎標的":underlying,"趨勢 (GMMA)":trend_text,
            "信號":latest['Signal_Type'],"建議倉位":f"{int(latest['Suggested_Size']*100)}%",
            "當前價格":round(latest['close_levered'],2),"布林下軌":round(latest[bbl_col],2)}
        # DCA suggestion
        ui['DCA']={"RSI60":"建議倉位30%","RSI45":"補倉40%"}
        # volatility adjust comment
        if vol_filter_pct and atr_pct.iloc[-1]>vol_filter_pct:
            ui['Volatility_Notice']='ATR in high range, reduce allocation'
        return {"Ticker":leverage_ticker,"Underlying":underlying,"APY":apy,"Total_Return":total_ret,
                "Result_DF":result_df,"UI_Summary":ui,"Signal_Statistics":signal_counts.to_dict()}

    def analyze_result_df(self, df, trade_cost=0.001):
        res={}
        df=df.copy()
        res['days']=(df.index[-1]-df.index[0]).days
        eq=df['Equity'].fillna(method='ffill').fillna(1.0)
        cummax=eq.cummax(); dd=eq/cummax-1.0; res['max_drawdown']=dd.min()
        strat=df['Strategy_Ret'].fillna(0); mean_d=strat.mean(); std_d=strat.std()
        res['sharpe']=(mean_d/std_d*(252**0.5)) if std_d and std_d>0 else None
        pos=df['Position'].fillna(0)
        entries=df[(pos>0)&(pos.shift(1).fillna(0)==0)].index.tolist()
        exits=df[(pos==0)&(pos.shift(1).fillna(0)>0)].index.tolist()
        trades=[]
        for i,e in enumerate(entries):
            ex=exits[i] if i<len(exits) else df.index[-1]
            entry_price=df.at[e,'close_levered']; exit_price=df.at[ex,'close_levered']
            if pd.isna(entry_price) or pd.isna(exit_price): continue
            ret=exit_price/entry_price-1.0; trades.append(ret-trade_cost*2)
        res['num_trades']=len(trades)
        if trades:
            wins=[t for t in trades if t>0]; res['win_rate']=len(wins)/len(trades);
            res['avg_trade_return']=sum(trades)/len(trades)
            # SQN
            mean_tr=sum(trades)/len(trades); std_tr=np.std(trades, ddof=1)
            res['sqn']=(mean_tr/std_tr)*np.sqrt(len(trades)) if std_tr and std_tr>0 else None
        else:
            res['win_rate']=None; res['avg_trade_return']=None; res['sqn']=None
        split_idx=int(len(df)*0.7)
        if split_idx<2: res['in_sample_apy']=None; res['oos_apy']=None
        else:
            df_ins=df.iloc[:split_idx]; df_oos=df.iloc[split_idx:]
            ins_years=max((df_ins.index[-1]-df_ins.index[0]).days/365.25,1/365.25)
            oos_years=max((df_oos.index[-1]-df_oos.index[0]).days/365.25,1/365.25)
            res['in_sample_apy']=(df_ins['Equity'].iloc[-1]/df_ins['Equity'].iloc[0])**(1.0/ins_years)-1
            res['oos_apy']=(df_oos['Equity'].iloc[-1]/df_oos['Equity'].iloc[0])**(1.0/oos_years)-1
        res['signal_counts']=df['Signal_Type'].value_counts().to_dict()
        return res

# --- end of StrategyEngine definition ---

# placeholder for optional external config
LOADED_CONFIG = None

def normalize_columns(df):
    """將 DataFrame 列名統一轉換為小寫字符串。
    對於 MultiIndex 会先取第一級再小寫。
    返回修改後的 DataFrame 以支持鏈式調用。
    """
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0).str.lower()
    else:
        df.columns = [c.lower() if isinstance(c, str) else c for c in df.columns]
    return df


# === 支持邏輯：槓桿ETF 與 基礎ETF 映射 ===
UNDERLYING_MAP = {
    'soxl': 'SOXX',  # 3x Semicon Bull <-> SOX Index
    'soxs': 'SOXX',
    'tqqq': 'QQQ',   # 3x Nasdaq 100 Bull <-> QQQ
    'sqqq': 'QQQ'
}

def get_underlying_ticker(levered_ticker: str) -> str:
    """返回對應的基礎標的，如果沒有映射則返回自身。"""
    return UNDERLYING_MAP.get(levered_ticker.lower(), levered_ticker)


def compute_gmma_trend(df, fast_emas=[3, 5, 8, 10, 12, 15], slow_emas=[30, 35, 40, 45, 50, 60]):
    """計算 GMMA (Guppy Multiple Moving Average)
    返回: Series，值為 'uptrend' / 'downtrend' / 'mixed'
    """
    close = df['close']
    for p in fast_emas:
        df[f'ema_fast_{p}'] = close.ewm(span=p, adjust=False).mean()
    for p in slow_emas:
        df[f'ema_slow_{p}'] = close.ewm(span=p, adjust=False).mean()

    slow_avg = df[[f'ema_slow_{p}' for p in slow_emas]].mean(axis=1)
    fast_above_slow = (df[[f'ema_fast_{p}' for p in fast_emas]] > slow_avg.values.reshape(-1, 1)).all(axis=1)
    fast_below_slow = (df[[f'ema_fast_{p}' for p in fast_emas]] < slow_avg.values.reshape(-1, 1)).all(axis=1)

    # expose slow_avg as a column for exit logic
    df['gmma_slow_avg'] = slow_avg

    trend = pd.Series('mixed', index=df.index)
    trend[fast_above_slow] = 'uptrend'
    trend[fast_below_slow] = 'downtrend'
    return trend


# === 支持邏輯：槓桿ETF 與 基礎ETF 映射 ===


def run_tiered_backtest(leverage_ticker, start_date="2024-01-01", end_date="2026-02-27",
                        rsi_threshold=30, bbl_buffer=1.0, strict_gmma=False, vol_filter_pct=None):
    """Compatibility wrapper that uses :class:`StrategyEngine` under the hood."""
    engine = StrategyEngine()
    return engine.backtest(leverage_ticker, start_date=start_date, end_date=end_date,
                            rsi_threshold=rsi_threshold, bbl_buffer=bbl_buffer,
                            strict_gmma=strict_gmma, vol_filter_pct=vol_filter_pct)


# === 運行示例：tiered backtest ===
# 运行并比较多个阈值场景
scenarios = [
    {"name": "original", "rsi": 30, "bbl": 1.0},
    {"name": "moderate", "rsi": 40, "bbl": 1.01},
    {"name": "relaxed", "rsi": 50, "bbl": 1.03},
]

tickers = list(UNDERLYING_MAP.keys())

for s in scenarios:
    print(f"\n==== Scenario: {s['name']} (RSI<{s['rsi']}, BB buffer={s['bbl']}) ====")
    apys = []
    for t in tickers:
        res = run_tiered_backtest(t, rsi_threshold=s['rsi'], bbl_buffer=s['bbl'])
        if not res: continue
        apy = res.get('APY', 0.0)
        apys.append(apy)
        print(f"{res['Ticker']:<6} | APY: {apy:>7.2%} | CumRet: {res['Total_Return']:>7.2%} | Underlying: {res['Underlying']}")
    if apys:
        avg_apy = sum(apys) / len(apys)
        print(f"Average APY (equal-weighted across tickers): {avg_apy:.2%}")
        print("Target 20% APY reached:" , avg_apy >= 0.20)


def find_threshold_for_target(ticker, target_apy=0.20, rsi_vals=range(30, 61, 5), bbl_steps=None):
    """Grid-search RSI thresholds and BB buffer to find a combination reaching target APY.
    Returns the best (rsi, bbl, apy) tuple that meets target (maximizing APY), or None.
    """
    if bbl_steps is None:
        bbl_steps = [1.00 + i*0.01 for i in range(0, 11)]  # 1.00 .. 1.10

    best = None
    for r in rsi_vals:
        for b in bbl_steps:
            res = run_tiered_backtest(ticker, rsi_threshold=r, bbl_buffer=b)
            if not res:
                continue
            apy = res.get('APY', -99)
            if apy >= target_apy:
                if best is None or apy > best[2]:
                    best = (r, b, apy)
    return best


def apply_config_and_run(config):
    """Run backtest for tickers using a config mapping: {ticker: {'rsi':.., 'bbl':..}}"""
    results = {}
    for t, params in config.items():
        res = run_tiered_backtest(t, rsi_threshold=params.get('rsi', 30), bbl_buffer=params.get('bbl', 1.0))
        results[t] = res
        if res:
            print(f"Applied {t}: APY {res['APY']:.2%}, CumRet {res['Total_Return']:.2%}")
        else:
            print(f"No result for {t}")
    return results


# 如果存在 config.json，则加载以便按需应用 per-ticker 阈值
CONFIG_PATH = 'config.json'
LOADED_CONFIG = None
if os.path.exists(CONFIG_PATH):
    try:
        with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
            cfg = json.load(f)
        per = cfg.get('per_ticker')
        if per:
            LOADED_CONFIG = per
            print(f"Loaded per-ticker config from {CONFIG_PATH}: {list(per.keys())}")
    except Exception as e:
        print(f"Failed to load {CONFIG_PATH}: {e}")


# --- 自动为 tqqq 和 soxl 搜索能达到 20% 的阈值（若存在） ---
targets = ['tqqq', 'soxl']
found = {}
for tk in targets:
    print(f"\nSearching thresholds for {tk} to reach 20% APY...")
    best = find_threshold_for_target(tk, target_apy=0.20, rsi_vals=range(30, 66, 5), bbl_steps=[1.00 + i*0.01 for i in range(0, 21)])
    if best:
        r, b, apy = best
        print(f"Found for {tk}: RSI<{r}, BB buffer={b:.2f} -> APY {apy:.2%}")
        found[tk] = {'rsi': r, 'bbl': b, 'apy': apy}
    else:
        print(f"No threshold combo in grid reaches 20% APY for {tk}.")


if found:
    print("\nApplying found per-ticker thresholds and showing results:")
    cfg = {t: {'rsi': found[t]['rsi'], 'bbl': found[t]['bbl']} for t in found}
    apply_config_and_run(cfg)
else:
    print("\nNo per-ticker thresholds found that meet 20% APY in the searched grid.")


def analyze_result_df(df, trade_cost=0.001):
    """Compute robustness metrics for a result DataFrame from run_tiered_backtest.

    Returns a dict with trades, win_rate, avg_trade_ret, max_dd, sharpe, oos_apy, in_sample_apy, signal_counts
    """
    res = {}
    df = df.copy()
    # basic stats
    res['days'] = (df.index[-1] - df.index[0]).days
    # max drawdown
    eq = df['Equity'].fillna(method='ffill').fillna(1.0)
    cummax = eq.cummax()
    dd = eq / cummax - 1.0
    res['max_drawdown'] = dd.min()

    # strategy daily returns
    strat = df['Strategy_Ret'].fillna(0)
    mean_d = strat.mean()
    std_d = strat.std()
    res['sharpe'] = (mean_d / std_d * (252 ** 0.5)) if std_d and std_d > 0 else None

    # trades: detect entries and exits based on Position
    pos = df['Position'].fillna(0)
    entries = df[(pos > 0) & (pos.shift(1).fillna(0) == 0)].index.tolist()
    exits = df[(pos == 0) & (pos.shift(1).fillna(0) > 0)].index.tolist()
    # pair entries/exits
    trades = []
    for i, e in enumerate(entries):
        ex = exits[i] if i < len(exits) else df.index[-1]
        entry_price = df.at[e, 'close_levered']
        exit_price = df.at[ex, 'close_levered']
        if pd.isna(entry_price) or pd.isna(exit_price):
            continue
        ret = exit_price / entry_price - 1.0
        # subtract simple round-trip cost
        ret_after_cost = ret - trade_cost * 2
        trades.append(ret_after_cost)
    res['num_trades'] = len(trades)
    if trades:
        wins = [t for t in trades if t > 0]
        res['win_rate'] = len(wins) / len(trades)
        res['avg_trade_return'] = sum(trades) / len(trades)
    else:
        res['win_rate'] = None
        res['avg_trade_return'] = None

    # in-sample / out-of-sample split (70/30 by time)
    split_idx = int(len(df) * 0.7)
    if split_idx < 2:
        res['in_sample_apy'] = None
        res['oos_apy'] = None
    else:
        df_ins = df.iloc[:split_idx]
        df_oos = df.iloc[split_idx:]
        ins_years = max((df_ins.index[-1] - df_ins.index[0]).days / 365.25, 1/365.25)
        oos_years = max((df_oos.index[-1] - df_oos.index[0]).days / 365.25, 1/365.25)
        ins_apy = (df_ins['Equity'].iloc[-1] / df_ins['Equity'].iloc[0]) ** (1.0/ins_years) - 1
        oos_apy = (df_oos['Equity'].iloc[-1] / df_oos['Equity'].iloc[0]) ** (1.0/oos_years) - 1
        res['in_sample_apy'] = ins_apy
        res['oos_apy'] = oos_apy

    # signal counts
    res['signal_counts'] = df['Signal_Type'].value_counts().to_dict()
    return res


# --- 验证已找到阈值的稳健性（针对 tqqq 和 soxl） ---
print("\n=== Robustness checks for found thresholds ===")
validated = {}
for tk, params in found.items():
    print(f"\nValidating {tk} with RSI<{params['rsi']}, BB={params['bbl']}")
    res = run_tiered_backtest(tk, rsi_threshold=params['rsi'], bbl_buffer=params['bbl'])
    if not res:
        print("no result")
        continue
    df_r = res.get('Result_DF')
    metrics = analyze_result_df(df_r, trade_cost=0.001)
    validated[tk] = {'params': params, 'metrics': metrics}
    print(json.dumps({'APY': res['APY'], 'CumRet': res['Total_Return'], 'metrics': metrics}, indent=2, ensure_ascii=False))



def extract_trades_from_df(df, trade_cost=0.001):
    """Return a list of trade returns (round-trip) from result_df."""
    trades = []
    pos = df['Position'].fillna(0)
    entries = df[(pos > 0) & (pos.shift(1).fillna(0) == 0)].index.tolist()
    exits = df[(pos == 0) & (pos.shift(1).fillna(0) > 0)].index.tolist()
    for i, e in enumerate(entries):
        ex = exits[i] if i < len(exits) else df.index[-1]
        entry_price = df.at[e, 'close_levered']
        exit_price = df.at[ex, 'close_levered']
        if pd.isna(entry_price) or pd.isna(exit_price):
            continue
        ret = exit_price / entry_price - 1.0
        trades.append(ret - trade_cost * 2)
    return trades


def monte_carlo_drawdown(trades, n_iter=1000, seed=42):
    """Shuffle trade order and compute worst-case max drawdown across iterations."""
    if not trades:
        return None
    random.seed(seed)
    worst_dd = 0
    dd_list = []
    for _ in range(n_iter):
        seq = trades[:]  # copy
        random.shuffle(seq)
        eq = 1.0
        series = [eq]
        for r in seq:
            eq = eq * (1 + r)
            series.append(eq)
        cummax = max(series)
        dd = min([(v / cummax - 1.0) for v in series])
        dd_list.append(dd)
        if dd < worst_dd:
            worst_dd = dd
    return {"worst_dd": worst_dd, "median_dd": statistics.median(dd_list), "pct_5_dd": sorted(dd_list)[int(0.05*len(dd_list))]}


def run_filter_and_montecarlo_from_sensitivity(filename, ticker, min_trades=50, min_apy=0.20, max_dd=0.30, n_iter=2000):
    """Load sensitivity json and find combos meeting constraints, then run Monte Carlo for each and save results."""
    if not os.path.exists(filename):
        print(f"File {filename} not found")
        return {}
    with open(filename, 'r', encoding='utf-8') as f:
        rows = json.load(f)
    matched = []
    for r in rows:
        if (r.get('num_trades') or 0) >= min_trades and (r.get('apy') or 0) >= min_apy and abs(r.get('max_dd') or 1) <= max_dd:
            matched.append(r)
    results = {}
    for m in matched:
        rsi = m['rsi']; bbl = m['bbl']
        print(f"Running montecarlo for {ticker} RSI<{rsi} BB={bbl} ...")
        res = run_tiered_backtest(ticker, rsi_threshold=rsi, bbl_buffer=bbl)
        if not res:
            continue
        df = res.get('Result_DF')
        trades = extract_trades_from_df(df, trade_cost=0.0)
        mc = monte_carlo_drawdown(trades, n_iter=n_iter, seed=42)
        results[f"rsi_{rsi}_bbl_{bbl}"] = {'res_summary': {'apy': res['APY'], 'apy_cost_0.001': res.get('APY_with_cost_0.001'), 'num_trades': len(trades), 'max_dd': analyze_result_df(df).get('max_drawdown')}, 'montecarlo': mc}
    outp = f"montecarlo_{ticker}.json"
    with open(outp, 'w', encoding='utf-8') as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"Monte Carlo results saved to {outp}")
    return results


def sensitivity_analysis(ticker, start_date="2016-01-01", end_date="2026-02-27", rsi_range=range(30,66,5), bbl_range=None, vol_filter=None, strict_gmma=False):
    """Run grid sensitivity analysis and return summary list of (rsi,bbl,apy,num_trades,max_dd).
    If strict_gmma=True it will pass that flag to the backtest.
    bbl_range: iterable of multipliers e.g. [1.00,1.01,...]
    """
    if bbl_range is None:
        bbl_range = [1.0 + i*0.01 for i in range(0,21)]
    rows = []
    for r in rsi_range:
        for b in bbl_range:
            res = run_tiered_backtest(ticker, start_date=start_date, end_date=end_date, rsi_threshold=r, bbl_buffer=b, strict_gmma=strict_gmma)
            if not res:
                continue
            df = res.get('Result_DF')
            metrics = analyze_result_df(df)
            rows.append({"rsi": r, "bbl": b, "apy": res['APY'], "apy_cost_0.001": res.get('APY_with_cost_0.001'), "apy_cost_0.002": res.get('APY_with_cost_0.002'), "num_trades": metrics.get('num_trades'), "max_dd": metrics.get('max_drawdown'), "win_rate": metrics.get('win_rate')})
    # sort by apy desc
    rows_sorted = sorted(rows, key=lambda x: x['apy'], reverse=True)
    # print top 10
    print(f"\nSensitivity top results for {ticker} (showing top 10):")
    for r in rows_sorted[:10]:
        print(r)
    return rows_sorted


# --- Run sensitivity tests for recommended ranges and save results ---
def save_json(path, obj):
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)

print("\n=== Running sensitivity analysis for recommended ranges ===")
# TQQQ: RSI < 55, BB buffer 0.98-1.02
tqqq_rsi = range(30, 56, 5)
tqqq_bbl = [0.98, 0.99, 1.00, 1.01, 1.02]
tqqq_rows = sensitivity_analysis('tqqq', start_date='2016-01-01', end_date='2026-02-27', rsi_range=tqqq_rsi, bbl_range=tqqq_bbl)
save_json('sensitivity_tqqq.json', tqqq_rows)

# SOXL: RSI < 50, BB buffer 0.95-1.00
soxl_rsi = range(30, 51, 5)
soxl_bbl = [0.95, 0.96, 0.97, 0.98, 0.99, 1.00]
soxl_rows = sensitivity_analysis('soxl', start_date='2016-01-01', end_date='2026-02-27', rsi_range=soxl_rsi, bbl_range=soxl_bbl)
save_json('sensitivity_soxl.json', soxl_rows)

print("Sensitivity results saved: sensitivity_tqqq.json, sensitivity_soxl.json")

# --- Refined SOXL grid per new instructions ---
print("\n=== Running refined SOXL sensitivity with extended RSI range and strict GMMA ===")
# include backup candidate RSI 40 and relaxed high range 55-60
soxl_refined_rsi = [40] + list(range(55, 61, 5))
soxl_refined_bbl = [1.00, 1.03, 1.04, 1.05]
soxl_refined_rows = sensitivity_analysis('soxl', start_date='2016-01-01', end_date='2026-02-27', rsi_range=soxl_refined_rsi, bbl_range=soxl_refined_bbl, strict_gmma=True)
save_json('sensitivity_soxl_refined.json', soxl_refined_rows)
print("Refined sensitivity saved: sensitivity_soxl_refined.json")

