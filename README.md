# operation_prediction — 策略版本演进与使用说明

本项目从最初的 RSI 反转回测脚本一路演进，目前已发展到支持多种指标、双模式策略、面向类的引擎及命令行入口。下面简要记录各个主要版本的特性。

## 版本历史

### v1: RSI 单指标准则
- 最初版本只对某个杠杆 ETF 计算 RSI，若低于阈值则买入，RSI>70 卖出。
- 只回测无参数搜索。

### v2: 多指标 + 网格参数化
- 引入三层滤网：GMMA 趋势、Bollinger 带下轨、RSI 动量；并添加 ADX、ATR 波动率作为辅助。
- 参数化 RSI 阈值 (`rsi_threshold`) 与 BB 下轨乘数 (`bbl_buffer`)。
- 可针对每个标的进行网格搜索寻找满足目标 APY 的阈值。
- 实现信号分级策略（STRONG、CAUTIOUS、WEAK、WAIT）以及“严格 GMMA”选项。
- 添加稳健性分析：交易次数、胜率、平均每笔收益、最大回撤、Sharpe、样本内/样本外 APY。
- 增加波动率过滤与交易成本计入 APY 的计算。
- 输出 `sensitivity_*.json` 文件保存网格结果。

### v3: 类封装与命令行入口
- 将策略逻辑封装到 `StrategyEngine` 类中，支持可配置的父/子映射、Dual-Mode 信号、SQN 计算等。
- 提供轻量 CLI (`operation_prediction_v3.py`) 支持 `run` 和 `sensitivity` 子命令，并可导出 JSON。
- 保持与 v2 兼容的 wrapper 函数以便渐进迁移。
- **2026-03-07 更新**：多 Ticker 支持、safe_external 异常保护、输出人性化（布林上轨/MA50、DCA 动态数据、APY 百分比、建议仓位修正）、Windows 控制台编码兼容。

## 时间管理与更新记录

- 最后更新：2026-03-07
- 版本时间线（请在发布时补充或维护）：
  - v1: 初始 RSI 实验 — (日期: 2026-02-27)
  - v2: 多指标 + 网格参数化 — (日期: 2026-02-27)
  - v3: 类封装与 CLI — (创建日期: 2026-02-28)
  - v3.1: 多 Ticker、稳健性、输出人性化 — (日期: 2026-03-07)

- 建议：每次重大变更（新增指标、变更回测窗口、调整成本模型）请在此记录变更理由与影响，以便回溯与审计。

---

## Changelog — 2026-03-07

> 本次更新使 `operation_prediction_v3.py` 更稳健、输出更人性化，并为后续作为 Web App 后端做准备。供团队 review 与讨论下一步细节。

### 1. 多 Ticker 支持

- **`--ticker`** 支持多个标的，例如：`--ticker tqqq soxl sqqq soxs`
- **返回值** 改为 JSON 列表格式，每个 ticker 对应一个对象
- **sensitivity** 子命令同样支持多 ticker，结果中增加 `ticker` 字段

### 2. 稳健性增强

- **`get_underlying`**：未映射 ETF 返回 `levered.upper()`，保证 ticker 全大写
- **`safe_external` 装饰器**：统一包装 `yf.download`、日期解析、JSON 读写，失败时返回默认值或重新抛出
- VIX 下载失败时用 NaN 填充，策略继续运行

### 3. 输出人性化调整

| 项 | 说明 |
|----|------|
| 布林上軌、MA50 | 新增 `bbu_20_2.0`、`ema_slow_50` 到 UI_Summary |
| DCA 动态数据 | 當前RSI、當前風險因子、RSI<55/RSI<45 的实际建仓比例 |
| APY / Total_Return | 格式化为 `"2.53%"`、`"28.96%"` 字符串 |
| 建议仓位 | 使用 `max(Position, Suggested_Size)` 解决持仓日显示 0% 问题 |
| Windows 编码 | 先 save 再 print，捕获 UnicodeEncodeError 时 fallback 到 ensure_ascii |

### 4. 待讨论：Web App 后端扩展

作为 API 返回给前端时，可考虑增加：

1. **`meta`**：`generated_at`、`data_range` 等元数据
2. **`chart_data`**：精简历史数据（日期、价格、净值、仓位），供前端绘图
3. **`metrics`**：Sharpe、最大回撤、交易次数、胜率等策略指标
4. **`action_hint`**：简短操作提示（如「等待 RSI < 55 且牛市確認」）
5. **`signal_badge` / `trend_icon`**：前端展示用的样式/图标建议

## 当前功能概述
（此处保留原来的 v2 描述，略作调整以反映最新状态）

## 已完成的工作（本次）
- 实现 GMMA、RSI、Bollinger、ADX 指标并生成分级信号（STRONG/CAUTIOUS/WEAK/WAIT）。
- 将阈值参数化：`rsi_threshold` 与 `bbl_buffer`（布林下轨乘数）。
- 增加场景比较（original/moderate/relaxed）。
- 添加稳健性分析：交易次数、胜率、平均每笔交易收益、最大回撤、Sharpe、样本内/样本外 APY。- 近期实验添加“严格 GMMA”选项（要求长线组均线连续上升）并对 SOXL 重新网格扫描，输出 `sensitivity_soxl_refined.json`。
## 运行方法
### v2 脚本
在项目根目录（包含 `operation_prediction_v2.py`）运行：

