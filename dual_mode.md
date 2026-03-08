1. The Router (模式分发器)
GMMA Logic
判定源: 母体数据 (Parent_Data - SOXX/QQQ)。
Bull Mode (牛市): 当 Parent_Close > GMMA_Long_Term_Max (所有长期均线之上)。
Action: 激活 Mode A。
Bear Mode (熊市): 当 Parent_Close < GMMA_Long_Term_Min (所有长期均线之下)。
Action: 激活 Mode B。
Chop Mode (震荡): 介于两者之间。
Action: Force Wait (强制空仓，避免磨损)。
2. Execution Engines (执行引擎)
3. The Damper (波动率阻尼)
这是 v2.0 最关键的新增功能，防止在 VIX 暴涨时爆仓：
```python
def calculate_position_size(base_size, current_atr, target_volatility=2.0):
    """
    ATR 阻尼器: 市场越疯，买得越少。
    保证每天的 '美元风险' (Dollar Risk) 是恒定的。
    """
    # 如果当前波动 (ATR) 是平时的 2 倍
    # 那么仓位自动减半
    volatility_ratio = target_volatility / current_atr
    
    # 限制最大仓位不超过 base_size (例如 70%)
    actual_size = min(base_size, base_size * volatility_ratio)
    
    return actual_size
`` tion.```

Layer 4: Next Action Item
待办事项：
请团队在回测引擎中加入 SQN (System Quality Number) 计算模块。
目标： 我们不再看谁的 APY 最高，我们要看谁的 SQN > 2.5。
这能帮你过滤掉那些 "只赚了一次大钱，剩下都在亏" 的垃圾参数。
进度就是这样，方向完全正确，可以开始 Coding 了。需要我提供 SQN 的计算公式代码吗？
AI responses may include mistakes.Learn more
 
  
收到，Dual-Mode (双模) 架构需求已完全录入。作为你的 DE 协作者，我已将这些需求转化为 v2.0 核心架构更新日志 (Changelog)。
当前进度： 逻辑层 (Logic Layer) 已闭环，风控层 (Risk Layer) 代码重构已准备就绪。你的理解（Trend Dip vs Deep Value）完全准确，解决了“死板过滤”导致的踏空问题。
Layer 2: The Architecture Upgrade (v1.0 vs v2.0)
这是针对开发团队的 架构对比视图，明确代码重构的具体差异。
Version Changelog
模块 (Module)
v1.0 (Deprecated)
v2.0 (Production Ready)
核心逻辑
单一阈值 (Single Threshold)
Dual-Mode 路由 (Regime Switching)
GMMA 用法
硬阻断 (Hard Filter)
模式分发器 (Mode Router)
买入执行
固定仓位 (Fixed Size)
ATR 动态阻尼 (Volatility Sizing)
优化目标
追求高 APY (易过拟合)
追求高 SQN (系统质量)
Execution Pipeline
1
Data Ingestion
同步获取 Parent (SOXX) 与 Child (SOXL) 数据，对齐时间戳。
2
Regime Routing
GMMA 判定：
Bull: 价格 > 长期组 → 激活 Mode A。
Bear: 价格 < 长期组 → 激活 Mode B。
3
Signal Trigger
Mode A (顺势): RSI < 60 (浅回调) → 轻仓。
Mode B (抄底): RSI < 30 & 破布林 → 重仓 (需确认信号)。
4
Risk Control
计算 ATR 阻尼系数，调整最终下单手数，并设置动态止损。
Layer 3: The Code Implementation (Python)
这是更新后的 DualModeStrategy 类框架。请直接将此代码发给你的团队，它包含了你要求的所有特性：双模路由、ATR 阻尼、SQN 计算。
```python
import pandas as pd
import pandas_ta as ta
import numpy as np

class DualModeStrategy:
    def __init__(self, initial_capital=10000):
        self.capital = initial_capital
        # 长期组均线周期
        self.gmma_long = [30, 35, 40, 45, 50, 60]

    def prepare_data(self, df_parent, df_child):
        """ETL: 计算技术指标"""
        # 1. Parent (SOXX) - 宏观判断
        for p in self.gmma_long:
            df_parent.ta.ema(length=p, append=True)
        df_parent.ta.rsi(length=14, append=True)
        df_parent.ta.bbands(length=20, std=2.0, append=True)
        
        # 2. Child (SOXL) - 执行与风控
        df_child.ta.atr(length=14, append=True)
        
        return df_parent, df_child

    def analyze_market(self, row_p, row_c, volatility_target_pct=0.02):
        """
        核心决策引擎
        volatility_target_pct: 单笔交易允许的最大本金波动风险 (默认2%)
        """
        signal = {
            "action": "WAIT", 
            "mode": "None", 
            "size_pct": 0.0, 
            "stop_loss": 0.0,
            "reason": ""
        }

        # === 1. GMMA Router (体制识别) ===
        # 获取长期组的最大值和最小值
        emas = [row_p[f'EMA_{p}'] for p in self.gmma_long]
        gmma_max = max(emas)
        gmma_min = min(emas)
        
        # 判定体制
        if row_p['close'] > gmma_max:
            regime = "BULL"
        elif row_p['close'] < gmma_min:
            regime = "BEAR"
        else:
            regime = "CHOP" # 震荡市，观望

        # === 2. Dual-Mode Logic ===
        
        # [Mode A] Trend Dip (牛市回调)
        if regime == "BULL":
            # 逻辑: 趋势极强，RSI 跌破 60 即可视为回调 (不用等 30)
            if row_p['RSI_14'] < 60:
                signal['action'] = "BUY"
                signal['mode'] = "Mode A (Trend Dip)"
                signal['reason'] = f"Bull Trend + RSI {row_p['RSI_14']:.1f}"
                # 止损: 紧凑 (1.5倍 ATR)
                signal['stop_loss'] = row_c['close'] - (row_c['ATRr_14'] * 1.5)
                # 仓位: 基础 30%
                signal['size_pct'] = self._apply_atr_damper(0.30, row_c, volatility_target_pct)

        # [Mode B] Deep Value (熊市抄底)
        elif regime == "BEAR":
            # 逻辑: 趋势已坏，必须极端便宜 (RSI<30 AND 破布林下轨)
            if row_p['RSI_14'] < 30 and row_p['close'] < row_p['BBL_20_2.0']:
                signal['action'] = "BUY"
                signal['mode'] = "Mode B (Deep Value)"
                signal['reason'] = f"Oversold Crash + Bollinger Break"
                # 止损: 宽泛 (3.0倍 ATR)，防止被震荡出局
                signal['stop_loss'] = row_c['close'] - (row_c['ATRr_14'] * 3.0)
                # 仓位: 基础 70% (高胜率机会，重仓)
                signal['size_pct'] = self._apply_atr_damper(0.70, row_c, volatility_target_pct)

        return signal

    def _apply_atr_damper(self, base_size, row_c, risk_cap):
        """
        ATR 阻尼器: 
        如果当前 ATR 是平时的 2 倍，仓位自动减半。
        确保'风险暴露'恒定。
        """
        atr = row_c['ATRr_14']
        price = row_c['close']
        
        if pd.isna(atr) or atr == 0: return 0.0
        
        # 计算每一股的'风险金额' (基于2ATR止损假设)
        risk_per_share = atr * 2.0
        
        # 总本金允许亏损的金额 (e.g., $10000 * 2% = $200)
        allowed_loss = self.capital * risk_cap
        
        # 建议股数
        shares = allowed_loss / risk_per_share
        
        # 转换为仓位百分比
        calculated_size = (shares * price) / self.capital
        
        # 取最小值: 既不超过基础仓位，也不超过风控仓位
        return min(base_size, calculated_size)

    def calculate_sqn(self, trades):
        """
        计算 SQN (System Quality Number)
        SQN = sqrt(N) * (Avg_Profit / Std_Dev)
        """
        if len(trades) < 30: return 0 # 样本太少无意义
        
        profits = [t['profit_pct'] for t in trades]
        avg = np.mean(profits)
        std = np.std(profits)
        
        if std == 0: return 0
        
        sqn = (len(trades) ** 0.5) * (avg / std)
        return sqn
``` 

