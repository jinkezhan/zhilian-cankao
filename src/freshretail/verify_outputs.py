"""第 2 步产物验收自检。"""
from __future__ import annotations

import pandas as pd

CLEAN = "data/freshretail/clean_daily.parquet"
SUMMARY = "output/freshretail_sku_summary_all.csv"
KEYS = ["store_id", "product_id"]

p = pd.read_parquet(CLEAN)
s = pd.read_csv(SUMMARY, encoding="utf-8-sig")
print("clean columns:", list(p.columns))
print("clean dtypes:\n", p.dtypes.to_string())
print(f"rows={len(p):,} series={p.groupby(KEYS).ngroups} "
      f"dates={p['dt'].nunique()} {p['dt'].min()} ~ {p['dt'].max()}")
print("rows by is_eval:", p["is_eval"].value_counts().sort_index().to_dict())
print("cities:", p["city_id"].nunique(), "products:", p["product_id"].nunique(),
      "third_categories:", p["third_category_id"].nunique())
days = p.groupby(KEYS)["dt"].nunique()
print("series coverage min/max:", int(days.min()), int(days.max()))
print("null cells:", int(p.isna().sum().sum()), "dupes:", int(p.duplicated(KEYS + ["dt"]).sum()))
print("stockout_rate min/max:", float(p["stockout_rate"].min()), float(p["stockout_rate"].max()))
print("selected series avg stockout>0.2:",
      int((p.groupby(KEYS)["stockout_rate"].mean() > 0.2).sum()))
print("sale_amount min/mean/max:", float(p["sale_amount"].min()),
      round(float(p["sale_amount"].mean()), 4), float(p["sale_amount"].max()))
print("discount <1 share:", round(float((p["discount"] < 0.999).mean()), 4))
print("\nsummary columns:", list(s.columns))
print("summary rows:", len(s), "selected:", int(s["selected"].sum()))
print("summary all-NaN columns:", [c for c in s.columns if s[c].isna().all()])
print("summary null counts:\n", s.isna().sum().to_string())
print("summary dtypes:\n", s.dtypes.to_string())
print("avg_stockout>0.2:", int((s["avg_stockout_rate"] > 0.2).sum()))
print("coverage days distribution:", s["coverage_days"].value_counts().to_dict())
print("\ntop 10 selected series:\n",
      s[s["selected"] == 1].head(10).to_string(index=False))
