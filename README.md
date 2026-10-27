# 智链参谋 · 中小制造企业供应链助手

> zhilian-canmou

面向中小制造企业的供应链分析与辅助决策工具，围绕需求预测、库存优化、供应商评估等场景提供数据驱动的参考建议。

## 项目状态

🚧 需求预测 + 库存优化 + Agent 阶段 —— 已完成数据探查、周度基线/池化 LightGBM/组合对比、未来 H 周累计需求分位数预测、分位数驱动的补货建议与三策略仿真；Agent 对话层已实现（`app/agent.py` + `app/agent_test.py`，设计见 [AGENT_DESIGN.md](AGENT_DESIGN.md)），10 条测试 **10/10 通过**；Streamlit 演示看板已完成（`app/ui/dashboard.py`）。供应商评估与正式前端产品化待开发。

> **当前主线已切换为生鲜线（FreshRetailNet-50K）**：预处理 ✅、需求预测 ✅、效期库存仿真 ✅（第二轮收口：target_eff + 效期倍数扫描 + 成本分层结论）、Agent ✅（四工具 + 14 条用例）、生鲜线看板 ✅（默认数据源；顶部可切回制造企业）。原 DataCo 制造/订单线保留为**保底方案（DataCo 版），已存档 `4e34518`**，见下方同名标注。

## 目录结构

```
zhilian-canmou/
├── data/            # 原始数据与处理后数据（freshretail/ 为生鲜线面板）
├── models/          # 训练好的模型文件
├── app/             # 应用代码（DataCo 脚本、freshretail_inventory.py、app/ui/dashboard.py 看板）
├── src/freshretail/ # 生鲜线预处理、预测、报告生成脚本
├── config/          # freshretail_inventory.yaml：生鲜效期库存仿真参数（队友 10/30 参数表入口）
├── .streamlit/      # Streamlit 浅色主题与无头启动配置
├── output/          # 分析结果、图表、看板截图与报告
├── config.yaml      # DataCo 预测 + 库存参数（累计需求跨度、分位数、提前期、服务水平等，企业可调）
├── requirements.txt # Python 依赖
├── VENV_SETUP.md    # 虚拟环境搭建说明
├── AGENT_DESIGN.md  # 第 5 步 Agent 对话层设计（工具 schema、系统提示词、测试方案）
└── README.md
```

## 环境准备

推荐 Python 3.10 及以上版本。详细步骤见 [VENV_SETUP.md](VENV_SETUP.md)，快速开始：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## 数据说明

原始数据为 Kaggle DataCo Supply Chain 数据集，存放于项目外部：

| 文件 | 说明 |
| --- | --- |
| `D:\ai-race data\DataCoSupplyChainDataset.xlsx` | 订单明细，180,519 行 × 53 列，下单时间 2015-01-01 至 2018-01-31 |
| `D:\ai-race data\DescriptionDataCoSupplyChain.xlsx` | 字段说明，52 个字段 |

主要字段：`Order Id`（订单）、`Order Item Quantity`（件数）、`Product Card Id` / `Product Name`（SKU）、`Category Name`（品类）、`order date (DateOrders)`（下单时间）。

> 数据质量：约 4.2% 的行存在字段错位（商品名称为数值、商品状态为运输方式等），且
> 2017-10 至 2018-01 的订单结构与前期不一致。脚本会剔除这些行/月份，详见 `output/`。

### 生鲜线数据（主线）

| 文件 | 说明 |
| --- | --- |
| `data/freshretail/train.parquet` / `eval.parquet` | FreshRetailNet-50K 原始 train/eval 段 |
| `data/freshretail/clean_daily.parquet` | 下游唯一输入：200 条“店×SKU”序列 × 97 天（2024-03-28 ~ 2024-07-02） |
| `output/freshretail_forecast_predictions.csv` | 预测交付表：7 天 × 200 序列 = 1,400 行，含 `y_actual` / `y_pred_p50` / `y_pred_p90` / `stockout_rate` |

数据来源：**Dingdong-Inc, FreshRetailNet-50K, arXiv:2505.16319, CC BY 4.0**。898 门店、18 城、865 生鲜 SKU，2024-03-28 ~ 2024-07-02 的小时级销售与缺货标注；本项目取其中 200 条高销、连续 97 天的“店×SKU”序列。

## 功能规划

### 生鲜线（主线）

- [x] 预处理与 EDA（200 条“店×SKU”序列，`clean_daily.parquet`）
- [x] 7 天日销预测（WMAPE **0.2588**，较 MA7 基线 +8.09%；P50/P90 覆盖 50.57% / 89.64%）
- [x] 效期库存仿真（三策略 × 三档保质期 × 基准/高压缺货惩罚）
- [x] 生鲜线 Agent 对话层（4 个只读工具 + 14 条用例）
- [x] 生鲜线 Streamlit 看板（默认数据源；四区块 + 顶部切回制造企业）

### DataCo 保底线（原功能规划）

- [x] 数据加载与清洗
- [x] 需求预测模型（基线 + 池化 LightGBM + 组合 + 累计需求分位数）
- [x] 库存优化建议（分位数 ROP + 安全库存 + 漂移监测 + 三策略仿真）
- [x] Agent 对话层实现（4 个工具 + 命令行交互 + 测试脚本）
- [x] Agent 完整 10 条测试（10/10 通过）
- [x] Streamlit 演示看板（预测 + 库存 + 补货表 + Agent 对话）
- [ ] 供应商评估
- [ ] 正式前端产品化（文件上传、用户体系等）

## 使用方法

激活虚拟环境并安装依赖（详见 [VENV_SETUP.md](VENV_SETUP.md)）：

```powershell
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

运行数据探查脚本：

```powershell
python app/explore_data.py
```

默认读取 `D:\ai-race data\DataCoSupplyChainDataset.xlsx`，可通过参数指定其他文件：

```powershell
python app/explore_data.py --input "D:\path\to\DataCoSupplyChainDataset.xlsx"
```

探查结果输出到 `output/`：

| 文件 | 说明 |
| --- | --- |
| `column_profile.csv` | 每个字段的类型、缺失比例、唯一值数量 |
| `time_range.csv` | 下单时间、发货时间的起止范围 |
| `monthly_structure.csv` | 月度订单结构（每单行数、每行件数）及异常月份 |
| `data_quality.csv` | 数据质量校验明细 |
| `top10_sku.csv` / `top10_category.csv` / `top10_dates.csv` | SKU、品类、日期的需求量 Top 10 |
| `sku_density.csv` | 各 SKU 的日/周/月覆盖度与需求波动 |
| `demand_daily.csv` / `demand_weekly.csv` / `demand_monthly.csv` | 日/周/月需求聚合数据 |
| `demand_trend_*.png` | 全量日/周/月趋势图（阴影标注结构突变期） |
| `demand_trend_stable_*.png` | 剔除结构突变期后的稳定期趋势图 |
| `exploration_summary.txt` | 探查摘要 |

> 注意：原始 Excel 中约 4.2% 的行存在字段错位；2017-10 起订单结构发生突变（每单约 1 行、每行约 1 件，仅覆盖美国/波多黎各）。脚本会自动校验并剔除，详见 `output/data_quality.csv` 与 `output/monthly_structure.csv`。

## 生鲜线（freshretail）

> **数据来源**：Dingdong-Inc. **FreshRetailNet-50K**. arXiv:2505.16319. CC BY 4.0。
> 叮咚买菜官方数据，2024-03-28 ~ 2024-07-02，本项目取 200 条“店×SKU”连续序列（train 90 天 + eval 7 天）。
> 品类名称已官方脱敏，只有 `third_category_id`，因此保质期只能按档位做敏感性，不能逐品类赋值。

### 模块状态

| 模块 | 状态 | 当前证据 |
| --- | --- | --- |
| 预处理 | ✅ | `data/freshretail/clean_daily.parquet`：19,400 行 × 14 列，200 序列 × 97 天，无缺日/重复键 |
| 需求预测 | ✅ | `output/freshretail_forecast_predictions.csv`：1,400 行；WMAPE **0.2588**，较 MA7 基线 **+8.09%**；P50/P90 覆盖 **50.57% / 89.64%** |
| 库存仿真 | ✅ | 三策略 × 三保质期 9 行对比、200 行补货快照、12,600 行逐日状态、15 行效期倍数扫描；默认 multiplier **1.00**，报告见 `output/freshretail_inventory_report.md` |
| Agent | ✅ | 复用 DeepSeek/run_turn 框架，新增 4 个生鲜工具；14/14 用例、9/9 单元测试；报告见 `output/freshretail_agent_report.md` |
| 看板 | ✅ | `app/ui/dashboard.py`：顶部切换「生鲜前置仓（默认）/ 制造企业（保底）」；生鲜版四区块（KPI / 补货清单 / 单品详情 / Agent）；截图见 `output/dashboard_fresh_*.png` |

### 运行

```powershell
$env:PYTHONUTF8=1; $env:PYTHONIOENCODING='utf-8'

# 1) 预处理（生成 clean_daily.parquet）
.\.venv\Scripts\python.exe -I src\freshretail\prepare_freshretail.py

# 2) 7 天需求预测（生成 freshretail_forecast_predictions.csv）
.\.venv\Scripts\python.exe -I -u src\freshretail\forecast_freshretail.py

# 3) 预测报告
.\.venv\Scripts\python.exe -I src\freshretail\make_forecast_report.py

