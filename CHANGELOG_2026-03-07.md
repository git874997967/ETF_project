# Update Log — 2026-03-07

> 本次更新使 `operation_prediction_v3.py` 更稳健、输出更人性化，并为后续作为 Web App 后端做准备。供团队 review 与讨论下一步细节。

---

## 1. 多 Ticker 支持

- **`--ticker`** 支持多个标的，例如：`--ticker tqqq soxl sqqq soxs`
- **返回值** 改为 JSON 列表格式，每个 ticker 对应一个对象
- **sensitivity** 子命令同样支持多 ticker，结果中增加 `ticker` 字段

```bash
python operation_prediction_v3.py run --ticker tqqq soxl --out result.json
```

---

## 2. 稳健性增强

### 2.1 `get_underlying` 适配未映射 ETF

- 未在 `underlying_map` 中的 ETF 返回 `levered.upper()`，保证 ticker 全大写
- yfinance 不区分大小写，但统一格式便于后续逻辑与展示

### 2.2 `safe_external` 装饰器（异常保护）

统一包装以下操作，捕获异常并返回默认值或重新抛出：

| 场景     | 函数                | 失败时行为       |
|----------|---------------------|------------------|
| 外部 API | `_fetch_yahoo()`    | 返回空 DataFrame |
| 日期解析 | `_safe_datetime()`  | 返回 None        |
| I/O 读取 | `_load_json_file()` | 返回 None        |
| I/O 写入 | `save_json()`       | 打印错误并 reraise |

- VIX 下载失败时，用 NaN 填充 `vix_close`，策略继续运行
- 所有 `yf.download` 调用改为 `_fetch_yahoo`

---

## 3. 输出人性化调整

### 3.1 新增指标

| 字段     | 说明                     |
|----------|--------------------------|
| 布林上軌 | `bbu_20_2.0`             |
| MA50     | `ema_slow_50`（GMMA 50 日 EMA） |

### 3.2 DCA 动态数据

- 移除固定文案，改为基于当日参数的动态说明
- **說明**：说明 DCA 逻辑与参考意义
- **當前RSI**：当日 RSI
- **當前風險因子**：ATR/VIX 调节后的风险因子
- **RSI&lt;55 (Tier1)** / **RSI&lt;45 (Tier2)**：满足条件时的实际建仓比例（30%/70% × 风险因子）

### 3.3 APY / Total_Return 百分比格式

- 输出格式：`"2.53%"`、`"28.96%"` 等字符串

### 3.4 建议仓位逻辑修正

- **原问题**：持仓日无新信号时，`Suggested_Size` 为 0，导致建议仓位显示 0%
- **处理**：使用 `max(Position, Suggested_Size)` 作为展示值
  - 持仓日显示当前 `Position`
  - 有新买入则显示 `Suggested_Size`
  - 已清仓时两者为 0，显示 0%

### 3.5 Windows 控制台编码

- 写入 JSON 时先 `save_json` 再 `print`，确保即使控制台报错也能保存
- 捕获 `UnicodeEncodeError` 时改用 `ensure_ascii=True`，避免脚本直接退出

---

## 4. 输出结构示例

```json
[
  {
    "Ticker": "tqqq",
    "Underlying": "QQQ",
    "APY": "2.53%",
    "Total_Return": "28.96%",
    "UI_Summary": {
      "日期": "2026-03-06",
      "槓杆ETF": "tqqq",
      "基礎標的": "QQQ",
      "趨勢 (GMMA)": "熊市",
      "信號": "TIME-EXIT (Reduce 50%)",
      "建議倉位": "0%",
      "當前價格": 47.54,
      "布林下軌": 597.46,
      "布林上軌": 616.75,
      "MA50": 611.48,
      "DCA": {
        "說明": "牛市回調時分批建倉，避免一次性重倉。滿足條件時建議倉位如下：",
        "當前RSI": 43.1,
        "當前風險因子": 0.18,
        "RSI<55 (Tier1)": "建倉5%",
        "RSI<45 (Tier2)": "補倉至12%"
      }
    }
  }
]
```

---

## 5. 待讨论：Web App 后端扩展

作为 API 返回给前端时，可考虑增加：

1. **`meta`**：`generated_at`、`data_range` 等元数据
2. **`chart_data`**：精简历史数据（日期、价格、净值、仓位），供前端绘图
3. **`metrics`**：Sharpe、最大回撤、交易次数、胜率等策略指标
4. **`action_hint`**：简短操作提示（如「等待 RSI &lt; 55 且牛市確認」）
5. **`signal_badge` / `trend_icon`**：前端展示用的样式/图标建议

---

## 6. 涉及文件

- `operation_prediction_v3.py`（主逻辑与 CLI）

---

## 7. 运行验证

```powershell
cd c:\Users\Administrator\Documents\ETF_project
.\venv_ta\Scripts\python.exe operation_prediction_v3.py run --ticker tqqq soxl --out result.json
```

使用 Python 3.11 虚拟环境 `venv_ta`。若 PowerShell 限制脚本执行，可使用：

```powershell
.\venv_ta\Scripts\python.exe operation_prediction_v3.py run --ticker tqqq soxl
```
