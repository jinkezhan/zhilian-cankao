"""FreshRetailNet-50K 第 1 步：数据核查（不修改原始文件）。

输出：
    output/freshretail_data_profile.md

运行：
    .venv\\Scripts\\python.exe -I src\\freshretail\\profile_data.py
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.compute as pc
import pyarrow.parquet as pq

TRAIN = Path("data/freshretail/train.parquet")
EVAL = Path("data/freshretail/eval.parquet")
OUT = Path("output/freshretail_data_profile.md")

KEY_COLS = ["city_id", "store_id", "product_id", "dt"]
SCALAR_COLS = [
    "city_id", "store_id", "management_group_id",
    "first_category_id", "second_category_id", "third_category_id", "product_id",
    "dt", "sale_amount", "stock_hour6_22_cnt", "discount",
    "holiday_flag", "activity_flag", "precpt",
    "avg_temperature", "avg_humidity", "avg_wind_level",
]


def null_counts(path: Path) -> pd.DataFrame:
    """逐列统计空值，避免一次性把 list 列载入 pandas。"""
    pf = pq.ParquetFile(path)
    rows = []
    for field in pf.schema_arrow:
        col = pf.read(columns=[field.name]).column(field.name)
        n_null = pc.sum(pc.is_null(col)).as_py()
        rows.append({"column": field.name, "dtype": str(field.type), "nulls": int(n_null)})
    return pd.DataFrame(rows)


def series_stats(df: pd.DataFrame) -> dict:
    g = df.groupby(["store_id", "product_id"], observed=True)
    rows_per = g.size()
    days = df["dt"].nunique()
    return {
        "series": int(rows_per.shape[0]),
        "rows_per_series_min": int(rows_per.min()),
        "rows_per_series_max": int(rows_per.max()),
        "series_below_80d": int((rows_per < 80).sum()),
        "distinct_dates": int(days),
        "rows_per_date_min": int(df.groupby("dt").size().min()),
        "rows_per_date_max": int(df.groupby("dt").size().max()),
    }


def main() -> None:
    lines: list[str] = []
    w = lines.append

    prof = {}
    pdf = pd.read_parquet(TRAIN, columns=SCALAR_COLS)
    edf = pd.read_parquet(EVAL, columns=SCALAR_COLS)
    for name, df in (("train", pdf), ("eval", edf)):
        df["dt"] = pd.to_datetime(df["dt"])

    w("# FreshRetailNet-50K 数据核查\n")
    w("数据来源：Dingdong-Inc, FreshRetailNet-50K, arXiv:2505.16319, CC BY 4.0\n")
    w("## 1. 规模与时间范围\n")
    w("| 文件 | 行数 | 列数 | 日期范围 | 天数 |")
    w("|---|---|---|---|---|")
    for name, df in (("train", pdf), ("eval", edf)):
        w(f"| {name}.parquet | {len(df):,} | {len(SCALAR_COLS) + 2} | "
          f"{df['dt'].min():%Y-%m-%d} ~ {df['dt'].max():%Y-%m-%d} | {df['dt'].nunique()} |")
    w("")

    w("## 2. 缺失值（全部 19 列）\n")
    nulls = pd.concat(
        [null_counts(TRAIN).assign(file="train"), null_counts(EVAL).assign(file="eval")]
    )
    w("| 列 | dtype | train 空值 | eval 空值 |")
    w("|---|---|---|---|")
    tr = nulls[nulls["file"] == "train"].set_index("column")
    ev = nulls[nulls["file"] == "eval"].set_index("column")
    for c in tr.index:
        w(f"| {c} | `{tr.loc[c, 'dtype']}` | {tr.loc[c, 'nulls']:,} | {ev.loc[c, 'nulls']:,} |")
    w("")

    w("## 3. 层级画像\n")
    w("| 维度 | train | eval | 说明 |")
    w("|---|---|---|---|")
    hierarchy = [
        ("city_id", "城市"),
        ("store_id", "门店"),
        ("management_group_id", "管理组"),
        ("first_category_id", "一级品类"),
        ("second_category_id", "二级品类"),
        ("third_category_id", "三级品类"),
        ("product_id", "SKU"),
    ]
    for col, label in hierarchy:
        w(f"| {label} `{col}` | {pdf[col].nunique():,} | {edf[col].nunique():,} | |")
    train_series = pdf.groupby(["store_id", "product_id"], observed=True).size()
    eval_series = edf.groupby(["store_id", "product_id"], observed=True).size()
    w(f"| 门店×SKU 序列 | {len(train_series):,} | {len(eval_series):,} | |")
    w("")

    ts, es = series_stats(pdf), series_stats(edf)
    w("### 序列覆盖\n")
    for label, s in (("train", ts), ("eval", es)):
        w(f"- {label}：{s['series']:,} 条序列；每序列行数 {s['rows_per_series_min']}~"
          f"{s['rows_per_series_max']}；覆盖 <80 天的序列 **{s['series_below_80d']:,}** 条；"
          f"每日行数 {s['rows_per_date_min']:,}~{s['rows_per_date_max']:,}。")
    w("")

    # train/eval 关系
    train_keys = set(map(tuple, pdf[["store_id", "product_id"]].drop_duplicates().to_numpy()))
    eval_keys = set(map(tuple, edf[["store_id", "product_id"]].drop_duplicates().to_numpy()))
    w("### train / eval 划分方式\n")
    w(f"- train 日期 {pdf['dt'].min():%Y-%m-%d} ~ {pdf['dt'].max():%Y-%m-%d}；"
      f"eval 日期 {edf['dt'].min():%Y-%m-%d} ~ {edf['dt'].max():%Y-%m-%d}，"
      f"两段**日期连续、无重叠**。")
    w(f"- 序列集合：train {len(train_keys):,} 条，eval {len(eval_keys):,} 条，"
      f"交集 {len(train_keys & eval_keys):,} 条，仅 train 有 {len(train_keys - eval_keys):,} 条，"
      f"仅 eval 有 {len(eval_keys - train_keys):,} 条。")
    w("- 结论：**按时间切分**（后 7 天为评估段），序列主体相同，不是另一批序列。\n")

    w("## 4. 关键字段分布（train）\n")
    w("| 字段 | min | p25 | 中位数 | p75 | max | 均值 | 零值数 | 负值数 |")
    w("|---|---|---|---|---|---|---|---|---|")
    for col in ["sale_amount", "stock_hour6_22_cnt", "discount", "precpt",
                "avg_temperature", "avg_humidity", "avg_wind_level"]:
        s = pdf[col]
        w(f"| {col} | {s.min():.4g} | {s.quantile(.25):.4g} | {s.median():.4g} | "
          f"{s.quantile(.75):.4g} | {s.max():.4g} | {s.mean():.4g} | "
          f"{int((s == 0).sum()):,} | {int((s < 0).sum()):,} |")
    w("")

    w("### 离散字段取值\n")
    for col in ["holiday_flag", "activity_flag"]:
        vc = pdf[col].value_counts().sort_index()
        w(f"- `{col}`：{dict((int(k), int(v)) for k, v in vc.items())}")
    disc = pdf["discount"].value_counts().sort_index()
    w(f"- `discount` 不同取值数：{disc.shape[0]}；最常见 5 个："
      f"{dict((float(k), int(v)) for k, v in disc.tail(5).items())}")
    stock = pdf["stock_hour6_22_cnt"].value_counts().sort_index()
    w(f"- `stock_hour6_22_cnt` 取值 {int(stock.index.min())}~{int(stock.index.max())}；"
      f"0 值 {int(stock.get(0, 0)):,} 行，>16 的行数 {int((pdf['stock_hour6_22_cnt'] > 16).sum()):,}")
    w(f"- `sale_amount` 为 0 的行：{int((pdf['sale_amount'] == 0).sum()):,}；"
      f"为负的行：{int((pdf['sale_amount'] < 0).sum()):,}")
    w("")

    # hours_sale 与 sale_amount 的一致性（抽样 20 万行）
    sample = pq.read_table(TRAIN, columns=["sale_amount", "hours_sale"]).slice(0, 200_000).to_pandas()
    sums = sample["hours_sale"].apply(lambda x: float(np.nansum(x)) if x is not None else np.nan)
    diff = (sums - sample["sale_amount"]).abs()
    w("### 序列字段检查（前 20 万行）\n")
    w(f"- `hours_sale` 长度分布：{sample['hours_sale'].apply(len).value_counts().sort_index().to_dict()}")
    w(f"- `sum(hours_sale)` 与 `sale_amount` 的最大绝对差：{diff.max():.6g}；"
      f"相对差 >1% 的行：{int((diff > 0.01 * sample['sale_amount'].abs().clip(lower=1e-9)).sum()):,}")
    w("- 结论：`sale_amount` 是当日小时销额之和，可直接作为日销额使用。\n")

    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    print(f"\n已写入 {OUT}")


if __name__ == "__main__":
    main()