# 4) 效期库存仿真（生鲜线第 3 步）
.\.venv\Scripts\python.exe -I app\freshretail_inventory.py
```

> 库存仿真额外依赖 `PyYAML`（已加入 `requirements.txt`），用于读取 `config/freshretail_inventory.yaml`。

### 输入与输出

输入（只读，不改预测模块）：

| 文件 | 说明 |
| --- | --- |
| `output/freshretail_forecast_predictions.csv` | 1,400 行 = 200 序列 × 7 天；含 `y_actual` / `y_pred_p50` / `y_pred_p90` / `stockout_rate` |
| `data/freshretail/clean_daily.parquet` | 训练段历史销量，用于策略 A 的 MA7 与策略 B 的 σ14 |
| `config/freshretail_inventory.yaml` | 全部业务参数；代码不硬编码 |

输出：

| 文件 | 行数 | 用途 |
| --- | ---: | --- |
| `output/freshretail_inventory_replenishment.csv` | 200 | 补货快照（基准保质期、策略 C） |
| `output/freshretail_inventory_policy_comparison.csv` | 9 | 三策略 × 三保质期指标对比 |
| `output/freshretail_inventory_simulation_states.csv` | 12,600 | 逐日逐序列明细（策略 × 保质期 × 序列 × 天） |
| `output/freshretail_inventory_penalty_sensitivity.csv` | 18 | 缺货惩罚 5 / 20 的敏感性（附加第四张 csv） |
| `output/freshretail_inventory_cap_sweep.csv` | 15 | 效期倍数 0.70~1.10 × 3 档的 C 扫描（第五张 csv） |
| `output/freshretail_inventory_report.md` | — | 参数表、三策略对比、倍数扫描、成本分解、分层结论、局限 |

### 五张库存输出 csv 的列名契约（四张主表 + 扫描表）

**1）`freshretail_inventory_replenishment.csv`（补货快照，每序列一行）**

| 列 | 含义 |
| --- | --- |
| `store_id` | 门店 ID |
| `product_id` | SKU ID |
| `shelf_life_days` | 基准保质期档位（天） |
| `on_hand` | 快照时点现有库存（件） |
| `rop_p90` | P90 提前期需求触发线 |
| `target_level` | P50 的 (lead_time + target_cover_days) 日累计目标水位 |
| `suggest_qty` | 建议订货量（件，整数；受效期上限约束） |
| `urgency` | 紧急 / 建议补货 / 计划补货 / 正常 |
| `expiring_qty` | 剩余保质期 ≤ 1 天的临期库存（件） |

**2）`freshretail_inventory_policy_comparison.csv`（3 策略 × 3 保质期 = 9 行）**

| 列 | 含义 |
| --- | --- |
| `policy` | A 经验规则 / B 传统 ROP / C 效期感知 |
| `shelf_life_days` | 保质期档位（天） |
| `fill_rate` | 满足率 = 售出 / 需求 |
| `stockout_rate` | 缺货率 = 未满足 / 需求 |
| `spoilage_rate` | 报损率 = 报损 / (初始库存 + 累计到货) |
| `avg_on_hand` | 日均在库（件） |
| `total_cost` | 缺货惩罚 + 报损 + 持有成本 |
| `turnover_days` | 库存周转天数 = 在库合计 / 售出合计 |
| `stockout_units` | 缺货件数 |
| `spoilage_units` | 报损件数 |

**3）`freshretail_inventory_simulation_states.csv`（逐日逐序列明细）**

| 列 | 含义 |
| --- | --- |
| `dt` | 日期（2024-06-26 ~ 2024-07-02） |
| `store_id` | 门店 ID |
| `product_id` | SKU ID |
| `policy` | A / B / C |
| `shelf_life_days` | 保质期档位 |
| `on_hand_start` | 当日营业前库存（含当日到货） |
| `received` | 当日到货 |
| `demand` | 当日观测需求（缺货截断值） |
| `sold` | 当日售出 |
| `spoilage` | 当日日终报损 |
| `on_hand_end` | 当日日终库存 |

**4）`freshretail_inventory_penalty_sensitivity.csv`（附加，18 行 = 3 策略 × 3 保质期 × 2 惩罚）**

| 列 | 含义 |
| --- | --- |
| `policy` | A / B / C |
| `shelf_life_days` | 保质期档位 |
| `stockout_penalty_per_unit` | 缺货惩罚（归一化金额/件） |
| `fill_rate` | 满足率 |
| `stockout_rate` | 缺货率 |
| `spoilage_rate` | 报损率 |
| `avg_on_hand` | 日均在库 |
| `total_cost` | 该惩罚下的总成本 |
| `turnover_days` | 周转天数 |
| `stockout_units` | 缺货件数 |
| `spoilage_units` | 报损件数 |

**5）`freshretail_inventory_cap_sweep.csv`（附加，15 行 = 3 保质期 × 5 个倍数，仅 C）**

| 列 | 含义 |
| --- | --- |
| `policy` | 固定为 C |
| `shelf_life_days` | 保质期档位 |
| `multiplier` | 效期倍数（0.70 / 0.80 / 0.90 / 1.00 / 1.10） |
| `fill_rate` | 满足率 |
| `spoilage_rate` | 报损率 |
| `avg_on_hand` | 日均在库 |
| `total_cost` | 基准缺货惩罚下的总成本 |
| `stockout_units` | 缺货件数 |
| `spoilage_units` | 报损件数 |

> 默认 multiplier 不是人工挑的：每档在 `fill_rate ≥ B_fill − 2pp` 的候选中取 `total_cost` 最小，三档取众数；无众数则取短保档最优值。本轮各档选中 `{3: 1.0, 5: 0.9, 7: 1.0}`，全局默认 **1.00**。

### Agent（生鲜线）

复用已验收的 Agent 框架（DeepSeek API、`.env` 的 `DEEPSEEK_API_KEY`、`requests` 直调、`run_turn`、函数调用循环），只替换数据口径；DataCo 四工具实现保留在 `app/agent.py` 不动。

```powershell
# 显式切换数据源
.\.venv\Scripts\python.exe app\agent.py --dataset freshretail --once "SKU 145-802 现在该补多少？"
# 或设置环境变量
$env:ZHILIAN_DATASET='freshretail'
.\.venv\Scripts\python.exe app\agent.py --once "哪些序列有临期库存？"
```

| 工具 | 输入 | 返回 | 数据来源 |
| --- | --- | --- | --- |
| `get_forecast` | `store_id, product_id` | 未来 7 天逐日 P50/P90、平均 `stockout_rate`；说明“P90 已含缺货风险冗余” | `output/freshretail_forecast_predictions.csv` |
| `get_inventory_status` | `store_id, product_id` | `on_hand`、`rop_p90`、`target_level`、`target_eff`、`expiring_qty`、`shelf_life_days`；临期时提示优先促销 | `output/freshretail_inventory_replenishment.csv` |
| `recommend_replenishment` | `store_id, product_id` | `suggest_qty`、紧急度、效期上限数字与理由（上限 = 保质期内可售量 × config 倍数） | 补货快照 + 7 天预测 + `config/freshretail_inventory.yaml` |
| `list_risk_skus` | 无 | 紧急度非“正常”的全部序列（按紧急/建议/计划排序）+ 单列“临期风险”（含正常档） | 补货快照 |

- 紧急度四档沿用旧口径，一字不改：紧急（`on_hand < 0.5×ROP_P90`）/ 建议补货（`< ROP_P90`）/ 计划补货（`< 目标水位`）/ 正常（`≥ 目标水位` 且订货 0）。Agent 层启动时再断言一次“正常档 `suggest_qty=0`”。
- 当前快照 200 条的分布是 **建议补货 153 / 计划补货 45 / 紧急 2 / 正常 0**；“正常”档结构性为空（效期约束把目标水位收紧，库存长期低于目标），不是数据遗漏。Agent 被要求如实回答“在这批序列里没有正常档”，不得编造。
- 商品统一说 **SKU `{store_id}-{product_id}`**；品类名称已官方脱敏，禁止编造具体品类名。
- 测试：`output/freshretail_agent_test_cases.csv`（14 条，全部正确）；报告 `output/freshretail_agent_report.md`（含架构、工具映射、单元测试 9/9、局限）。

#### Agent 数据源与规模

| 文件 | 行数 | Agent 用途 |
| --- | ---: | --- |
| `output/freshretail_forecast_predictions.csv` | 1,400 | 200 序列 × 7 天；`get_forecast` 的 P50/P90/stockout_rate |
| `output/freshretail_inventory_replenishment.csv` | 200 | 每序列一行；库存/临期/建议订货量/紧急度 |
| `output/freshretail_sku_summary_all.csv` | 50,000（其中 selected=200） | 判断序列是否存在/是否在分析范围 |
| `config/freshretail_inventory.yaml` | — | 效期倍数、提前期、快照日期、紧急度阈值 |
| `output/freshretail_agent_test_cases.csv` | 14 | 问题、工具调用链、回答全文、人工判定 |

> 所有库存/临期/建议订货量数字来自 **补货快照 2024-07-02**；预测数字来自 **7 天预测表 2024-06-26 ~ 07-02**。Agent 回答必须带来源，不得混用。

#### Agent 关键数据样例（来自补货快照 2024-07-02）

| SKU | on_hand | ROP_P90 | 目标水位 | target_eff | 建议订货 | 紧急度 | 临期 |
| --- | ---: | ---: | ---: | ---: | ---: | --- | ---: |
| SKU 145-802 | 2.00 | 4.06 | 11.29 | 7.06 | 6 | 紧急 | 0 |
| SKU 781-802 | 2.00 | 4.12 | 11.20 | 7.00 | 6 | 紧急 | 0 |
| SKU 18-19 | 4.00 | 5.58 | 29.99 | 18.74 | 15 | 建议补货 | 0 |
| SKU 555-300 | 12.08 | 15.34 | 94.30 | 58.94 | 47 | 建议补货 | 6.08 |
| SKU 18-300 | 26.36 | 18.58 | 130.31 | 81.44 | 56 | 计划补货 | 26.36 |
| SKU 3-834 | 4.00 | 6.13 | 26.88 | 16.80 | 13 | 建议补货 | 0 |

- 快照整体：**紧急 2 / 建议补货 153 / 计划补货 45 / 正常 0**；临期 29 条，临期合计 147.42 件。
- `SKU 3-834` 的 7 天预测：P50 3.16~5.11 件、P90 5.71~7.85 件、平均 `stockout_rate` 0.00。
- 正常档无真实实例；`on_hand=target` 的构造用例验证“正常且订货 0”，并验证快照中 `suggest_qty>0` 会触发 Agent 层断言。

#### Agent 测试用例（14/14 正确）

| # | 类别 | 问题 | 工具调用链 | 判定 |
| ---: | --- | --- | --- | --- |
| 1 | 预测 | SKU 3-834 未来 7 天 P50/P90 和缺货率？ | `get_forecast` | 正确 |
| 2 | 库存+临期 | SKU 18-300 库存和临期？ | `get_inventory_status` | 正确 |
| 3 | 紧急补货 | SKU 145-802 该补多少？ | `recommend_replenishment` (+`get_inventory_status`) | 正确 |
| 4 | 批量风险 | 哪些序列需要补货？ | `list_risk_skus` | 正确 |
| 5 | 正常档提问 | 有没有库存正常的序列？ | 无（直接按事实回答） | 正确 |
| 6 | 跨店跨 SKU | SKU 3-834 和 SKU 18-19 库存？ | `get_inventory_status` ×2 | 正确 |
| 7 | 不存在序列 | SKU 999-999 未来 7 天预测？ | `get_forecast`（found=false） | 正确 |
| 8 | 空数据 | 哪些序列需要补货？（空夹具） | `list_risk_skus`（0 条） | 正确 |
| 9 | 批量临期 | 哪些序列有临期库存？ | `list_risk_skus` | 正确 |
| 10 | 连续追问 | 哪些序列紧急？→ 第一个呢？ | `list_risk_skus` → `get_forecast`/`get_inventory_status`/`recommend_replenishment` | 正确 |
| 11 | P90 解释 | SKU 18-19 的 P90 为什么高？ | `get_forecast` | 正确 |
| 12 | 计划补货 | SKU 18-300 应该订多少？ | `recommend_replenishment` | 正确 |
| 13 | 品类脱敏 | SKU 18-300 是什么商品？ | 无（说明无法判断） | 正确 |
| 14 | 建议补货+临期 | SKU 555-300 该补多少？临期怎么办？ | `recommend_replenishment` + `get_inventory_status` | 正确 |

> 单元测试 9/9：200 条建议量与快照逐条一致、逐日 P50/P90 与预测表一致、库存/临期字段一致、推荐理由含上限数字、构造正常档、正常档订货 0 断言、空数据、不存在序列、SKU 标签/禁用词。完整回答见 `output/freshretail_agent_test_cases.csv` 与 `output/freshretail_agent_report.md`。

### 参数（`config/freshretail_inventory.yaml`）

所有业务参数集中在配置文件，代码只读配置；队友 1 的正式参数表 **2026-10-30** 到位后，**只改此文件重跑** `app/freshretail_inventory.py`，不改代码。

| 参数 | 当前值 | 说明 |
| --- | ---: | --- |
| `shelf_life.options_days` | [3, 5, 7] | 短保 / 中标 / 长保三档敏感性 |
| `shelf_life.base_days` | 5 | 补货快照使用的档位 |
| `replenishment.lead_time_days` | 1 | 前置仓日配，次日到货 |
| `replenishment.service_level` | 0.90 | 目标服务水平，代码用正态分位数反算 z≈1.2816 |
| `replenishment.target_cover_days` | 7 | 目标水位覆盖天数 |
| `replenishment.expiry_cap_multiplier` | 1.0 | 效期约束：到货后库存 ≤ 保质期内可售量 × 倍数；由下方扫描规则选出 |
| `replenishment.cap_sweep_multipliers` | [0.70, 0.80, 0.90, 1.00, 1.10] | 效期倍数扫描候选（仅 C） |
| `replenishment.auto_select_cap_multiplier` | true | 用扫描规则自动覆盖 multiplier，便于复算 |
| `replenishment.min_order_qty` | 1 | 最小订货量（件） |
| `replenishment.order_pack_size` | 1 | 包装倍数（件） |
| `costs.stockout_penalty_per_unit` | 5.0 | 缺货惩罚（归一化金额/件） |
| `costs.penalty_sensitivity` | [5.0, 20.0] | 问题 4.3 的惩罚敏感性 |
| `costs.spoilage_cost_rate` | 1.0 | 报损成本率（全损） |
| `costs.holding_cost_rate_per_day` | 0.02 | 日持有成本率 |
| `initial_inventory.cover_days` | 2 | 初始库存覆盖 2 天 P50 需求 |
| `history_windows.ma_days` / `ma_multiplier` | 7 / 1.2 | 策略 A 的 MA 与订货倍数 |
| `history_windows.sigma_days` | 14 | 策略 B 的 σ 窗口 |

### 库存仿真结论（数字均来自上述 csv）

| 保质期 | A 总成本 | B 总成本 | C 总成本 | 推荐 | 理由 |
| ---: | ---: | ---: | ---: | --- | --- |
| 3 天（短保） | 1,204.5 | 3,281.1 | **980.2** | **C** | C 相对 B 报损 32.04% → 8.38%（−73.86%），满足率几乎持平；C 比 A 满足率高 2.64pp |
| 5 天（中保） | 1,131.1 | 1,422.3 | **917.5** | C（但依赖报损成本） | C 比 A 低 213.6，但 A 报损率仅 0.11%；报损单位成本升到约 1.55× 时 A 反超 |
| 7 天（长保） | 1,128.1 | **527.1** | 557.6 | **B** | 两策略报损都≈0，C 的效期约束没有报损收益，B 满足率略高、成本更低 |

- **第一轮验收修正说明**：第一轮曾用 A 的报损率作 C 的报损基线。复核后，A 的低报损来自系统性欠货（短保档 214 件未满足、缺货成本 1,070），不宜作为报损标杆；第二轮改为 **C vs B 比报损、C vs A 比总成本与满足率**。
- **默认 multiplier = 1.00**：扫描 0.70~1.10 后，各档规则选中 `{3: 1.0, 5: 0.9, 7: 1.0}`，三档取众数得 1.00。扫描显示倍数收紧会降低报损、抬高缺货，总成本先降后升。
- **成本阈值**：短保档若单件报损成本升到 **1.48×归一化单位成本**（按单位成本 1.0 计约 1.48 元/件；若真实单位成本为 P 元/件，阈值约 1.48P 元/件），A 反而优于 C；若缺货惩罚降到 **3.54/件** 以下，A 也优于 C。结论保持克制：C 不是全面最优。
- **企业建议**：短保品类用效期感知补货（C）；中保按真实报损成本在 A/C 间选；长保用传统 ROP（B）即可，不必上复杂策略。

### 边界与局限

- 仿真窗口仅 7 天（2024-06-26 ~ 2024-07-02，1,400 条序列-日），是真实留出期的 out-of-sample 结果，但无法覆盖长周期季节波动。
- `y_actual` 是缺货截断后的观测销量，不是真实需求；仿真满足率会被高估，只能做策略间相对比较。
- 数据没有真实库存水位，初始库存、每日到货、销售扣减、报损全部由仿真器维护；初始批次按“刚到货、完整保质期”处理。
- 保质期按 3/5/7 天三档假设，不能替代品类主数据；正式参数表到位后只改 config。
- 目标水位在预测窗口末端会被截断（预测只有 7 天），窗口后段的目标按可用天数缩短，末日不再下单。
- 成本是归一化值，不是人民币；缺货惩罚与持有成本只用于策略排序。

## 需求预测（周 × Top10 SKU）

> **保底方案（DataCo 版），已存档 `4e34518`。** 以下从本节到“Streamlit 演示看板”章节均为 DataCo 制造/订单线，保留用于回退；主线结论见上一节“生鲜线（freshretail）”。

按顺序运行基线与 LightGBM：

```powershell
.\.venv\Scripts\python.exe app\forecast_baseline.py
.\.venv\Scripts\python.exe app\forecast_lightgbm.py
```

- 数据：`data/clean_main.parquet`，质量过滤后 164,213 行；142 个完整周，训练 130 周，验证 12 周（2017-07-03 ~ 2017-09-24）。
- 基线：MA4、MA8、季节朴素（52 周）、朴素（上周）。
- LightGBM：全局面板模型（Top10 SKU 一起训练），滞后/滚动/日历特征，验证期逐周滚动重训。
- 评估：MAE、WMAPE，覆盖 Top10 合计、全部 SKU-周、单 SKU 三个层级。
- 输出：`output/forecast_baseline_*`、`output/forecast_comparison_*`、`output/forecast_lightgbm*`，模型在 `models/lgbm_top10_weekly.txt`。

### 上一轮对比结论（验证期 2017-07-03 ~ 2017-09-24）

> 本节是加入组合预测与累计需求之前的五模型结果；最新结论见下方「预测模块结论」。

| 评估范围 | 指标 | MA4 | MA8 | 季节朴素(52周) | 朴素(上周) | LightGBM |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| Top10 全部 SKU-周 | WMAPE | 8.59% | **8.55%** | 11.09% | 10.37% | 9.11% |
| Top10 全部 SKU-周 | MAE | 19.71 | **19.64** | 25.47 | 23.80 | 20.90 |
| Top10 合计（按周） | WMAPE | **3.56%** | 4.81% | 5.50% | 4.12% | 3.73% |
| Top10 合计（按周） | MAE | **81.69** | 110.33 | 126.17 | 94.50 | 85.60 |

- **SKU 级整体最优：MA8**（WMAPE 8.55%，MAE 19.64）；**Top10 合计最优：MA4**（WMAPE 3.56%，MAE 81.69）。聚合后各 SKU 误差相互抵消，合计层面误差明显低于单 SKU 层面。
- **LightGBM 未超过简单基线**：全部 SKU-周 WMAPE 9.11%，Top10 合计 3.73%，与 MA4 接近但略高。分 SKU 看，LightGBM 在 3 个 SKU（1014、1073、957）上取得最优，说明模型并非无效；只是在仅 130 周训练数据、需求平稳且高度集中的场景下，移动平均的偏差-方差权衡更好。
- **SKU 116 在验证期实际需求为 0**，WMAPE 无定义（表中记为“—”），只能参考 MAE，不能据此判断模型优劣。
- 结论：当前数据上 **MA4/MA8 是更稳的基准**；后续若要提升，应优先增加多步预测、外生变量（促销/价格/库存）或按 SKU 选择模型，而不是继续堆叠复杂模型。

> 说明：以上均为“最后 12 个完整周”的滚动一步预测；2017-10 之后的断层期不参与训练与评估。

## 预测模块结论

**结论速览**

- **周度预测要分口径看**：**SKU-周口径推荐组合（MA4 + MA8 + LightGBM），WMAPE 8.41%**（MA4 8.59%、LightGBM 8.94%）；**Top10 合计（按周）口径推荐 LightGBM，WMAPE 3.53%**（MA4 3.56%、组合 3.70%）。两者不矛盾：组合的价值在 SKU 级降低单模型偏差，到了总量层面各 SKU 的误差已经相互抵消，再平均只会把 LightGBM 已经捕捉到的总量信号钝化。
- **备货最终方案：未来 H 周累计需求分位数模型（默认 H=4）**。P50 作中心计划量，P90 作备货上限/安全库存参考，P95 用于高服务水平场景。
- **累积分位数已加入保形校准（conformal）**：逐起点、只用“目标窗口已实现”的历史残差修正，校准窗口 8 个可用起点（约 2 个月、80 条残差）。校准后留出集 P50 覆盖率 39.2% → **47.5%**，52 周回测 P90 86.3% → **87.9%**。
- **留出集 P90/P95 仍未达验收线**：P90 77.5% → 78.3%（目标 88%~92%），P95 85.0%（目标 90%~96%）。原因不是校准算法，而是 2017-06-05、06-12 两个起点在因果信息下不可预测——两周异常低点之后立刻回到常态水平，历史中没有同类台阶，而保形校准必须等 4 周目标窗口实现后才能更新。详见下方“方案 A：保形校准”与“验收线逐条对照”。
- **累计需求 P50 精度**：校准后留出集 WMAPE 7.19%（校准前 6.92%，MA4×4 基准 6.98%），52 周回测 6.62%（校准前 6.51%，MA4×4 6.59%）。校准优先保证覆盖率，牺牲了少量点精度，这个取舍需要在使用时知情。
- **不采用按 SKU 滚动选优**：验证期 WMAPE 10.12%，12 周小样本下选择噪声过大。
- **不采用“用季节基线做强锚定”**：它能把留出集覆盖率凑到验收线内，但会把留出集之前的 WMAPE 从 6.38% 推到 7.58%，属于对 6 月的过拟合，已明确放弃（见“失败过的尝试”）。

本轮把预测模块从“单一周度 LightGBM”升级为“池化 + 组合 + 累计需求分位数”。运行命令：

```powershell
.\.venv\Scripts\python.exe app\forecast_ensemble.py     # 第一步：池化、组合、按 SKU 滚动选优
.\.venv\Scripts\python.exe app\forecast_cumulative.py   # 第二步：未来 H 周累计需求 + P50/P90/P95
```

### 一、周度预测：池化 LightGBM + 简单平均组合

- **池化训练**：Top-10 SKU 放进同一个 LightGBM（34 个特征），SKU 编码、品类、价格、价格档位作为特征；验证期每个验证周重新训练，只用该周之前的真实数据。
- **组合**：`组合 = (MA4 + MA8 + LightGBM) / 3`。
- **按 SKU 滚动选优**：每个 SKU 在每个验证周只用此前验证周的误差挑模型，首周默认 MA4，避免用未来信息。
- 验证集与上一轮完全一致：最后 12 个完整周（2017-07-03 ~ 2017-09-24），120 个 SKU-周样本。

| 评估范围 | MA4 | MA8 | 朴素 | 季节朴素 | LightGBM | 组合 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Top10 全部 SKU-周 | 8.59% / 19.71 | 8.55% / 19.64 | 10.37% / 23.80 | 11.09% / 25.47 | 8.94% / 20.51 | **8.41% / 19.31** |
| Top10 合计（按周） | 3.56% / 81.69 | 4.81% / 110.33 | 4.12% / 94.50 | 5.50% / 126.17 | **3.53% / 81.02** | 3.70% / 84.85 |

> 每格为 **WMAPE / MAE**。WMAPE 越低越好；SKU 116 验证期实际需求接近 0，WMAPE 无定义（记为“—”）。

**口径说明：SKU-周推荐组合，合计推荐 LightGBM。**

| 口径 | 推荐模型 | WMAPE | 适用场景 |
| --- | --- | ---: | --- |
| Top10 全部 SKU-周 | **组合（MA4+MA8+LightGBM）** | **8.41%** | 分 SKU 排产、补货、单 SKU 计划 |
| Top10 合计（按周） | **LightGBM** | **3.53%** | 只关心总量、产能/资金总量测算 |

- 合计口径下 MA4 为 3.56%、组合为 3.70%，三者差距只有 0.17pp，但排序与 SKU-周口径相反。
- 原因是**组合在总量层面被平均钝化**：MA4、MA8 在 SKU 级各有偏差，但按周加总后偏差方向不同、相互抵消，合计曲线本身已经很平滑；再把三条平滑曲线平均一次，等于对总量做了额外平滑，反而削弱了 LightGBM 对总量拐点的响应。SKU 级则相反，单模型偏差不会被抵消，平均能降低方差。
- 因此不要只报一个数：**向业务交付时，SKU 级排产用组合，总量测算用 LightGBM**；两个口径的模型选择分别固化在评估脚本里。

**结论：**

- **组合在 SKU-周层面最优**：WMAPE 8.41%、MAE 19.31，较 MA4（8.59%）相对改善约 2.1%，也优于单独 LightGBM（8.94%）。组合的主要价值是降低单模型的偏差，而不是追求某个 SKU 的极限精度。
- **合计层面 LightGBM 最优**（WMAPE 3.53%，MA4 3.56%，组合 3.70%）；聚合后各 SKU 误差相互抵消，三个模型差距很小。
- **按 SKU 滚动选优不可取**：WMAPE 10.12%、MAE 23.23，明显差于组合。原因是验证期只有 12 周，前几周的选择样本太少，模型选择本身噪声很大（10 个 SKU 中有 8 个大部分时间退回 MA4/季节朴素）。因此**不采用滚动选优**。
- 输出：`output/forecast_ensemble_predictions.csv`、`output/forecast_ensemble_metrics_wide.csv`、`output/forecast_selection_by_sku.csv`；同时刷新对比表 `output/forecast_comparison_metrics_wide.csv`。

### 二、未来 H 周累计需求与分位数（H=4，可配置）

**目标定义**：站在第 t 周末，预测未来 H 周累计需求 `target[t] = sum(qty[t+1 .. t+H])`。H 由 `config.yaml` 的 `forecast.horizon_weeks` 控制，默认 4；企业按采购提前期/补货周期调整即可，无需改代码。

**模型**：

- 池化 LightGBM，`objective="quantile"`，分别训练 P50 / P90 / P95 三条线；Top-10 SKU 一起训练。
- 先学习“累计需求 / (H × 近 4 周均值)”的倍数关系，预测时再乘回基准，以缓解不同 SKU 的量纲差异和水平漂移（`normalize_by_recent_level: true`，可关闭）。
- 每个起点只使用“目标窗口已经实现”的历史样本训练（要求 `u + H ≤ t`），避免未来信息泄漏；分位数做了不交叉处理（P50 ≤ P90 ≤ P95）。
- **保形校准**（`conformal_calibration: true`）：对每个起点，用最近 8 个“目标窗口已实现”的历史起点残差（每个起点 Top-10 SKU 各一条，共 80 条）估计 P50/P90/P95 的偏移量；采用有限样本分位 `ceil((n+1)q)/n`，且只向上修正（本轮诊断为系统性低估，没有低估证据时不压低分位线）。偏移量逐起点记录在 `output/forecast_conformal_offsets.csv`。

**验证口径**：

- **留出集**：最近 12 个可完整观测 4 周未来的起点（2017-06-05 ~ 2017-08-21），预测窗口最远覆盖到 2017-09-17，120 个 SKU-起点。
- **滚动回测**：最近 52 个起点（2016-08-29 ~ 2017-08-21），520 个 SKU-起点，用于更稳地检验分位数校准。

| 评估范围 | n | P50 WMAPE（前→后） | P50 覆盖（前→后） | P90 覆盖（前→后） | P95 覆盖（前→后） | MA4×4 WMAPE |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 留出集（12 个起点） | 120 | 6.92% → **7.19%** | 39.2% → **47.5%** | 77.5% → 78.3% | 85.0% → 85.0% | 6.98% |
| 滚动回测（52 个起点） | 520 | 6.51% → 6.62% | 50.8% → **53.8%** | 86.3% → **87.9%** | 92.7% → **93.7%** | 6.59% |

> 覆盖率 = 真实累计需求 ≤ 分位线的比例；P50 目标 50%，P90 目标 90%，P95 目标 95%。“前”为原始分位线，“后”为保形校准后。完整逐项对照见 `output/forecast_coverage_summary.csv`（保留 `coverage_before` / `coverage` 两列）。

#### 1. 水平漂移的量化

以 2017-06 的台阶为切点，统计 Top10 合计的周需求均值：

| 切点 | 前 8 周均值 | 后 8 周均值 | 变化 |
| --- | ---: | ---: | ---: |
| 2017-06-05 | 2,023.1 | 2,184.2 | +8.0% |
| 2017-06-12 | 2,013.6 | 2,241.0 | +11.3% |
| **2017-06-19（实际台阶周）** | **1,982.1** | **2,299.0** | **+16.0%** |

- 周级看更陡：2017-06-05、06-12 分别为 1,829、1,811 件，06-19 直接跳到 2,329 件，**单周 +28.7%**，之后 8 周稳定在 2,200~2,430 件。
- 这个台阶持续了整个留出集（≥8 周），所以校准窗口不能太长：52 周窗口里台阶只占 8/52≈15%，会被稀释到几乎不起作用；本轮取 8 个可用起点（约 2 个月、80 条残差），既覆盖台阶后的新水平，又有足够样本估计分位数。

#### 2. 方案 A：保形校准（已采用）

**做法**：对每个起点 t，只取 `u + H ≤ t` 的历史起点残差 `actual - pred`，按分位数取偏移量加到当前预测上：

- P50 用残差中位数（q=0.50），P90/P95 分别用 q=0.90/0.95 的残差分位；
- 有限样本修正 `ceil((n+1)q)/n`；
- `conformal_positive_only: true`，只向上修正；
- 窗口 8 个可用起点，`conformal_min_origins: 4`；最早 4 个回测起点因不足最小样本不校准；
- 参数已写入 `config.yaml`，每个起点的实际校准窗口（`calib_start` / `calib_end` / 残差条数）记录在 `output/forecast_conformal_offsets.csv`。

**结果**：

- 留出集 P50 覆盖率 39.2% → **47.5%**，进入 45%~55% 验收区间；回测 P50 50.8% → 53.8%。
- 回测 P90 86.3% → **87.9%**、P95 92.7% → **93.7%**，常规经营期校准更稳。
- 留出集 P90 仅 77.5% → 78.3%、P95 维持 85.0%，**未达验收线**。
- 代价：留出集 P50 WMAPE 6.92% → 7.19%（+0.27pp），P90/P95 的 pinball loss 由 20.60/15.07 升到 24.88/18.74。原因是 7 月起校准窗口纳入 6 月的大残差，分位线被抬高，而 7-8 月本身已经校准良好，属于“为了覆盖率牺牲锐度”的取舍。业务上如果更看重区间窄，可把 `conformal_window_origins` 调大，但 P50 覆盖率会回落。

**为什么留出集 P90/P95 补不上**：

- 未通过的缺口全部在 2017-06-05 / 06-12 两个起点。它们各自的未来 4 周里包含“两周异常低点 → 立刻回到常态”的台阶；在 t 时刻，模型只能看到 6 月第一周的数据，历史中没有同类形态，因果可得的信息无法预判。
- 保形校准本身也受 H 周滞后约束：起点 t 只能用 `u+H ≤ t` 的残差。06-05 的校准历史截止 05-08，06-12 截止 05-15，都是台阶前数据，偏移量为 0（见 `output/forecast_conformal_offsets.csv`）；到 07-03 才第一次拿到 6 月的残差，此时 7-8 月已经被原始分位线覆盖到 100%。
- 因此这是**信息约束**，不是校准公式没调好。继续在不看未来的前提下调参，只会把 7-8 月抬得更高（已经 100%），无法把 6 月抬上去。

#### 3. 方案 B：补水平/趋势特征（已尝试，未采用）

按规定加入三类特征后重训分位数模型（只跑 12 个留出起点做快速比较）：

| 特征集 | P50 / P90 / P95 覆盖 | P50 WMAPE | 结论 |
| --- | --- | ---: | --- |
| 原特征（39 个） | 39.2% / 77.5% / 85.0% | 6.92% | 基准 |
| + 周序号、滚动 26 周均值、距训练末端周数 | 39.2% / 77.5% / **82.5%** | 7.02% | P95 变差，无改善 |
| 再加动量、季节比率、短/中期水平比 | **21.7%** / 62.5% / 76.7% | 8.63% | 明显变差 |
| 关闭近 4 周归一化、直接预测累计值 | 24.2% / 70.8% / 81.7% | 8.42% | 6 月前两周略好，整体更差 |

- 直接预测累计值确实让 06-05 的 P90 从 30% 升到 70%（季节水平信号被保留），但其他月份大幅退化，净效果为负。**方案 B 不采用，模型保持原特征。**

#### 4. 方案 C：walk-forward 滚动重训（本来就是这样）

- 现有实现**已经是 walk-forward**：`forecast_cumulative.py` 对 52 个回测起点逐个重训（每个起点只用 `u+H ≤ t` 的样本），不存在“固定训练集”。
- 运行成本实测：**累计模块 26.6 秒**（52 起点 × 3 个分位数 = 156 次 LightGBM 训练），周度组合模块 20.1 秒；演示时一分钟内可以完整跑完，不需要额外做性能优化。
- 因此方案 C 没有额外的改造空间；真正的瓶颈是 6 月的不可预测性，而不是重训频率。

#### 5. 失败过的尝试（留档，避免重复踩坑）

| 尝试 | 留出集 P50/P90/P95 | 为什么放弃 |
| --- | --- | --- |
| 静态校准，只用留出集之前 40 个起点 | 29.2% / 77.5% / 85.0% | 台阶前残差太小，P50 反而变差 |
| 静态校准，用全部 52 个起点 | 39.2% / 81.7% / 88.3% | 校准集包含留出集，存在泄漏；且仍未达 P90 目标 |
| 非因果 walk-forward（允许用未实现窗口的起点） | 51.7% / 87.5% / 91.7% | 看起来接近验收线，但用了未来 H 周的残差，属于泄漏，不能交付 |
| 按 SKU 校准 + 向全局收缩（λ=0.5） | 42.5% / 78.3% / 83.3% | 单 SKU 残差样本少、噪声大，反而更差 |
| 乘法偏移而非加法 | 51.7% / 78.3% / 85.0% | 与加法接近，但 WMAPE 更差（7.66% vs 7.19%） |
| 用 52 周季节基线做合计层锚定（w=0.5） | **50.8% / 90.8% / 94.2%** | 三项都进验收线，但留出集前 40 起点的 WMAPE 从 6.38% 恶化到 7.58%，覆盖率从 54.2/89.0/95.0 变成 70.5/95.5/98.0——是对 2017-06 的过拟合，不能作为通用校准 |
| 完全锚定季节基线（w=1.0） | 62.5% / 96.7% / 99.2% | 同上，留出前 WMAPE 10.33%，严重过拟合 |

> 结论：任何“能把留出集 P90 凑进 88%~92%”的方案，要么用了未来信息，要么严重牺牲此前区间的精度。按纪律停在两轮，不再继续调参。

#### 6. 验收线逐条对照

| 验收项 | 目标 | 修正前 | 修正后 | 结果 |
| --- | --- | ---: | ---: | --- |
| 留出集 P90 覆盖率 | 88%~92% | 77.5% | 78.3% | ❌ 未通过 |
| 留出集 P95 覆盖率 | 90%~96% | 85.0% | 85.0% | ❌ 未通过 |
| 留出集 P50 覆盖率 | 45%~55% | 39.2% | **47.5%** | ✅ 通过 |
| 滚动回测 P90 覆盖率 | ≥86% | 86.3% | **87.9%** | ✅ 通过 |
| 周度组合 WMAPE（SKU-周） | ≤8.61% | 8.41% | 8.41% | ✅ 通过（未改动） |

- 未通过的两项已定位为 2017-06-05 / 06-12 的信息约束，不继续调参；业务上若必须覆盖这两周，只能在季节切换期人工加宽缓冲（例如按历史台阶幅度 +15%~+30% 临时上浮 P90/P95），并把这部分作为显式的人工假设记录，而不是伪装成模型校准。
- 每个 SKU 的覆盖率：回测 P90 在 75.0%~98.1% 之间；留出集 P90 最低为 SKU 191/365/627（66.7%）。完整表见 `output/forecast_coverage_by_sku.csv`。SKU 116 接近零需求，建议单独按慢动销品管理。

- 输出：`output/forecast_cumulative_predictions.csv`（留出集，含 `*_raw` 校准前分位线）、`output/forecast_cumulative_backtest_predictions.csv`（回测）、`output/forecast_cumulative_metrics.csv`、`output/forecast_coverage_summary.csv`（修正前后两列）、`output/forecast_coverage_by_sku.csv`、`output/forecast_coverage_by_origin.csv`、`output/forecast_conformal_offsets.csv`（逐起点校准窗口与偏移量）、`output/forecast_cumulative.png`。

### 三、最终采用的方案

1. **周度短期跟踪（SKU 级）**：采用**池化 LightGBM + 简单平均组合**（MA4+MA8+LightGBM），12 周验证集 WMAPE 8.41%、MAE 19.31。**总量测算单独看 LightGBM**（合计口径 3.53%，组合 3.70%），不要拿组合的总量数去和 LightGBM 比。
2. **备货/补货决策**：采用**未来 H 周累计需求的池化分位数模型 + 保形校准**。默认 H=4；P50 作为中心计划量，P90 作为备货上限/安全库存参考，P95 用于高服务水平场景。企业改 `config.yaml` 的 `horizon_weeks` 即可切换为 2 周、6 周等；`conformal_*` 参数控制校准窗口与是否只向上修正。
3. **不采用**：按 SKU 滚动选优（10.12%，小样本选择噪声大）；单独 LightGBM 作为周度 SKU 级最优模型（8.94%，不如组合）；用季节基线强锚定累计分位数（会把留出前 WMAPE 从 6.38% 推到 7.58%，属于对 6 月的过拟合）。
4. **覆盖率现状与风险提示**：校准后留出集 P50 已达标（47.5%），回测 P90 达标（87.9%）；**留出集 P90/P95 仍为 78.3%/85.0%，未达验收线**。根因是 2017-06 的台阶在 06-05/06-12 两个起点不可预测，且校准受 H 周实现滞后约束。业务落地时：
   - 常规经营期直接使用校准后的分位线；
   - 季节切换/需求台阶期，按历史台阶幅度（本轮 +16.0%，极端周 +28.7%）对 P90/P95 做**显式的人工加宽**，并在计划单上注明这是人工假设而非模型输出；
   - 优先补充促销、价格、库存等外生变量，这是从根上解决台阶期覆盖率的路径；
   - SKU 116 等接近零需求的 SKU 单独归类，不进入常规补货模型。

## 库存模块（库存优化）

### 本轮最终结论（2026-10-25）

> **一句话结论**：C 用比 B 低 **14.7%** 的平均库存、低 **5.3%** 的总成本，达到与 B 持平的满足率；A 在默认缺货惩罚下总成本最低，但靠 4,287 件缺货换来的；周转口径与四档紧急度已修正，数字可直接用于答辩。

- **补货建议与三策略仿真已落地**：`app/inventory_policy.py` 读取累积分位数预测，按 SKU 输出订货量、SS、ROP、紧急度和中文理由；并用 2017-01 ~ 2017-09 的历史需求做 walk-forward 仿真。运行约 **4~7 秒**。
- **原验收线两条都通过**：策略 C 满足率 **99.7866%** > A 94.7421% / B 99.7841%；平均库存 **351.7** ≤ 1.1×B（453.7）。本轮补充的四项修正验收（周转、四档自洽、10 行表、快照可追溯）也全部通过，见四·验收线。
- **但不能把 C 说成“显著优于 B”**：C 与 B 的满足率只差 **2 件**（99.7866% vs 99.7841%），实质是持平。C 的真实优势在库存和成本：平均库存比 B 低 **14.7%**、总成本低 **5.3%**、缺货 SKU-周少 1 周。
- **成本排序依赖缺货惩罚假设**：默认 `stockout_cost_per_unit=10` 时，A 的总成本 231,355 反而最低（缺货便宜、A 用低库存换 5pp 满足率）；把惩罚调到 50 元/件，C 总成本 **247,026** 低于 B 260,666 和 A 402,835。这条必须和数字一起报，不能只报一个参数下的结论。
- **漂移监测只能在突变后约 H 周响应**：2017-06 台阶到 7 月才被检出，06-05 ~ 06-26 的起点无法触发；它不会阻止突变当周的缺货，只能在事后标记“需重估”并把目标水位 P90→P95。消融显示本次回测满足率不变（都是 99.7866%），平均库存 351.7 vs 337.2（+4.3%）、总成本 240,066 vs 238,801（+0.5%）——它是工程补偿/保险，不是预测能力的提升。详见④。
- **周转口径已修正**：原 `turnover_annualized` 用 Top-10 合计 ÷ 单 SKU 平均库存，虚高约 **10 倍**；现改为单 SKU 口径，A/B/C = **143.61 / 26.99 / 31.66 次/年**（`turnover_period` 同步修正为 104.95 / 19.73 / 23.13）。分子用实际满足件数（缺货按丢失销售处理），不是 Top-10 合计需求。详见③。
- **四档紧急度与订货量严格自洽**：紧急 0、建议补货 3（365/502/1073）、计划补货 7、正常 0；10 行全部满足“非正常档订货量 > 0、正常档订货量 = 0”，脚本断言 **0 条违规**；最终 10 行表见二。
- **冷启动是本次满足率的主要限制**：2017-01-23 的 163 件缺货占 C 全部缺货的 **93.7%**，A/B/C 完全相同，不是策略差异；因此不要把剩余缺货归因于策略 C 的缺陷。详见三、四。

**答辩口径：可以说 / 不能说**

| 可以这样说（有数据支撑） | 不能这样说（容易被追问穿） |
| --- | --- |
| C 满足率 99.7866%，高于 A 94.7421%，与 B 99.7841% 基本持平 | C 的满足率显著高于 B（只多满足 **2 件**） |
| C 平均库存 351.7，比 B 412.5 低 **14.7%**；总成本 240,066，比 B 253,626 低 **5.3%** | C 在所有成本参数下都最优（默认缺货惩罚 10 元/件时 A 总成本 231,355 最低） |
| 缺货惩罚 50 元/件时，C 247,026 < B 260,666 < A 402,835 | A 一定最差（默认参数下 A 最低，但靠 4,287 件缺货） |
| 年化周转 A 143.61 / B 26.99 / C 31.66 次/年（单 SKU 口径） | 旧值 1436 / 270 / 317 次/年 |
| 四档紧急度与订货量严格自洽，当前 3 建议 + 7 计划，0 条违规 | “状态正常但建议补 401 件”（已修复） |
| 漂移监测能事后标记“需重估”并把目标 P90→P95 | 漂移监测能阻止突变当周缺货 |
| 2017-01-23 的 163 件冷启动缺货占 C 全部缺货 93.7% | 把冷启动缺货归因于策略 C 的缺陷 |

### 一、运行方法与参数

```powershell
# 主流程：补货建议 + 三策略仿真 + 漂移监测 + 图表
.\.venv\Scripts\python.exe app\inventory_policy.py

