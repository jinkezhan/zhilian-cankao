#!/usr/bin/env python
"""DataCo Supply Chain 数据集探查脚本。

输出：
  1. 每列的名称、类型、缺失值比例、唯一值数量（output/column_profile.csv）
  2. 时间字段范围（output/time_range.csv）
  3. 商品/SKU、品类、日期维度的订单量 Top 10（output/top10_*.csv）
  4. 日/周/月整体需求趋势图（output/demand_trend_*.png）及聚合数据 CSV

用法：
  python app/explore_data.py
  python app/explore_data.py --input "D:\\ai-race data\\DataCoSupplyChainDataset.xlsx"
"""

from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import pandas as pd

BASE_DIR = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = Path(r"D:\ai-race data\DataCoSupplyChainDataset.xlsx")
DEFAULT_OUTPUT = BASE_DIR / "output"

ORDER_DATE = "order date (DateOrders)"
SHIP_DATE = "shipping date (DateOrders)"
ORDER_ID = "Order Id"
SKU_ID = "Product Card Id"
SKU_NAME = "Product Name"
CATEGORY = "Category Name"
QTY = "Order Item Quantity"
SHIPPING_MODES = ("Standard Class", "First Class", "Second Class", "Same Day")


def setup_chinese_font() -> None:
    """优先使用 Windows 自带中文字体，避免图中中文显示为方框。"""
    plt.rcParams["font.sans-serif"] = [
        "Microsoft YaHei",
        "SimHei",
        "Noto Sans CJK SC",
        "Arial Unicode MS",
    ]
    plt.rcParams["axes.unicode_minus"] = False


def load_data(path: Path) -> pd.DataFrame:
    print(f"读取数据：{path}")
    df = pd.read_excel(path, engine="openpyxl")
    print(f"读取完成，数据形状：{df.shape[0]:,} 行 × {df.shape[1]} 列")
    return df


def build_column_profile(df: pd.DataFrame) -> pd.DataFrame:
    profile = pd.DataFrame(
        {
            "column": df.columns,
            "dtype": df.dtypes.astype(str).values,
            "non_null_count": df.notna().sum().values,
            "missing_count": df.isna().sum().values,
            "missing_ratio": df.isna().mean().round(4).values,
            "unique_count": [df[c].nunique(dropna=True) for c in df.columns],
        }
    )
    return profile.sort_values("missing_ratio", ascending=False).reset_index(drop=True)


def parse_dates(df: pd.DataFrame) -> pd.DataFrame:
    for col in (ORDER_DATE, SHIP_DATE):
        df[col] = pd.to_datetime(
            df[col].astype("string"), format="%m/%d/%Y %H:%M", errors="coerce"
        )
    return df


def build_quality_report(df: pd.DataFrame) -> tuple[pd.Series, pd.DataFrame]:
    """识别因原始 Excel 字段错位而损坏的行。

    该文件中部分行存在字段整体左移（如商品名称列出现价格、商品状态列出现
    运输方式），若直接聚合会把销售额当成销量。这里用字段类型与取值范围
    做保守校验，只保留完整、可信的订单行参与后续分析。
    """
    qty_num = pd.to_numeric(df[QTY], errors="coerce")
    price_num = pd.to_numeric(df["Product Price"], errors="coerce")
    status_num = pd.to_numeric(df["Product Status"], errors="coerce")
    name_num = pd.to_numeric(df[SKU_NAME], errors="coerce")

    checks = pd.DataFrame(
        {
            "下单时间可解析": df[ORDER_DATE].notna(),
            "发货时间可解析": df[SHIP_DATE].notna(),
            "SKU 编码非空": df[SKU_ID].notna(),
            "商品名称为文本": df[SKU_NAME].notna() & name_num.isna(),
            "商品价格可转数值": price_num.notna(),
            "商品状态为 0/1": status_num.isin([0, 1]),
            "运输方式合法": df["Shipping Mode"].isin(SHIPPING_MODES),
            "数量在 1~10": qty_num.between(1, 10),
            "品类非空": df[CATEGORY].notna(),
        }
    )
    mask = checks.all(axis=1)
    report = pd.DataFrame(
        {
            "check": checks.columns,
            "passed_count": checks.sum().values,
            "failed_count": (~checks).sum().values,
            "failed_ratio": (1 - checks.mean()).round(4).values,
        }
    )
    return mask, report


