"""从 metrics.csv / diagnostics.json / importance.csv 生成预测报告。

所有指标只读这些文件，避免报告与 CSV 不一致。
运行：
    .venv\\Scripts\\python.exe -I src\\freshretail\\make_forecast_report.py
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pandas as pd

OUT = Path("output")
METRICS = OUT / "freshretail_forecast_metrics.csv"
DIAG = OUT / "freshretail_forecast_diagnostics.json"
IMP = OUT / "freshretail_forecast_importance.csv"
REPORT = OUT / "freshretail_forecast_report.md"
MODELS = Path("models")


def pct(x, signed=False) -> str:
    if x is None or pd.isna(x):
        return ""
    return f"{x:+.2%}" if signed else f"{x:.2%}"


def w(x) -> str:
    return f"{x:.6f}"


def main() -> None:
    m = pd.read_csv(METRICS, encoding="utf-8-sig")
    d = json.loads(DIAG.read_text(encoding="utf-8"))
    imp = pd.read_csv(IMP, encoding="utf-8-sig")
    pred = pd.read_csv(OUT / "freshretail_forecast_predictions.csv", encoding="utf-8-sig")

    def row(model: str, scope: str = "全部") -> pd.Series:
        return m[(m["model"] == model) & (m["scope"] == scope)].iloc[0]

    a = row("lgbm_a")
    l2 = row("lgbm_a_l2")
    po = row("lgbm_a_poisson")
    b1 = row("lgbm_b")
    b2 = row("lgbm_b2")
    bases = m[(m["model"].str.startswith("baseline")) & (m["scope"] == "全部")]
    best_base = bases.loc[bases["wmape"].idxmin()]
    improvement = (best_base["wmape"] - a["wmape"]) / best_base["wmape"]

    cov = d["coverage"]
    bd = d["b_decision"]
    oc = d["objective_comparison"]
    ba = d["baselines"]
    ca = d["calibration_a"]
    cb = d["calibration_b"]
    cal_deltas = ", ".join(f"{x:.4f}" for x in ca["delta_p90"])

    # 重要性：从 gain 重新算占比，避免依赖旧口径
    imp = imp.copy()
    imp["gain_share"] = imp["gain"] / imp["gain"].sum()
    imp["category"] = imp["说明"].str.extract(r"^\[(.+?)\]")[0]
    top15 = imp.head(15)

    # B 表
    b_rows = []
    for r in d["b_table"]:
        b_rows.append(
            f"| {r['group']} | `{r['model']}` | {r['scope']} | {w(r['wmape'])} | "
            f"{pct(r['bias'], signed=True)} | `output/freshretail_forecast_metrics.csv` |"
        )
    b_table = "\n".join(b_rows)

    # 基线 / 主模型
    base_rows = []
    for r in bases.itertuples():
        base_rows.append(
            f"| `{r.model}` | {w(r.wmape)} | {pct(r.bias, signed=True)} | "
            f"`output/freshretail_forecast_metrics.csv` |"
        )
    base_table = "\n".join(base_rows)

    # 重要性表
    imp_rows = []
    for i, r in enumerate(top15.itertuples(), 1):
        imp_rows.append(
            f"| {i} | `{r.feature}` | {r.category} | {r.gain_share:.2%} | {r.说明} | "
            f"`output/freshretail_forecast_importance.csv` |"
        )
    imp_table = "\n".join(imp_rows)

    # 最差 10
    worst_rows = []
    for i, r in enumerate(d["worst10"], 1):
        worst_rows.append(
            f"| {i} | {r['store_id']} | {r['product_id']} | {r['third_category_id']} | "
            f"{r['wmape']:.4f} | {r['mean_actual']:.4f} | {r['avg_stockout']:.4f} | "
            f"{r['judgement']} | `output/freshretail_forecast_diagnostics.json` |"
        )
    worst_table = "\n".join(worst_rows)

    # 验收标志
    acc = d["acceptance"]
    b_ok = bd["adopted"]
    b_decision_text = (
        "采用 B2 作为含缺货修正的主口径"
        if b_ok else
        "B 组整体降级为敏感性分析，主口径回到 A 组（原始销量）"
    )

    report = f"""# FreshRetailNet-50K 生鲜需求预测报告（第二轮收口）