# 参数演示：改 config，不改代码；--tag 另存输出，不覆盖主结果
.\.venv\Scripts\python.exe app\inventory_policy.py --config output\_demo_service95.yaml --tag service95
.\.venv\Scripts\python.exe app\inventory_policy.py --config output\_demo_nodrift.yaml --tag nodrift
.\.venv\Scripts\python.exe app\inventory_policy.py --config output\_demo_stockout50.yaml --tag stockout50
```

| 参数 | 默认 | 含义 |
| --- | ---: | --- |
| `lead_time_weeks` | 4 | 采购提前期（周） |
| `service_level_target` | 0.90 | 目标服务水平；0.90 取 P90，0.95 取 P95，0.80~0.95 之间在 P50/P90/P95 间线性插值 |
| `review_period_weeks` | 1 | 补货检查周期（周） |
| `order_cost` | 500 | 单次订货固定成本（元/次） |
| `holding_cost_per_unit_week` | 0.5 | 单位周持有成本（元/件/周） |
| `stockout_cost_per_unit` | 10 | 单位缺货惩罚（元/件） |
| `moq` | 10 | 最小起订量（件） |
| `pack_size` | 1 | 包装倍数，订货量向上取整 |
| `initial_inventory_weeks` | 4 | 仿真初始库存 = 起点前 4 周平均需求 × 该值 |
| `drift_monitor_enabled` | true | 漂移监测开关 |
| `drift_monitor_window_weeks` | 4 | 回看的已实现起点数 |
| `drift_monitor_threshold_pct` | 0.05 | 连续同向偏差的平均幅度阈值 |
| `drift_monitor_consecutive_weeks` | 3 | 连续同向的最少周数 |
| `drift_escalate_quantile` | true | 检出低估漂移时目标水位 P90→P95 |

> 说明：项目的极简 YAML 解析器只支持两层键，因此 `drift_monitor` 组用 `drift_monitor_*` 平铺命名，语义与嵌套写法一致。

### 二、补货口径与手工核对

**公式**（所有数字都能从 `output/inventory_replenishment.csv` 追溯）：

```text
提前期需求 Pxx_LT = 未来 H 周累计 Pxx × (lead_time_weeks / H)
安全库存 SS       = P90_LT − P50_LT
再订货点 ROP      = P50_LT + SS = P90_LT
目标水位 S        = P90_cum × (lead_time_weeks + review_period_weeks) / H
建议订货量        = max(0, S − 可用库存)，向上取整到 pack_size，且不低于 MOQ
可用库存          = 现有库存 + 在途
```

- `lead_time_weeks > horizon_weeks` 时脚本会打印告警，并在补货表 `lead_time_extrapolated=True` 显式标记，不静默外推。已用 `lead_time_weeks=6` 实测：脚本逐个 SKU 打印“提前期需求按比例外推，已显式标记”。
- 紧急度四档，按顺序判断：`可用 < 0.5×ROP → 紧急`；`0.5×ROP ≤ 可用 < ROP → 建议补货`；`ROP ≤ 可用 < 目标水位 → 计划补货`；`可用 ≥ 目标水位 → 正常`。四档与建议订货量严格自洽：前三档 `gap > 0`、订货量 > 0；正常档 `gap = 0`、订货量必为 0。脚本内置一致性断言，出现“正常但有订货量”会直接报错并中止。

**手工核对：SKU 365 · Perfect Fitness Perfect Rip Deck（快照周 2017-08-21）**

```text
1) 未来 4 周累计预测：P50=2056.34，P90=2270.46，P95=2409.40
2) lead_time=4，horizon=4，折算系数=1.0000
   提前期需求：P50_LT=2056.34，P90_LT=2270.46，P95_LT=2409.40