def build_time_range(df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for col, label in ((ORDER_DATE, "下单时间"), (SHIP_DATE, "发货时间")):
        valid = df[col].dropna()
        rows.append(
            {
                "field": col,
                "label": label,
                "start": valid.min(),
                "end": valid.max(),
                "valid_count": len(valid),
                "invalid_count": int(df[col].isna().sum()),
                "span_days": (valid.max() - valid.min()).days,
            }
        )
    return pd.DataFrame(rows)


def top_skus(df: pd.DataFrame, n: int = 10) -> tuple[pd.DataFrame, pd.DataFrame]:
    work = df.copy()
    work[SKU_NAME] = work[SKU_NAME].fillna("(未知商品)")
    work[SKU_ID] = pd.to_numeric(work[SKU_ID], errors="coerce").astype("Int64")
    sku = (
        work.groupby([SKU_ID, SKU_NAME], dropna=False)
        .agg(
            demand_qty=(QTY, "sum"),
            order_count=(ORDER_ID, "nunique"),
            line_count=(ORDER_ID, "size"),
        )
        .reset_index()
        .sort_values("demand_qty", ascending=False)
    )
    sku.insert(0, "rank", range(1, len(sku) + 1))
    return sku.head(n), sku


def build_sku_density(df: pd.DataFrame) -> pd.DataFrame:
    """每个 SKU 的时间覆盖度，用于判断可用的预测颗粒度。"""
    n_days = df["date"].nunique()
    n_weeks = df["week"].nunique()
    n_months = df["month"].nunique()
    work = df.copy()
    work[SKU_ID] = pd.to_numeric(work[SKU_ID], errors="coerce").astype("Int64")

    g = (
        work.groupby([SKU_ID, SKU_NAME], dropna=False)
        .agg(
            demand_qty=(QTY, "sum"),
            line_count=(ORDER_ID, "size"),
            order_count=(ORDER_ID, "nunique"),
            active_days=("date", "nunique"),
            active_weeks=("week", "nunique"),
            active_months=("month", "nunique"),
        )
        .reset_index()
    )
    g["day_coverage"] = (g["active_days"] / n_days).round(4)
    g["week_coverage"] = (g["active_weeks"] / n_weeks).round(4)
    g["month_coverage"] = (g["active_months"] / n_months).round(4)
    g["avg_qty_per_active_day"] = (g["demand_qty"] / g["active_days"]).round(2)

    sku_day_demand = work.groupby([SKU_ID, "date"], observed=True)[QTY].sum()
    cv = sku_day_demand.groupby(level=0).agg(
        lambda s: s.std() / s.mean() if s.mean() else float("nan")
    )
    g["cv_active_day"] = g[SKU_ID].map(cv).round(3)
    return g.sort_values("demand_qty", ascending=False).reset_index(drop=True)


def top_categories(df: pd.DataFrame, n: int = 10) -> pd.DataFrame:
    cat = (
        df.groupby(CATEGORY, dropna=False)
        .agg(
            demand_qty=(QTY, "sum"),
            order_count=(ORDER_ID, "nunique"),
            sku_count=(SKU_ID, "nunique"),
            line_count=(ORDER_ID, "size"),
        )
        .reset_index()
        .sort_values("demand_qty", ascending=False)
    )
    cat.insert(0, "rank", range(1, len(cat) + 1))
    return cat.head(n)


def aggregate_demand(df: pd.DataFrame, freq: str) -> pd.DataFrame:
    """按给定频率聚合整体需求。freq: D / W / M。"""
    if freq == "D":
        key = "date"
    elif freq == "W":
        key = "week"
    elif freq == "M":
        key = "month"
    else:
        raise ValueError(freq)

    out = (
        df.groupby(key)
        .agg(
            demand_qty=(QTY, "sum"),
            order_count=(ORDER_ID, "nunique"),
            line_count=(ORDER_ID, "size"),
            days=("date", "nunique"),
        )
        .sort_index()
    )
    # 去掉窗口首尾不完整的时间桶，避免第一周/最后一周被低估
    if freq == "W":
        out = out[out["days"] >= 7]
    elif freq == "M":
        out = out[out["days"] >= 28]
    out = out.drop(columns="days")
    out.index.name = "period"
    return out.reset_index()


def build_monthly_structure(df: pd.DataFrame) -> pd.DataFrame:
    """按月检查订单结构，识别每单行数/每行件数异常的月份。"""
    monthly = (
        df.groupby("month")
        .agg(
            rows=(ORDER_ID, "size"),
            orders=(ORDER_ID, "nunique"),
            demand_qty=(QTY, "sum"),
        )
        .reset_index()
    )
    monthly["lines_per_order"] = (monthly["rows"] / monthly["orders"]).round(2)
    monthly["qty_per_line"] = (monthly["demand_qty"] / monthly["rows"]).round(2)
    monthly["regime_anomaly"] = (monthly["lines_per_order"] < 1.5) | (monthly["qty_per_line"] < 1.5)
    return monthly


def top_dates(daily: pd.DataFrame, n: int = 10) -> pd.DataFrame:
    out = daily.sort_values("demand_qty", ascending=False).head(n).copy()
    out.insert(0, "rank", range(1, len(out) + 1))
    out["period"] = out["period"].dt.strftime("%Y-%m-%d")
    return out.reset_index(drop=True)


def save_plots(
    daily: pd.DataFrame,
    weekly: pd.DataFrame,
    monthly: pd.DataFrame,
    out_dir: Path,
    prefix: str = "demand_trend",
    anomaly_start: pd.Timestamp | None = None,
    title_suffix: str = "",
) -> None:
    setup_chinese_font()
    series = [
        (daily, "按日", "日", f"{prefix}_daily.png", True),
        (weekly, "按周", "周", f"{prefix}_weekly.png", False),
        (monthly, "按月", "月", f"{prefix}_monthly.png", False),
    ]

    def draw(ax: plt.Axes, frame: pd.DataFrame, title: str, label: str, with_ma: bool, small: bool) -> None:
        x = frame["period"]
        ax.plot(x, frame["demand_qty"], color="#2a6fdb", linewidth=0.8, alpha=0.55, label=f"{label}需求")
        if with_ma:
            ma = frame["demand_qty"].rolling(7, min_periods=1).mean()
            ax.plot(x, ma, color="#d9480f", linewidth=1.8, label="7期移动平均")
        if anomaly_start is not None and x.max() >= anomaly_start:
            ax.axvspan(anomaly_start, x.max(), color="#f4cccc", alpha=0.45, label="结构突变期")
        ax.set_title(f"整体需求趋势（{title}{title_suffix}）", fontsize=12 if small else 14)
        ax.set_ylabel("订单商品件数")
        ax.grid(alpha=0.25)
        ax.legend(loc="upper left")
        ax.xaxis.set_major_locator(mdates.AutoDateLocator())
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))

    for frame, title, label, filename, with_ma in series:
        fig, ax = plt.subplots(figsize=(13, 4.8))
        draw(ax, frame, title, label, with_ma, small=False)
        ax.set_xlabel("日期")
        fig.autofmt_xdate()
        fig.tight_layout()
        fig.savefig(out_dir / filename, dpi=150)
        plt.close(fig)

    # 三联图
    fig, axes = plt.subplots(3, 1, figsize=(13, 13))
    for ax, (frame, title, label, _, with_ma) in zip(axes, series):
        draw(ax, frame, title, label, with_ma, small=True)
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(out_dir / f"{prefix}_combined.png", dpi=150)
    plt.close(fig)


