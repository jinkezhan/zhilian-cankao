"""LightGBM 需求预测：周 × Top10 SKU，滚动一步验证。

依赖基线脚本先生成 output/forecast_baseline_predictions.csv。
模型为全局面板模型（10 个 SKU 一起训练），SKU 与品类作为类别特征；
特征只使用预测周之前的信息，避免未来数据泄漏。

用法：
    .venv\\Scripts\\python.exe app\\forecast_lightgbm.py

输出：
    output/forecast_lightgbm_predictions.csv
    output/forecast_comparison_metrics_long.csv
    output/forecast_comparison_metrics_wide.csv
    output/forecast_lightgbm.png
    output/forecast_comparison.png
    output/forecast_feature_importance.csv
    output/forecast_lightgbm_summary.txt
    models/lgbm_top10_weekly.txt
"""

from __future__ import annotations

from pathlib import Path

import lightgbm as lgb
import matplotlib
matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from forecast_common import (
    CATEGORICAL_COLS,
    DATA_PATH,
    FEATURE_COLS,
    MODEL_LABELS,
    MODEL_ORDER,
    MODEL_DIR,
    OUTPUT_DIR,
    VAL_WEEKS,
    add_features,
    build_panel,
    evaluate_predictions,
    format_pivot,
    load_clean_orders,
    pivot_metrics,
    save_metrics,
    setup_plot_style,
)

PREDICTION_PATH = OUTPUT_DIR / "forecast_baseline_predictions.csv"
MODELS = ["ma4", "ma8", "sn", "naive", "lgbm"]

PARAMS = {
    "objective": "regression_l1",
    "metric": "mae",
    "learning_rate": 0.03,
    "num_leaves": 15,
    "min_data_in_leaf": 20,
    "feature_fraction": 0.8,
    "bagging_fraction": 0.9,
    "bagging_freq": 1,
    "lambda_l2": 1.0,
    "verbosity": -1,
    "seed": 42,
    "num_threads": 1,
}


def _date_range(weeks: list[pd.Timestamp]) -> str:
    return f"{weeks[0]:%Y-%m-%d} ~ {weeks[-1] + pd.Timedelta(days=6):%Y-%m-%d}"


def train_model(train: pd.DataFrame, val: pd.DataFrame) -> tuple[lgb.Booster, int]:
    """训练全局 LightGBM；用训练集末尾 12 周做早停，再全量重训。"""
    train_weeks = sorted(train["week"].unique())
    inner_val_weeks = set(train_weeks[-VAL_WEEKS:])
    inner_train = train[~train["week"].isin(inner_val_weeks)]
    inner_val = train[train["week"].isin(inner_val_weeks)]

    dtrain = lgb.Dataset(
        inner_train[FEATURE_COLS],
        label=inner_train["qty"],
        categorical_feature=CATEGORICAL_COLS,
        free_raw_data=False,
    )
    dvalid = lgb.Dataset(
        inner_val[FEATURE_COLS],
        label=inner_val["qty"],
        categorical_feature=CATEGORICAL_COLS,
        reference=dtrain,
        free_raw_data=False,
    )
    probe = lgb.train(
        PARAMS,
        dtrain,
        num_boost_round=1000,
        valid_sets=[dvalid],
        callbacks=[lgb.early_stopping(50, verbose=False), lgb.log_evaluation(0)],
    )
    best_iter = int(probe.best_iteration or 300)
    best_iter = max(best_iter, 20)

    final = lgb.train(
        PARAMS,
        lgb.Dataset(
            train[FEATURE_COLS],
            label=train["qty"],
            categorical_feature=CATEGORICAL_COLS,
            free_raw_data=False,
        ),
        num_boost_round=best_iter,
    )
    return final, best_iter


def plot_forecasts(val: pd.DataFrame, sku_info: pd.DataFrame, out_path: Path) -> None:
    setup_plot_style()
    fig, axes = plt.subplots(2, 2, figsize=(14, 8.5))
    axes = axes.ravel()

    total = val.groupby("week")[["actual", "ma4", "sn", "lgbm"]].sum().sort_index()
    ax = axes[0]
    ax.plot(total.index, total["actual"], color="#111111", linewidth=2.0, label="实际")
    ax.plot(total.index, total["ma4"], color="#2a6fdb", linewidth=1.4, label=MODEL_LABELS["ma4"])
    ax.plot(total.index, total["sn"], color="#d9480f", linewidth=1.2, label=MODEL_LABELS["sn"])
    ax.plot(total.index, total["lgbm"], color="#2f9e44", linewidth=1.8, label=MODEL_LABELS["lgbm"])
    ax.set_title("Top10 合计周需求")
    ax.set_ylabel("件数")
    ax.legend(fontsize=9)
    ax.grid(alpha=0.25)

    for ax, sku_id in zip(axes[1:], list(sku_info.index[:3])):
        g = val[val["sku_id"] == sku_id].sort_values("week")
        ax.plot(g["week"], g["actual"], color="#111111", linewidth=2.0, label="实际")
        ax.plot(g["week"], g["ma4"], color="#2a6fdb", linewidth=1.4, label=MODEL_LABELS["ma4"])
        ax.plot(g["week"], g["lgbm"], color="#2f9e44", linewidth=1.8, label=MODEL_LABELS["lgbm"])
        ax.set_title(str(g["sku_name"].iloc[0]), fontsize=10)
        ax.set_ylabel("件数")
        ax.legend(fontsize=8)
        ax.grid(alpha=0.25)

    for ax in axes:
        ax.xaxis.set_major_locator(mdates.WeekdayLocator(interval=2))
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d"))
        ax.tick_params(axis="x", labelrotation=30)

    fig.suptitle("LightGBM 与基线：最后 12 个完整周的滚动一步预测", fontsize=14)
    fig.tight_layout()
    fig.savefig(out_path, dpi=150)
    plt.close(fig)