3) SS = P90_LT − P50_LT = 2270.46 − 2056.34 = 214.12
4) ROP = P50_LT + SS = 2056.34 + 214.12 = 2270.46（等于 P90_LT）
   目标水位 S = 2270.46 × (4+1)/4 = 2838.08
5) 可用库存 = 现有 1141.00 + 在途 1090.00 = 2231.00
6) 缺口 = max(0, 2838.08 − 2231.00) = 607.08
7) 建议订货量 = 向上取整到 pack=1、不低于 MOQ=10 → 608
8) 紧急度（按顺序）：可用 2231.00 vs 0.5×ROP 1135.23 / ROP 2270.46 / 目标 2838.08 → 建议补货
```

#### 本轮最终补货表（快照周 2017-08-21，策略 C）

排序：紧急 > 建议补货 > 计划补货 > 正常；当前快照无紧急、无正常。所有数字直接来自 `output/inventory_replenishment.csv`，未在文档中重算。

| SKU | 名称 | 可用库存 | SS | ROP | 目标水位 | 建议订货 | 紧急度 | 漂移 |
| ---: | --- | ---: | ---: | ---: | ---: | ---: | --- | --- |
| 365 | Perfect Fitness Perfect Rip Deck | 2,231.00 | 214.12 | 2,270.46 | 2,838.08 | 608 | 建议补货 | 正常 |
| 502 | Nike Men's Dri-FIT Victory Golf Polo | 2,032.00 | 242.01 | 2,043.62 | 2,554.53 | 523 | 建议补货 | 正常 |
| 1073 | Pelican Sunstream 100 Kayak | 564.00 | 108.23 | 602.98 | 753.73 | 190 | 建议补货 | 正常 |
| 1014 | O'Brien Men's Neoprene Life Vest | 1,793.00 | 219.74 | 1,754.43 | 2,193.04 | 401 | 计划补货 | 正常 |
| 1004 | Field & Stream Sportsman 16 Gun Fire Safe | 656.00 | 106.29 | 652.66 | 815.82 | 160 | 计划补货 | 正常 |
| 627 | Under Armour Girls' Toddler Spine Surge Runni | 1,169.00 | 135.87 | 1,023.96 | 1,279.95 | 111 | 计划补货 | 正常 |
| 957 | Diamondback Women's Serene Classic Comfort Bi | 535.00 | 99.32 | 504.02 | 630.03 | 96 | 计划补货 | 正常 |
| 191 | Nike Men's Free 5.0+ Running Shoe | 1,410.00 | 142.94 | 1,174.74 | 1,468.42 | 59 | 计划补货 | 正常 |
| 403 | Nike Men's CJ Elite 2 TD Football Cleat | 950.00 | 115.64 | 795.91 | 994.88 | 45 | 计划补货 | 正常 |
| 116 | Nike Men's Comfort 2 Slide | 89.00 | 53.72 | 78.16 | 97.69 | 10 | 计划补货 | 正常 |

> 完整性：10 行；紧急 0 / 建议补货 3 / 计划补货 7 / 正常 0。非正常档订货量均 > 0，正常档订货量必为 0；脚本断言 0 条违规。

### 三、仿真假设（数据集没有真实库存字段）

- **用历史需求 + 模拟库存做 walk-forward**，不代表真实仓库执行结果。仿真期 2017-01-02 ~ 2017-09-18，38 周 × Top-10 SKU。
- **按 SKU 的周需求**由 `data/clean_main.parquet` 经 `build_panel` 重建；`output/demand_weekly.csv` 是全量合计、不含 SKU 维度，仅用于口径校验：仿真期全量合计 89,958 件，Top-10 面板 81,535 件（占 90.6%）。
- **缺货按丢失销售处理**，不补单；满足率 = 总满足件数 / 总需求件数；缺货周 = 该 SKU-周缺货件数 > 0。
- **同一初始库存**：仿真起点前 4 周平均需求 × `initial_inventory_weeks`，三种策略完全一致；仿真开始时无在途。
- 每周顺序：到货 → 满足本周需求 → 周期检查下单（下单后 `lead_time_weeks` 周到货）。
- **策略 A（经验规则）**：补到 4 周平均销量（`initial_inventory_weeks × MA4`）。按字面“每周固定订 4 周销量”会让库存无界增长，不作为可比基准；这里按“补到 4 周水平”实现。
- **策略 B（MA4 + 固定安全系数）**：目标水位 = `1.2 × MA4 × (lead_time + review)`。
- **策略 C（本方案）**：提前期需求分位数 → SS → ROP → 目标水位；周期检查下用“可用 − 检查期 P50 需求 < ROP”触发（保护期 L+R），补到目标水位；检出低估漂移时目标分位 P90→P95。
- 2017-08-21 之后的 4 个仿真周沿用 08-21 的预测（`forecast_cumulative_backtest_predictions.csv` 的最后一个起点），状态表 `forecast_carried_forward=True`。
- 2017-01-23 的 163 件缺货是冷启动（初始库存 4 周均值、首批订单 4 周后才到），A/B/C 完全相同，不是策略差异；它占 C 全部缺货的 93.7%。

### 四、仿真结果与最终结论

#### ① 三策略对比表

| 策略 | 满足率 | 平均库存 | 年化周转（次/年） | 总成本 | 缺货 SKU-周 | 缺货自然周 | 订单数 | 缺货件数 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| A 经验规则（补到 4 周 MA4） | 94.7421% | 73.6 | 143.61 | 231,355 | 112 | 34 | 349 | 4,287 |
| B MA4 + 1.2 安全系数 | 99.7841% | 412.5 | 26.99 | 253,626 | 5 | 3 | 347 | 176 |
| **C 分位数 ROP + SS + 漂移** | **99.7866%** | **351.7** | **31.66** | **240,066** | **4** | **2** | **343** | **174** |

> “缺货 SKU-周” = 所有 SKU 的缺货周累计；“缺货自然周” = 至少一个 SKU 缺货的日历周数。年化周转为单 SKU 口径：`(total_served / n_skus) / avg_inventory × 52 / n_weeks`，口径与修正过程见③。

**成本拆解**

| 策略 | 订货成本 | 持有成本 | 缺货成本 | 总成本 |
| --- | ---: | ---: | ---: | ---: |
| A | 174,500 | 13,985 | 42,870 | 231,355 |
| B | 173,500 | 78,366 | 1,760 | 253,626 |
| C | 171,500 | 66,826 | 1,740 | 240,066 |

#### ② 三条必须一起报的边界

1. **C 对 B 的满足率只是持平，不是显著更好**：C 99.7866% vs B 99.7841%，81,535 件总需求里只多满足 **2 件**（174 vs 176 件缺货）。任何“C 的满足率明显高于 B”的说法都不成立。
2. **C 的真实优势是库存和成本**：平均库存 351.7 vs B 412.5（**低 14.7%**），总成本 240,066 vs B 253,626（**低 5.3%**），缺货自然周 2 vs 3。C 用更少的库存和更低的成本达到了与 B 相同的服务水平。
3. **A 的总成本最低，但这是靠欠货省出来的**：默认 `stockout_cost_per_unit=10` 时，A 总成本 231,355 最低；代价是满足率只有 94.7421%、缺货 4,287 件、112 个缺货 SKU-周（34 个自然周）。A 的持有成本只有 13,985（C 66,826），但缺货成本 42,870（C 1,740）——它把成本从库存转移到了缺货。把惩罚调到 50 元/件，排序立刻反转：

| `stockout_cost_per_unit` | A 总成本 | B 总成本 | C 总成本 | 成本排序 |
| ---: | ---: | ---: | ---: | --- |
| 10（默认） | 231,355 | 253,626 | 240,066 | A < C < B |
| 50 | 402,835 | 260,666 | 247,026 | **C < B < A** |

**缺货惩罚越高，A 的“低库存”越不划算；C 在两种惩罚下都优于 B。** 成本结论必须带这个敏感性，不能只报默认参数下的一个数。

> 补充：满足率被 2017-01-23 的冷启动主导（163/174 = 93.7%），本次回测无法充分体现 C 在 6 月台阶期的相对优势；这是仿真窗口/初始库存假设的限制，不是策略 C 的缺陷。

#### ③ 周转口径修正（已修复）

- **原问题**：旧公式为 `turnover = total_served / avg_inventory`。`total_served` 是全部 SKU 的总满足件数，`avg_inventory` 却是**单 SKU-周的平均库存**（C 策略 351.7 件/SKU）；分子是 Top-10 合计、分母是单 SKU，量纲不一致，结果虚高约 **10 倍**，A/B/C 分别得到 1436 / 270 / 317 次/年。
- **修正后口径**（单 SKU 对单 SKU）：

```text
平均单 SKU 已满足件数 = total_served / n_skus
平均单 SKU 库存       = avg_inventory（状态表按 SKU-周平铺，均值即单 SKU 周平均库存）
turnover_period       = (total_served / n_skus) / avg_inventory
turnover_annualized   = turnover_period × (52 / n_weeks)
```

- **修正结果**：

| 策略 | turnover_period（38 周） | turnover_annualized（次/年） |
| --- | ---: | ---: |
| A 经验规则 | 104.95 | **143.61** |
| B MA4×1.2 | 19.73 | **26.99** |
| C 分位数 ROP | 23.13 | **31.66** |

- **分子为什么用 `total_served` 而不是 `total_demand`**：库存周转的物理含义是“出库件数 / 平均库存”。本仿真把缺货按丢失销售处理，未满足的 174~4,287 件并没有出库，因此分子用实际满足件数。若改用 `total_demand`，结果为 A 151.58 / B 27.05 / C 31.72；B/C 几乎不变，A 因缺货多而高出约 8 次/年。代码与 summary 已注明当前采用 `total_served`；本节同时留档 `total_demand` 口径的对照值。
- **改动位置**：`app/inventory_policy.py` 的 `simulate_strategy()`；同时更新了模块 docstring 和 `output/inventory_policy_summary.txt` 的“假设与口径”。已重新生成 `inventory_policy_simulation.csv`、`inventory_policy_simulation_states.csv`、`inventory_simulation.png` 与 summary。
- **对外表述**：排序仍是 A 最高、C > B，但绝对倍数已回到物理合理区间（单 SKU 一年周转 27~144 次），可以直接用于答辩。

#### ④ 漂移监测的滞后边界

- 漂移监测只能用 `u + H ≤ t` 的已实现起点，因此**只能在突变被数据确认后约 H 周响应**。2017-06-19 的台阶，最早要到 07-03 ~ 07-17 才能被检出；06-05 ~ 06-26 的四个起点**无法触发**（它们能用的残差都在台阶之前）。
- 这意味着它**不能阻止突变当周的缺货**，只能在事后把该 SKU 标记为“需重估”、把目标水位 P90→P95，属于工程补偿/风险标记，不是预测能力的提升。
- 消融也印证了这一点：开启 vs 关闭，满足率都是 99.7866%，平均库存 351.7 vs 337.2（+4.3%）、总成本 240,066 vs 238,801（+0.5%）——它买到的是“持续低估时的保险”，不是本次回测的满足率。
- 要缩短滞后，只能引入前置信号（促销计划、渠道订单、价格/库存外生变量）或把监控频率提到周内；继续调同一个残差窗口无法突破信息约束。

#### 验收线

| 验收项 | 目标 | 实测 | 结果 |
| --- | --- | --- | --- |
| C 满足率高于 A、B | > A、B | C 99.7866% vs A 94.7421% / B 99.7841% | ✅ 通过（对 B 仅高 2 件，实质持平） |
| C 平均库存 ≤ 1.1×B | ≤453.7 | 351.7（85.3% of B） | ✅ 通过 |
| 手工核对全链路 | 四步算式可复现 | 见上文 SKU 365 | ✅ 通过 |
| 参数全部来自 config.yaml | 改参数不碰代码 | `--config` + `--tag` 已验证 | ✅ 通过 |
| 输出文件齐全 | 3 个主文件 | 见“输出文件” | ✅ 通过 |

**本轮修正验收（2026-10-25）**

| 验收项 | 目标 | 实测 | 结果 |
| --- | --- | --- | --- |
| 周转口径 | 单 SKU 对单 SKU | A 143.61 / B 26.99 / C 31.66 次/年；`turnover_period` 104.95 / 19.73 / 23.13 | ✅ 通过 |
| 四档紧急度与订货量自洽 | 正常档订货量 = 0；非正常档 > 0 | 0 条违规；紧急 0 / 建议补货 3 / 计划补货 7 / 正常 0 | ✅ 通过 |
| 最终补货表 | 10 行完整 | 见二·本轮最终补货表，10 行齐全 | ✅ 通过 |
| 快照数字可追溯 | 与 CSV 一致 | SKU 365 手工核对 + 10 行表逐项对照 | ✅ 通过 |

### 五、漂移监测（针对 2017-06 台阶）

> 滞后边界与消融结论已在④给出；本节只记录规则和逐周实测，避免重复。

**规则**：对每个 SKU，在每个起点 t 只用 `u + H ≤ t` 的已实现起点；取最近 `drift_monitor_window_weeks=4` 个，若最近连续 `drift_monitor_consecutive_weeks=3` 个起点的相对偏差 `(actual − p50)/p50` 同向、且平均幅度 ≥ `drift_monitor_threshold_pct=5%`，则标记“需重估”。**只对“实际持续高于预测”（低估）方向升级**，目标水位从 P90 升到 P95；高估方向不升级，避免无谓库存。

**实测**：

- 仿真 380 个 SKU-周中触发 74 个（19.5%），覆盖 23 个周。
- 6 月台阶**在 7 月被检出**：07-03 的 502/1004/1073 低估 11.9%/12.2%/5.4%，07-10 的 502/627/1004/1073 低估 15.0%/13.8%/14.3%/9.6%，07-17 有 8 个 SKU 触发（365 低估 19.4%、191 18.7%）。
- 06-05 ~ 06-26 的起点没有触发——它们能用的已实现残差都在台阶之前，这正好暴露了 H 周滞后：**监测只能在台阶被数据确认后反应，不能在台阶当下预知**。
- 快照周 2017-08-21 没有 SKU 触发（保形校准已跟上），因此补货表的“漂移状态”全部为“正常”；触发发生在仿真过程中的 7 月。

**消融（开启 vs 关闭）**：见④——满足率都是 99.7866%，平均库存 351.7 vs 337.2、总成本 240,066 vs 238,801；它买到的是“持续低估时的保险”，不是本次回测的满足率。企业可在 `config.yaml` 关闭或调阈值；如果更看重库存，可把阈值调高（如 10%）或关闭升级。

### 六、参数演示（不碰代码）

| 版本 | 改动 | C 满足率 | C 平均库存 | C 总成本 |
| --- | --- | ---: | ---: | ---: |
| 默认 | — | 99.7866% | 351.7 | 240,066 |
| 服务水平 0.95 | `service_level_target: 0.95` | 99.7866% | 380.4 | 247,007 |
| 关闭漂移监测 | `drift_monitor_enabled: false` | 99.7866% | 337.2 | 238,801 |
| 缺货惩罚 50 | `stockout_cost_per_unit: 50` | 99.7866% | 351.7 | 247,026（A 402,835 / B 260,666） |

- 服务水平从 90% 提到 95%，库存 +8.2%、成本 +2.9%，满足率不变——因为剩余缺货被冷启动主导，提高服务水平只体现为更多安全库存。这个结果本身就是一个提醒：**服务水平的边际收益要在非冷启动、非突变期评估**。
- 三个演示配置文件放在 `output/_demo_*.yaml`，用 `--tag` 把结果另存为 `inventory_policy_simulation_{tag}.csv` 等，不覆盖主结果。

### 七、输出文件

| 文件 | 内容 |
| --- | --- |
| `output/inventory_replenishment.csv` | 按 SKU 的补货建议：可用库存、在途、P50/P90/P95、SS、ROP、目标水位、建议订货量、紧急度、漂移状态、中文理由 |
| `output/inventory_policy_simulation.csv` | A/B/C 三策略的满足率、缺货 SKU-周、平均库存、年化周转、成本拆解、订单数；`turnover_period` / `turnover_annualized` 均为单 SKU 口径（见③） |
| `output/inventory_policy_simulation_states.csv` | 每个策略 × 周 × SKU 的状态明细（审计/复核用） |
| `output/inventory_simulation.png` | 满足率、平均库存、总成本、缺货周数四联柱状图 |
| `output/inventory_policy_summary.txt` | 上述表格与手工核对的文本摘要 |
| `output/_demo_*.yaml` + `output/inventory_policy_simulation_*.csv` | 参数演示配置与结果 |

### 八、与预测模块的边界

- 本轮**没有修改** `forecast_cumulative.py`、`forecast_ensemble.py`、`forecast_common.py` 或任何 `conformal_*` 校准参数；库存模块只读取预测输出和 `clean_main.parquet`。
- 补货建议表的快照用 `forecast_cumulative_predictions.csv` 的最后一个起点（2017-08-21）；仿真需要 2017-01 ~ 2017-09 每个起点的预测，因此额外读取 `forecast_cumulative_backtest_predictions.csv`（同一预测模块的 52 起点 walk-forward 输出）。这是仿真所需的历史预测序列，不是另训模型。
- 预测模块遗留的 2017-06 覆盖率缺口（P90 78.3%、P95 85.0%）在本模块中表现为：6 月台阶期库存可能不足；库存策略只能在台阶被数据确认后（7 月）通过漂移监测补偿，无法在 06-05/06-12 当下提前备货。

## Agent 对话层（第 5 步 · 已实现，测试待 key）

设计文档：[AGENT_DESIGN.md](AGENT_DESIGN.md)。实现文件：`app/agent.py`（交互 + `--once` + 4 个工具）和 `app/agent_test.py`（10 条测试，输出 `output/agent_test_cases.csv`）。10 条测试已全部执行并通过（10/10），密钥只存在于本地 `.env`，不进入版本库。

> 命名约定：对外名称统一为「智链参谋」；目录名 `zhilian-canmou` 保持不变。第 6 步 Streamlit 界面标题也使用「智链参谋」。

### 结论速览

- **4 个工具，全部只读已有产物**，不重训模型、不重算库存：

| 工具 | 用途 | 数据源 |
| --- | --- | --- |
| `get_forecast(sku_id, weeks)` | 返回未来累计需求 P50/P90/P95 | `output/forecast_cumulative_predictions.csv` |
| `get_inventory_status(sku_id)` | 可用库存、在途、SS、ROP、目标水位、漂移状态 | `output/inventory_replenishment.csv` |
| `recommend_replenishment(sku_id)` | 建议订货量、紧急度、中文理由 | `output/inventory_replenishment.csv` |
| `list_risk_skus()` | 所有需要关注的 SKU 清单 | `output/inventory_replenishment.csv` |

- **模型接入**：DeepSeek OpenAI 兼容接口（`https://api.deepseek.com`、`model=deepseek-chat`）。因环境约定禁止装包，而 venv 未安装 `openai`，改为用已安装的 `requests` 直接发 `/chat/completions`；请求体、`tools` schema、`tool_calls` 回填与官方 SDK 的 function calling 一致。
- **密钥纪律**：只从项目根目录 `.env` 的 `DEEPSEEK_API_KEY` 读取（进程环境变量优先）；代码中不出现明文 key；缺 key 时输出一句中文提示并退出（退出码 2），不打印 traceback。请求设置 `timeout=(5, 30)`、`max_tokens=800`、最多 4 次工具往返。
- **回答纪律**：只用工具返回的数据，不编造数字；先给结论（要不要补、补多少），再给依据（P90、可用库存、ROP）；回答 3~5 句，数字带单位和“未来 N 周”口径；批量/模糊问题先调 `list_risk_skus`；不存在的 SKU 明确说“数据里没有这个 SKU”。
- **每个工具关键的边界**：