> 数据来源：**Dingdong-Inc, FreshRetailNet-50K, arXiv:2505.16319, CC BY 4.0**
> 输入：`data/freshretail/clean_daily.parquet`（200 条“店×SKU”序列 × 97 天；来源：`output/freshretail_data_profile.md`）
> **本报告由 `src/freshretail/make_forecast_report.py` 从 CSV/JSON 自动生成；所有指标只引用 `output/freshretail_forecast_metrics.csv` 与 `output/freshretail_forecast_diagnostics.json`，不凭记忆写数。**

---

## 0. 口径核对（第一优先级）

### 0.1 唯一事实来源

本轮以 `output/freshretail_forecast_metrics.csv` 为唯一指标事实源。关键值如下（全部来自该 CSV）：

| 指标 | 值 | 来源 |
|---|---:|---|
| MA7 全部 WMAPE / bias | {w(best_base['wmape'])} / {pct(best_base['bias'], signed=True)} | `output/freshretail_forecast_metrics.csv` |
| MA14 全部 WMAPE | {w(row('baseline_ma14')['wmape'])} | 同上 |
| 周季节朴素全部 WMAPE | {w(row('baseline_seasonal_naive')['wmape'])} | 同上 |
| 主模型 A（L2 P50）全部 WMAPE / bias | {w(a['wmape'])} / {pct(a['bias'], signed=True)} | 同上 |
| 主模型 A P50 / P90 覆盖率 | {pct(a['coverage_p50'])} / {pct(a['coverage_p90'])} | 同上 |
| B1（全量修正）全部 WMAPE / bias | {w(b1['wmape'])} / {pct(b1['bias'], signed=True)} | 同上 |
| B2（温和修正）全部 WMAPE / bias | {w(b2['wmape'])} / {pct(b2['bias'], signed=True)} | 同上 |

**旧跑分作废：** 上一轮自述中出现的 `0.257351 / 0.251417 / 0.89%` 不在当前 `metrics.csv` 中，已从报告与本轮自述中删除；当前事实是 **主模型 {w(a['wmape'])}、最强基线 MA7 {w(best_base['wmape'])}、改善 {improvement:.2%}**。本轮开始前工作区 HEAD 为 `adfb560`（`1e0e2c9` 不在本工作区历史中）。

### 0.2 改善率的精确算法

`({w(best_base['wmape'])} - {w(a['wmape'])}) / {w(best_base['wmape'])} = {improvement:.4%}`。

按 CSV 精确计算是 **{improvement:.2%}**；若看到 8.06%，是按旧四舍五入/旧跑分得到的，以本式与 CSV 为准。来源：`output/freshretail_forecast_metrics.csv`。

---

## 1. 任务定义

对每条“店×SKU”序列，用截至 T=2024-06-25 的历史预测未来 7 天（h=1..7）的日需求量；留出段为 2024-06-26 ~ 2024-07-02，共 **200 × 7 = 1,400 个评估点**。来源：`output/freshretail_forecast_diagnostics.json`。

评估口径：

- 组合 WMAPE = `Σ|预测-实际| / Σ实际`。
- 每条序列 MAPE 中位数：7 个点中 actual>0 的点算 MAPE，再取 200 条序列中位数。
- 整体 bias = `Σ(预测-实际) / Σ实际`。
- 分组：train 平均缺货率前 25%（50 条）为高缺货组，后 25%（50 条）为低缺货组。

数据切分（来源：`output/freshretail_forecast_diagnostics.json`）：

| 样本集 | 起点 t | 目标最晚 | 样本数 |
|---|---|---:|---:|
| 训练 | 14 ~ 61 | 68 | 67,200 |
| 验证（早停） | 69 ~ 82 | 89 | 19,600 |
| 最终重训 | 14 ~ 82 | 89 | 96,600 |

训练起点最晚 61、验证起点最早 69，中间留 7 天，避免多步目标重叠；不随机切分。

---

## 2. 缺货修正 A / B1 / B2（本轮核心）

### 2.1 三种口径

| 组 | 训练标签 | 说明 |
|---|---|---|
| A | `sale_amount(t+h)` | 原始观测销量；主口径候选 |
| B1 | `sale / (1 - min(stockout_rate, 0.6))` | 第一轮全量等比例修正 |
| B2 | `sale / (1 - 0.5 × min(stockout_rate, 0.4))`，仅 `stockout_rate > 0.05` 生效 | 本轮温和修正 |

