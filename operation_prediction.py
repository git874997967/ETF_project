import pandas as pd
import pandas_ta as ta
import numpy as np
import yfinance as yf

def normalize_columns(df):
    """将 DataFrame 列名统一转换为小写字符串。
    对于 MultiIndex 会先取第一级再小写。
    返回修改后的 DataFrame 以支持链式调用。
    """
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0).str.lower()
    else:
        df.columns = [c.lower() if isinstance(c, str) else c for c in df.columns]
    return df


def generate_trade_plan(df, ticker, risk_multiplier=2.0):
    """
    为 App 生成次日交易计划
    输入: 包含 RSI 和 ATR 的 DataFrame
    输出: 具体的买入价、止损价、止盈价
    """
    # 规范列名
    df = normalize_columns(df)

    # 1. 确保计算了 ATR (波动率指标)
    # ATR = Average True Range, 衡量过去14天的平均波动幅度
    if 'atr_14' not in df.columns:
        df.ta.atr(length=14, append=True)
        df = normalize_columns(df)
    
    last_row = df.iloc[-1]
    rsi = last_row['rsi_14']
    close = last_row['close']
    atr = last_row['atr_14']
    
    # 2. 信号判断
    signal = "观望"
    if rsi < 30: signal = "买入"
    elif rsi > 70: signal = "卖出"
    
    # 3. 计算点位 (傻瓜式)
    # 止损位 (Stop Loss): 当前价格 - 2倍的波动幅度 (给足震荡空间)
    stop_loss = close - (atr * risk_multiplier)
    
    # 止盈位 (Take Profit): 既然是反弹策略，目标通常是均值回归
    # 简单设为: 当前价格 + 3倍波动幅度 (1:1.5 盈亏比)
    take_profit = close + (atr * risk_multiplier * 1.5)
    
    # 建议买入区间: 收盘价 ~ (收盘价 - 0.5*ATR) 挂单接飞刀
    buy_zone_low = close - (atr * 0.5)
    
    # 简单工具：将数值转换为字符串，并用“无数据”替换 NaN
    def fmt(x, prec=2):
        return "无数据" if pd.isna(x) else f"${round(x, prec)}"

    buy_zone_str = (
        "无数据" if pd.isna(buy_zone_low) or pd.isna(close)
        else f"{fmt(buy_zone_low)} - {fmt(close)}"
    )
    stop_str = (
        "无数据" if pd.isna(stop_loss) or pd.isna(close)
        else f"{fmt(stop_loss)} (风险: -{round((close-stop_loss)/close*100, 1)}%)"
    )
    targ_str = fmt(take_profit)

    return {
        "日期": str(last_row.name.date()),
        "ETF 名称": ticker,
        "操作信号": signal,
        "RSI 指标": "无数据" if pd.isna(rsi) else round(rsi, 2),
        "实际价格": "无数据" if pd.isna(close) else round(close, 2),
        "操作建议": {
            "买入区间": buy_zone_str,
            "止损点": stop_str,
            "目标价": targ_str
        }
    }