| 场景 | 设计行为 |
| --- | --- |
| `weeks ≠ 4` | 预测文件只有未来 4 周累计，返回 `horizon_matched=false` 并拒绝外推，不用 4 周值代替 N 周 |
| 不存在的 SKU（9999） | `reason="unknown_sku"`，回答“数据里没有这个 SKU”，列出可用 Top-10 |
| 存在但不在 Top-10（如 977） | `reason="not_in_scope"`，说明“存在但当前只覆盖 Top-10”，不误报为“数据里没有” |
| 风险清单 | 默认返回紧急 + 建议补货（当前 3 条：365/502/1073）；`include_planned=True` 追加计划补货（当前共 10 条）；漂移单独标注，可与任一级并列 |
| 空表 | 明确回答“当前没有需要补货的 SKU”，不编造 |
| 连续追问（“那第二个呢”） | 沿用上一轮意图，解析为对应顺序的 SKU 后重新调用工具 |

- **测试与验收**：10 条用例覆盖正常问缺货、问补量、批量、未知 SKU、模糊问题、非 Top-10、超出预测周数、缺 key、空表、连续追问；**10/10 通过**。回答中的 608 / 523 / 190、2270.46 / 2043.62 / 602.98、2231.00 / 2032.00 / 564.00 等数字已与 `inventory_replenishment.csv` / `forecast_cumulative_predictions.csv` 核对一致。完整记录见 `output/agent_test_cases.csv`。
- **密钥状态**：本地 `.env` 已配置有效 `DEEPSEEK_API_KEY`（仅本机，已被 `.gitignore` 排除）；代码中无明文密钥。

