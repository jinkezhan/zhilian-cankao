# 前端工作区（app/ui/）

这个文件夹归前端同学所有，可以自由修改；核心脚本与 `output/` 产物不要动。

## 当前页面

`dashboard.py` 顶部有数据源切换：

| 选项 | 数据 | 说明 |
| --- | --- | --- |
| **生鲜前置仓（默认）** | `output/freshretail_*` 五张表 | 四区块：KPI、补货清单、单品详情、Agent 对话 |
| **制造企业（保底）** | DataCo 原有 CSV | 原来的预测 + 库存 + 补货表 + 对话，作为回退方案保留 |

切换后四个区块全部使用对应数据源，不混用。生鲜版标题为「智链参谋 · 生鲜前置仓补货决策助手」。

## 运行

```powershell
$env:PYTHONUTF8=1; $env:PYTHONIOENCODING='utf-8'
.\.venv\Scripts\python.exe -m streamlit run app\ui\dashboard.py
```

默认地址 `http://localhost:8501`。只使用项目内 `.venv`，不要用裸 `python`。

## 生鲜五张表（列名即契约，只读不写）

| 文件 | 行数 | 关键列 | 页面用途 |
| --- | ---: | --- | --- |
| `freshretail_inventory_replenishment.csv` | 200 | `store_id, product_id, shelf_life_days, on_hand, rop_p90, target_level, suggest_qty, urgency, expiring_qty` | KPI、补货清单、单品详情 |
| `freshretail_forecast_predictions.csv` | 1,400 | `store_id, product_id, dt, horizon, y_actual, y_pred_p50, y_pred_p90, stockout_rate` | 7 天 P50/P90 折线与实测点；效期上限 |
| `freshretail_inventory_policy_comparison.csv` | 9 | `policy, shelf_life_days, fill_rate, stockout_rate, spoilage_rate, avg_on_hand, total_cost, turnover_days, stockout_units, spoilage_units` | 短保报损对比 KPI（策略 C vs B） |
| `freshretail_inventory_cap_sweep.csv` | 15 | `policy, shelf_life_days, multiplier, fill_rate, spoilage_rate, avg_on_hand, total_cost, stockout_units, spoilage_units` | 补货清单下方「效期倍数扫描」 |
| `freshretail_sku_summary_all.csv` | 50,000（`selected=1` 为分析范围） | `store_id, product_id, total_sale, daily_mean, cv, coverage_days, avg_stockout_rate, selected` | 单品详情的历史序列汇总 |

### 取数口径

- **所有业务数字都从上述 CSV 现算**，页面不硬编码 200、153、8.38%、32.04% 等值。
- **效期修正目标**：CSV 没有单独的 `target_eff` 列，页面按与 Agent 相同的口径现算：
  `min(target_level, 保质期内预计可售量 × expiry_cap_multiplier)`。
  可售量由 `freshretail_forecast_predictions.csv` 的 P50 从到货日起累加 `shelf_life_days` 天得到；倍数、提前期、快照日来自 `config/freshretail_inventory.yaml`。
- **快照日期**：取预测表 `dt` 的最大值（当前 `2024-07-02`）。
- **商品名称统一写 `SKU {store_id}-{product_id}`**。品类已官方脱敏，禁止编造“苹果 / 蔬菜 / 猪肉”等具体品名。
- **生鲜页面不出现 DataCo 的 SKU 编号与数字**；DataCo 分支只在切到「制造企业（保底）」时渲染。

## 四个区块

1. **今日概览**：在管序列、今天要补（紧急 / 建议补货）、临期货（件数 + 涉及序列）、短保报损对比（策略 C vs B，标注保质期档）。每张卡下方注明来源。
2. **补货清单**：200 行主表，支持紧急度多选、保质期档（3/5/7）、临期仅看、SKU 搜索（如 `145-802`）；默认按紧急度、临期数量降序；临期行高亮并带「临期」标签；空结果有空状态提示。
3. **单品详情**：7 天 P50/P90 折线 + 实测点；现有库存 / ROP_P90 / 效期修正目标 / 建议订货 / 临期数量 / 保质期档六张卡；模板拼接的补货建议文案（不调用 LLM）。
4. **补货 Agent**：复用 `app/agent.py` 的 `run_turn()`，数据源切到 `freshretail`；快捷问题为「哪些 SKU 最紧急？」「SKU 145-802 该补多少？」「哪些货快过期了？」；每条回答下方附「数据来源：补货快照 …」。

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
