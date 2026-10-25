"""需求预测基线：移动平均 / 季节朴素 / 朴素法。

预测对象：周度需求量（Order Item Quantity 之和），Top10 SKU。
验证方式：最后 12 个完整周做留出集，逐周一步预测，评估 MAE 与 WMAPE。

用法：
    .venv\\Scripts\\python.exe app\\forecast_baseline.py

输出：
    output/forecast_baseline_predictions.csv
    output/forecast_baseline_metrics_long.csv
    output/forecast_baseline_metrics_wide.csv
    output/forecast_baseline.png
    output/forecast_baseline_summary.txt
"""

from __future__ import annotations

from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import pandas as pd

from forecast_common import (
    DATA_PATH,
    MODEL_LABELS,
    MODEL_ORDER,
    OUTPUT_DIR,
    VAL_WEEKS,
    add_baselines,
    build_panel,
    evaluate_predictions,
    format_pivot,
    load_clean_orders,
    pivot_metrics,
    save_metrics,
    setup_plot_style,
)

BASELINE_MODELS = ["ma4", "ma8", "sn", "naive"]


def _date_range(weeks: list[pd.Timestamp]) -> str:
    return f"{weeks[0]:%Y-%m-%d} ~ {weeks[-1] + pd.Timedelta(days=6):%Y-%m-%d}"


def plot_baselines(val: pd.DataFrame, sku_info: pd.DataFrame, out_path: Path) -> None:
    setup_plot_style()
    fig, axes = plt.subplots(2, 2, figsize=(14, 8.5))
    axes = axes.ravel()

    total = val.groupby("week")[["actual", "ma4", "ma8", "sn"]].sum().sort_index()
    ax = axes[0]
    ax.plot(total.index, total["actual"], color="#111111", linewidth=2.0, label="实际")
    ax.plot(total.index, total["ma4"], color="#2a6fdb", linewidth=1.5, label=MODEL_LABELS["ma4"])
    ax.plot(total.index, total["ma8"], color="#2f9e44", linewidth=1.3, label=MODEL_LABELS["ma8"])
    ax.plot(total.index, total["sn"], color="#d9480f", linewidth=1.3, label=MODEL_LABELS["sn"])
    ax.set_title("Top10 合计周需求")
    ax.set_ylabel("件数")
    ax.legend(fontsize=9)
    ax.grid(alpha=0.25)

    sku_ids = list(sku_info.index[:3]) + [int(sku_info.index[-1])]
    for ax, sku_id in zip(axes[1:], sku_ids):
        g = val[val["sku_id"] == sku_id].sort_values("week")
        name = str(g["sku_name"].iloc[0])
        ax.plot(g["week"], g["actual"], color="#111111", linewidth=2.0, label="实际")
        ax.plot(g["week"], g["ma4"], color="#2a6fdb", linewidth=1.5, label=MODEL_LABELS["ma4"])
        ax.plot(g["week"], g["sn"], color="#d9480f", linewidth=1.3, label=MODEL_LABELS["sn"])
        ax.set_title(name, fontsize=10)
        ax.set_ylabel("件数")
        ax.legend(fontsize=8)
        ax.grid(alpha=0.25)

    for ax in axes:
        ax.xaxis.set_major_locator(mdates.WeekdayLocator(interval=2))
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d"))
        ax.tick_params(axis="x", labelrotation=30)

    fig.suptitle("基线模型：最后 12 个完整周的滚动一步预测", fontsize=14)
    fig.tight_layout()
    fig.savefig(out_path, dpi=150)
    plt.close(fig)


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    raw_rows = int(pd.read_parquet(DATA_PATH).shape[0])
    clean_orders = load_clean_orders()
    panel, sku_info, train_weeks, val_weeks = build_panel()

    panel = add_baselines(panel)
    val = panel[panel["week"].isin(val_weeks)].copy()
    val = val.rename(columns={"qty": "actual"})
    val = val[["week", "sku_id", "sku_name", "category", "actual", "ma4", "ma8", "sn", "naive"]]

    metrics = evaluate_predictions(val, BASELINE_MODELS)
    pivot = pivot_metrics(metrics, sku_info)
    save_metrics(metrics, pivot, "forecast_baseline")

    val.to_csv(
        OUTPUT_DIR / "forecast_baseline_predictions.csv",
        index=False,
        encoding="utf-8-sig",
    )
    plot_baselines(val, sku_info, OUTPUT_DIR / "forecast_baseline.png")

    display = format_pivot(pivot)
    print("========== 需求预测基线（周 × Top10 SKU） ==========")
    print(
        f"数据：clean_main {raw_rows:,} 行 -> 质量过滤后 {len(clean_orders):,} 行；"
        f"完整周 {len(train_weeks) + len(val_weeks)}（训练 {len(train_weeks)} / 验证 {len(val_weeks)}）"
    )
    print(f"训练窗口：{_date_range(train_weeks)}")
    print(f"验证窗口：{_date_range(val_weeks)}")
    print("\n对比表（MAE / WMAPE）：")
    print(display.to_string(index=False))

    overall = metrics[(metrics["level"] == "overall")]
    print("\nTop10 全部 SKU-周 WMAPE：")
    for model in BASELINE_MODELS:
        row = overall[overall["model"] == model].iloc[0]
        print(f"  {MODEL_LABELS[model]:<14} MAE={row['mae']:.2f}  WMAPE={row['wmape']:.2%}")

    summary_path = OUTPUT_DIR / "forecast_baseline_summary.txt"
    with open(summary_path, "w", encoding="utf-8") as f:
        f.write("需求预测基线摘要（周 × Top10 SKU）\n")
        f.write("=" * 48 + "\n")
        f.write(f"数据源：{DATA_PATH}\n")
        f.write(f"clean_main：{raw_rows:,} 行；质量过滤后：{len(clean_orders):,} 行\n")
        f.write(f"完整周数：{len(train_weeks) + len(val_weeks)}；训练：{len(train_weeks)} 周；验证：{len(val_weeks)} 周\n")
        f.write(f"训练窗口：{_date_range(train_weeks)}\n")
        f.write(f"验证窗口：{_date_range(val_weeks)}\n")
        f.write(f"SKU 数：{len(sku_info)}；预测模型：{', '.join(MODEL_LABELS[m] for m in BASELINE_MODELS)}\n\n")
        f.write("对比表（MAE / WMAPE）：\n")
        f.write(display.to_string(index=False))
        f.write("\n\n说明：\n")
        f.write("- 每周以周一为起点；缺失周补 0。\n")
        f.write("- MA4/MA8 使用验证周之前 4/8 周的真实需求均值。\n")
        f.write("- 季节朴素使用 52 周前同周需求；历史不足时退化为 MA4。\n")
        f.write("- WMAPE = sum(|实际-预测|) / sum(实际)，按对应 scope 汇总。\n")
        f.write("- 若某个 scope 在验证期实际需求为 0（如 SKU 116），WMAPE 无定义，表中记为“—”。\n")
        f.write("- 这是滚动一步预测；多步预测误差通常会更大。\n")

    print(f"\n已输出：{OUTPUT_DIR / 'forecast_baseline.png'}")
    print(f"已输出：{OUTPUT_DIR / 'forecast_baseline_metrics_wide.csv'}")
    print(f"已输出：{summary_path}")


if __name__ == "__main__":
    main()