## 生鲜前置仓看板（默认数据源）

看板顶部提供数据源切换：

| 选项 | 读取数据 | 说明 |
| --- | --- | --- |
| **生鲜前置仓（默认）** | `output/freshretail_*` 五张表 | 本项目的生鲜主线演示 |
| **制造企业（保底）** | DataCo 原有 CSV | 回退方案，页面逻辑与第 6 步一致 |

切换后四个区块全部换数据，不混用。标题「智链参谋 · 生鲜前置仓补货决策助手」，副标题「每天告诉你，每家店每个 SKU 明天订多少件」。商品统一写 `SKU {store_id}-{product_id}`，不出现具体品名，也不出现 DataCo 的 SKU 编号。

### 运行

```powershell
$env:PYTHONUTF8=1; $env:PYTHONIOENCODING='utf-8'
.\.venv\Scripts\python.exe -m streamlit run app\ui\dashboard.py
```

### 四个区块

| 区块 | 内容 | 数据来源 |
| --- | --- | --- |
| 今日概览 | 在管序列、今天要补（紧急 / 建议补货）、临期货（件数 + 序列数）、短保报损对比（C vs B） | 补货快照 + 策略对比表 |
| 补货清单 | 200 行；紧急度多选、保质期档 3/5/7、临期仅看、SKU 搜索；默认紧急度→临期降序；临期行高亮 + 标签；空结果提示 | 补货快照 + 效期倍数扫描 |
| 单品详情 | 7 天 P50/P90 折线 + 实测点；六张数字卡；模板拼接的建议文案 | 预测表 + 补货快照 + 序列汇总 + config |
| 补货 Agent | 复用 `run_turn(dataset=freshretail)`；生鲜三条快捷问题；回答下方附「数据来源：补货快照 …」 | `app/agent.py` + 生鲜四工具 |