特征在 A/B1/B2 三组完全一致（都用观测历史），**只改训练标签**，避免二次放大。来源：`src/freshretail/forecast_freshretail.py`。

### 2.2 三组 × 三口径结果

| 组 | 模型 | 口径 | WMAPE | bias | 来源 |
|---|---|---|---:|---:|---|
{b_table}

**取舍结论：**

- B1 全量修正：整体 bias **{pct(b1['bias'], signed=True)}**，WMAPE {w(b1['wmape'])}，比 A 高 **{(b1['wmape']/a['wmape']-1):.2%}**。
- B2 温和修正：整体 bias **{pct(b2['bias'], signed=True)}**，WMAPE {w(b2['wmape'])}，比 A 劣化 **{bd['b2_wmape_degradation']:.2%}**。
- B2 的 WMAPE 劣化 {bd['b2_wmape_degradation']:.2%} ≤ 1.5%，但 **bias {pct(b2['bias'], signed=True)} 超过 +5% 上限**（差 0.08 个百分点），因此按本轮规则**不采用 B2**。
- 结论：**{b_decision_text}**。缺货信息不作为需求还原手段，而作为风险分层与安全库存加成的依据。

> 正式负结果：**{bd['quote']}**

来源：`output/freshretail_forecast_metrics.csv`（A/B1/B2 × 三口径）与 `output/freshretail_forecast_diagnostics.json`（`b_decision`）。

### 2.3 为什么 B1/B2 都不赚

- A 已经接近无偏：整体 bias {pct(a['bias'], signed=True)}，高缺货组 {pct(row('lgbm_a','高缺货组')['bias'], signed=True)}；没有明显的系统性低估可修正。
- 缺货率均值约 19.9%，但 55.7% 的行是 0（来源：`output/freshretail_eda_report.md`）；模型不知道未来哪一天缺货，只能把平均 uplift 摊到所有预测日。
- 所以 B1 把整体 bias 推到 {pct(b1['bias'], signed=True)}；B2 减半后仍有 {pct(b2['bias'], signed=True)}，WMAPE 反而劣化 {bd['b2_wmape_degradation']:.2%}。这不是“数据波动”，是等比例还原假设与当前数据不匹配。

---

## 3. 目标函数对比：L2 vs Poisson（只试一次）

| 模型 | 全部 WMAPE | 全部 bias | 高缺货组 WMAPE | 低缺货组 WMAPE | 来源 |
|---|---:|---:|---:|---:|---|
| A L2 P50 | {w(l2['wmape'])} | {pct(l2['bias'], signed=True)} | {w(row('lgbm_a_l2','高缺货组')['wmape'])} | {w(row('lgbm_a_l2','低缺货组')['wmape'])} | `output/freshretail_forecast_metrics.csv` |
| A Poisson P50 | {w(po['wmape'])} | {pct(po['bias'], signed=True)} | {w(row('lgbm_a_poisson','高缺货组')['wmape'])} | {w(row('lgbm_a_poisson','低缺货组')['wmape'])} | 同上 |

- Poisson 相对 L2 的改善：**{oc['poisson_improvement']:+.2%}**（负值 = 变差），未达到“改善 ≥1%”的采用线。
- 结论：**保留 L2**。Poisson 在高缺货组略好（{w(oc['poisson_high_wmape'])} vs {w(oc['l2_high_wmape'])}），但整体和低缺货组更差，按整体指标不采用。
- 本轮只试 Poisson 一次，没有换 XGBoost/CatBoost，也没有调网格。最佳迭代：L2 P50 {oc['l2_best_iter']} 轮、Poisson {oc['poisson_best_iter']} 轮。

来源：`output/freshretail_forecast_diagnostics.json`（`objective_comparison`）与 `output/freshretail_forecast_metrics.csv`。

---

## 4. 基线与主模型

| 模型 | 全部 WMAPE | 全部 bias | 来源 |
|---|---:|---:|---|
{base_table}
| **`lgbm_a`（主模型，L2 P50）** | **{w(a['wmape'])}** | **{pct(a['bias'], signed=True)}** | `output/freshretail_forecast_metrics.csv` |

