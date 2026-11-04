# 前端工作区（app/ui/）

这个文件夹归前端同学所有，可以自由修改；核心脚本与 `output/` 产物不要动。

## 当前页面

`dashboard.py` 顶部有数据源切换：

| 选项 | 数据 | 说明 |
| --- | --- | --- |
| **生鲜前置仓（默认）** | `output/freshretail_*` 八张表 | 五区块：KPI、补货清单（可点选展开）、单品详情、决策留痕、Agent 对话 |
| **制造企业（保底）** | DataCo 原有 CSV | 原来的预测 + 库存 + 补货表 + 对话，作为回退方案保留 |

切换后四个区块全部使用对应数据源，不混用。生鲜版标题为「智链参谋 · 生鲜前置仓补货决策助手」。

## 运行

```powershell
$env:PYTHONUTF8=1; $env:PYTHONIOENCODING='utf-8'
.\.venv\Scripts\python.exe -m streamlit run app\ui\dashboard.py
```

默认地址 `http://localhost:8501`。只使用项目内 `.venv`，不要用裸 `python`。

## 生鲜八张表（列名即契约，只读不写）

| 文件 | 行数 | 关键列 | 页面用途 |
| --- | ---: | --- | --- |
| `freshretail_inventory_replenishment.csv` | 200 | `store_id, product_id, shelf_life_days, on_hand, rop_p90, target_level, suggest_qty, urgency, expiring_qty` | KPI、补货清单、单品详情 |
| `freshretail_forecast_predictions.csv` | 1,400 | `store_id, product_id, dt, horizon, y_actual, y_pred_p50, y_pred_p90, stockout_rate` | 7 天 P50/P90 折线与实测点（回测窗口 2024-06-26~07-02）；效期上限 |
| `freshretail_inventory_policy_comparison.csv` | 9 | `policy, shelf_life_days, fill_rate, stockout_rate, spoilage_rate, avg_on_hand, total_cost, turnover_days, stockout_units, spoilage_units` | 短保报损对比 KPI（策略 C vs B） |
| `freshretail_inventory_cap_sweep.csv` | 15 | `policy, shelf_life_days, multiplier, fill_rate, spoilage_rate, avg_on_hand, total_cost, stockout_units, spoilage_units` | 效期倍数扫描 + 三方案依据 |
| `freshretail_sku_summary_all.csv` | 50,000（`selected=1` 为分析范围） | `store_id, product_id, total_sale, daily_mean, cv, coverage_days, avg_stockout_rate, selected` | 单品详情的历史序列汇总 |
| `freshretail_what_if.csv` | 1,400 | `store_id, product_id, base_order_qty, scenario_ratio, scenario_order_qty, unmet_units, spoiled_units, fill_rate, total_cost, delta_*_vs_base` | 溯源面板「② 不照做会怎样」单序列明细 |
| `freshretail_what_if_summary.csv` | 7 | `scenario_ratio, scenario_label, 总缺货件, 总报损件, 平均满足率, 总成本, 相对基线成本差` | 同上的全局兜底 |
| `alpha_sweep_terminal.csv` | 60 | `alpha, shelf_life_days, basis, total_cost, unmet_units, spoiled_units` | 溯源面板「③ 分位凭什么定在这」 |

> `alpha_sweep_terminal.csv` 只有 3 天 / 5 天两档，`basis` 有 `no_settle`（主口径）与
> `full_settle`（期末折价边界）两组；画图只用 `no_settle`，两种混画会看不懂。

### 取数口径

- **所有业务数字都从上述 CSV 现算**，页面不硬编码 200、153、8.38%、32.04% 等值。
- **效期修正目标**：CSV 没有单独的 `target_eff` 列，页面按与 Agent 相同的口径现算：
  `min(target_level, 保质期内预计可售量 × expiry_cap_multiplier)`。
  可售量由 `freshretail_forecast_predictions.csv` 的 P50 从到货日起累加 `shelf_life_days` 天得到；倍数、提前期、快照日来自 `config/freshretail_inventory.yaml`。
- **快照日期**：取预测表 `dt` 的最大值（当前 `2024-07-02`）。
- **商品名称按演示品名映射展示**：`app/product_names.py` 为补货快照中的全部 50 个 `product_id` 硬编码了具体生鲜品名，表格首列格式为「具体品名（门店-编号）」，例如 `黑猪五花肉（145-802）`；未在映射表中的编号从真实品名池稳定分配。原始数据已脱敏，这些名称仅用于演示，不对外宣称是官方品类。
- **生鲜页面不出现 DataCo 的 SKU 编号与数字**；DataCo 分支只在切到「制造企业（保底）」时渲染。

## 五个区块

