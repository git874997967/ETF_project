## 我有一下几点 要求 帮我改进下 
1 策略平平我承认  因为都是杠杆类etf 指标不准确 soxl 应该关注 soxx tqqq 应该关注 qqq  请帮我 实现一个method 来通过etf 指标进行映射到 指定 杠杆etf的 操作建议：
获取 SOXX 的数据计算 RSI 和布林带。
当 SOXX 发出买入信号时。
下单买入 SOXL。

2 目前只有一个 rsi指标  我承认准确率略显单薄  成熟的选择是 gmma  Bollinger Bands l  rsi 三个一起看 并且进行加权   强买入  中等买入 谨慎买入 等 
计算 ADX (Average Directional Index)。如果 ADX > 30 且 slope 向上，说明当前是强趋势（无论涨跌），此时 禁用 RSI 反转策略。

### 多指标融合 (Feature Selection)
GMMA、MACD、Bollinger，怎么组合最有效？
推荐 "Triple Screen Strategy" (三层滤网) 逻辑，这在算法交易中很常见：
宏观滤网 (Trend): 用 MA200 或 GMMA 长期组。
逻辑: 只有在长期趋势向上时，才允许做多 (Long Only)。
波段滤网 (Volatility): 用 Bollinger Bands。
逻辑: 价格必须跌破布林带下轨 (Lower Band)。这代表价格偏离均值 2 个标准差，属于统计学上的"异常值"。
触发扳机 (Momentum): 用 RSI (14) 或 KDJ。
逻辑: RSI < 30。
# 只有当三个条件同时满足，才生成 Buy Signal
```python
condition_1 = df['close'] > df['ma_200']          # 处于牛市
condition_2 = df['close'] < df['bb_lower']        # 价格极端超跌
condition_3 = df['rsi_14'] < 30                   # 动量超卖
df['Signal'] = condition_1 & condition_2 & condition_3
```
类似于这种 
再把 "信号加权" (Signal Weighting) 或 "信心指数" (Confidence Score)
考虑进去 通过结合 趋势 (Trend)、波动 (Volatility) 和 动量 (Momentum) 三个维度的指标，我们可以把二进制的 "买/不买" 变成一个 "仓位管理" (Position Sizing) 系统。
核心策略逻辑：三维共振 (The 3D Confluence)

我们将信号分为三个等级，对应不同的买入比例 (Position Size)：
Strong Buy (100% 仓位): "牛市回调"。趋势向上 + 极端超卖 + 突破布林下轨。这是胜率最高的黄金坑。
Standard Buy (50% 仓位): "超跌反弹"。趋势向下（熊市）+ 极端超卖 + 突破布林下轨。这是在接飞刀，只能轻仓博反弹。
Wait (0%): 信号不全，比如只有 RSI 低但没破布林带（可能是阴跌）。 
## 思路逻辑是
逻辑: 如果 GMMA 长期组发散向上 -> 定义为牛市 -> 允许 Strong Buy。
逻辑: 如果 GMMA 长期组发散向下 -> 定义为熊市 -> 最高只能给 Cautious Buy。
这套逻辑比单纯的 RSI < 30 稳健得多，能有效过滤掉大部分"接飞刀"的亏损交易
就像
 