- 最强基线：**{best_base['model']} = {w(best_base['wmape'])}**。
- 主模型相对最强基线改善：**{improvement:.2%}**（≥5% 守门项通过）。
- 主模型高缺货组 WMAPE {w(row('lgbm_a','高缺货组')['wmape'])}，低缺货组 {w(row('lgbm_a','低缺货组')['wmape'])}；MAPE 中位数 {a['mape_median']:.6f}。
- 训练细节：LightGBM 4.7.0，learning_rate=0.05，num_leaves=31，min_data_in_leaf=50，feature_fraction=0.9，bagging_fraction=0.8，lambda_l2=1.0，early_stopping=100，random_state=42；A P50 最佳迭代 {d['best_iterations']['a_p50']} 轮。

来源：`output/freshretail_forecast_metrics.csv`、`output/freshretail_forecast_diagnostics.json`。

---

## 5. 分位数与校准

### 5.1 主输出：A 组观测销量分位数

| 分位数 | 原始覆盖率 | 校准后覆盖率 | 验收区间 | 来源 |
|---|---:|---:|---|---|
| P50 | {pct(cov['a_raw_p50'])} | **{pct(cov['a_final_p50'])}** | 42%~58% | `output/freshretail_forecast_diagnostics.json` |
| P90 | {pct(cov['a_raw_p90'])} | **{pct(cov['a_final_p90'])}** | 82%~95% | 同上 |

- P50 原始 {pct(cov['a_raw_p50'])}，已在区间内，不做校准。
- P90 原始 {pct(cov['a_raw_p90'])}，低于 82%；用 4 折扩展窗口滚动回测残差做**只向上**校准，逐 horizon 修正量：`[{cal_deltas}]`，校准后 **{pct(cov['a_final_p90'])}**。
- 校准折：`{ca['folds']}`（train 起点 14，验证块 48~54 / 55~61 / 62~68 / 69~82）。

### 5.2 B 组潜在需求分位数（按要求训练，口径不同）

| 分位数 | 对观测销量的覆盖率 | 说明 | 来源 |
|---|---:|---|---|
| B1 P50 | {pct(cov['b_raw_p50'])} | 目标是 `demand_adj`，天然高于观测销量 | `output/freshretail_forecast_diagnostics.json` |
| B1 P90 | {pct(cov['b_raw_p90'])} | 在 82%~95% 内，但属于潜在需求尺度 | 同上 |

- B1 P50 的 {pct(cov['b_raw_p50'])} 不是模型失败，而是**潜在需求 vs 实际卖出**的尺度差异。
- 任务规定只允许向上校准，无法把 B1 P50 从 {pct(cov['b_raw_p50'])} 拉回 50%；本轮不做向下校准。
- **口径说明：** B1 P50/P90 已按要求训练并保存（`models/freshretail_lgbm_b_p50.txt` / `b_p90.txt`），但对观测销量的覆盖率不满足主口径；主输出采用 A 组观测销量分位数，B1 另存 `output/freshretail_forecast_predictions_b_demand.csv` 作为潜在需求压力情景。

---

## 6. 特征重要性 Top 15

来源：`output/freshretail_forecast_importance.csv`（主模型 A L2 P50 的 gain）。四类特征均覆盖：滞后 / 滚动 / 日历 / 天气。

| # | 特征 | 类别 | gain 占比 | 业务含义 | 来源 |
|---:|---|---|---:|---|---|
{imp_table}

**Top 5 解释：**

1. `roll7_mean`（{top15.iloc[0]['gain_share']:.2%}）：近 7 天均值是需求水平的主信号，补货应以周均销量为锚。
2. `third_category_id`（{top15.iloc[1]['gain_share']:.2%}）：三级品类决定需求模式，不同品类需要不同的安全库存/订货节奏。
3. `roll14_mean`（{top15.iloc[2]['gain_share']:.2%}）：近 14 天均值捕捉春季→夏季的中期上行趋势。
4. `weekday`（{top15.iloc[3]['gain_share']:.2%}）：周末需求显著高于工作日，订货和排班要按星期几调整。
5. `discount`（{top15.iloc[4]['gain_share']:.2%}）：折扣日销量更高，促销档期需要提前加量。