```powershell
& "C:/Program Files/Python/Python311/python.exe" "c:/Users/Zac/Documents/etf_project/operation_prediction_v2.py"
```

脚本会依次比较预定义场景，并对 `tqqq`、`soxl` 做网格搜索与稳健性验证。结果会打印在控制台（包含 APY、CumRet、以及 robustness metrics）。

### v3 CLI
若使用新版引擎，可调用 `operation_prediction_v3.py`：

```powershell
# 单 ticker
python operation_prediction_v3.py run --ticker tqqq --rsi 40 --bbl 1.02
python operation_prediction_v3.py sensitivity --ticker soxl --rsi 30 --bbl 1.00

# 多 ticker（返回 JSON 列表）
python operation_prediction_v3.py run --ticker tqqq soxl --out result.json
```

使用 venv（Python 3.11）：

```powershell
.\venv_ta\Scripts\python.exe operation_prediction_v3.py run --ticker tqqq soxl --out result.json
```

这两个命令分别执行一次完整回测或简单的阈值敏感度查看，输出 JSON 结果并可通过 `--out` 保存文件。

版本信息可在 README 顶部查看。随着进一步迭代，建议将运行脚本及阈值配置保存到 `config.json` 并在生产中引用。

## 当前关键结果（脚本输出摘要）
- 为 `tqqq` 找到阈值：`RSI < 65`, `BB buffer = 1.14` → APY ≈ 59.1%（不同运行间会有小幅浮动，先前曾见到 61% 以上，是网格边缘的过拟合结果）。
- 为 `soxl` 经过多轮调整与“严格 GMMA”过滤，当前网格最佳为 `RSI < 60`, `BB buffer = 1.05` → APY ≈ 17.99% （交易次数 136 次，最大回撤 ≈ -49.7%，严格 GMMA 导致 RSI=40 几乎无交易）。
  > 注意：该 APY 未达到 20%，说明单纯阈值调节已不足，需进一步交易管理（分批入场/移动止盈）或放松过滤。

稳健性指标（示例）：
- TQQQ: Trades=11, WinRate≈81.8%, MaxDrawdown≈-39.1%, Sharpe≈1.31, OOS APY≈42.4%
- SOXL: Trades=10, WinRate=90%, MaxDrawdown≈-54.8%, Sharpe≈1.54, OOS APY≈312.7%

> 注意：高 APY 伴随高回撤与交易稀少（每只仅 ~10 次交易），样本外结果极端值提示可能存在过拟合或样本偏差。

## 稳健性评估结论（简要）
- 这类极高 APY 通常警示过拟合风险：网格搜索范围较大，且没有加入回撤/交易次数等约束。
- 样本外 APY 虽然在部分标的仍然高，但数量级偏差（如 SOXL 的 OOS APY）需要进一步验证与谨慎对待。

## 建议的下一步调优（优先级排序）
1. 限制搜索空间并添加约束：要求最小交易次数、对最大回撤设上限（例如不超过 -30%）、并把交易成本/滑点计入优化目标。 
2. 使用滑动窗口或时间序列交叉验证（walk-forward）来减轻过拟合，报告平均 OOS 表现。 
3. 将阈值导出/版本化（JSON），并建立半年自动重新调参流程（只在样本外表现稳定时替换生产阈值）。
4. 添加交易管理：分批建仓和移动止盈/跟踪止盈已在脚本框架中预留，明日将实现测试。
4. 引入风险管理（止损/仓位上限/杠杆限制）并在回测中加入真实交易成本（佣金 + 滑点）。
5. 如果目标是组合级 APY（非单只），对组合权重进行优化（把更多权重给稳健的标的，或仅做多表现良好的标的）。
6. 增加更多验证指标：最大连亏、月度收益分布、回撤恢复时间等。

## 文件说明
- `operation_prediction_v2.py`：主脚本，包含策略、网格搜索、稳健性分析、以及打印/应用结果。
- `operation_prediction_v3.py`：v3 策略引擎 CLI，支持多 Ticker、DCA、ATR/VIX 风险调节，输出 JSON 供 API 或前端调用。
- `src/main.py`：MA200 vs GMMA 对比回测实验脚本。
- `CHANGELOG_2026-03-07.md`：2026-03-07 更新详情（已合并至本文 Changelog 章节）。
- `README.md`：当前文件，包含使用与调优建议。

## 未来计划
- 引入 SQN (系统质量数) 作为搜索/筛选评分并添加最小交易次数、最大回撤等约束。
- 实现 DCA/分批入场和移动止盈——根据 ATR 估算波动调整仓位、按照 RSI 不同阶段自动加仓/减仓。
- 将阈值与参数版本化，配合定期回测触发配置更新。
- 增加单元测试和端到端流程验证（尤其是 `operation_prediction_v3.py` CLI）。
- 对搜索结果做 Monte-Carlo 重排以评估最差回撤情况并输出 JSON 报表。

## 我可以继续帮你做（可选）
- 将当前最佳阈值保存为 `config.json` 并实现读取/应用逻辑（方便半年更新）。
- 限制搜索并把回撤/交易次数纳入优化目标后重新搜索更稳健的阈值。
- 实现 walk-forward 验证并输出平均 OOS 表现与置信区间。

如果你同意，我会把当前找到的阈值保存到 `config.json` 并实现下载/加载逻辑，随后做受约束的重新搜索。