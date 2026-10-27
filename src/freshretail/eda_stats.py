"""FreshRetailNet-50K 第 3 步：EDA 指标计算（供报告引用）。

输入：
    data/freshretail/train.parquet
    data/freshretail/clean_daily.parquet
    output/freshretail_sku_summary_all.csv
    output/sku_density.csv（DataCo 旧产物，只读，用于 CV 对比）

输出：
    output/freshretail_eda_stats.json

运行：
    .venv\\Scripts\\python.exe -I src\\freshretail\\eda_stats.py
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.compute as pc
import pyarrow.parquet as pq

T0 = time.time()


def log(msg: str) -> None:
    print(f"[{time.time() - T0:6.1f}s] {msg}", flush=True)

TRAIN = Path("data/freshretail/train.parquet")
CLEAN = Path("data/freshretail/clean_daily.parquet")
SUMMARY = Path("output/freshretail_sku_summary_all.csv")
DATACO_SKU = Path("output/sku_density.csv")
OUT = Path("output/freshretail_eda_stats.json")

COLS = [
    "store_id", "product_id", "dt", "sale_amount", "discount",
    "holiday_flag", "activity_flag", "avg_temperature",
]
KEYS = ["store_id", "product_id"]


def cv(s: pd.Series) -> float:
    m = s.mean()
    return float(s.std() / m) if m else float("nan")


def q(s: pd.Series, probs=(0, .1, .25, .5, .75, .9, 1)) -> dict:
    v = s.quantile(probs)
    return {f"p{int(p * 100)}": round(float(v.loc[p]), 4) for p in probs}


def dist(s: pd.Series) -> dict:
    d = q(s)
    d.update({"mean": round(float(s.mean()), 4), "std": round(float(s.std()), 4)})
    return d


def paired_flag_ratio(frame: pd.DataFrame, flag_col: str) -> dict:
    """同一条序列内，flag=1 日均销额 / flag=0 日均销额。"""
    rows = []
    for _, g in frame.groupby(KEYS, observed=True):
        a = g.loc[g[flag_col].astype(bool), "sale_amount"]
        b = g.loc[~g[flag_col].astype(bool), "sale_amount"]
        if len(a) >= 5 and len(b) >= 5 and b.mean() > 0:
            rows.append((a.mean() / b.mean(), a.mean(), b.mean()))
    r = np.array(rows)
    return {
        "series_with_both": int(len(rows)),
        "median_ratio": round(float(np.median(r[:, 0])), 4) if len(rows) else None,
        "mean_ratio": round(float(r[:, 0].mean()), 4) if len(rows) else None,
        "weighted_ratio": round(float(r[:, 1].sum() / r[:, 2].sum()), 4) if len(rows) else None,
        "share_ratio_above_1": round(float((r[:, 0] > 1).mean()), 4) if len(rows) else None,
        "share_ratio_below_1": round(float((r[:, 0] < 1).mean()), 4) if len(rows) else None,
    }


def date_flag_shares(frame: pd.DataFrame, flag_col: str) -> dict:
    per_date = frame.groupby("dt")[flag_col].mean()
    return {
        "date_share_any_flag1": round(float((per_date > 0).mean()), 4),
        "date_share_all_flag1": round(float((per_date == 1).mean()), 4),
        "mean_row_share_per_date": round(float(per_date.mean()), 4),
    }


def main() -> None:
    log("start")
    tr = pd.read_parquet(TRAIN, columns=COLS)
    tr["dt"] = pd.to_datetime(tr["dt"])
    tr["stockout_rate"] = (
        pd.read_parquet(TRAIN, columns=["stock_hour6_22_cnt"])["stock_hour6_22_cnt"]
        .astype("float64") / 16
    ).clip(0, 1).to_numpy()
    tr["discounted"] = tr["discount"] < 0.999

    panel = pd.read_parquet(CLEAN)
    panel["dt"] = pd.to_datetime(panel["dt"])
    panel["discounted"] = panel["discount"] < 0.999
    ptr = panel[panel["is_eval"] == 0].copy()
    pev = panel[panel["is_eval"] == 1].copy()
    summary = pd.read_csv(SUMMARY, encoding="utf-8-sig")
    log(f"loaded train={len(tr):,} panel={len(panel):,} summary={len(summary):,}")

    R: dict = {"source": "Dingdong-Inc, FreshRetailNet-50K, arXiv:2505.16319, CC BY 4.0"}

    # ---------- 3.1 需求波动 ----------
    all_daily = tr.groupby("dt")["sale_amount"].sum()
    panel_daily = ptr.groupby("dt")["sale_amount"].sum()
    R["cv"] = {
        "dataco_aggregate_daily_cv": 0.124,
        "all_series_aggregate_daily_cv": round(cv(all_daily), 4),
        "selected_aggregate_daily_cv": round(cv(panel_daily), 4),
        "all_series_cv_dist": dist(summary["cv"]),
        "selected_series_cv_dist": dist(summary.loc[summary["selected"] == 1, "cv"]),
        "all_share_cv_above_03": round(float((summary["cv"] > 0.3).mean()), 4),
        "all_share_cv_above_05": round(float((summary["cv"] > 0.5).mean()), 4),
    }
    if DATACO_SKU.exists():
        dc = pd.read_csv(DATACO_SKU, encoding="utf-8-sig")
        dc_cv = pd.to_numeric(dc["cv_active_day"], errors="coerce").dropna()
        R["cv"]["dataco_sku_cv_median"] = round(float(dc_cv.median()), 4)
        R["cv"]["dataco_sku_cv_mean"] = round(float(dc_cv.mean()), 4)
        R["cv"]["dataco_sku_count"] = int(len(dc_cv))

    top20 = summary.head(20).copy()
    R["top20_series"] = [
        {
            "rank": int(r.sales_rank),
            "store_id": int(r.store_id),
            "product_id": int(r.product_id),
            "third_category_id": int(r.third_category_id),
            "total_sale": round(float(r.total_sale), 2),
            "daily_mean": round(float(r.daily_mean), 4),
            "cv": round(float(r.cv), 4),
            "avg_stockout_rate": round(float(r.avg_stockout_rate), 4),
            "selected": int(r.selected),
        }
        for r in top20.itertuples()
    ]
    R["cv"]["top20_cv_dist"] = dist(top20["cv"])
    R["cv"]["top20_selected_count"] = int(top20["selected"].sum())
    R["cv"]["top20_sku_count"] = int(top20["product_id"].nunique())
    log("cv block done")

    # ---------- 3.2 周内效应与趋势 ----------
    wd_names = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
    wd = ptr.assign(wd=ptr["dt"].dt.day_of_week).groupby("wd")["sale_amount"].mean()
    wd_all = tr.assign(wd=tr["dt"].dt.day_of_week).groupby("wd")["sale_amount"].mean()
    R["weekday"] = {
        "selected": {wd_names[i]: round(float(wd.loc[i] / ptr["sale_amount"].mean()), 4) for i in range(7)},
        "all": {wd_names[i]: round(float(wd_all.loc[i] / tr["sale_amount"].mean()), 4) for i in range(7)},
    }
    first7 = ptr[ptr["dt"] <= ptr["dt"].min() + pd.Timedelta(days=6)]["sale_amount"].sum()
    last7 = ptr[ptr["dt"] >= ptr["dt"].max() - pd.Timedelta(days=6)]["sale_amount"].sum()
    x = np.arange(len(panel_daily))
    slope, intercept = np.polyfit(x, panel_daily.to_numpy(), 1)
    pred = slope * x + intercept
    ss_res = float(((panel_daily.to_numpy() - pred) ** 2).sum())
    ss_tot = float(((panel_daily.to_numpy() - panel_daily.mean()) ** 2).sum())
    R["trend"] = {
        "selected_first7_total": round(float(first7), 2),
        "selected_last7_total": round(float(last7), 2),
        "selected_last7_vs_first7_pct": round(float(last7 / first7 - 1), 4),
        "selected_slope_per_day_pct_of_mean": round(float(slope / panel_daily.mean()), 6),
        "selected_r2": round(1 - ss_res / ss_tot, 4),
        "days": int(len(panel_daily)),
        "forecast_task": "未来 7 天日销预测（数据仅 90 天，无法做年季节性）",
    }
    log("weekday/trend done")

    # ---------- 3.3 缺货画像 ----------
    R["stockout"] = {
        "row_rate_dist": dist(tr["stockout_rate"]),
        "row_zero_share": round(float((tr["stockout_rate"] == 0).mean()), 4),
        "row_gt_02_share": round(float((tr["stockout_rate"] > 0.2).mean()), 4),
        "row_gt_05_share": round(float((tr["stockout_rate"] > 0.5).mean()), 4),
        "series_avg_dist": dist(summary["avg_stockout_rate"]),
        "series_gt_02": int((summary["avg_stockout_rate"] > 0.2).sum()),
        "series_gt_05": int((summary["avg_stockout_rate"] > 0.5).sum()),
        "series_total": int(len(summary)),
        "selected_series_gt_02": int((summary.loc[summary["selected"] == 1, "avg_stockout_rate"] > 0.2).sum()),
        "note": "需求恢复（用无缺货小时推算截断需求）留待预测模块，本步只统计与标记。",
    }

    summary["so_q"] = pd.qcut(
        summary["avg_stockout_rate"], 4, labels=["Q1低", "Q2", "Q3", "Q4高"]
    )
    sd = summary.groupby("so_q", observed=True).agg(
        n=("store_id", "size"),
        so_mean=("avg_stockout_rate", "mean"),
        sale_mean=("daily_mean", "mean"),
        sale_median=("daily_mean", "median"),
        cv_median=("cv", "median"),
        disc_share=("discount_day_share", "mean"),
    )
    R["stockout"]["quartile_compare"] = {
        str(k): {c: round(float(v), 4) for c, v in row.items()} for k, row in sd.iterrows()
    }
    zero_share = (
        tr[KEYS + ["sale_amount"]]
        .assign(is_zero=tr["sale_amount"].eq(0).astype("float64"))
        .groupby(KEYS, observed=True)["is_zero"].mean()
        .rename("zero_day_share").reset_index()
    )
    summary = summary.merge(zero_share, on=KEYS, how="left")
    zq = summary.groupby("so_q", observed=True)["zero_day_share"].mean()
    R["stockout"]["zero_day_share_by_quartile"] = {str(k): round(float(v), 4) for k, v in zq.items()}

    # 高缺货组在“无缺货日”的销量，用于识别反向因果/选择效应
    labels = summary[KEYS + ["so_q"]]
    ns = (
        tr.loc[tr["stockout_rate"] == 0, KEYS + ["sale_amount"]]
        .merge(labels, on=KEYS, how="left")
        .groupby("so_q", observed=True)["sale_amount"].mean()
    )
    R["stockout"]["mean_sale_on_nonstockout_days_by_quartile"] = {
        str(k): round(float(v), 4) for k, v in ns.items()
    }
    log("stockout basic + nonstockout means done")

    # 全量 5 万序列：各缺货分位的“缺货日/非缺货日”销量比（同序列内）
    # 用 PyArrow 多线程分组，避免 450 万行 pandas 分组时的内存换页。
    tbl = pq.read_table(
        TRAIN, columns=["store_id", "product_id", "sale_amount", "stock_hour6_22_cnt"]
    )
    tbl = tbl.append_column(
        "has_stockout", pc.greater(tbl["stock_hour6_22_cnt"], 0)
    )
    aggt = tbl.group_by(
        ["store_id", "product_id", "has_stockout"], use_threads=True
    ).aggregate([("sale_amount", "mean"), ("sale_amount", "count")])
    a = aggt.to_pandas()
    a.columns = ["store_id", "product_id", "has_stockout", "mean", "n"]
    piv = a.pivot(index=KEYS, columns="has_stockout", values=["mean", "n"])
    piv.columns = [f"{kind}_{'yes' if flag else 'no'}" for kind, flag in piv.columns]
    ok = (piv["n_no"] >= 5) & (piv["n_yes"] >= 5) & (piv["mean_no"] > 0)
    ratio_df = piv[ok].copy()
    ratio_df["ratio"] = ratio_df["mean_yes"] / ratio_df["mean_no"]
    ratio_df = ratio_df.reset_index().merge(labels, on=KEYS, how="left")
    ratio_df["sales_yes"] = ratio_df["mean_yes"] * ratio_df["n_yes"]
    ratio_df["sales_no"] = ratio_df["mean_no"] * ratio_df["n_no"]
    qr = ratio_df.groupby("so_q", observed=True).agg(
        series=("ratio", "size"),
        median_ratio=("ratio", "median"),
        sales_yes=("sales_yes", "sum"),
        sales_no=("sales_no", "sum"),
        days_yes=("n_yes", "sum"),
        days_no=("n_no", "sum"),
    )
    # 按天加权的日均销量比，避免“缺货天数占比”把总销量比放大
    qr["weighted_ratio"] = (qr["sales_yes"] / qr["days_yes"]) / (qr["sales_no"] / qr["days_no"])
    R["stockout"]["within_series_ratio_by_quartile_all"] = {
        str(k): {c: round(float(row[c]), 4) for c in ["series", "median_ratio", "weighted_ratio"]}
        for k, row in qr.iterrows()
    }
    del tbl, aggt, a, piv, ratio_df, qr
    log("stockout quartile ratios done")

    # 同序列内：缺货日 vs 非缺货日（选中面板，训练段）
    ptr["has_stockout"] = ptr["stockout_rate"] > 0
    R["stockout"]["within_series_stockout_day_sales"] = paired_flag_ratio(ptr, "has_stockout")
    hi_keys = summary.loc[summary["avg_stockout_rate"] > 0.2, KEYS]
    hi = ptr.merge(hi_keys, on=KEYS, how="inner")
    R["stockout"]["high_series_within_ratio"] = paired_flag_ratio(hi, "has_stockout")
    log("stockout within-series done")

    # ---------- 3.4 外生变量 ----------
    def discount_effect(frame: pd.DataFrame) -> dict:
        d = frame.groupby("discounted")["sale_amount"].agg(["mean", "size"])
        ratio = (
            float(d.loc[True, "mean"] / d.loc[False, "mean"])
            if True in d.index and False in d.index else None
        )
        return {
            "discount_day_share": round(float(frame["discounted"].mean()), 4),
            "mean_sale_discounted": round(float(d.loc[True, "mean"]), 4),
            "mean_sale_regular": round(float(d.loc[False, "mean"]), 4),
            "multiple": round(ratio, 4) if ratio else None,
            **paired_flag_ratio(frame, "discounted"),
        }

    R["discount"] = {"all": discount_effect(tr), "selected": discount_effect(ptr)}

    def discount_bin_relative(frame: pd.DataFrame) -> dict:
        rel = frame["sale_amount"] / frame.groupby(KEYS, observed=True)["sale_amount"].transform("mean")
        bins = [0, .85, .9, .95, .99, 1.0, 1.09]
        cut = pd.cut(frame["discount"], bins=bins, right=False)
        g = pd.DataFrame({"rel": rel, "bin": cut}).groupby("bin", observed=True)["rel"].agg(["mean", "size"])
        return {str(k): {"rel_sale": round(float(r["mean"]), 4), "rows": int(r["size"])}
                for k, r in g.iterrows()}

    R["discount"]["within_series_by_bin_all"] = discount_bin_relative(tr)
    R["discount"]["within_series_by_bin_selected"] = discount_bin_relative(ptr)
    log("discount done")

    def temp_corr(frame: pd.DataFrame) -> dict:
        row_r = float(frame["avg_temperature"].corr(frame["sale_amount"]))
        daily = frame.groupby("dt").agg(sale=("sale_amount", "sum"), temp=("avg_temperature", "mean"))
        daily_r = float(daily["sale"].corr(daily["temp"]))
        grp = frame.groupby(KEYS, observed=True)
        s_dm = frame["sale_amount"] - grp["sale_amount"].transform("mean")
        t_dm = frame["avg_temperature"] - grp["avg_temperature"].transform("mean")
        within_r = float(s_dm.corr(t_dm))
        return {"row_pearson": round(row_r, 4), "daily_aggregate_pearson": round(daily_r, 4),
                "within_series_pearson": round(within_r, 4)}

    R["temperature"] = {"all": temp_corr(tr), "selected": temp_corr(ptr)}
    log("temperature done")

    def flag_effect(frame: pd.DataFrame, col: str) -> dict:
        g = frame.groupby(col)["sale_amount"].agg(["mean", "size"])
        ratio = float(g.loc[1, "mean"] / g.loc[0, "mean"]) if 1 in g.index and 0 in g.index else None
        return {
            "row_share_flag1": round(float((frame[col] == 1).mean()), 4),
            "mean_sale_flag1": round(float(g.loc[1, "mean"]), 4) if 1 in g.index else None,
            "mean_sale_flag0": round(float(g.loc[0, "mean"]), 4) if 0 in g.index else None,
            "multiple": round(ratio, 4) if ratio else None,
            **date_flag_shares(frame, col),
            **paired_flag_ratio(frame, col),
        }

    R["holiday"] = {"all": flag_effect(tr, "holiday_flag"), "selected": flag_effect(ptr, "holiday_flag")}
    R["activity"] = {"all": flag_effect(tr, "activity_flag"), "selected": flag_effect(ptr, "activity_flag")}

    hol = ptr.groupby(["dt", "holiday_flag"]).size().reset_index(name="n")
    hol["wd"] = hol["dt"].dt.day_of_week
    R["holiday"]["selected_flag1_weekday_counts"] = {
        wd_names[i]: int((hol.loc[(hol["holiday_flag"] == 1) & (hol["wd"] == i), "n"] > 0).sum())
        for i in range(7)
    }
    R["holiday"]["selected_flag1_dates"] = int(hol.loc[hol["holiday_flag"] == 1, "dt"].nunique())
    R["holiday"]["selected_flag0_dates"] = int(hol.loc[hol["holiday_flag"] == 0, "dt"].nunique())
    act_wd = ptr.assign(wd=ptr["dt"].dt.day_of_week, f=ptr["activity_flag"])
    R["activity"]["selected_flag1_weekday_share"] = {
        wd_names[i]: round(float(act_wd.loc[act_wd["wd"] == i, "f"].mean()), 4) for i in range(7)
    }
    log("holiday/activity done")

    # ---------- 3.5 eval 关系 ----------
    last7_train = ptr[ptr["dt"] >= ptr["dt"].max() - pd.Timedelta(days=6)]
    R["eval"] = {
        "train_start": str(tr["dt"].min().date()),
        "train_end": str(tr["dt"].max().date()),
        "eval_start": str(pev["dt"].min().date()),
        "eval_end": str(pev["dt"].max().date()),
        "eval_rows": int(len(pev)),
        "eval_series": int(pev.groupby(KEYS, observed=True).ngroups),
        "date_diff_days": int((pev["dt"].min() - tr["dt"].max()).days),
        "train_last7_daily_mean": round(float(last7_train["sale_amount"].mean()), 4),
        "eval_daily_mean": round(float(pev["sale_amount"].mean()), 4),
        "eval_vs_last7_pct": round(float(pev["sale_amount"].mean() / last7_train["sale_amount"].mean() - 1), 4),
        "validation_plan": "用 eval（is_eval=1，2024-06-26~07-02）做未来 7 天验证；train 90 天做训练/回测。",
    }

    log("eval done; writing json")
    OUT.write_text(json.dumps(R, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(R, ensure_ascii=False, indent=2))
    print(f"\n已写入 {OUT}")


if __name__ == "__main__":
    main()