def run_vectorized_backtest(ticker, start_date="2024-01-01", end_date="2026-02-28"):
    # 1. 获取数据 (Data Ingestion)
    df = yf.download(ticker, start=start_date, end=end_date, progress=False)
    if df.empty: return None
    
    # Normalize columns for consistency
    df = normalize_columns(df)

    # 2. 特征工程 (Feature Engineering via pandas_ta)
    # 使用 ta 扩展，无需手动计算
    df.ta.rsi(length=14, append=True)  # 生成列名通常为 'rsi_14'
    # 有些指标返回的是大写名字，统一小写
    df.columns = [c.lower() if isinstance(c, str) else c for c in df.columns]
    rsi_col = 'rsi_14'

    # 额外：基于 RSI 生成每日信号并计算准确率
    df['次日交易信号'] = np.where(df[rsi_col] < 30, '买入',
                                  np.where(df[rsi_col] > 70, '卖出', '观望'))
    # 下一交易日收益（用于验证信号是否正确）
    df['next_ret'] = df['close'].shift(-1) / df['close'] - 1
    df['信号准确性'] = (((df['次日交易信号'] == '买入') & (df['next_ret'] > 0)) |
                             ((df['次日交易信号'] == '卖出') & (df['next_ret'] < 0)))
    # 只在实际有买入或卖出信号的日期进行准确率计算
    valid = df['次日交易信号'].isin(['买入','卖出'])
    # NaN（最后一天的 next_ret）转为 False 再求平均
    df['信号准确性'] = df['信号准确性'].fillna(False)
    daily_accuracy = df.loc[valid, '信号准确性'].mean() if valid.any() else float('nan')
    
    # 3. 向量化信号生成 (Vectorized Signal Generation)
    # 避免使用 for 循环，直接操作 Series
    
    # 信号：昨日 RSI < 30 且 今日 RSI >= 30 (上穿买入) 
    # 或 激进策略：只要 RSI < 30 就持有
    # 这里我们使用：RSI < 30 买入持有，RSI > 70 卖出空仓
    
    # 创建信号掩码
    df['Signal'] = 0
    # 设为 1 (持仓), 0 (空仓)
    # 使用 np.where 进行条件填充：
    # 这是一个状态机问题，向量化比较难处理"持有"状态，
    # 这里我们使用 shift() 来模拟简单的状态传递或使用 ta.xsignals
    
    # === 简单逻辑：RSI < 30 买入次日开盘，RSI > 70 卖出次日开盘 ===
    buy_signal = (df[rsi_col] < 30)
    卖出_signal = (df[rsi_col] > 70)
    
    # 使用 ffill() 填充持仓状态 (Forward Fill)
    # 1 = 买入, -1 = 卖出, NaN = Hold
    df['Action'] = np.nan
    df.loc[buy_signal, 'Action'] = 1
    df.loc[卖出_signal, 'Action'] = 0
    
    # 核心 trick: 向下填充状态，这就模拟了"持仓直到卖出信号"
    df['Position'] = df['Action'].ffill().shift(1) # shift(1) 避免未来函数，今日信号操作明日
    df['Position'] = df['Position'].fillna(0) # 初始空仓
    
    # 4. 盈亏计算 (PnL Calculation)
    # 每日收益 = (今日收盘 - 昨日收盘) / 昨日收盘
    df['Market_Ret'] = df['close'].pct_change()
    
    # 策略收益 = 持仓状态 * 市场收益
    df['Strategy_Ret'] = df['Position'] * df['Market_Ret']
    
    # 5. 胜率统计 (Trade Statistics)
    # 定义一笔交易为：Position 从 0 变 1 (开仓) 到 Position 变 0 (平仓)
    trades = df[df['Position'].diff() != 0].copy()
    # 只有 Position=0 的行才代表"刚平仓"或"刚开仓"，需要更复杂的逻辑统计胜率
    # 简易版胜率：统计所有"持仓日"中收益为正的比例 (Day Win Rate)
    day_win_rate = len(df[(df['Position'] == 1) & (df['Strategy_Ret'] > 0)]) / \
                   len(df[df['Position'] == 1]) if len(df[df['Position'] == 1]) > 0 else 0
                   
    # 累计收益
    df['Equity_Curve'] = (1 + df['Strategy_Ret']).cumprod()
    
    return {
        "Ticker": ticker,
        "DataFrame": df,                         # 返回带指标的 df 供后续使用
        "Total_Return": df['Equity_Curve'].iloc[-1] - 1,
        "Day_Win_Rate": day_win_rate,
        "Max_Drawdown": (df['Equity_Curve'] / df['Equity_Curve'].cummax() - 1).min(),
        "Daily_Accuracy": daily_accuracy      # 信号的日准确率
    }

# === 支持逻辑：杠杆ETF 与 基础ETF 映射 ===
UNDERLYING_MAP = {
    'soxl': 'SOXX',
    'soxs': 'SOXX',
    'tqqq': 'QQQ',
    'sqqq': 'QQQ'
}

def get_underlying_ticker(levered_ticker: str) -> str:
    """返回对应的基础标的，如果没有映射则返回自身。"""
    return UNDERLYING_MAP.get(levered_ticker.lower(), levered_ticker)