def plot_comparison(metrics: pd.DataFrame, out_path: Path) -> None:
    setup_plot_style()
    fig, axes = plt.subplots(1, 2, figsize=(13, 4.6))
    for ax, level, title in zip(
        axes,
        ["overall", "total"],
        ["Top10 全部 SKU-周", "Top10 合计（按周）"],
    ):
        sub = metrics[metrics["level"] == level].set_index("model").reindex(MODELS)
        labels = [MODEL_LABELS[m] for m in MODELS]
        values = sub["wmape"].to_numpy() * 100
        colors = ["#adb5bd", "#adb5bd", "#adb5bd", "#adb5bd", "#2f9e44"]
        bars = ax.bar(range(len(values)), values, color=colors)
        ax.set_xticks(range(len(values)))
        ax.set_xticklabels(labels, rotation=20, ha="right", fontsize=9)
        ax.set_title(title)
        ax.set_ylabel("WMAPE (%)")
        ax.grid(axis="y", alpha=0.25)
        for bar, value in zip(bars, values):
            ax.text(
                bar.get_x() + bar.get_width() / 2,
                bar.get_height(),
                f"{value:.2f}%",
                ha="center",
                va="bottom",
                fontsize=8,
            )
    fig.suptitle("模型误差对比（越低越好）", fontsize=14)
    fig.tight_layout()
    fig.savefig(out_path, dpi=150)
    plt.close(fig)