### 看板数据快照（补货快照 2024-07-02）

以下数字由 `app/ui/dashboard.py` 从五张 CSV 现算；换数据后重跑脚本即可刷新，页面代码不硬编码这些值。

**1）今日概览（KPI）**

| 卡片 | 当前值 | 计算口径 | 来源 |
| --- | --- | --- | --- |
| 在管序列 | 200 条（店 × SKU） | 补货快照行数 / 序列数 | 补货快照 2024-07-02 |
| 今天要补 | 紧急 2 条 / 建议补货 153 条 | `urgency` 计数 | 补货快照 2024-07-02 |
| 临期货 | 147.42 件，涉及 29 条序列 | `expiring_qty > 0` 求和与计数 | 补货快照 2024-07-02 |
| 短保报损对比 | 本方案 8.38% vs 传统补货 32.04% | 策略 C vs B 的 `spoilage_rate`，最短保质期档 | 策略对比表 · 3 天保质期 |

**2）紧急度与临期分布**

| 紧急度 | 序列数 |
| --- | ---: |
| 紧急 | 2 |
| 建议补货 | 153 |
| 计划补货 | 45 |
| 正常 | 0 |

> 正常档为 0 是结构性结果：效期约束收紧目标水位后，在库普遍低于目标；筛选「正常」时页面显示空状态提示，不会白屏。快照使用的保质期档为 5 天。