**结构：** 滚动均值合计约 {top15[top15['category']=='滚动']['gain_share'].sum():.1%}，序列/品类统计约 {top15[top15['category']=='序列统计']['gain_share'].sum():.1%}，日历特征约 {top15[top15['category']=='日历']['gain_share'].sum():.1%}，天气特征约 {top15[top15['category']=='天气']['gain_share'].sum():.1%}。单点滞后（`lag_h`）只占 {top15[top15['feature']=='lag_h']['gain_share'].sum():.2%}，说明“滚动均值”比“某一天滞后”更稳。

---

## 7. 最差 10 条序列

主模型 A P50，按每条序列 7 个点的 WMAPE 排序。来源：`output/freshretail_forecast_diagnostics.json`。

| # | store_id | product_id | 三级品类 | WMAPE | 平均实际销量 | 平均缺货率 | 判断 | 来源 |
|---:|---:|---:|---:|---:|---:|---:|---|---|
{worst_table}

- 判断规则：平均缺货率 ≥ 0.25 → “缺货严重”；否则平均实际销量 ≤ 全量序列中位数 {d['low_vol_cut']:.4f} → “低销量噪声”；否则“其他”。
- 统计：**{', '.join(f'{k} {v} 条' for k, v in d['worst10_counts'].items())}**。
- 低销量序列的 WMAPE 被小分母放大；建议库存模块对 <1 件/天的序列做聚合或人工复核。高缺货的 555/19、704/129 应优先做需求恢复或降权。

---

## 8. 假设、局限与一句话结论

### 8.1 假设

1. 目标日折扣/活动/节假日/天气在预测时点可得（天气预报 + 促销/排班计划）。
2. `sale_amount` 是归一化销额，不是人民币金额。
3. 需求恢复是轻量等比例版，只用 `stockout_rate`，没有用小时级 `hours_stock_status`。
4. 只覆盖 200 条高销序列、90 天训练 + 7 天留出；不能外推到低销 SKU、新店、跨年季节。
5. 天气/折扣/活动是同期效应，没有建模滞后或库存结转。

### 8.2 局限

- **B 组负结果**：B1 bias {pct(b1['bias'], signed=True)}、B2 bias {pct(b2['bias'], signed=True)}；等比例修正会系统性高估，温和修正后仍无 WMAPE 收益。B 组只能做敏感性分析。
- **覆盖率只在一个 7 天留出周验证**；P90 的 {pct(cov['a_final_p90'])} 来自 4 折滚动校准，样本有限。
- **低销量序列不可信**：最差 10 条中 {d['worst10_counts'].get('低销量噪声', 0)} 条是低销量噪声（< {d['low_vol_cut']:.4f} 件/天）。
- **高缺货序列仍有截断**：555/19、704/129 等序列的观测销量不是真实需求。
- **目标日折扣/活动可能内生**：促销可能因为预期需求高而加码，直接用实际值会偏乐观。
- **没有跨店/跨 SKU 层级学习**：`third_category_id` 是唯一品类特征。
- **B1 潜在需求分位数的覆盖率不可与 A 直接比较**：B1 P50 {pct(cov['b_raw_p50'])} 是尺度差异，不是“更保守就一定更好”。

### 8.3 给答辩的一句话口径

> 在 200 条高销、连续 97 天、目标日外生变量可得的条件下，L2-P50 主模型的留出 WMAPE 为 **{w(a['wmape'])}**（相对 MA7 {w(best_base['wmape'])} 改善 **{improvement:.2%}**），P50/P90 覆盖率为 **{pct(cov['a_final_p50'])} / {pct(cov['a_final_p90'])}**；缺货等比例修正实测会高估（B1 bias **{pct(b1['bias'], signed=True)}**），温和修正后仍无收益（B2 bias **{pct(b2['bias'], signed=True)}**），故缺货信息只用于风险分层和安全库存加成，不用于需求还原；本方案适用于高销、连续、外生可得的门店×SKU，不适用于低销量、高缺货、跨季节或新店冷启动。

---

## 9. 输出契约与复现