def main() -> None:
    if not PREDICTION_PATH.exists():
        raise SystemExit(
            f"找不到基线预测文件：{PREDICTION_PATH}\n"
            "请先运行：.venv\\Scripts\\python.exe app\\forecast_baseline.py"
        )

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    MODEL_DIR.mkdir(parents=True, exist_ok=True)

    raw_rows = int(pd.read_parquet(DATA_PATH).shape[0])
    clean_orders = load_clean_orders()
    panel, sku_info, train_weeks, val_weeks = build_panel()
    features = add_features(panel)

    baseline = pd.read_csv(PREDICTION_PATH, parse_dates=["week"])
    baseline = baseline[["week", "sku_id", "actual", "ma4", "ma8", "sn", "naive"]]
    baseline["sku_id"] = baseline["sku_id"].astype(int)

    val = features[features["week"].isin(val_weeks)].copy()
    val = val.merge(baseline, on=["week", "sku_id"], how="inner", validate="one_to_one")
    if len(val) != len(baseline):
        raise SystemExit(f"验证集拼接异常：features {len(val)} 行，baseline {len(baseline)} 行")

    train = features[features["week"] < val_weeks[0]].copy()
    train = train[train["qty"].notna()]

    # 滚动起点：每个验证周都重新训练一次，只使用该周之前的真实数据，
    # 与 MA/季节朴素一样具备“随新数据更新”的能力，比较更公平。
    pred_frames: list[pd.DataFrame] = []
    best_iters: list[int] = []
    last_model: lgb.Booster | None = None
    for week in sorted(val["week"].unique()):
        train_w = features[features["week"] < week].copy()
        train_w = train_w[train_w["qty"].notna()]
        val_w = val[val["week"] == week].copy()
        model_w, iter_w = train_model(train_w, val_w)
        pred_frames.append(
            pd.DataFrame(
                {
                    "week": week,
                    "sku_id": val_w["sku_id"].to_numpy(),
                    "lgbm": np.clip(model_w.predict(val_w[FEATURE_COLS]), 0, None),
                }
            )
        )
        best_iters.append(iter_w)
        last_model = model_w

    if last_model is None:
        raise SystemExit("验证集为空，无法训练 LightGBM。")
    lgbm_pred = pd.concat(pred_frames, ignore_index=True)
    val = val.merge(lgbm_pred, on=["week", "sku_id"], how="left", validate="one_to_one")
    if val["lgbm"].isna().any():
        raise SystemExit("LightGBM 预测未覆盖全部验证样本。")
    model = last_model
    best_iter = best_iters[-1]

    metrics = evaluate_predictions(val, MODELS)
    pivot = pivot_metrics(metrics, sku_info)
    save_metrics(metrics, pivot, "forecast_comparison")
    val[
        ["week", "sku_id", "sku_name", "category", "actual", "ma4", "ma8", "sn", "naive", "lgbm"]
    ].to_csv(
        OUTPUT_DIR / "forecast_lightgbm_predictions.csv",
        index=False,
        encoding="utf-8-sig",
    )

    importance = pd.DataFrame(
        {
            "feature": FEATURE_COLS,
            "gain": model.feature_importance(importance_type="gain"),
            "split": model.feature_importance(importance_type="split"),
        }
    ).sort_values("gain", ascending=False)
    importance.to_csv(
        OUTPUT_DIR / "forecast_feature_importance.csv",
        index=False,
        encoding="utf-8-sig",
    )

    model.save_model(str(MODEL_DIR / "lgbm_top10_weekly.txt"))
    plot_forecasts(val, sku_info, OUTPUT_DIR / "forecast_lightgbm.png")
    plot_comparison(metrics, OUTPUT_DIR / "forecast_comparison.png")

    display = format_pivot(pivot)
    print("========== LightGBM 需求预测（周 × Top10 SKU） ==========")
    print(
        f"数据：clean_main {raw_rows:,} 行 -> 质量过滤后 {len(clean_orders):,} 行；"
        f"完整周 {len(train_weeks) + len(val_weeks)}（训练 {len(train_weeks)} / 验证 {len(val_weeks)}）"
    )
    print(f"训练窗口：{_date_range(train_weeks)}")
    print(f"验证窗口：{_date_range(val_weeks)}")
    print(
        f"初始训练样本：{len(train):,} 行；验证期逐周重训；"
        f"早停轮数 {min(best_iters)}~{max(best_iters)}（末次 {best_iter}）；特征数：{len(FEATURE_COLS)}"
    )
    print("\n对比表（MAE / WMAPE）：")
    print(display.to_string(index=False))

    overall = metrics[metrics["level"] == "overall"].set_index("model")
    best_model = overall["wmape"].idxmin()
    print(
        f"\nTop10 全部 SKU-周最佳模型：{MODEL_LABELS[best_model]} "
        f"(WMAPE={overall.loc[best_model, 'wmape']:.2%}, MAE={overall.loc[best_model, 'mae']:.2f})"
    )
    print("\n特征重要性 Top 10（gain）：")
    print(importance.head(10).to_string(index=False))

    summary_path = OUTPUT_DIR / "forecast_lightgbm_summary.txt"
    with open(summary_path, "w", encoding="utf-8") as f:
        f.write("LightGBM 需求预测摘要（周 × Top10 SKU）\n")
        f.write("=" * 52 + "\n")
        f.write(f"数据源：{DATA_PATH}\n")
        f.write(f"clean_main：{raw_rows:,} 行；质量过滤后：{len(clean_orders):,} 行\n")
        f.write(f"完整周数：{len(train_weeks) + len(val_weeks)}；训练：{len(train_weeks)} 周；验证：{len(val_weeks)} 周\n")
        f.write(f"训练窗口：{_date_range(train_weeks)}\n")
        f.write(f"验证窗口：{_date_range(val_weeks)}\n")
        f.write(f"初始训练样本：{len(train):,} 行；验证期逐周重训\n")
        f.write(
            f"早停轮数：{min(best_iters)} ~ {max(best_iters)}（末次 {best_iter}）\n"
        )
        f.write(f"参数：{PARAMS}\n\n")
        f.write("对比表（MAE / WMAPE）：\n")
        f.write(display.to_string(index=False))
        f.write("\n\nTop10 全部 SKU-周最佳模型：")
        f.write(f"{MODEL_LABELS[best_model]} (WMAPE={overall.loc[best_model, 'wmape']:.2%})\n")
        f.write("\n特征重要性 Top 10（gain）：\n")
        f.write(importance.head(10).to_string(index=False))
        f.write("\n\n说明：\n")
        f.write("- 全局面板模型，10 个 SKU 一起训练，SKU/品类为类别特征。\n")
        f.write("- 滞后与滚动特征均 shift(1)，验证周只使用此前真实值，避免泄漏。\n")
        f.write("- 每个验证周滚动重训：只用该周之前的真实数据，训练集末 12 周早停。\n")
        f.write("- 验证为一步预测；多步预测误差会更大。\n")
        f.write("- 若某个 scope 在验证期实际需求为 0（如 SKU 116），WMAPE 无定义，表中记为“—”。\n")
        f.write("- 2017-10 之后口径突变，未用于训练/验证。\n")

    print(f"\n已输出：{OUTPUT_DIR / 'forecast_lightgbm.png'}")
    print(f"已输出：{OUTPUT_DIR / 'forecast_comparison.png'}")
    print(f"已输出：{OUTPUT_DIR / 'forecast_comparison_metrics_wide.csv'}")
    print(f"已输出：{MODEL_DIR / 'lgbm_top10_weekly.txt'}")


if __name__ == "__main__":
    main()