**3）补货清单前 10 条（默认排序：紧急度 → 临期数量降序）**

| 店 / SKU | 现有库存 | ROP_P90 | 效期修正目标 | 建议订货 | 紧急度 | 临期数量 |
| --- | ---: | ---: | ---: | ---: | --- | ---: |
| SKU 145-802 | 2.00 | 4.06 | 7.06 | 6 | 紧急 | 0.00 |
| SKU 781-802 | 2.00 | 4.12 | 7.00 | 6 | 紧急 | 0.00 |
| SKU 555-300 | 12.08 | 15.34 | 58.94 | 47 | 建议补货 | 6.08 |
| SKU 471-666 | 5.30 | 6.34 | 22.63 | 18 | 建议补货 | 4.30 |
| SKU 145-41 | 4.10 | 4.62 | 12.12 | 9 | 建议补货 | 4.10 |
| SKU 182-486 | 3.70 | 4.38 | 17.33 | 14 | 建议补货 | 3.70 |
| SKU 462-775 | 3.70 | 3.83 | 12.25 | 9 | 建议补货 | 3.70 |
| SKU 431-207 | 3.40 | 4.14 | 13.39 | 10 | 建议补货 | 2.40 |
| SKU 750-740 | 2.30 | 2.59 | 7.80 | 6 | 建议补货 | 2.30 |
| SKU 145-631 | 2.25 | 2.52 | 7.60 | 6 | 建议补货 | 2.25 |

> 完整 200 条见 `output/freshretail_inventory_replenishment.csv`。效期修正目标 = `min(target_level, 保质期内预计可售量 × 1.00)`；表中 10 条均为快照的默认排序头部。

**4）单品详情示例：SKU 18-300**

| 指标 | 数值 |
| --- | ---: |
| 现有库存 | 26.36 件 |
| ROP_P90 | 18.58 件 |
| 效期修正目标 | 81.44 件（原始目标水位 130.31 件，被效期上限收紧） |
| 建议订货 | 56 件 |
| 临期数量 | 26.36 件 |
| 保质期档 | 5 天 |

自动生成建议（模板拼接，不调用 LLM）：

> 建议今天订 56 件：低于目标水位，且效期上限 81.44 件，订 56 件可在 5 天内售完。另有 26.36 件临期，建议优先促销。

7 天预测与实测（件）：

| 日期 | 实测 | P50 | P90 |
| --- | ---: | ---: | ---: |
| 2024-06-26 | 13.50 | 16.43 | 18.88 |
| 2024-06-27 | 14.60 | 16.33 | 18.53 |
| 2024-06-28 | 13.41 | 16.21 | 18.79 |
| 2024-06-29 | 13.63 | 16.78 | 19.30 |
| 2024-06-30 | 18.60 | 16.81 | 19.40 |
| 2024-07-01 | 10.90 | 16.55 | 18.66 |
| 2024-07-02 | 16.90 | 16.29 | 18.58 |

**5）Agent 对话数据示例**

问：SKU 145-802 该补多少？

> 答：SKU 145-802 建议订货 6 件，紧急度属于“紧急”（现有库存 2 件，低于 0.5×ROP_P90=2.03 件）。依据来自补货快照 2024-07-02：现有库存 2 件、ROP_P90 为 4.06 件、目标水位 11.29 件、效期修正目标 7.06 件；订货上限 = 保质期内预计可售量 × config 倍数 1.00 = 7.06 件，因此建议订货 6 件。该序列临期数量为 0 件，无需优先促销。

工具调用：`recommend_replenishment(store_id=145, product_id=802)`；数据来源：补货快照 2024-07-02。

### 五张表契约（列名即接口，只读）

| 文件 | 行数 | 页面用到的列 |
| --- | ---: | --- |
| `freshretail_inventory_replenishment.csv` | 200 | `store_id, product_id, shelf_life_days, on_hand, rop_p90, target_level, suggest_qty, urgency, expiring_qty` |
| `freshretail_forecast_predictions.csv` | 1,400 | `store_id, product_id, dt, y_actual, y_pred_p50, y_pred_p90, stockout_rate` |
| `freshretail_inventory_policy_comparison.csv` | 9 | `policy, shelf_life_days, spoilage_rate, fill_rate, …` |
| `freshretail_inventory_cap_sweep.csv` | 15 | `policy, shelf_life_days, multiplier, fill_rate, spoilage_rate, avg_on_hand, total_cost` |
| `freshretail_sku_summary_all.csv` | 50,000（`selected=1` 为分析范围） | `store_id, product_id, total_sale, cv, coverage_days, selected` |

取数口径：

- **所有业务数字从 CSV 现算**，页面不硬编码 200、153、8.38%、32.04% 等值；快照日期取预测表 `dt` 最大值（当前 `2024-07-02`）。
- **效期修正目标**：CSV 无 `target_eff` 列，页面按与 Agent 相同的口径现算 `min(target_level, 保质期内预计可售量 × expiry_cap_multiplier)`。可售量由 7 天预测 P50 从到货日起累加 `shelf_life_days` 天；倍数、提前期、快照日读 `config/freshretail_inventory.yaml`。
- **短保报损对比**：取策略对比表中 `shelf_life_days` 最小档，策略 C 的 `spoilage_rate` vs 策略 B；当前为 3 天档的 8.38% vs 32.04%。
- 缺文件时显示中文提示，不清空整页；筛「正常」档且批次无正常序列时，显示「当前批次没有正常档序列（效期约束把目标水位收紧后，在库普遍低于目标）。」

### 验收（2026-10-25）

| 验收项 | 实测 |
| --- | --- |
| 启动 | `.venv\Scripts\python.exe -m streamlit run app\ui\dashboard.py` 正常；AppTest 无异常 |
| 四区块 + 空状态 | KPI、补货清单、单品详情、对话齐全；`正常` 档空状态提示正确；搜索 `145-802` 精确命中 1 条 |
| KPI 与 CSV 一致 | 控制台打印 `[生鲜看板 KPI] 快照=2024-07-02 在管序列=200 紧急=2 建议补货=153 临期件数=147.42 临期序列=29 短保档=3天 报损C=8.3800% 报损B=32.0400%` |
| 制造企业回归 | 切回后原 DataCo 页面四区块正常，Agent 工具集恢复为 DataCo（`DataStore` / 原系统提示词） |
| 文案纪律 | 生鲜页渲染结果无 DataCo SKU 编号；商品名均为 `SKU {store}-{product}`；未出现具体品名 |
| 数据与文档 | DataCo 脚本与输出零改动；`README.md` 与 `app/ui/README.md` 已更新 |

**截图**

- `output/dashboard_fresh_kpi.png`：数据源切换 + 四张 KPI
- `output/dashboard_fresh_replenishment.png`：补货清单、筛选与临期高亮
- `output/dashboard_fresh_detail.png`：单品详情（折线 + 数字块 + 建议文案）
- `output/dashboard_fresh_chat.png`：Agent 回答与工具调用
- `output/dashboard_dataco_regression.png`：切回制造企业的回归截图

## Streamlit 演示看板（第 6 步 · DataCo 保底数据源）

> 以下为原 DataCo 看板说明，保留用于回退；默认数据源见上一节「生鲜前置仓看板」。

### 运行

```powershell
.\.venv\Scripts\python.exe -m streamlit run app\ui\dashboard.py
```

默认地址 `http://localhost:8501`。`.streamlit/config.toml` 已配置浅色主题与 headless 启动，命令不需要再加参数。

### 页面结构

- **顶部栏**：「智链参谋」+ 副标题 + 快照周（2017-08-21）+「上传企业数据（即将开放）」占位按钮；点击提示“该功能将在区域赛版本开放”。
- **左侧**：SKU 下拉（默认 365，显示名称与品类）；历史周需求折线 + 未来 4 周累计需求 P50/P90/P95 条形对比。
- **右侧**：可用库存、ROP、安全库存、目标水位四张指标卡；建议订货量大字号 + 紧急度徽标 + reason 原文；全量 10 个 SKU 的补货表，按紧急度排序并用颜色区分。
- **底部**：Agent 对话；3 个快捷问题；每条回答可展开「查看工具调用」，显示本轮工具名与参数。

### 数据源与实现口径

- 只读 `output/` 下的 CSV，**不重算 SS / ROP / 订货量**；页面显示的数字直接来自 CSV。
- 预测：`forecast_cumulative_predictions.csv`；库存/补货：`inventory_replenishment.csv`；策略 KPI：`inventory_policy_simulation.csv`。
- 历史逐周需求：`inventory_policy_simulation_states.csv`（策略 C 的 `demand` 列，38 周）。`demand_weekly.csv` 只有全量合计、不含 SKU 维度，因此仅用于全量背景校验，不画单 SKU 曲线。
- Agent 复用 `app/agent.py` 的 `run_turn()`，不修改其对外接口；对话历史放在 `st.session_state`，切换 SKU 不清空，页面刷新才清空。
- 图表使用内联 SVG、指标卡使用 HTML，绕过 Vega / Streamlit 组件骨架的首屏等待；只使用已装好的 streamlit / pandas 和 Python 标准库，不新增依赖。
- 缺 CSV 时显示“数据文件缺失，请先运行对应脚本”，其余区块照常渲染；缺 API Key 或调用失败时只在对话区显示中文提示，图表与库存不受影响。

### 验收结果

| 验收项 | 实测 |
| --- | --- |
| 启动命令 | `.\.venv\Scripts\python.exe -m streamlit run app\ui\dashboard.py`，health ok，无 Python 异常 |
| 首屏出图 | 服务就绪后，浏览器导航到指标/图表/表格就绪：冷会话 **4.28 秒**、热会话约 **3.1 秒**；Streamlit 进程冷启动另约 3 秒（不计入首屏） |
| 3 个 SKU 切换 | 365 / 502 / 1004 的 P90、ROP、目标水位、建议订货量逐项与 CSV 一致（见下表） |
| 对话 | 输入「SKU 365 该补多少？」回答含 **608**；展开「查看工具调用」可见 `get_forecast` / `get_inventory_status` / `recommend_replenishment` 及参数 |
| 缺 key | 临时改名 `.env` 后，图表与库存正常，对话区显示「未配置 DEEPSEEK_API_KEY，对话功能暂不可用」，无报错栈 |
| 补货表 | 10 行齐全；紧急 0 / 建议补货 3 / 计划补货 7 / 正常 0，颜色与 `urgency` 一致 |

**三个 SKU 的页面/CSV 对照**

| SKU | 可用库存 | P90 | ROP | 目标水位 | 建议订货 | 紧急度 |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| 365 | 2,231.00 | 2,270.46 | 2,270.46 | 2,838.08 | 608 | 建议补货 |
| 502 | 2,032.00 | 2,043.62 | 2,043.62 | 2,554.53 | 523 | 建议补货 |
| 1004 | 656.00 | 652.66 | 652.66 | 815.82 | 160 | 计划补货 |

> 本轮 `lead_time_weeks = horizon_weeks = 4`，因此 P90 与 ROP 数值相同；页面上的 P90 来自左侧预测条形图，ROP 来自右侧指标卡和补货表。

**截图**

- `output/dashboard_home.png`：首屏（预测图 + 库存卡 + 补货表 + 对话入口）
- `output/dashboard_chat.png`：完整对话 + 展开的工具调用
- `output/dashboard_nokey.png`：临时改名 `.env` 后的缺 key 状态

## 开发约定

<!-- 待补充：分支策略、提交规范、代码风格 -->

## 许可证

<!-- 待补充 -->