def run_tiered_backtest(leverage_ticker, start_date="2024-01-01", end_date="2026-02-27"):
    """基于三层滤网策略对杠杆ETF进行回测。
    指标是在基础ETF上计算，收益用杠杆ETF的价格。
    返回一份包含回报和最近信号的小结字典。
    """
    underlying = get_underlying_ticker(leverage_ticker)

    # 历史窗口提前300天用于MA200计算
    data_start = pd.to_datetime(start_date) - pd.DateOffset(days=300)
    df_u = yf.download(underlying, start=data_start, end=end_date, progress=False)
    if df_u.empty: return None
    df_u = normalize_columns(df_u)
    if 'close' not in df_u.columns:
        return None

    # 计算指标
    df_u.ta.rsi(length=14, append=True)
    df_u.ta.bbands(length=20, std=2.0, append=True)
    df_u.ta.sma(length=200, append=True)
    # 计算 ADX 用于识别强趋势
    df_u.ta.adx(length=14, append=True)
    df_u = normalize_columns(df_u)

    # 清洗 NaN 并复制以避免后续赋值警告
    df_u = df_u.dropna().copy()

    # 原子条件
    rsi_col = 'rsi_14'
    # pandas_ta defaults to simple names for BBands when std=2
    bbl_col = 'bbl_20'
    sma_col = 'sma_200'
    adx_col = 'adx_14'

    # 如果 ADX>30 且正在上行，则认为为强趋势，此时不应使用 RSI 反转策略
    disable_rsi = (df_u[adx_col] > 30) & (df_u[adx_col].diff() > 0)
    cond_oversold = (df_u[rsi_col] < 30) & (~disable_rsi)
    cond_breakout = (df_u['close'] < df_u[bbl_col])
    cond_bull_trend = (df_u['close'] > df_u[sma_col])

    df_u['Signal_Type'] = "WAIT"
    df_u['Suggested_Size'] = 0.0

    mask_strong = cond_oversold & cond_breakout & cond_bull_trend
    df_u.loc[mask_strong, 'Signal_Type'] = "STRONG BUY (100%)"
    df_u.loc[mask_strong, 'Suggested_Size'] = 1.0

    mask_spec = cond_oversold & cond_breakout & (~cond_bull_trend)
    df_u.loc[mask_spec, 'Signal_Type'] = "CAUTIOUS BUY (50%)"
    df_u.loc[mask_spec, 'Suggested_Size'] = 0.5

    mask_weak = cond_oversold & (~cond_breakout)
    df_u.loc[mask_weak, 'Signal_Type'] = "WEAK BUY (20%)"
    df_u.loc[mask_weak, 'Suggested_Size'] = 0.2

    # 载入杠杆ETF价格并对齐
    df_l = yf.download(leverage_ticker, start=data_start, end=end_date, progress=False)
    df_l = normalize_columns(df_l)
    df_u = df_u.join(df_l[['close']], how='left', rsuffix='_lev')
    df_u.rename(columns={'close_lev': 'close_levered'}, inplace=True)

    # 卖出信号
    sell_signal = (df_u[rsi_col] > 70)

    # 持仓比例计算
    df_u['Position'] = np.nan
    df_u.loc[df_u['Suggested_Size'] > 0, 'Position'] = df_u['Suggested_Size']
    df_u.loc[sell_signal, 'Position'] = 0
    df_u['Position'] = df_u['Position'].ffill().shift(1).fillna(0)

    # 收益计算使用杠杆ETF
    df_u['Market_Ret'] = df_u['close_levered'].pct_change()
    df_u['Strategy_Ret'] = df_u['Position'] * df_u['Market_Ret']
    df_u['Equity'] = (1 + df_u['Strategy_Ret']).cumprod()

    result_df = df_u.loc[start_date:]
    if result_df.empty: return None

    total_ret = result_df['Equity'].iloc[-1] / result_df['Equity'].iloc[0] - 1
    latest_signals = result_df[['close_levered', rsi_col, bbl_col, sma_col, 'Signal_Type', 'Suggested_Size']].tail(5)

    # 生成前端友好摘要
    last = latest_signals.iloc[-1]
    trend_flag = "牛市" if last['close_levered'] > last[sma_col] else "熊市"
    ui = {
        "日期": str(last.name.date()),
        "ETF": leverage_ticker,
        "基础": underlying,
        "趋势": trend_flag + (" (在年线之上)" if trend_flag == "牛市" else " (在年线之下)"),
        "信号": last['Signal_Type'],
        "建议仓位": f"{int(last['Suggested_Size']*100)}%",
        "当前价格": round(last['close_levered'], 2),
        "布林下轨": round(last[bbl_col], 2)
    }

    return {
        "Ticker": leverage_ticker,
        "Underlying": underlying,
        "Total_Return": total_ret,
        "Final_Equity": result_df['Equity'].iloc[-1],
        "Recent_Signals": latest_signals,
        "UI_Summary": ui
    }

# === 运行示例：tiered backtest ===
tickers = ['SOXL', 'SOXS', 'TQQQ', 'SQQQ']
print(f"{'代码':<6} | {'回报':<10} | {'基础ETF':<8}")
print("-" * 40)
for t in tickers:
    res = run_tiered_backtest(t)
    if res:
        print(f"{res['Ticker']:<6} | {res['Total_Return']:.2%} | {res['Underlying']:<8}")
        print(f"最近信号 ({res['Ticker']}):")
        print(res['Recent_Signals'])
        print()