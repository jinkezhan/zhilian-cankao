# src/freshretail —— 生鲜数据预处理与交接契约

本目录只负责 **FreshRetailNet-50K 的核查、清洗、筛选与 EDA**，不做预测、不改库存与 Agent 代码。
DataCo 的旧代码/旧产物全部保留，作为回退方案。

数据来源：**Dingdong-Inc, FreshRetailNet-50K, arXiv:2505.16319, CC BY 4.0**。

---

## 1. 下游唯一输入

后续预测模块请只读这一个文件：

```
data/freshretail/clean_daily.parquet
```

### 1.1 规模

| 项目 | 值 |
|---|---|
| 行数 | **19,400** |
| 列数 | **14** |
| 序列数 | **200**（门店×SKU） |
| 日期范围 | **2024-03-28 ~ 2024-07-02** |
| 天数 | **97**（train 90 + eval 7） |
| 每序列覆盖 | **97 / 97 天**，无缺日 |
| 城市 / SKU / 三级品类 | 12 / 50 / 40 |
| 缺失单元格 / 重复键 | 0 / 0 |

### 1.2 列契约

| 列 | dtype | 含义 |
|---|---|---|
| `dt` | datetime64[us] | 日期，2024-03-28 ~ 2024-07-02 |
| `city_id` | int64 | 城市 ID（每个 store 唯一对应一个 city） |
| `store_id` | int64 | 门店 ID |
| `product_id` | int64 | SKU ID |
| `third_category_id` | int64 | 三级品类 ID |
| `sale_amount` | float64 | 日销额（官方已做全局归一化，**不是人民币金额**，只适合作相对比较/建模） |
| `discount` | float64 | 折扣率，**1.0 = 无折扣**；`<0.999` 视为折扣日；少量 >1 的溢价记录 |
| `holiday_flag` | int32 | 节假日/周末指示（本数据中 31 个 flag=1 日期有 25 个是周末） |
| `activity_flag` | int32 | 门店活动指示（**门店级**，不是全城/全网活动日） |
| `precpt` | float64 | 降水量 |
| `avg_temperature` | float64 | 日均气温 |
| `avg_humidity` | float64 | 日均湿度 |
| `stockout_rate` | float64 | `clip(stock_hour6_22_cnt / 16, 0, 1)`，6-22 点缺货小时占比 |
| `is_eval` | int8 | 0 = train（2024-03-28~06-25），1 = eval（2024-06-26~07-02） |

### 1.3 train / eval 划分

- train：`is_eval == 0`，90 天，18,000 行。
- eval：`is_eval == 1`，7 天，1,400 行。
- 两段**日期连续、无重叠**，序列集合完全相同；eval 是 train 之后 7 天的同批序列。
- 建议：train 90 天做训练/滚动回测；eval 7 天做最终时间外验证，**不得用于调参**。

---

## 2. 筛选口径

全量 50,000 条序列中，按以下规则选出 200 条：

1. 先剔除 train 90 天覆盖 <80 天的序列（本数据全部 90 天，实际剔除 **0** 条）。
2. 按 train 90 天总销额取 **Top 50 个 SKU**。
3. 每个 SKU 内按 train 90 天总销额取 **Top 4 家门店**。
4. 结果：50 × 4 = **200 条序列**，落在验收线 150~300 内。

> 原任务书中的“4060”“150300”“Top 35”是区间符号丢失，按验收线补全为 “Top 40~60”“150~300”“Top 3~5”。实际取 50 × 4 = 200。
> “13 万行”与 150~300 序列 × 97 天不一致，按验收线执行；实际 200×97=19,400 行。

---

## 3. 运行方式

全部使用项目 `.venv`，不要用裸 `python`：

