import pandas as pd
import pandas_ta as ta
import numpy as np
import yfinance as yf
from datetime import timedelta

class StrategyEngine:
    def __init__(self, target_ticker="SOXL", parent_ticker="SOXX"):
        self.target = target_ticker
        self.parent = parent_ticker
        
    def fetch_data(self, ticker, period="2y"):
        """ETL: 处理 MultiIndex 和 列名标准化"""
        df = yf.download(ticker, period=period, progress=False, auto_adjust=True)
        if df.empty: return None
        
        # Flatten MultiIndex if exists
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        df.columns = [c.lower() for c in df.columns]
        return df

    def run_analysis(self):
        print(f"🚀 初始化策略引擎: {self.target} (由 {self.parent} 驱动)...")
        
        # 1. 获取数据 (Data Ingestion)
        df_target = self.fetch_data(self.target)
        df_parent = self.fetch_data(self.parent) # 母体 (SOXX/QQQ)
        
        if df_target is None or df_parent is None:
            return "Error: Data fetch failed"

        # 2. 在母体上计算 GMMA 和 RSI (信号源)
        # 原因: SOXX 的趋势更真实，不受杠杆损耗干扰
        # GMMA 长期组: 30, 35, 40, 45, 50, 60
        gmma_periods = [30, 35, 40, 45, 50, 60]
        for p in gmma_periods:
            df_parent.ta.ema(length=p, append=True)
        
        df_parent.ta.rsi(length=14, append=True)
        df_parent.ta.bbands(length=20, std=2.0, append=True)
        
        # 3. 在子体上计算 ATR (用于止损)
        df_target.ta.atr(length=14, append=True)
        
        # === 核心逻辑: 跨资产信号映射 ===
        # 我们需要把 Parent 的指标对齐到 Target 的日期上
        # 使用 reindex 确保日期一致
        common_index = df_target.index.intersection(df_parent.index)
        df_t = df_target.loc[common_index].copy()
        df_p = df_parent.loc[common_index].copy()
        
        # 提取 Parent 指标
        rsi_p = df_p['RSI_14']
        bbl_p = df_p['BBL_20_2.0'] # 布林下轨
        close_p = df_p['close']
        
        # GMMA 趋势判断 (Parent)
        long_emas = [f'EMA_{p}' for p in gmma_periods]
        # 牛市: Parent 价格 > 所有长期均线
        is_bull_trend = close_p > df_p[long_emas].max(axis=1)
        # 熊市: Parent 价格 < 所有长期均线
        is_bear_trend = close_p < df_p[long_emas].min(axis=1)
        
        # 4. 生成信号 (Signal Generation)
        df_t['Signal_Score'] = 0 # 0=Wait, 1=Weak, 2=Cautious, 3=Strong
        df_t['Action'] = "观望"
        
        # Condition A: Parent 超卖 (RSI < 30)
        cond_oversold = (rsi_p < 30)
        # Condition B: Parent 破布林下轨
        cond_breakout = (close_p < bbl_p)
        
        # === 分级策略 ===
        # Strong Buy: 牛市回调 (最肥的肉)
        mask_strong = cond_oversold & cond_breakout & is_bull_trend
        df_t.loc[mask_strong, 'Signal_Score'] = 3
        df_t.loc[mask_strong, 'Action'] = "强力买入 (100%)"
        
        # Cautious Buy: 熊市反弹 (接飞刀)
        mask_cautious = cond_oversold & cond_breakout & is_bear_trend
        df_t.loc[mask_cautious, 'Signal_Score'] = 2
        df_t.loc[mask_cautious, 'Action'] = "谨慎博反弹 (50%)"
        
        # Sell: Parent RSI > 70
        mask_sell = (rsi_p > 70)
        df_t.loc[mask_sell, 'Action'] = "卖出/止盈"
        
        # 5. 准确率验证 (Backtest Verification)
        # 只统计有"买入"信号的日子
        buy_days = df_t[df_t['Signal_Score'] > 0].copy()
        
        # 计算买入后 5 天的收益 (Swing Trade)
        # 这里我们看: 信号发出后持有5天能不能赚钱?
        buy_days['5d_Return'] = df_t['close'].shift(-5) / df_t['close'] - 1
        buy_days['Win'] = buy_days['5d_Return'] > 0
        
        win_rate = buy_days['Win'].mean()
        
        # 6. 生成今日交易计划 (For App)
        latest = df_t.iloc[-1]
        latest_atr = latest['ATRr_14']
        latest_close = latest['close']
        
        # 动态止损计算
        stop_loss = latest_close - (latest_atr * 2.0)
        target_price = latest_close + (latest_atr * 3.0)
        
        report = {
            "Target": self.target,
            "Reference": self.parent,
            "Current_Date": str(latest.name.date()),
            "Current_Price": f"${latest_close:.2f}",
            "Parent_Trend": "🟢 牛市 (GMMA之上)" if is_bull_trend.iloc[-1] else ("🔴 熊市" if is_bear_trend.iloc[-1] else "🟡 震荡"),
            "Parent_RSI": round(rsi_p.iloc[-1], 2),
            "Action_Signal": latest['Action'],
            "Trade_Plan": {
                "Buy_Zone": f"${latest_close:.2f}",
                "Stop_Loss": f"${stop_loss:.2f} (-{latest_atr*2/latest_close:.1%})",
                "Take_Profit": f"${target_price:.2f}"
            },
            "Backtest_Stats": {
                "Total_Signals": len(buy_days),
                "5_Day_Win_Rate": f"{win_rate:.1%} (Sample size: {len(buy_days)})" 
            }
        }
        
        return report

# === 运行 ===
# 用 SOXX (半导体指数) 指导 SOXL 交易
engine = StrategyEngine(target_ticker="SOXL", parent_ticker="SOXX")
result = engine.run_analysis()

# 打印漂亮的 JSON 报告
import json
print(json.dumps(result, indent=2, ensure_ascii=False))