```python
def run_tiered_backtest(ticker, start_date="2024-01-01", end_date="2026-02-27"):
    # 1. 获取数据 (多取200天以计算MA200)
    # 我们需要足够长的历史数据来计算 SMA_200，所以 start 稍微前移
    data_start = pd.to_datetime(start_date) - pd.DateOffset(days=300)
    df = yf.download(ticker, start=data_start, end=end_date, progress=False, auto_adjust=True)
    
    if df.empty: return None
    
    # Schema 清洗 (适配 MultiIndex)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df.columns = [c.lower() for c in df.columns]
    
    if 'close' not in df.columns: return None

    # 2. 计算三维指标 (3D Indicators)
    # A. 动量 (Momentum): RSI
    df.ta.rsi(length=14, append=True)
    
    # B. 波动 (Volatility): Bollinger Bands
    # 返回列名通常是 BBL_20_2.0 (下轨), BBU_20_2.0 (上轨), BBM_20_2.0 (中轨)
    df.ta.bbands(length=20, std=2.0, append=True)
    
    # C. 趋势 (Trend): SMA 200
    df.ta.sma(length=200, append=True)
    
    # 3. 定义原子条件 (Atomic Conditions)
    # 这里的列名需要根据 pandas_ta 的默认输出调整，通常是固定的
    rsi_col = 'RSI_14'
    bbl_col = 'BBL_20_2.0'
    sma_col = 'SMA_200'
    
    # 清洗 NaN (MA200 前200天为空)
    df = df.dropna()
    
    # Condition 1: 极端超卖 (RSI < 30)
    cond_oversold = (df[rsi_col] < 30)
    
    # Condition 2: 价格跌破布林带下轨 (统计学异常值)
    cond_breakout = (df['close'] < df[bbl_col])
    
    # Condition 3: 处于长期牛市 (价格 > 年线)
    cond_bull_trend = (df['close'] > df[sma_col])
    
    # 4. 信号分级 (Signal Tiering)
    df['Signal_Type'] = "WAIT"
    df['Suggested_Size'] = 0.0
    
    # === 逻辑分层 ===
    
    # Scenario A: Strong Buy (牛市黄金坑) -> 满仓 (100%)
    # 满足: RSI超卖 AND 破布林下轨 AND 在年线之上
    mask_strong = cond_oversold & cond_breakout & cond_bull_trend
    df.loc[mask_strong, 'Signal_Type'] = "STRONG BUY (100%)"
    df.loc[mask_strong, 'Suggested_Size'] = 1.0
    
    # Scenario B: Speculative Buy (熊市博反弹/接飞刀) -> 半仓 (50%)
    # 满足: RSI超卖 AND 破布林下轨 AND (但在年线之下)
    mask_spec = cond_oversold & cond_breakout & (~cond_bull_trend)
    df.loc[mask_spec, 'Signal_Type'] = "CAUTIOUS BUY (50%)"
    df.loc[mask_spec, 'Suggested_Size'] = 0.5
    
    # Scenario C: Weak Buy (仅 RSI 低) -> 极轻仓或观望 (30%)
    # 满足: RSI超卖 但 没破布林 (可能是阴跌，动能不足)
    mask_weak = cond_oversold & (~cond_breakout)
    df.loc[mask_weak, 'Signal_Type'] = "WEAK BUY (20%)"
    df.loc[mask_weak, 'Suggested_Size'] = 0.2
    
    # 5. 向量化回测 (Vectorized Backtest with Position Sizing)
    # 卖出逻辑保持简单: RSI > 70 清仓
    sell_signal = (df[rsi_col] > 70)
    
    # 状态机逻辑:
    # 我们需要记录"持仓比例"。如果买入信号触发，持仓变为 Suggested_Size。
    # 如果卖出信号触发，持仓变为 0。
    # 如果无信号，保持昨日持仓? 或者每次信号重置?
    # 简单起见：信号触发当日建立对应仓位，直到 RSI > 70 全部卖出。
    
    df['Position'] = np.nan
    # 填入买入仓位
    df.loc[df['Suggested_Size'] > 0, 'Position'] = df['Suggested_Size']
    # 填入卖出点 (仓位=0)
    df.loc[sell_signal, 'Position'] = 0
    
    # 向下填充 (Hold)
    df['Position'] = df['Position'].ffill().shift(1).fillna(0)
    
    # 收益计算
    df['Market_Ret'] = df['close'].pct_change()
    # 策略收益 = 市场涨跌幅 * 持仓比例 (0.2, 0.5, 1.0)
    df['Strategy_Ret'] = df['Position'] * df['Market_Ret']
    
    df['Equity'] = (1 + df['Strategy_Ret']).cumprod()
    
    # 截取用户请求的时间段
    result_df = df.loc[start_date:]
    
    if result_df.empty: return None
    
    total_ret = result_df['Equity'].iloc[-1] / result_df['Equity'].iloc[0] - 1
    
    # 打印最近几天的信号 (App 前端展示用)
    latest_signals = result_df[['close', rsi_col, bbl_col, 'Signal_Type', 'Suggested_Size']].tail(5)
    
    return {
        "Ticker": ticker,
        "Total_Return": total_ret,
        "Final_Equity": result_df['Equity'].iloc[-1],
        "Recent_Signals": latest_signals
    }

# === 运行测试 ===
tickers = ['SOXL', 'SOXS'] # 看看牛熊证的区别
print(f"{'Ticker':<6} | {'Return':<10}")
print("-" * 30)

for t in tickers:
    res = run_tiered_backtest(t)
    if res:
        print(f"{res['Ticker']:<6} | {res['Total_Return']:.2%}")
        print(f"\n--- Recent Signals for {t} ---")
        print(res['Recent_Signals'])
        print("\n")
```

### 方法功能方面 注重 
1. 代码注释 可读性 
2. 复用性 封装性  
3. 可扩展性  目前是  soxx soxl  tqqq 和qqq  但不排除 引如其他 杠杆etf

### UI 输出方面 体现一下特性 

1. 信号分级的意义
Strong Buy (Bull Dip): 这是赚钱的主力。当 SOXL 在上涨趋势中回调（比如因为地缘政治新闻短期下跌），同时 RSI<30 且跌破布林带。这种时候要重拳出击。
Cautious Buy (Bear Rally): 类似于 2022 年或 2025 年初的崩盘期。股价在年线（MA200）下方。虽然 RSI 超卖，但如果你全仓买入，很可能被埋。减半仓位可以保护本金，同时不错过反弹。
2. 如何融入 App (Scenario Integration)
你需要一个简单的 JSON 输出逻辑，把上面的 Recent_Signals 转换成给用户的建议：
User Interface (Example):
今日交易建议 (2026-02-27)
SOXL (3x Semi Bull)
当前价格: $62.45
趋势: 🟢 位于年线之上 (牛市)
信号强度: STRONG BUY 🔥
建议操作:
买入仓位: 100% (激进)
入场价: $62.00 (Limit)
止损: $58.50 (布林下轨 - 2% Buffer) 
 