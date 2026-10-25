"""池化 LightGBM + 简单平均组合 + 按 SKU 滚动选优。

预测对象：周度需求量（未来 1 周），Top-N SKU；验证集与基线/LightGBM 脚本一致
（最后 12 个完整周，逐周一步预测）。

关键点：
- 池化训练：所有 Top-N SKU 放进同一个 LightGBM，SKU 编码、品类、价格档位作为特征。
- 组合：ensemble = (MA4 + MA8 + LightGBM) / 3。
- 按 SKU 滚动选优：每个验证周只用该 SKU 在此前验证周的误差挑模型，避免用未来信息。

用法：
    .venv\\Scripts\\python.exe app\\forecast_ensemble.py

输出：
    output/forecast_ensemble_predictions.csv
    output/forecast_ensemble_metrics_long.csv
    output/forecast_ensemble_metrics_wide.csv
    output/forecast_comparison_metrics_long.csv   （同步刷新，六模型）
    output/forecast_comparison_metrics_wide.csv   （同步刷新，六模型）
    output/forecast_selection_by_sku.csv
    output/forecast_ensemble_feature_importance.csv
    output/forecast_ensemble.png
    output/forecast_ensemble_summary.txt
    models/lgbm_pooled_topn_weekly.txt
"""

from __future__ import annotations

from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from forecast_common import (
    CATEGORICAL_COLS_POOLED,
    DATA_PATH,
    MODEL_DIR,
    MODEL_LABELS,
    OUTPUT_DIR,
    POOLED_FEATURE_COLS,
    add_baselines,
    add_features,
    add_price_band,
    build_panel,
    evaluate_predictions,
    forecast_config,
    load_clean_orders,
    pivot_metrics,
    save_metrics,
    setup_plot_style,
    train_point_model,
)

MODELS = ["ma4", "ma8", "naive", "sn", "lgbm", "ensemble"]
SELECT_MODEL = "select"
SHORT_LABELS = {
    "ma4": "MA4",
    "ma8": "MA8",
    "sn": "季节朴素",
    "naive": "朴素",
    "lgbm": "LightGBM",
    "ensemble": "组合",
    "select": "滚动选优",
}
SELECTION_CANDIDATES = MODELS

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


def _best_model(history: list[tuple[float, dict[str, float]]]) -> str:
    """在历史验证周上按 WMAPE（实际为 0 时用 MAE）选误差最小的模型。"""
    actual = np.array([h[0] for h in history], dtype=float)
    scores: dict[str, float] = {}
    for model in SELECTION_CANDIDATES:
        pred = np.array([h[1][model] for h in history], dtype=float)
        err = np.abs(actual - pred)
        scores[model] = (
            float(err.sum() / actual.sum()) if actual.sum() > 0 else float(err.mean())
        )
    # 分数相同时按候选顺序稳定选择
    return min(SELECTION_CANDIDATES, key=lambda m: (scores[m], SELECTION_CANDIDATES.index(m)))


def add_rolling_selection(val: pd.DataFrame) -> pd.DataFrame:
    """为每个 SKU 逐验证周滚动选优；第一周无历史误差，默认 MA4。"""
    records: list[dict] = []
    for sku_id, grp in val.groupby("sku_id", sort=False):
        grp = grp.sort_values("week")
        history: list[tuple[float, dict[str, float]]] = []
        for row in grp.itertuples(index=False):
            if history:
                chosen = _best_model(history)
            else:
                chosen = "ma4"  # 冷启动：无验证误差可参考，沿用先验最优的单模型
            records.append(
                {
                    "week": row.week,
                    "sku_id": int(sku_id),
                    "selected_model": chosen,
                    "selected": float(getattr(row, chosen)),
                }
            )
            history.append(
                (float(row.actual), {m: float(getattr(row, m)) for m in SELECTION_CANDIDATES})
            )
    return pd.DataFrame(records)


