import argparse
import json
import os
import sys
from datetime import datetime
from functools import wraps

import pandas as pd
import pandas_ta_classic as ta
import numpy as np
import yfinance as yf


def safe_external(default=None, reraise=False):
    """装饰器：包装外部 API / I/O / 类型转换，捕获异常并返回默认值或重新抛出。"""

    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            try:
                return func(*args, **kwargs)
            except Exception as e:
                print(f"[safe_external] {func.__name__} failed: {e}", file=sys.stderr)
                if reraise:
                    raise
                return default

        return wrapper

    return decorator


@safe_external(default=pd.DataFrame())
def _fetch_yahoo(ticker, start, end):
    """包装 yf.download，失败时返回空 DataFrame。"""
    return yf.download(ticker, start=start, end=end, progress=False)


@safe_external(default=None)
def _safe_datetime(s):
    """安全日期解析，失败时返回 None。"""
    return pd.to_datetime(s)


@safe_external(default=None)
def _load_json_file(path):
    """安全读取 JSON，失败时返回 None。"""
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


class StrategyEngine:
    """封装的策略引擎：参数化、双模逻辑与诊断方法。可独立于 v2 运行以避免导入副作用。"""

    def __init__(self, parent_map=None):
        self.underlying_map = parent_map or {
            "soxl": "SOXX",
            "soxs": "SOXX",
            "tqqq": "QQQ",
            "sqqq": "QQQ",
        }

    @staticmethod
    def normalize_columns(df):
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0).str.lower()
        else:
            df.columns = [c.lower() if isinstance(c, str) else c for c in df.columns]
        return df

    @staticmethod
    def compute_gmma_trend(
        df, fast_emas=[3, 5, 8, 10, 12, 15], slow_emas=[30, 35, 40, 45, 50, 60]
    ):
        close = df["close"]
        for p in fast_emas:
            df[f"ema_fast_{p}"] = close.ewm(span=p, adjust=False).mean()
        for p in slow_emas:
            df[f"ema_slow_{p}"] = close.ewm(span=p, adjust=False).mean()
        slow_avg = df[[f"ema_slow_{p}" for p in slow_emas]].mean(axis=1)
        fast_above_slow = (
            df[[f"ema_fast_{p}" for p in fast_emas]] > slow_avg.values.reshape(-1, 1)
        ).all(axis=1)
        fast_below_slow = (
            df[[f"ema_fast_{p}" for p in fast_emas]] < slow_avg.values.reshape(-1, 1)
        ).all(axis=1)
        df["gmma_slow_avg"] = slow_avg
        trend = pd.Series("mixed", index=df.index)
        trend[fast_above_slow] = "uptrend"
        trend[fast_below_slow] = "downtrend"
        return trend

    def get_underlying(self, levered):
        # 查找 map：key 用 lower，未命中则返回 upper 以保证 ticker 全大写
        return self.underlying_map.get(levered.lower(), levered.upper())

    @staticmethod
    def load_config(path="config.json"):
        if not os.path.exists(path):
            return None
        cfg = _load_json_file(path)
        return cfg.get("per_ticker") if cfg else None

    def backtest(
        self,
        leverage_ticker,
        start_date="2016-01-01",
        end_date="2026-02-27",
        mode_a_rsi=55,
        mode_b_rsi=30,
        bbl_buffer=1.0,
        strict_gmma=False,
        vol_filter_pct=None,
        mode_b_confirm=True,
    ):
        underlying = self.get_underlying(leverage_ticker)
        data_start = _safe_datetime(start_date)
        if data_start is None:
            return None
        data_start = data_start - pd.DateOffset(days=300)

        # Parent (underlying) and Child (levered) data
        df_parent = _fetch_yahoo(underlying, data_start, end_date)
        df_child = _fetch_yahoo(leverage_ticker, data_start, end_date)
        # [新增] 下载 VIX 数据用于宏观对冲（失败时用 NaN，VIX 过滤器不生效）
        df_vix = _fetch_yahoo("^VIX", data_start, end_date)

        if df_parent.empty or df_child.empty:
            return None
        df_parent = self.normalize_columns(df_parent)
        df_child = self.normalize_columns(df_child)

        # Indicators: parent -> regime & signals; child -> ATR & execution
        df_parent.ta.rsi(length=14, append=True)
        df_parent.ta.bbands(length=20, std=2.0, append=True)
        df_parent.ta.adx(length=14, append=True)
        df_parent = self.normalize_columns(df_parent)
        df_parent["gmma_trend"] = self.compute_gmma_trend(df_parent)

        df_child.ta.atr(length=14, append=True)
        df_child = self.normalize_columns(df_child)

        # align indices
        df = df_parent.join(
            df_child[["close", "atrr_14", "open"]], how="inner", rsuffix="_child"
        )
        if not df_vix.empty:
            df_vix = self.normalize_columns(df_vix)
            df = df.join(df_vix[["close"]], how="left", rsuffix="_vix")
        else:
            df["close_vix"] = np.nan
        df.rename(
            columns={
                "close_child": "close_levered",
                "atrr_14": "atr_14_child",
                "open_child": "open_child",
                "close_vix": "vix_close",
            },
            inplace=True,
        )
        df = df.dropna().copy()
        # print('df cols', df.columns.tolist())
        # columns
        rsi_col = "rsi_14"
        bbl_col = "bbl_20_2.0"
        bbu_col = "bbu_20_2.0"
        ma50_col = "ema_slow_50"
        adx_col = "adx_14"
        atr_col_child = "atr_14_child"

        # ADX-based disable for RSI
        disable_rsi = (df[adx_col] > 30) & (df[adx_col].diff() > 0)

        # Volume of conditions
        cond_breakout = df["close"] < df[bbl_col] * bbl_buffer
        cond_bull_trend = df["gmma_trend"] == "uptrend"
        if strict_gmma:
            cond_bull_trend = cond_bull_trend & (df["gmma_slow_avg"].diff() > 0)

        # prepare signal columns
        df["Signal_Type"] = "WAIT"
        df["Suggested_Size"] = 0.0

        # === 1. 计算 ATR 风险调节因子 (基于杠杆 ETF 波动率) ===
        # 计算当前 ATR 占价格的百分比
        atr_pct = df[atr_col_child] / df["close_levered"]

        # 风险对齐因子：假设目标单日风险暴露为 2% (0.02)
        # 如果 atr_pct 是 4%，则因子为 0.5，仓位减半
        # 使用 .clip(0, 1.0) 确保在波动极小时不会过度放大杠杆（最高不超过原始设定的 0.3/0.7）
        risk_factor = (0.02 / atr_pct).clip(0, 1.0)
        #
        # === [新增] VIX 过滤器：宏观避险 ===
        # 计算 VIX 日涨幅
        vix_spike = (df["vix_close"] > 35) | (df["vix_close"].pct_change() > 0.20)

        # 如果 VIX 处于高位或出现 20% 以上的单日暴涨，强制将风险因子减半
        risk_factor = np.where(vix_spike, risk_factor * 0.5, risk_factor)

        # === 2. Mode A: 分批入场 (DCA) 逻辑 ===

        # [Tier 1] 基础入场：RSI 跌破初始阈值 (例如 60)
        cond_mode_a_t1 = (df[rsi_col] < mode_a_rsi) & (~disable_rsi) & cond_bull_trend
        df.loc[cond_mode_a_t1, "Signal_Type"] = "MODE A (Tier 1)"
        # 实际仓位 = 基础 30% * 风险调节因子

        df.loc[cond_mode_a_t1, "Suggested_Size"] = 0.3 * risk_factor[cond_mode_a_t1]

        # [Tier 2] 深度回调补仓：RSI 进一步跌破 45
        # 此时将目标总仓位提升至 70%
        cond_mode_a_t2 = (df[rsi_col] < 45) & (~disable_rsi) & cond_bull_trend
        df.loc[cond_mode_a_t2, "Signal_Type"] = "MODE A (Tier 2/DCA)"
        # 实际仓位 = 目标总额 70% * 风险调节因子
        df.loc[cond_mode_a_t2, "Suggested_Size"] = 0.7 * risk_factor[cond_mode_a_t2]

        # Mode B: Deep Value (use parent RSI lower threshold + bollinger break + bear regime)
        prev_rsi = df[rsi_col].shift(1)
        prev_close = df["close"].shift(1)
        prev_bbl = df[bbl_col].shift(1)
        curr_close = df["close"]
        curr_open = df["open"]  # 注意：这里使用 Parent 的开盘价来判断收阳确认

        cond_panic_yesterday = (prev_rsi < mode_b_rsi) & (prev_close < prev_bbl)
        cond_rebound_today = (curr_close > curr_open) & (curr_close > prev_close)
        cond_bear_regime = df["gmma_trend"] == "downtrend"

        if mode_b_confirm:
            cond_mode_b = cond_panic_yesterday & cond_rebound_today & cond_bear_regime
        else:
            cond_mode_b = (
                (df[rsi_col] < mode_b_rsi)
                & (df["close"] < df[bbl_col] * bbl_buffer)
                & cond_bear_regime
            )

        df.loc[cond_mode_b, "Signal_Type"] = "MODE B BUY (Deep Value)"
        df.loc[cond_mode_b, "Suggested_Size"] = 0.7

        # === [新增] ATR 吊灯止损 (Chandelier Exit) ===
        # 计算过去 20 天的最高价 (基于杠杆 ETF)
        rolling_max_child = df["close_levered"].rolling(window=20).max()

        # 止损线 = 20日最高价 - 3倍 ATR
        df["chandelier_stop"] = rolling_max_child - (df[atr_col_child] * 3.0)

        # === 修改卖出逻辑 ===
        # 原逻辑：RSI > 70 立即卖出
        # 提升逻辑：RSI > 70 仅触发卖出，或价格跌破“吊灯止损线”，或跌破 GMMA 慢速均线
        sell_rsi = df[rsi_col] > 70
        sell_stop = df["close_levered"] < df["chandelier_stop"]

        sell = sell_rsi | sell_stop
        if "gmma_slow_avg" in df.columns:
            sell = sell | (df["close"] < df["gmma_slow_avg"])

        # === [新增] 时间维度压力测试：持仓时长控制 ===
        # 1. 识别每一笔交易的入场点 (仓位从 0 变为 0.3 或 0.7 的时刻)
        # 注意：这里需要先临时计算一个 position 趋势
        temp_pos = df["Suggested_Size"].copy()
        is_entry = (temp_pos > 0) & (temp_pos.shift(1).fillna(0) == 0)

        # 2. 计算自入场以来的天数 (简单实现：记录入场后的累积天数)
        # 我们利用 cumsum 为每笔交易编号，然后计算组内排名
        trade_id = is_entry.cumsum()
        days_held = df.groupby(trade_id).cumcount()

        # 3. 计算自入场以来的收益率
        # 记录入场时的价格
        entry_price = df["close_levered"].where(is_entry).ffill()
        current_profit = (df["close_levered"] / entry_price) - 1.0

        # 4. 强制减仓逻辑：持仓 > 10 天 且 收益 < 2%
        # 触发该条件时，将 Suggested_Size 强制减半
        time_risk_mask = (days_held > 10) & (current_profit < 0.02)
        df.loc[time_risk_mask, "Suggested_Size"] *= 0.5
        df.loc[time_risk_mask, "Signal_Type"] = "TIME-EXIT (Reduce 50%)"

        # Position (apply sizes, shift to avoid lookahead)
        df["Position"] = np.nan
        df.loc[df["Suggested_Size"] > 0, "Position"] = df["Suggested_Size"]
        df.loc[sell, "Position"] = 0
        df["Position"] = df["Position"].ffill().shift(1).fillna(0)

        # Returns using levered close
        df["Market_Ret"] = df["close_levered"].pct_change()
        df["Strategy_Ret"] = df["Position"] * df["Market_Ret"]
        df["Equity"] = (1 + df["Strategy_Ret"]).cumprod()
        result_df = df.loc[start_date:]
        if result_df.empty:
            return None
        eq_final = result_df["Equity"].iloc[-1]
        eq_init = result_df["Equity"].iloc[0]
        days = (result_df.index[-1] - result_df.index[0]).days
        years = max(days / 365.25, 1 / 365.25)
        total_ret_raw = eq_final / eq_init - 1
        apy_raw = (eq_final / eq_init) ** (1.0 / years) - 1
        total_ret = f"{total_ret_raw * 100:.2f}%"
        apy = f"{apy_raw * 100:.2f}%"
        signal_counts = result_df["Signal_Type"].value_counts()
        latest = result_df.iloc[-1]
        trend_text = (
            "牛市 (上升趋势)"
            if latest.get("gmma_trend", "mixed") == "uptrend"
            else "熊市" if latest.get("gmma_trend", "mixed") == "downtrend" else "震荡"
        )
        # 建议仓位：持仓时用 Position，否则用 Suggested_Size（解决 hold 日显示 0 的问题）
        rec_size = max(latest["Position"], latest["Suggested_Size"])

        # 布林上轨、MA50（若 bbu/ema_slow_50 不存在则跳过）
        bbu_val = round(latest[bbu_col], 2) if bbu_col in result_df.columns else None
        ma50_val = round(latest[ma50_col], 2) if ma50_col in result_df.columns else None

        ui = {
            "日期": str(latest.name.date()),
            "槓杆ETF": leverage_ticker,
            "基礎標的": underlying,
            "趨勢 (GMMA)": trend_text,
            "信號": latest["Signal_Type"],
            "建議倉位": f"{int(rec_size * 100)}%",
            "當前價格": round(latest["close_levered"], 2),
            "布林下軌": round(latest[bbl_col], 2),
            "布林上軌": bbu_val,
            "MA50": ma50_val,
        }
        # DCA 动态数据：当前 RSI、风险因子，以及满足条件时的理论仓位
        curr_rsi = round(latest[rsi_col], 1)
        curr_rf = round(float(np.asarray(risk_factor).flat[-1]), 2)
        tier1_size = int(0.3 * curr_rf * 100)
        tier2_size = int(0.7 * curr_rf * 100)
        ui["DCA"] = {
            "說明": "牛市回調時分批建倉，避免一次性重倉。滿足條件時建議倉位如下：",
            "當前RSI": curr_rsi,
            "當前風險因子": curr_rf,
            f"RSI<{mode_a_rsi} (Tier1)": f"建倉{tier1_size}%",
            "RSI<45 (Tier2)": f"補倉至{tier2_size}%",
        }
        if vol_filter_pct and atr_pct.iloc[-1] > vol_filter_pct:
            ui["Volatility_Notice"] = "ATR in high range, reduce allocation"
        return {
            "Ticker": leverage_ticker,
            "Underlying": underlying,
            "APY": apy,
            "Total_Return": total_ret,
            "Result_DF": result_df,
            "UI_Summary": ui,
            "Signal_Statistics": signal_counts.to_dict(),
        }

    def analyze_result_df(self, df, trade_cost=0.001):
        res = {}
        df = df.copy()
        res["days"] = (df.index[-1] - df.index[0]).days
        eq = df["Equity"].ffill().fillna(1.0)
        cummax = eq.cummax()
        dd = eq / cummax - 1.0
        res["max_drawdown"] = dd.min()
        strat = df["Strategy_Ret"].fillna(0)
        mean_d = strat.mean()
        std_d = strat.std()
        res["sharpe"] = (mean_d / std_d * (252**0.5)) if std_d and std_d > 0 else None
        pos = df["Position"].fillna(0)
        entries = df[(pos > 0) & (pos.shift(1).fillna(0) == 0)].index.tolist()
        exits = df[(pos == 0) & (pos.shift(1).fillna(0) > 0)].index.tolist()
        trades = []
        for i, e in enumerate(entries):
            ex = exits[i] if i < len(exits) else df.index[-1]
            entry_price = df.at[e, "close_levered"]
            exit_price = df.at[ex, "close_levered"]
            if pd.isna(entry_price) or pd.isna(exit_price):
                continue
            ret = exit_price / entry_price - 1.0
            trades.append(ret - trade_cost * 2)
        res["num_trades"] = len(trades)
        if trades:
            wins = [t for t in trades if t > 0]
            res["win_rate"] = len(wins) / len(trades)
            res["avg_trade_return"] = sum(trades) / len(trades)
            mean_tr = sum(trades) / len(trades)
            std_tr = np.std(trades, ddof=1)
            res["sqn"] = (
                (mean_tr / std_tr) * np.sqrt(len(trades))
                if std_tr and std_tr > 0
                else None
            )
        else:
            res["win_rate"] = None
            res["avg_trade_return"] = None
            res["sqn"] = None
        split_idx = int(len(df) * 0.7)
        if split_idx < 2:
            res["in_sample_apy"] = None
            res["oos_apy"] = None
        else:
            df_ins = df.iloc[:split_idx]
            df_oos = df.iloc[split_idx:]
            ins_years = max(
                (df_ins.index[-1] - df_ins.index[0]).days / 365.25, 1 / 365.25
            )
            oos_years = max(
                (df_oos.index[-1] - df_oos.index[0]).days / 365.25, 1 / 365.25
            )
            res["in_sample_apy"] = (
                df_ins["Equity"].iloc[-1] / df_ins["Equity"].iloc[0]
            ) ** (1.0 / ins_years) - 1
            res["oos_apy"] = (df_oos["Equity"].iloc[-1] / df_oos["Equity"].iloc[0]) ** (
                1.0 / oos_years
            ) - 1
        res["signal_counts"] = df["Signal_Type"].value_counts().to_dict()
        return res