1. **今日概览**：在管序列、今天要补（紧急 / 建议补货）、临期货（件数 + 涉及序列）、短保报损对比（策略 C vs B，标注保质期档）。每张卡下方注明来源。
2. **补货清单**：200 行主表（`st.dataframe` + `on_select="rerun"` / `selection_mode="single-row"`），支持紧急度多选、保质期档（**按补货表实际存在的档位出选项**，当前只有 5 天；config 的 3/5/7 是设计值，别照抄成死选项）、临期仅看、SKU 搜索（如 `145-802`）；默认按紧急度、临期数量降序；「建议订货」保持 int 以便排序。**点行后表格下方就地展开溯源面板**，见下节。
3. **单品详情**：7 天 P50/P90 折线 + 实测点；六张数字卡；模板拼接的补货建议文案（不调用 LLM）。表格点行后会**跟随切到同一条序列**，两个入口不打架。
4. **决策留痕与偏差校准**：整单执行比例下拉 + 确认按钮 → 追加写 `output/user_decisions.csv`；累计 5 条以上且系统性偏离时给出参数建议，需人工确认才写 `config/freshretail_inventory_overrides.yaml`。
5. **补货 Agent**：复用 `app/agent.py` 的 `run_turn()`，数据源切到 `freshretail`；快捷问题为「哪些 SKU 最紧急？」「SKU 145-802 该补多少？」「哪些货快过期了？」；每条回答下方附「数据来源：补货快照 …」。

## 补货清单的点行展开（第 13 步）

`render_trace_panel()` 渲染，三个分页：

| 分页 | 数据来源 | 回答什么 |
| --- | --- | --- |
| ① 这个数怎么算的 | 04 + 07 + config | 四步推导链（紧急度分档 → 目标水位 → 效期上限 → 订货量）+ 三方案对比 + 该档倍数扫描 |
| ② 不照做会怎样 | `freshretail_what_if.csv` | 7 档下单比例的缺货 / 报损 / 成本差 |
| ③ 分位凭什么定在这 | `alpha_sweep_terminal.csv` | α → 期末成本曲线 + 「0.90 以上不可辨识」区间 |

**三条硬约束，改视觉时不要破坏：**

1. **订货量的闸门是目标水位，不是 ROP。** 在库高于 ROP 但低于目标水位时仍要下单——04 表里 45 条「计划补货」就是这种。用 ROP 做闸门会让这 45 行全变 0 件。紧急度只用 ROP 做分档。
2. **三方案的「当前配置」版必须等于 04 表的 `suggest_qty`，200/200 逐行相等。** 页面有自检（`scheme_base_check()`），对不上会自动降级成只显示推导链并弹警告。这是防止口径漂移的护栏，不要删。
3. **三方案是「配置倍数 ±0.1」（当前 0.9 / 1.0 / 1.1），不是最优排序。** 不要把 0.7 叫「省货版」：5 天档 0.7 倍成本 1,234.19 比 1.0 倍的 917.50 高 34.5%，缺货惩罚（5 元/件）盖过了报损节省（1 元/件）。该档真正的最低点是 0.9 倍（836.05）。

## 决策留痕（第 15 步）

`app/decision_support.py` 只做纯计算与文件读写，不 import streamlit，可脱离界面单测。

- `output/user_decisions.csv`：15 列，一行一条，追加写、不覆盖。同一单 60 秒内不重复写（防误触把偏差统计污染）。
- 判定规则：有效记录 n ≥ 5、`|平均偏离| ≥ 15%`、同号占比 ≥ 80%，**三条同时满足**才提示。
- 参数建议调的是 `costs.stockout_penalty_per_unit`，夹在 [1, 10]；写 `config/freshretail_inventory_overrides.yaml`，**不碰主配置**。主配置一动，5 张交付表和 4 个 α 基线的 MD5 全部失效，所以这里只给建议、要重跑脚本才生效。

> 当前快照（2024-07-02）的 KPI、补货清单样例、单品详情与对话示例数字，见根目录 `README.md` 的「看板数据快照」一节。

## DataCo 保底数据（切到制造企业时）

| 用途 | 文件 | 关键列 |
| --- | --- | --- |
| 需求预测曲线 | `forecast_cumulative_predictions.csv` | `sku_id / week / p50 / p90 / p95 / actual` |
| 库存状态表 | `inventory_replenishment.csv` | `sku_id / available_inventory / reorder_point / safety_stock / target_level / recommended_order_qty / urgency / drift_status / reason` |
| 三策略对比 | `inventory_policy_simulation.csv` | `policy / service_rate / avg_inventory / total_cost` |
| 历史需求 | `demand_weekly.csv` | 周度需求序列 |

## 截图

位于 `output/`：

- `dashboard_fresh_kpi.png`：顶部数据源切换 + 四张 KPI
- `dashboard_fresh_replenishment.png`：补货清单与筛选
- `dashboard_fresh_detail.png`：单品详情（折线 + 数字块 + 建议文案）
- `dashboard_fresh_chat.png`：Agent 对话与工具调用展开
- `dashboard_dataco_regression.png`：切回制造企业后的回归截图

## 交接

收工前把 `app/ui/` 整个文件夹发群里（只有这一个文件夹属于前端）；开发线收到后覆盖进主项目。