def compute_diagnostics(
    df: pd.DataFrame,
    daily: pd.DataFrame,
    weekly: pd.DataFrame,
    monthly: pd.DataFrame,
    sku: pd.DataFrame,
    n_columns: int | None = None,
) -> dict:
    total_qty = int(df[QTY].sum())
    top10_share = float(sku.head(10)["demand_qty"].sum() / total_qty) if total_qty else float("nan")

    n_days = daily.shape[0]
    n_weeks = weekly.shape[0]
    n_skus = df[SKU_ID].nunique()
    n_categories = df[CATEGORY].nunique()
    sku_day_counts = df.groupby([SKU_ID, "date"], observed=True).size()
    observed_sku_days = int(sku_day_counts.shape[0])
    sku_week_counts = df.groupby([SKU_ID, "week"], observed=True).size()
    observed_sku_weeks = int(sku_week_counts.shape[0])
    cat_day_counts = df.groupby([CATEGORY, "date"], observed=True).size()
    observed_cat_days = int(cat_day_counts.shape[0])

    def cv(frame: pd.DataFrame) -> float:
        s = frame["demand_qty"]
        return float(s.std() / s.mean()) if s.mean() else float("nan")

    return {
        "rows": int(df.shape[0]),
        "columns": int(n_columns if n_columns is not None else df.shape[1]),
        "orders": int(df[ORDER_ID].nunique()),
        "order_lines": int(df.shape[0]),
        "skus": int(n_skus),
        "categories": int(n_categories),
        "avg_lines_per_order": round(df.shape[0] / df[ORDER_ID].nunique(), 2),
        "active_days": int(n_days),
        "active_weeks": int(n_weeks),
        "calendar_days": int((daily["period"].max() - daily["period"].min()).days + 1),
        "observed_sku_days": observed_sku_days,
        "possible_sku_days": int(n_skus * n_days),
        "sku_day_coverage": round(observed_sku_days / (n_skus * n_days), 4) if n_skus and n_days else float("nan"),
        "observed_sku_weeks": observed_sku_weeks,
        "possible_sku_weeks": int(n_skus * n_weeks),
        "sku_week_coverage": round(observed_sku_weeks / (n_skus * n_weeks), 4) if n_skus and n_weeks else float("nan"),
        "median_active_days_per_sku": int(sku_day_counts.groupby(level=0).size().median()),
        "median_active_weeks_per_sku": int(sku_week_counts.groupby(level=0).size().median()),
        "avg_lines_per_sku_day": round(df.shape[0] / observed_sku_days, 2) if observed_sku_days else float("nan"),
        "observed_category_days": observed_cat_days,
        "category_day_coverage": round(observed_cat_days / (n_categories * n_days), 4) if n_categories and n_days else float("nan"),
        "avg_lines_per_category_day": round(df.shape[0] / observed_cat_days, 2) if observed_cat_days else float("nan"),
        "total_demand_qty": total_qty,
        "top10_sku_share": round(top10_share, 4),
        "daily_cv": round(cv(daily), 3),
        "weekly_cv": round(cv(weekly), 3),
        "monthly_cv": round(cv(monthly), 3),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="DataCo Supply Chain 数据探查")
    parser.add_argument("--input", default=str(DEFAULT_INPUT), help="Excel 数据文件路径")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT), help="输出目录")
    parser.add_argument("--top-n", type=int, default=10, help="Top N 条数")
    args = parser.parse_args()

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    df = load_data(Path(args.input))
    df = parse_dates(df)

    # ---------- 1. 列画像 ----------
    profile = build_column_profile(df)
    profile.to_csv(out_dir / "column_profile.csv", index=False, encoding="utf-8-sig")
    print("\n========== 1. 字段画像 ==========")
    print(profile.to_string(index=False))

    # ---------- 2. 时间范围 ----------
    time_range = build_time_range(df)
    time_range.to_csv(out_dir / "time_range.csv", index=False, encoding="utf-8-sig")
    print("\n========== 2. 时间字段范围 ==========")
    print(time_range.to_string(index=False))

    # ---------- 数据质量校验 ----------
    clean_mask, quality_report = build_quality_report(df)
    quality_report.to_csv(out_dir / "data_quality.csv", index=False, encoding="utf-8-sig")
    analysis = df.loc[clean_mask].copy()
    excluded = len(df) - len(analysis)
    print("\n========== 数据质量校验 ==========")
    print(f"原始 {len(df):,} 行；通过校验 {len(analysis):,} 行；剔除 {excluded:,} 行（{excluded / len(df):.2%}）")
    print(quality_report.to_string(index=False))
    if analysis.empty:
        raise SystemExit("没有通过质量校验的数据行，请检查输入文件。")

    # 派生时间粒度（仅在干净数据上）
    analysis["date"] = analysis[ORDER_DATE].dt.normalize()
    analysis["week"] = analysis[ORDER_DATE].dt.to_period("W").dt.start_time
    analysis["month"] = analysis[ORDER_DATE].dt.to_period("M").dt.start_time

    # ---------- 订单结构检查 ----------
    structure = build_monthly_structure(analysis)
    structure.to_csv(out_dir / "monthly_structure.csv", index=False, encoding="utf-8-sig")
    anomaly_months = structure.loc[structure["regime_anomaly"], "month"]
    stable = analysis[~analysis["month"].isin(anomaly_months)].copy()
    if stable.empty:
        stable = analysis.copy()
    anomaly_start = anomaly_months.min() if len(anomaly_months) else None

    print("\n========== 3. 订单结构检查 ==========")
    print(structure.tail(8).to_string(index=False))
    if len(anomaly_months):
        print("检测到结构突变月份：" + "、".join(m.strftime("%Y-%m") for m in anomaly_months))
        print(
            f"主分析窗口：{stable[ORDER_DATE].min():%Y-%m-%d} ~ "
            f"{stable[ORDER_DATE].max():%Y-%m-%d}（剔除 {len(analysis) - len(stable):,} 行）"
        )
        print("这些月份每单仅约 1 行、每行约 1 件，且仅覆盖美国/波多黎各，统计口径与前期不同。")
    else:
        print("未检测到月度结构突变。")

    # ---------- 4. 维度 Top 10（稳定期） ----------
    top_sku_df, sku_all = top_skus(stable, args.top_n)
    top_sku_df.to_csv(out_dir / "top10_sku.csv", index=False, encoding="utf-8-sig")
    sku_all.to_csv(out_dir / "sku_summary_all.csv", index=False, encoding="utf-8-sig")

    sku_density = build_sku_density(stable)
    sku_density.to_csv(out_dir / "sku_density.csv", index=False, encoding="utf-8-sig")

    top_cat_df = top_categories(stable, args.top_n)
    top_cat_df.to_csv(out_dir / "top10_category.csv", index=False, encoding="utf-8-sig")

    stable_daily = aggregate_demand(stable, "D")
    stable_weekly = aggregate_demand(stable, "W")
    stable_monthly = aggregate_demand(stable, "M")
    for frame, name in (
        (stable_daily, "demand_daily.csv"),
        (stable_weekly, "demand_weekly.csv"),
        (stable_monthly, "demand_monthly.csv"),
    ):
        frame.to_csv(out_dir / name, index=False, encoding="utf-8-sig")

    top_date_df = top_dates(stable_daily, args.top_n)
    top_date_df.to_csv(out_dir / "top10_dates.csv", index=False, encoding="utf-8-sig")

    print("\n========== 4a. 商品/SKU 需求量 Top 10（稳定期） ==========")
    print(top_sku_df.to_string(index=False))
    print("\n========== 4b. 品类需求量 Top 10（稳定期） ==========")
    print(top_cat_df.to_string(index=False))
    print("\n========== 4c. 日期需求量 Top 10（稳定期） ==========")
    print(top_date_df.to_string(index=False))

    density_cols = [
        SKU_ID, SKU_NAME, "demand_qty", "active_days", "day_coverage",
        "active_weeks", "week_coverage", "cv_active_day",
    ]
    print("\n========== 4d. 头部 SKU 时间覆盖度 ==========")
    print(sku_density.head(args.top_n)[density_cols].to_string(index=False))

    # ---------- 5. 趋势图 ----------
    full_daily = aggregate_demand(analysis, "D")
    full_weekly = aggregate_demand(analysis, "W")
    full_monthly = aggregate_demand(analysis, "M")
    save_plots(
        full_daily, full_weekly, full_monthly, out_dir,
        prefix="demand_trend", anomaly_start=anomaly_start, title_suffix="（全量）",
    )
    save_plots(
        stable_daily, stable_weekly, stable_monthly, out_dir,
        prefix="demand_trend_stable", title_suffix="（稳定期）",
    )
    print(f"\n趋势图已保存至：{out_dir}")
    for name in (
        "demand_trend_daily.png", "demand_trend_weekly.png",
        "demand_trend_monthly.png", "demand_trend_combined.png",
        "demand_trend_stable_daily.png", "demand_trend_stable_weekly.png",
        "demand_trend_stable_monthly.png", "demand_trend_stable_combined.png",
    ):
        print(f"  - {name}")

    # ---------- 诊断指标（稳定期） ----------
    diag = compute_diagnostics(stable, stable_daily, stable_weekly, stable_monthly, sku_all, n_columns=len(profile))
    print("\n========== 4. 颗粒度诊断 ==========")
    for key, value in diag.items():
        print(f"{key}: {value}")

    with open(out_dir / "exploration_summary.txt", "w", encoding="utf-8") as f:
        f.write("DataCo Supply Chain 数据探查摘要\n")
        f.write("=" * 40 + "\n")
        f.write(f"原始数据：{len(df):,} 行 × {diag['columns']} 列\n")
        f.write(f"质量校验通过：{len(analysis):,} 行；剔除损坏行：{excluded:,} 行（{excluded / len(df):.2%}）\n")
        f.write("剔除原因详见 data_quality.csv（部分行存在字段错位/日期不可解析）\n")
        if len(anomaly_months):
            f.write(
                "结构突变月份：" + "、".join(m.strftime("%Y-%m") for m in anomaly_months)
                + f"；主分析窗口：{stable[ORDER_DATE].min():%Y-%m-%d} ~ {stable[ORDER_DATE].max():%Y-%m-%d}\n"
            )
            f.write("结构突变期每单仅约 1 行、每行约 1 件，仅覆盖美国/波多黎各，已从趋势与颗粒度分析中剔除。\n")
        f.write(f"订单数：{diag['orders']:,}；订单行数：{diag['order_lines']:,}\n")
        f.write(f"SKU 数：{diag['skus']:,}；品类数：{diag['categories']:,}\n")
        f.write(f"下单时间范围：{time_range.iloc[0]['start']} ~ {time_range.iloc[0]['end']}\n")
        f.write(f"发货时间范围：{time_range.iloc[1]['start']} ~ {time_range.iloc[1]['end']}\n")
        f.write(f"有销售日期数：{diag['active_days']:,}；有销售周数：{diag['active_weeks']:,}\n")
        f.write(f"SKU-日覆盖率：{diag['sku_day_coverage']:.2%}；SKU-周覆盖率：{diag['sku_week_coverage']:.2%}\n")
        f.write(f"SKU 活跃天数中位数：{diag['median_active_days_per_sku']} 天；活跃周数中位数：{diag['median_active_weeks_per_sku']} 周\n")
        f.write(f"品类-日覆盖率：{diag['category_day_coverage']:.2%}；品类日均订单行数：{diag['avg_lines_per_category_day']}\n")
        f.write(f"Top10 SKU 需求量占比：{diag['top10_sku_share']:.2%}\n")
        f.write(f"需求变异系数 CV：日 {diag['daily_cv']}；周 {diag['weekly_cv']}；月 {diag['monthly_cv']}\n")
        f.write("\n详见同目录下 column_profile.csv / data_quality.csv / monthly_structure.csv /\n")
        f.write("top10_*.csv / sku_density.csv / demand_*.csv 及趋势图。\n")


if __name__ == "__main__":
    main()