@safe_external(default=None, reraise=True)
def save_json(path, obj):
    """安全写入 JSON，失败时打印错误并重新抛出。"""
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


def main():
    p = argparse.ArgumentParser(
        description="operation_prediction v3 — StrategyEngine CLI"
    )
    p.add_argument("action", choices=["run", "sensitivity"], help="action to perform")
    p.add_argument(
        "--ticker",
        nargs="+",
        default=["tqqq"],
        help="one or more tickers, e.g. tqqq soxl",
    )
    p.add_argument("--rsi", type=int, default=30)
    p.add_argument("--bbl", type=float, default=1.0)
    p.add_argument("--start", default="2016-01-01")
    p.add_argument("--end", default=datetime.now().strftime("%Y-%m-%d"))
    p.add_argument("--out", default=None, help="optional json output path")
    args = p.parse_args()

    engine = StrategyEngine()

    if args.action == "run":
        tickers = args.ticker if isinstance(args.ticker, list) else [args.ticker]
        all_summary = []
        for t in tickers:
            res = engine.backtest(
                t,
                start_date=args.start,
                end_date=args.end,
                mode_b_rsi=args.rsi,
                bbl_buffer=args.bbl,
            )
            if not res:
                all_summary.append({"Ticker": t, "Error": "No results (empty data)"})
                continue
            summary = {
                k: v
                for k, v in res.items()
                if k in ("Ticker", "Underlying", "APY", "Total_Return", "UI_Summary")
            }
            all_summary.append(summary)
        if args.out:
            save_json(args.out, all_summary)
        try:
            print(json.dumps(all_summary, ensure_ascii=False, indent=2))
        except UnicodeEncodeError:
            print(json.dumps(all_summary, ensure_ascii=True, indent=2))

    elif args.action == "sensitivity":
        tickers = args.ticker if isinstance(args.ticker, list) else [args.ticker]
        rsi_vals = [args.rsi]
        bbl_vals = [args.bbl]
        rows = []
        for t in tickers:
            for r in rsi_vals:
                for b in bbl_vals:
                    res = engine.backtest(
                        t,
                        start_date=args.start,
                        end_date=args.end,
                        mode_b_rsi=r,
                        bbl_buffer=b,
                    )
                    if not res:
                        continue
                    metrics = engine.analyze_result_df(res["Result_DF"])
                    # 约束条件：交易次数 > 15 且 最大回撤 < 35%
                    if (
                        metrics.get("num_trades", 0) >= 15
                        and metrics.get("max_drawdown", 0) > -0.35
                    ):
                        rows.append(
                            {
                                "ticker": t,
                                "rsi": r,
                                "bbl": b,
                                "sqn": metrics.get("sqn", 0),
                                "num_trades": metrics.get("num_trades"),
                                "apy": res["APY"],
                                "max_dd": metrics.get("max_drawdown"),
                            }
                        )
        rows_sorted = sorted(rows, key=lambda x: x.get("sqn", 0), reverse=True)
        if args.out:
            save_json(args.out, rows_sorted)
        try:
            print(json.dumps(rows_sorted, ensure_ascii=False, indent=2))
        except UnicodeEncodeError:
            print(json.dumps(rows_sorted, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