```powershell
$env:PYTHONUTF8=1; $env:PYTHONIOENCODING='utf-8'

# 1) 数据核查（只读，输出 output/freshretail_data_profile.md）
.\.venv\Scripts\python.exe -I src\freshretail\profile_data.py

# 2) 清洗 + 筛选（输出 clean_daily.parquet 与 sku_summary_all.csv）
.\.venv\Scripts\python.exe -I src\freshretail\prepare_freshretail.py

# 3) EDA 指标（输出 output/freshretail_eda_stats.json）
.\.venv\Scripts\python.exe -I src\freshretail\eda_stats.py

# 4) 产物自检
.\.venv\Scripts\python.exe -I src\freshretail\verify_outputs.py
```

`prepare_freshretail.py` 是幂等的：重复运行会覆盖同名产物，不会修改原始 parquet。

---

## 4. 产物清单

| 路径 | 说明 |
|---|---|
| `data/freshretail/clean_daily.parquet` | **下游唯一输入**，200 序列 × 97 天 |
| `output/freshretail_sku_summary_all.csv` | 全量 50,000 条序列画像，一行一条；含 `high_stockout` |
| `output/freshretail_eda_report.md` | EDA 报告（含 CV/缺货/外生变量/验证方案） |
| `output/freshretail_data_profile.md` | 19 列缺失、层级、分布明细 |
| `output/freshretail_eda_stats.json` | 报告中全部指标的可复算来源 |

### 4.1 `freshretail_sku_summary_all.csv` 列

| 列 | 含义 |
|---|---|
| `sales_rank` | 按 90 天总销额的全量排名 |
| `city_id`, `store_id`, `product_id`, `third_category_id` | 序列静态属性 |
| `total_sale` | train 90 天总销额 |
| `daily_mean` | train 日均销额 |
| `cv` | train 日销 CV（std/mean，含零销日） |
| `coverage_days` | train 覆盖天数（全量均为 90） |
| `avg_stockout_rate` | train 平均 `stockout_rate` |
| `high_stockout` | 1 = `avg_stockout_rate > 0.2`，共 17,082 条 |
| `stockout_days` | train 中 `stockout_rate > 0` 的天数 |
| `discount_day_share` | train 中 `discount < 0.999` 的天数占比 |
| `selected` | 1 = 进入 200 条建模面板 |

---

## 5. 下游建模必须注意的坑

1. **`sale_amount` 是归一化销额，不是金额**。所有“销售额/成本/毛利”类业务结论都不能直接用它换算人民币；它适合做需求指数、相对比较和预测目标。
2. **缺货截断尚未恢复**。`stockout_rate` 是观测到的缺货占比，缺货日的 `sale_amount` 是“实际卖出”而不是“真实需求”。高缺货序列（尤其 Q4，平均缺货率 >0.2）的缺货日销量比非缺货日低约 9%~13%，预测前应做需求恢复或对缺货日降权。
3. **`holiday_flag` 与周末高度共线**：31 个 flag=1 日期中 25 个是周末。与星期几特征同时使用时注意共线性。
4. **`activity_flag` 是门店级**：直接按 flag 分组会把方向做反（选中面板原始倍数 0.694，同序列内 1.334）。必须加门店/SKU 固定效应或做同序列对比。
5. **`discount` 不是线性变量**：二值折扣效应稳健（+27%~39%），但折扣深度与销量不单调；建议先二值、再分箱/样条。
6. **气温的大部分相关来自共同趋势**：日聚合 r=0.54~0.65，同序列去均值后仅 0.11~0.17；务必同时加入时间/趋势项。
7. **只有 90 天**：做不了年季节性；预测任务限定为“未来 7 天日销预测”。90 天内有 +50% 的上行漂移，基线必须包含趋势和星期几因子。

---

## 6. 引用

> Dingdong-Inc. **FreshRetailNet-50K**. arXiv:2505.16319. CC BY 4.0.
> 898 门店、18 城、865 生鲜 SKU，2024-03-28 ~ 2024-07-02 的小时级销售与缺货标注。