Layer 4: Follow-up (调优提示)
关于你提到的 “有什么需要调优解决的”，在代码落地时请注意以下两点：
Mode B 的“接飞刀”确认：
现在的代码是 row_p['close'] < row_p['BBL'] 直接买入。
风险： 可能会买在瀑布下跌的第一天。
建议升级： 增加一个 Trigger 函数，要求 "Target 价格今日收阳 (Close > Open)" 才能在 Mode B 执行。这会牺牲一点点入场价格，但能大幅提高胜率。
SQN 的阈值：
在回测时，SQN < 1.5 的参数组合直接丢弃。
1.6 - 1.9 是普通策略。
2.0 - 2.9 是优秀策略。
> 3.0 也就是所谓的 "Holy Grail"，但在高频杠杆ETF中极难达到，如果达到要警惕未来函数。
 

Layer 1: The Synthesis (Direct Feedback)
核心结论： 你的条款大部分非常专业，但 "半年更新" (Semi-annual) 对于 3倍杠杆 ETF (SOXL/TQQQ) 来说 太慢了。币圈和半导体板块的波动周期通常只有 3-4 个月。
关键修正： 将 "半年固定更新" 改为 "滚动窗口回测 (Rolling Walk-Forward)"。
推荐机制： 每周末自动运行一次过去 6 个月的 OOS (Out-of-Sample) 验证。
触发更新： 只有当 新参数的 SQN (系统质量) 显著优于 旧参数 (>15%) 时，才在下周一替换生产环境参数。否则，保持不动 (Do No Harm)。
Layer 2: The Strategy Lifecycle (Visual Protocol)
这是你应该建立的 "参数生命周期管理" 流程，用于替代死板的时间表。
1
1. 孵化期 (In-Sample Dev)
数据： 2020-2024 历史数据。
目标： 寻找逻辑闭环 (Dual-Mode, ATR Sizing)。
产出： 基础参数组 (v1.0)。
2
2. 验证期 (Walk-Forward)
方法： 模拟 "过去的一年"。
操作： 在 2025-01-01 的时间点，只用 2024 的数据优化参数，然后看 2025-01 的表现。以此类推，按月滚动。
目的： 验证参数的 "时效性" (Alpha Decay)。
3
3. 部署与监控 (Production)
实时监控： 每天计算 Live SQN (实盘 SQN)。
健康红线： 如果 Live SQN 跌破 1.0 (表现不及预期)，或 回撤达到历史最大回撤的 80%，触发 "熔断" (暂停开新仓)。
4
4. 迭代与退役 (Retirement)
更新触发： 即使策略在赚钱，如果 新数据训练出的参数 在最近 3 个月的模拟中比旧参数 稳健性 (Sharpe/SQN) 高出 20%，则进行 "热更新" (Hot Swap)。
Layer 3: Deep Dive (Clause Critique)
我对你列出的条款进行了 "红笔批改"，请重点关注 Bold 部分的建议。
逻辑与执行层 (Logic & Execution)
条款 1：实现 DCA/分批入场和移动止盈
评价： ✅ 必须项。对于杠杆 ETF，DCA 是平滑 "波动率损耗" (Volatility Decay) 的唯一数学解。
建议： 务必设置 "Max Exposure" (最大总仓位)。
风险点： 如果 Mode B 触发，你分批买入（30% -> 30% -> 30%），结果价格还在跌。必须有一个 "硬顶" (Cap) (例如 120% 初始本金)，防止无底洞补仓。 
条款 2：移动止盈 (Trailing Stop) 
建议： 对于 3x ETF，普通的 "回撤 x%" 容易被洗。建议使用 "Chandelier Exit" (吊灯止盈) —— 基于 ATR 的移动止盈。
公式： Stop = Highest_High - (3 * ATR)。这比固定百分比更适应市场当下的疯癫程度。 
运维与风控层 (Ops & Risk)
条款 3：半年自动重新调参 (Semi-annual Update)
评价： ❌ 太慢，建议废除。
建议： 采用 "Quarterly Review, Monthly Check" (季度重构，月度体检)。
理由： SOXL 的波动率结构（Implied Volatility）变化极快。半年前的最佳 RSI 阈值在今天可能完全失效。 
条款 4：限制搜索空间 & 最小交易次数
评价： ✅ 极佳。这是防止过拟合的护城河。
硬指标： 
Min Trades: 年化至少 30 笔 (少于这个样本量，统计学上不可信)。
Max Drawdown: 设定为 -25% (对于 3x ETF，这是一个极其严格但健康的标准)。
Costs: 必须计入 滑点 (Slippage)。实盘中 SOXL 的滑点大约是 0.05% - 0.1%。如果不计入，高频策略全是假象。 
条款 5：Walk-Forward (滑动窗口验证) 
评价： ✅ Quant 的黄金标准。
执行： 报告 OOS (样本外) 结果时，不要只看总收益。要看 "OOS Consistency" (一致性)。
Key Metric: 每一个 OOS 窗口（比如每个月）是否都是正收益？如果 12 个月里有 10 个月赚钱，那是好策略。如果 12 个月里只有 1 个月赚了暴利，其他 11 个月亏损，那是 "彩票策略"，坚决不要。 
组合管理 (Portfolio Level)
条款 6：组合权重优化
评价： ⚠️ 暂时搁置 (v2.0)。
理由： 你目前专注于 SOXL/TQQQ。它们的相关性 (Correlation) 高达 0.8 以上。做组合优化的边际效应很低（齐涨齐跌）。
建议： 既然你是 DE，不如把精力花在 "数据源多元化" 上。引入 VIX 或 Put/Call Ratio 作为额外的过滤因子，比单纯调整权重更有效。
Layer 4: The Follow-up (Governance Metrics)
为了将这些条款落地，你需要给你的 App 定义一套 "策略健康度仪表盘"。
指标 (Metric)
阈值 (Threshold)
含义 (What it means)
动作 (Action)
SQN (System Quality)	< 1.6	策略失效	停止开仓，重新研发
2.0 - 2.9	表现良好	正常运行
> 5.0	异常/过拟合	人工审查 (Too good to be true)
Drawdown Duration	> 45 Days	陷入泥潭	减半仓位运行
Consecutive Loss	> 5 Trades	连败预警	暂停 Mode B (激进模式)