def plot_ensemble(
    val: pd.DataFrame,
    metrics: pd.DataFrame,
    selection: pd.DataFrame,
    out_path: Path,
) -> None:
    setup_plot_style()
    fig, axes = plt.subplots(2, 2, figsize=(14, 8.5))
    axes = axes.ravel()

    # 1) 全部 SKU-周 WMAPE
    overall = (
        metrics[metrics["level"] == "overall"]
        .set_index("model")
        .reindex(MODELS)
    )
    ax = axes[0]
    values = overall["wmape"].to_numpy() * 100
    colors = ["#adb5bd"] * (len(MODELS) - 1) + ["#2f9e44"]
    bars = ax.bar(range(len(MODELS)), values, color=colors)
    ax.set_xticks(range(len(MODELS)))
    ax.set_xticklabels([SHORT_LABELS[m] for m in MODELS], rotation=20, ha="right", fontsize=9)
    ax.set_title("Top-N 全部 SKU-周 WMAPE")
    ax.set_ylabel("WMAPE (%)")
    ax.grid(axis="y", alpha=0.25)
    for bar, value in zip(bars, values):
        ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height(), f"{value:.2f}%",
                ha="center", va="bottom", fontsize=8)

    # 2) Top-N 合计（按周）WMAPE
    total = (
        metrics[metrics["level"] == "total"]
        .set_index("model")
        .reindex(MODELS)
    )
    ax = axes[1]
    values = total["wmape"].to_numpy() * 100
    bars = ax.bar(range(len(MODELS)), values, color=colors)
    ax.set_xticks(range(len(MODELS)))
    ax.set_xticklabels([SHORT_LABELS[m] for m in MODELS], rotation=20, ha="right", fontsize=9)
    ax.set_title("Top-N 合计（按周）WMAPE")
    ax.set_ylabel("WMAPE (%)")
    ax.grid(axis="y", alpha=0.25)
    for bar, value in zip(bars, values):
        ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height(), f"{value:.2f}%",
                ha="center", va="bottom", fontsize=8)

    # 3) 合计需求曲线
    ax = axes[2]
    weekly = val.groupby("week")[["actual", "ma4", "lgbm", "ensemble"]].sum().sort_index()
    ax.plot(weekly.index, weekly["actual"], color="#111111", linewidth=2.0, label="实际")
    ax.plot(weekly.index, weekly["ma4"], color="#2a6fdb", linewidth=1.3, label="MA4")
    ax.plot(weekly.index, weekly["lgbm"], color="#2f9e44", linewidth=1.5, label="LightGBM")
    ax.plot(weekly.index, weekly["ensemble"], color="#d9480f", linewidth=1.8, label="组合")
    ax.set_title("Top-N 合计周需求")
    ax.set_ylabel("件数")
    ax.legend(fontsize=9)
    ax.grid(alpha=0.25)
    ax.xaxis.set_major_locator(mdates.WeekdayLocator(interval=2))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d"))
    ax.tick_params(axis="x", labelrotation=30)

    # 4) 滚动选优的模型使用次数
    ax = axes[3]
    counts = selection["selected_model"].value_counts().reindex(MODELS, fill_value=0)
    bars = ax.bar(range(len(counts)), counts.to_numpy(), color="#2a6fdb")
    ax.set_xticks(range(len(counts)))
    ax.set_xticklabels([SHORT_LABELS[m] for m in counts.index], rotation=20, ha="right", fontsize=9)
    ax.set_title("按 SKU 滚动选优：各模型被选中次数")
    ax.set_ylabel("SKU-周数")
    ax.grid(axis="y", alpha=0.25)
    for bar, value in zip(bars, counts.to_numpy()):
        ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height(), f"{int(value)}",
                ha="center", va="bottom", fontsize=8)

    fig.suptitle("池化 LightGBM、简单平均组合与按 SKU 滚动选优（最后 12 个完整周）", fontsize=14)
    fig.tight_layout()
    fig.savefig(out_path, dpi=150)
    plt.close(fig)


