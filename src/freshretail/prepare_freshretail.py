"""FreshRetailNet-50K 第 2 步：清洗、筛选，落到“门店×SKU×日”面板。

筛选口径（对应任务书第 2.2 条，区间符号按验收线补全）：
    * 按 train 90 天总销额取 Top 50 个 SKU；
    * 每个 SKU 内按总销额取 Top 4 家门店；
    * 目标序列数 150~300，实际 200 条；
    * 覆盖不足 80 天的序列先剔除（本数据 train 每序列均 90 天，实际剔除 0 条）。

输出：
    data/freshretail/clean_daily.parquet
    output/freshretail_sku_summary_all.csv

运行：
    .venv\\Scripts\\python.exe -I src\\freshretail\\prepare_freshretail.py
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

TRAIN = Path("data/freshretail/train.parquet")
EVAL = Path("data/freshretail/eval.parquet")
OUT_PARQUET = Path("data/freshretail/clean_daily.parquet")
OUT_SUMMARY = Path("output/freshretail_sku_summary_all.csv")

N_TOP_SKU = 50
N_TOP_STORE = 4
MIN_COVER_DAYS = 80
STOCKOUT_HOURS = 16  # 6-22 点，任务书口径

READ_COLS = [
    "city_id", "store_id", "product_id", "third_category_id", "dt",
    "sale_amount", "discount", "holiday_flag", "activity_flag", "precpt",
    "avg_temperature", "avg_humidity", "stock_hour6_22_cnt",
]
OUT_COLS = [
    "dt", "city_id", "store_id", "product_id", "third_category_id",
    "sale_amount", "discount", "holiday_flag", "activity_flag", "precpt",
    "avg_temperature", "avg_humidity", "stockout_rate", "is_eval",
]


def load(path: Path) -> pd.DataFrame:
    df = pd.read_parquet(path, columns=READ_COLS)
    df["dt"] = pd.to_datetime(df["dt"])
    df["stockout_rate"] = (
        df["stock_hour6_22_cnt"].astype("float64") / STOCKOUT_HOURS
    ).clip(0.0, 1.0)
    return df.drop(columns=["stock_hour6_22_cnt"])


def build_summary(train: pd.DataFrame, selected_keys: pd.DataFrame) -> pd.DataFrame:
    """全量 5 万条序列画像（训练段 90 天）。"""
    g = train.groupby(["store_id", "product_id"], observed=True)
    out = g.agg(
        city_id=("city_id", "first"),
        third_category_id=("third_category_id", "first"),
        total_sale=("sale_amount", "sum"),
        daily_mean=("sale_amount", "mean"),
        daily_std=("sale_amount", "std"),
        coverage_days=("dt", "nunique"),
        avg_stockout_rate=("stockout_rate", "mean"),
        stockout_days=("stockout_rate", lambda s: int((s > 0).sum())),
        discount_day_share=("discount", lambda s: float((s < 0.999).mean())),
    ).reset_index()
    out["cv"] = (out["daily_std"] / out["daily_mean"]).replace([np.inf, -np.inf], np.nan)
    out["high_stockout"] = (out["avg_stockout_rate"] > 0.2).astype("int8")
    out = out.sort_values("total_sale", ascending=False).reset_index(drop=True)
    out["sales_rank"] = np.arange(1, len(out) + 1)
    sel = selected_keys.assign(selected=1)
    out = out.merge(sel, on=["store_id", "product_id"], how="left")
    out["selected"] = out["selected"].fillna(0).astype("int8")
    return out[[
        "sales_rank", "city_id", "store_id", "product_id", "third_category_id",
        "total_sale", "daily_mean", "cv", "coverage_days", "avg_stockout_rate",
        "high_stockout", "stockout_days", "discount_day_share", "selected",
    ]]


def main() -> None:
    print("读取 train ...")
    train = load(TRAIN)
    print(f"train rows={len(train):,}  dates={train['dt'].nunique()}")

    # ---- 一致性检查：门店->城市、SKU->三级品类 是否唯一 ----
    store_city = train.groupby("store_id")["city_id"].nunique()
    prod_cat = train.groupby("product_id")["third_category_id"].nunique()
    print(f"门店对应多个城市的数量: {int((store_city > 1).sum())}")
    print(f"SKU 对应多个三级品类的数量: {int((prod_cat > 1).sum())}")

    # ---- 2.2 覆盖天数筛选（train 90 天） ----
    cover = train.groupby(["store_id", "product_id"], observed=True)["dt"].nunique()
    eligible = cover[cover >= MIN_COVER_DAYS].index.to_frame(index=False)
    print(f"序列 {len(cover):,} 条；覆盖 >= {MIN_COVER_DAYS} 天 {len(eligible):,} 条；"
          f"剔除 {len(cover) - len(eligible):,} 条")

    train_el = train.merge(eligible, on=["store_id", "product_id"], how="inner")

    # ---- Top SKU -> 每 SKU Top 门店 ----
    sku_tot = train_el.groupby("product_id", observed=True)["sale_amount"].sum()
    top_skus = sku_tot.sort_values(ascending=False).head(N_TOP_SKU).index
    sub = train_el[train_el["product_id"].isin(top_skus)]
    store_tot = (
        sub.groupby(["product_id", "store_id"], observed=True)["sale_amount"]
        .sum().reset_index()
        .sort_values(["product_id", "sale_amount"], ascending=[True, False])
    )
    store_tot["rk"] = store_tot.groupby("product_id").cumcount() + 1
    selected_keys = store_tot[store_tot["rk"] <= N_TOP_STORE][["store_id", "product_id"]]
    n_series = len(selected_keys)
    print(f"Top {N_TOP_SKU} SKU × 每 SKU Top {N_TOP_STORE} 门店 -> {n_series} 条序列")
    if not 150 <= n_series <= 300:
        raise SystemExit(f"序列数 {n_series} 不在 150~300 验收区间，请调整筛选参数")

    train_sel = train_el.merge(selected_keys, on=["store_id", "product_id"], how="inner").copy()
    train_sel["is_eval"] = np.int8(0)

    # ---- eval：只保留同批序列，标记评估段 ----
    print("读取 eval ...")
    ev = load(EVAL)
    eval_sel = ev.merge(selected_keys, on=["store_id", "product_id"], how="inner").copy()
    eval_sel["is_eval"] = np.int8(1)

    panel = pd.concat([train_sel, eval_sel], ignore_index=True)
    panel = panel.sort_values(["store_id", "product_id", "dt"]).reset_index(drop=True)
    panel = panel[OUT_COLS]

    # ---- 验收自检 ----
    per_series_days = panel.groupby(["store_id", "product_id"], observed=True)["dt"].nunique()
    assert panel[OUT_COLS].isna().sum().sum() == 0
    assert 150 <= panel.groupby(["store_id", "product_id"], observed=True).ngroups <= 300
    assert per_series_days.min() >= MIN_COVER_DAYS
    assert panel.duplicated(["store_id", "product_id", "dt"]).sum() == 0
    assert panel["stockout_rate"].between(0, 1).all()

    OUT_PARQUET.parent.mkdir(parents=True, exist_ok=True)
    panel.to_parquet(OUT_PARQUET, index=False)
    print(f"已写入 {OUT_PARQUET}：{len(panel):,} 行 × {panel.shape[1]} 列，"
          f"{per_series_days.shape[0]} 条序列，覆盖 {per_series_days.min()}~{per_series_days.max()} 天")

    # ---- 2.5 全量序列画像 ----
    summary = build_summary(train, selected_keys)
    OUT_SUMMARY.parent.mkdir(parents=True, exist_ok=True)
    summary.to_csv(OUT_SUMMARY, index=False, encoding="utf-8-sig")
    all_nan = [c for c in summary.columns if summary[c].isna().all()]
    print(f"已写入 {OUT_SUMMARY}：{len(summary):,} 行；全 NaN 列 {all_nan or '无'}")
    print(f"选中序列: {int(summary['selected'].sum())}；"
          f"CV 中位数 {summary['cv'].median():.3f}；"
          f"平均缺货率>0.2 的序列 {int((summary['avg_stockout_rate'] > 0.2).sum()):,} 条")


if __name__ == "__main__":
    main()