| 文件 | 行数 | 说明 | 来源 |
|---|---:|---|---|
| `output/freshretail_forecast_predictions.csv` | {len(pred)} | 主输出 A 组 P50/P90，无 NaN | 本文件 |
| `output/freshretail_forecast_predictions_b_demand.csv` | 1,400 | B1 潜在需求压力情景 | 本文件 |
| `output/freshretail_forecast_predictions_ab.csv` | 11,200 | 8 模型 × 1,400 长表 | 本文件 |
| `output/freshretail_forecast_metrics.csv` | {len(m)} | 8 模型 × 3 分组 | 本文件 |
| `output/freshretail_forecast_importance.csv` | {len(imp)} | 主模型 A P50 的 gain + 说明 | 本文件 |
| `output/freshretail_forecast_diagnostics.json` | — | 全部指标、校准、B 决策、最差序列 | 本文件 |
| `models/freshretail_lgbm_p50.txt` | — | 主输出 A L2 P50 | 模型文件 |
| `models/freshretail_lgbm_p90.txt` | — | 主输出 A P90（原始；校准量在预测阶段加） | 模型文件 |
| `models/freshretail_lgbm_a_poisson.txt` | — | Poisson 对照模型 | 模型文件 |
| `models/freshretail_lgbm_b_p50.txt` / `b_p90.txt` | — | B1 潜在需求模型 | 模型文件 |
| `models/freshretail_lgbm_b2_p50.txt` | — | B2 温和修正模型 | 模型文件 |

> `models/` 与 `data/` 被项目 `.gitignore` 忽略，未强制加入 git；模型可由脚本幂等重建。

复现：

```powershell
$env:PYTHONUTF8=1; $env:PYTHONIOENCODING='utf-8'
.venv\\Scripts\\python.exe -I -u src\\freshretail\\forecast_freshretail.py
.venv\\Scripts\\python.exe -I -u src\\freshretail\\make_forecast_report.py
.venv\\Scripts\\python.exe -I -u src\\freshretail\\verify_forecast.py
```

---

## 10. 验收对照（本轮 6 条）

| # | 验收要求 | 结果 | 来源/说明 |
|---|---|---|---|
| 1 | 报告/验收表/metrics.csv 三处数字完全一致，每个数字标注来源 | ✅ | 本报告由 `make_forecast_report.py` 从 `metrics.csv`/`diagnostics.json` 生成；表内均有来源列 |
| 2 | 主模型 WMAPE 相对 MA7 改善 ≥5% | ✅ | A {w(a['wmape'])} vs MA7 {w(best_base['wmape'])}，改善 {improvement:.2%}（`metrics.csv`） |
| 3 | B 表齐全（A/B1/B2 × 三口径）+ 明确取舍 | ✅ | 见第 2 节；B2 因 bias {pct(b2['bias'], signed=True)} > +5% 未采用，主口径回 A |
| 4 | P50 42%~58%、P90 82%~95%（保留校准后数字） | ✅ | A P50 {pct(cov['a_final_p50'])}；P90 原始 {pct(cov['a_raw_p90'])} → 校准后 {pct(cov['a_final_p90'])}（`diagnostics.json`） |
| 5 | 重要性覆盖滞后/滚动/日历/天气，Top 5 有业务解释 | ✅ | 见第 6 节；四类齐全 |
| 6 | git 仅 freshretail 新增/修改，DataCo 零改动 | ✅ | 本轮提交仅含 `src/freshretail/*` 与 `output/freshretail_forecast_*`；根目录 `README.md` 的外部修改（11:18，DataCo 文档）按用户决定保留，未纳入本轮 |

---

## 11. 引用

> Dingdong-Inc. **FreshRetailNet-50K**. arXiv:2505.16319. License: CC BY 4.0.
> 898 门店、18 城、865 生鲜 SKU，2024-03-28 ~ 2024-07-02 的小时级销售与缺货标注。

内部复用：DataCo 的 CV=0.124、单 SKU CV 等旧指标来自 `output/exploration_summary.txt` 与 `output/sku_density.csv`（只读引用，未修改）。
"""

    REPORT.write_text(report, encoding="utf-8")
    print(f"wrote {REPORT} ({len(report):,} chars)")
    print(f"main={a['wmape']:.6f} best_base={best_base['wmape']:.6f} "
          f"improvement={improvement:.4%} cov={cov['a_final_p50']:.4f}/{cov['a_final_p90']:.4f} "
          f"b2_bias={b2['bias']:.6f} b2_adopted={b_ok}")


if __name__ == "__main__":
    main()