def format_six_column_table(pivot: pd.DataFrame, metric: str) -> pd.DataFrame:
    """只保留六个模型列，生成终端表格（WMAPE 百分比 / MAE 两位小数）。"""
    rows = []
    for _, row in pivot.iterrows():
        out: dict[str, object] = {"scope": row["scope"]}
        for model in MODELS:
            value = row.get(f"{model}_{metric}")
            if pd.isna(value):
                out[SHORT_LABELS[model]] = "—"
            elif metric == "WMAPE":
                out[SHORT_LABELS[model]] = f"{float(value):.2%}"
            else:
                out[SHORT_LABELS[model]] = f"{float(value):.2f}"
        rows.append(out)
    return pd.DataFrame(rows)


def build_selection_table(
    val: pd.DataFrame,
    metrics: pd.DataFrame,
    selection: pd.DataFrame,
    sku_info: pd.DataFrame,
) -> pd.DataFrame:
    rows = []
    select_metrics = metrics[metrics["model"] == SELECT_MODEL].set_index("sku_id")
    for sku_id, row in sku_info.iterrows():
        sku_id = int(sku_id)
        sub = selection[selection["sku_id"] == sku_id]
        counts = sub["selected_model"].value_counts()
        record: dict[str, object] = {
            "sku_id": sku_id,
            "sku_name": row["sku_name"],
            "weeks": int(len(sub)),
        }
        for model in MODELS:
            record[f"selected_{model}_count"] = int(counts.get(model, 0))
        record["select_MAE"] = float(select_metrics.loc[sku_id, "mae"])
        record["select_WMAPE"] = float(select_metrics.loc[sku_id, "wmape"])
        # 事后最优（同一验证集，仅作参考，不参与决策）
        sku_metrics = metrics[(metrics["level"] == "sku") & (metrics["sku_id"] == sku_id)]
        single = sku_metrics[sku_metrics["model"].isin(MODELS)]
        best = single.loc[single["wmape"].idxmin()] if single["wmape"].notna().any() else None
        record["oracle_best_model"] = best["model"] if best is not None else "—"
        record["oracle_best_MAE"] = float(best["mae"]) if best is not None else float("nan")
        record["oracle_best_WMAPE"] = float(best["wmape"]) if best is not None else float("nan")
        rows.append(record)
    return pd.DataFrame(rows)


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    MODEL_DIR.mkdir(parents=True, exist_ok=True)

    cfg = forecast_config()
    top_n = int(cfg["top_n"])
    val_weeks_cfg = int(cfg["val_weeks"])

    raw_rows = int(pd.read_parquet(DATA_PATH).shape[0])
    clean_orders = load_clean_orders()
    panel, sku_info, train_weeks, val_weeks = build_panel(
        top_n=top_n, val_weeks=val_weeks_cfg
    )

    panel = add_price_band(panel, cfg["price_band_edges"])
    panel = add_baselines(panel)
    features = add_features(panel)

    val = features[features["week"].isin(val_weeks)].copy()
    val["actual"] = val["qty"]
    train = features[features["week"] < val_weeks[0]].copy()
    train = train[train["qty"].notna()]

    # ---- 池化 LightGBM：每个验证周重新训练，只用该周之前的真实数据 ----
    pred_frames: list[pd.DataFrame] = []
    best_iters: list[int] = []
    last_model = None
    for week in sorted(val["week"].unique()):
        train_w = features[features["week"] < week].copy()
        train_w = train_w[train_w["qty"].notna()]
        val_w = val[val["week"] == week].copy()
        model_w, iter_w = train_point_model(
            train_w,
            POOLED_FEATURE_COLS,
            CATEGORICAL_COLS_POOLED,
            PARAMS,
            inner_val_weeks=val_weeks_cfg,
            label_col="qty",
        )
        pred_frames.append(
            pd.DataFrame(
                {
                    "week": week,
                    "sku_id": val_w["sku_id"].to_numpy(),
                    "lgbm": np.clip(model_w.predict(val_w[POOLED_FEATURE_COLS]), 0, None),
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

    val["ensemble"] = (val["ma4"] + val["ma8"] + val["lgbm"]) / 3.0

    selection = add_rolling_selection(val)
    val = val.merge(
        selection[["week", "sku_id", "selected_model", "selected"]],
        on=["week", "sku_id"],
        how="left",
        validate="one_to_one",
    )
    val = val.rename(columns={"selected": SELECT_MODEL})

    eval_models = [*MODELS, SELECT_MODEL]
    metrics = evaluate_predictions(val, eval_models)
    pivot = pivot_metrics(metrics, sku_info, model_order=MODELS)
    save_metrics(metrics, pivot, "forecast_ensemble")
    # 同步刷新“对比表”文件名，保持与上一轮脚本一致
    metrics.to_csv(OUTPUT_DIR / "forecast_comparison_metrics_long.csv", index=False, encoding="utf-8-sig")
    pivot.to_csv(OUTPUT_DIR / "forecast_comparison_metrics_wide.csv", index=False, encoding="utf-8-sig")

    pred_cols = [
        "week", "sku_id", "sku_name", "category", "price", "price_band",
        "actual", *MODELS, SELECT_MODEL, "selected_model",
    ]
    val[pred_cols].sort_values(["sku_id", "week"]).to_csv(
        OUTPUT_DIR / "forecast_ensemble_predictions.csv",
        index=False,
        encoding="utf-8-sig",
    )

    selection_table = build_selection_table(val, metrics, selection, sku_info)
    selection_table.to_csv(
        OUTPUT_DIR / "forecast_selection_by_sku.csv", index=False, encoding="utf-8-sig"
    )

    importance = pd.DataFrame(
        {
            "feature": POOLED_FEATURE_COLS,
            "gain": last_model.feature_importance(importance_type="gain"),
            "split": last_model.feature_importance(importance_type="split"),
        }
    ).sort_values("gain", ascending=False)
    importance.to_csv(
        OUTPUT_DIR / "forecast_ensemble_feature_importance.csv",
        index=False,
        encoding="utf-8-sig",
    )
    last_model.save_model(str(MODEL_DIR / "lgbm_pooled_topn_weekly.txt"))

    plot_ensemble(val, metrics, selection, OUTPUT_DIR / "forecast_ensemble.png")

    wmape_table = format_six_column_table(pivot, "WMAPE")
    mae_table = format_six_column_table(pivot, "MAE")

    overall = metrics[metrics["level"] == "overall"].set_index("model")
    best_single = min(MODELS, key=lambda m: overall.loc[m, "wmape"])
    select_overall = overall.loc[SELECT_MODEL]

    print("========== 组合预测与池化 LightGBM（周 × Top-N SKU） ==========")
    print(
        f"数据：clean_main {raw_rows:,} 行 -> 质量过滤后 {len(clean_orders):,} 行；"
        f"完整周 {len(train_weeks) + len(val_weeks)}（训练 {len(train_weeks)} / 验证 {len(val_weeks)}）"
    )
    print(f"训练窗口：{_date_range(train_weeks)}")
    print(f"验证窗口：{_date_range(val_weeks)}")
    print(
        f"池化模型：Top-{top_n} SKU 共 {len(val)} 个验证样本；特征 {len(POOLED_FEATURE_COLS)} 个"
        f"（含 SKU、品类、价格、价格档位）；早停轮数 {min(best_iters)}~{max(best_iters)}（末次 {best_iters[-1]}）"
    )

    print("\n表 1  WMAPE（越低越好；每行一个评估范围）")
    print(wmape_table.to_string(index=False))
    print("\n表 2  MAE（越低越好）")
    print(mae_table.to_string(index=False))

    print("\n表 3  按 SKU 滚动选优（选中的模型 / 该 SKU 的选优误差）")
    show = selection_table.copy()
    for model in MODELS:
        col = f"selected_{model}_count"
        show[SHORT_LABELS[model]] = show[col].map(lambda n: "" if n == 0 else str(int(n)))
    show = show[
        ["sku_id", "sku_name", *[SHORT_LABELS[m] for m in MODELS],
         "select_WMAPE", "select_MAE", "oracle_best_model", "oracle_best_WMAPE"]
    ].rename(columns={"select_WMAPE": "选优WMAPE", "select_MAE": "选优MAE",
                      "oracle_best_model": "事后最优", "oracle_best_WMAPE": "事后最优WMAPE"})
    show["选优WMAPE"] = show["选优WMAPE"].map(lambda x: f"{x:.2%}" if pd.notna(x) else "—")
    show["选优MAE"] = show["选优MAE"].map(lambda x: f"{x:.2f}")
    show["事后最优WMAPE"] = show["事后最优WMAPE"].map(lambda x: f"{x:.2%}" if pd.notna(x) else "—")
    print(show.to_string(index=False))

    print("\n汇总（Top-N 全部 SKU-周）：")
    for model in eval_models:
        row = overall.loc[model]
        print(
            f"  {SHORT_LABELS[model]:<6} MAE={row['mae']:.2f}  "
            f"WMAPE={row['wmape']:.2%}  实际需求合计={row['actual_sum']:.0f}"
        )
    print(
        f"\n最佳方案：{SHORT_LABELS[best_single]}（WMAPE {overall.loc[best_single, 'wmape']:.2%}）；"
        f"组合：{overall.loc['ensemble', 'wmape']:.2%}；"
        f"滚动选优：{select_overall['wmape']:.2%}（MAE {select_overall['mae']:.2f}）"
    )

    summary_path = OUTPUT_DIR / "forecast_ensemble_summary.txt"
    with open(summary_path, "w", encoding="utf-8") as f:
        f.write("组合预测与池化 LightGBM 摘要（周 × Top-N SKU）\n")
        f.write("=" * 58 + "\n")
        f.write(f"数据源：{DATA_PATH}\n")
        f.write(f"clean_main：{raw_rows:,} 行；质量过滤后：{len(clean_orders):,} 行\n")
        f.write(
            f"完整周数：{len(train_weeks) + len(val_weeks)}；"
            f"训练：{len(train_weeks)} 周；验证：{len(val_weeks)} 周\n"
        )
        f.write(f"训练窗口：{_date_range(train_weeks)}\n")
        f.write(f"验证窗口：{_date_range(val_weeks)}\n")
        f.write(f"SKU 数：{len(sku_info)}；池化特征数：{len(POOLED_FEATURE_COLS)}\n")
        f.write(
            f"池化特征：SKU 编码、品类、价格、价格档位（{cfg['price_band_edges']}）、"
            "滞后/滚动/日历特征\n"
        )
        f.write(f"早停轮数：{min(best_iters)} ~ {max(best_iters)}（末次 {best_iters[-1]}）\n")
        f.write(f"LightGBM 参数：{PARAMS}\n\n")
        f.write("表 1  WMAPE\n")
        f.write(wmape_table.to_string(index=False))
        f.write("\n\n表 2  MAE\n")
        f.write(mae_table.to_string(index=False))
        f.write("\n\n表 3  按 SKU 滚动选优\n")
        f.write(show.to_string(index=False))
        f.write("\n\n说明：\n")
        f.write("- 池化训练：所有 Top-N SKU 共用一个 LightGBM，SKU/品类/价格档位为类别特征。\n")
        f.write("- 组合 = (MA4 + MA8 + LightGBM) / 3，三者权重相同。\n")
        f.write("- 滚动选优：每个 SKU 在每个验证周，只用此前验证周的误差挑模型；首周默认 MA4。\n")
        f.write("- “事后最优”是在同一验证集上事后回看的最优模型，存在信息泄漏，仅作参考。\n")
        f.write("- 每个验证周重新训练池化模型，只用该周之前的真实数据。\n")
        f.write("- 若某个 scope 在验证期实际需求为 0（如 SKU 116），WMAPE 无定义，记为“—”。\n")
        f.write("- 2017-10 之后口径突变，未用于训练/验证。\n")

    print(f"\n已输出：{OUTPUT_DIR / 'forecast_ensemble.png'}")
    print(f"已输出：{OUTPUT_DIR / 'forecast_ensemble_metrics_wide.csv'}")
    print(f"已输出：{OUTPUT_DIR / 'forecast_comparison_metrics_wide.csv'}")
    print(f"已输出：{OUTPUT_DIR / 'forecast_selection_by_sku.csv'}")
    print(f"已输出：{MODEL_DIR / 'lgbm_pooled_topn_weekly.txt'}")


if __name__ == "__main__":
    main()
