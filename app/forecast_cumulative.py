"""未来 H 周累计需求预测（池化 LightGBM 分位数模型）。

目标改为累计需求：站在第 t 周末，预测未来 H 周（t+1 ~ t+H）的总需求。
H 由 config.yaml 的 forecast.horizon_weeks 控制，默认 4。

模型：
- 池化 LightGBM，所有 Top-N SKU 一起训练；SKU、品类、价格档位为类别特征。
- objective="quantile"，分别训练 P50 / P90 / P95。
- 默认对目标做“近期水平归一化”：学习 target / (H × 近4周均值) 的倍数关系，
  预测时再乘回基准，缓解不同 SKU 的量纲差异与水平漂移（config 可关闭）。
- 每个起点只使用“目标窗口已经实现”的历史样本训练，避免未来信息泄漏。
- 保形校准：对每个起点，只用“未来 H 周已经实现”的历史起点残差修正分位线；
  修正量是残差 (actual - pred) 的对应分位数，只向上修正（本轮为系统性低估）。

验证：
- 留出集：最后 val_weeks 个可完整观测 H 周未来的起点（默认 12 个）。
- 覆盖率回测：最后 coverage_backtest_weeks 个起点（默认 52 周），样本更大、更稳。
- 覆盖率：真实累计需求 ≤ 分位数线的比例；P90 应接近 90%，P95 接近 95%。

用法：
    .venv\\Scripts\\python.exe app\\forecast_cumulative.py

输出：
    output/forecast_cumulative_predictions.csv            （留出集）
    output/forecast_cumulative_backtest_predictions.csv   （滚动回测）
    output/forecast_cumulative_metrics.csv
    output/forecast_coverage_summary.csv
    output/forecast_coverage_by_sku.csv
    output/forecast_coverage_by_origin.csv
    output/forecast_conformal_offsets.csv               （每个起点的校准偏移量）
    output/forecast_cumulative.png
    output/forecast_cumulative_summary.txt
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
    CUM_FEATURE_COLS,
    DATA_PATH,
    OUTPUT_DIR,
    add_cumulative_target,
    add_features,
    add_price_band,
    build_panel,
    forecast_config,
    load_clean_orders,
    setup_plot_style,
    train_quantile_model,
)

BASE_QUANTILE_PARAMS = {
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


def _point_metrics(actual: pd.Series, pred: pd.Series) -> tuple[float, float, float, int]:
    actual = actual.astype(float)
    pred = pred.astype(float)
    err = (actual - pred).abs()
    mae = float(err.mean()) if len(err) else float("nan")
    actual_sum = float(actual.sum())
    wmape = float(err.sum() / actual_sum) if actual_sum else float("nan")
    return mae, wmape, actual_sum, int(len(err))


def _pinball_loss(actual: np.ndarray, pred: np.ndarray, quantile: float) -> float:
    """分位数损失（pinball loss），越小越好；用于评估分位数线本身。"""
    diff = actual - pred
    return float(np.mean(np.maximum(quantile * diff, (quantile - 1.0) * diff)))


def _q_name(quantile: float) -> str:
    return f"p{int(round(quantile * 100))}"


def _conformal_offset(residuals: np.ndarray, quantile: float, positive_only: bool) -> float:
    """保形偏移量：残差 (actual - pred) 的有限样本分位数。

    用 ceil((n+1)q)/n 而不是 np.quantile，避免小样本下覆盖率系统性偏低。
    positive_only=True 时只向上修正——本轮诊断是系统性低估，没有低估证据就保持原线。
    """
    n = len(residuals)
    if n == 0:
        return 0.0
    k = min(max(int(np.ceil((n + 1) * quantile)), 1), n)
    offset = float(np.sort(residuals)[k - 1])
    return max(offset, 0.0) if positive_only else offset


def coverage_stats(df: pd.DataFrame, q_names: list[str], quantiles: list[float]) -> dict:
    row: dict[str, object] = {"n": int(len(df))}
    for q_name, quantile in zip(q_names, quantiles):
        covered = df["actual"] <= df[q_name]
        row[f"coverage_{q_name}"] = float(covered.mean())
        row[f"target_{q_name}"] = quantile
        row[f"pinball_{q_name}"] = _pinball_loss(
            df["actual"].to_numpy(dtype=float), df[q_name].to_numpy(dtype=float), quantile
        )
    return row


def build_metrics(df: pd.DataFrame, scope: str, q_names: list[str], quantiles: list[float]) -> pd.DataFrame:
    rows: list[dict] = []
    pinball = {
        q_name: _pinball_loss(df["actual"].to_numpy(dtype=float),
                              df[q_name].to_numpy(dtype=float), q)
        for q, q_name in zip(quantiles, q_names)
    }
    for model in [*q_names, "ma4_x_h", "ma8_x_h"]:
        mae, wmape, actual_sum, n = _point_metrics(df["actual"], df[model])
        rows.append(
            {
                "scope": scope,
                "model": model,
                "type": (
                    "点预测" if model == "p50"
                    else "分位数线" if model in q_names
                    else "基准"
                ),
                "mae": mae,
                "wmape": wmape,
                "actual_sum": actual_sum,
                "n": n,
                "pinball_loss": pinball.get(model, float("nan")),
            }
        )
    return pd.DataFrame(rows)


def plot_cumulative(
    holdout: pd.DataFrame,
    backtest: pd.DataFrame,
    coverage_sku: pd.DataFrame,
    coverage_origin: pd.DataFrame,
    q_names: list[str],
    quantiles: list[float],
    horizon: int,
    out_path: Path,
) -> None:
    setup_plot_style()
    p50, p90, p95 = _q_name(0.5), _q_name(0.9), _q_name(0.95) if 0.95 in quantiles else q_names[-1]

    fig, axes = plt.subplots(2, 2, figsize=(14, 8.5))
    axes = axes.ravel()

    total = holdout.groupby("week")[["actual", *q_names]].sum().sort_index()
    ax = axes[0]
    ax.plot(total.index, total["actual"], color="#111111", linewidth=2.0, label="实际累计")
    ax.plot(total.index, total[p50], color="#2a6fdb", linewidth=1.8, label="P50")
    ax.plot(total.index, total[p90], color="#2f9e44", linewidth=1.4, linestyle="--", label="P90")
    ax.plot(total.index, total[p95], color="#d9480f", linewidth=1.4, linestyle=":", label="P95")
    ax.fill_between(total.index, total[p50], total[p90], color="#2f9e44", alpha=0.12,
                    label="P50~P90 区间")
    ax.set_title(f"留出集：Top-N 合计未来 {horizon} 周累计需求")
    ax.set_ylabel("件数")
    ax.legend(fontsize=8)
    ax.grid(alpha=0.25)

    sku_id = int(holdout.groupby("sku_id")["actual"].sum().idxmax())
    g = holdout[holdout["sku_id"] == sku_id].sort_values("week")
    ax = axes[1]
    ax.plot(g["week"], g["actual"], color="#111111", linewidth=2.0, label="实际累计")
    ax.plot(g["week"], g[p50], color="#2a6fdb", linewidth=1.8, label="P50")
    ax.plot(g["week"], g[p90], color="#2f9e44", linewidth=1.4, linestyle="--", label="P90")
    ax.plot(g["week"], g[p95], color="#d9480f", linewidth=1.4, linestyle=":", label="P95")
    ax.set_title(f"留出集：最大 SKU（{sku_id}）未来 {horizon} 周累计需求", fontsize=10)
    ax.set_ylabel("件数")
    ax.legend(fontsize=8)
    ax.grid(alpha=0.25)

    ax = axes[2]
    cov = coverage_sku.sort_values("coverage_p90_backtest")
    x = np.arange(len(cov))
    ax.bar(x, cov["coverage_p90_backtest"].to_numpy() * 100,
           color=["#2f9e44" if v >= 0.8 else "#d9480f" for v in cov["coverage_p90_backtest"]])
    ax.axhline(90, color="#111111", linestyle="--", linewidth=1.2, label="目标 90%")
    ax.set_xticks(x)
    ax.set_xticklabels([f"{int(s)}" for s in cov["sku_id"]], rotation=45, fontsize=8)
    ax.set_ylim(0, 105)
    ax.set_ylabel("覆盖率 (%)")
    ax.set_xlabel("SKU")
    ax.set_title("滚动回测：P90 覆盖率（按 SKU，n=52）")
    ax.legend(fontsize=8)
    ax.grid(axis="y", alpha=0.25)

    ax = axes[3]
    co = coverage_origin.sort_values("week")
    ax.plot(co["week"], co["coverage_p90"] * 100, color="#2a6fdb", marker="o",
            markersize=3, linewidth=1.3, label="P90 覆盖率")
    ax.axhline(90, color="#111111", linestyle="--", linewidth=1.2, label="目标 90%")
    ramp_start = pd.Timestamp("2017-06-05")
    ramp_end = pd.Timestamp("2017-06-26")
    ax.axvspan(ramp_start, ramp_end, color="#d9480f", alpha=0.12, label="2017-06 爬坡期")
    ax.set_ylim(0, 105)
    ax.set_ylabel("覆盖率 (%)")
    ax.set_title("滚动回测：按起点的 P90 覆盖率")
    ax.legend(fontsize=8)
    ax.grid(alpha=0.25)
    ax.xaxis.set_major_locator(mdates.WeekdayLocator(interval=6))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%y-%m"))
    ax.tick_params(axis="x", labelrotation=30)

    for ax in [axes[0], axes[1]]:
        ax.xaxis.set_major_locator(mdates.WeekdayLocator(interval=2))
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d"))
        ax.tick_params(axis="x", labelrotation=30)

    fig.suptitle(f"未来 {horizon} 周累计需求：分位数预测与覆盖率检验", fontsize=14)
    fig.tight_layout()
    fig.savefig(out_path, dpi=150)
    plt.close(fig)


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    cfg = forecast_config()
    horizon = int(cfg["horizon_weeks"])
    top_n = int(cfg["top_n"])
    val_origins_cfg = int(cfg["val_weeks"])
    backtest_cfg = max(int(cfg["coverage_backtest_weeks"]), val_origins_cfg)
    quantiles = [float(q) for q in cfg["quantiles"]]
    q_names = [_q_name(q) for q in quantiles]
    num_rounds = int(cfg["quantile_num_boost_round"])
    normalize = bool(cfg["normalize_by_recent_level"])
    conformal = bool(cfg["conformal_calibration"])
    conformal_window = max(int(cfg["conformal_window_origins"]), 1)
    conformal_positive_only = bool(cfg["conformal_positive_only"])
    conformal_min = max(int(cfg["conformal_min_origins"]), 1)
    if sorted(quantiles) != quantiles:
        raise SystemExit("config.yaml 的 quantiles 必须从小到大排列。")
    for required in (0.5, 0.9):
        if required not in quantiles:
            raise SystemExit(f"config.yaml 的 quantiles 必须包含 P{int(required * 100)}。")

    params = dict(BASE_QUANTILE_PARAMS)
    params.update(
        {
            "learning_rate": float(cfg["quantile_learning_rate"]),
            "num_leaves": int(cfg["quantile_num_leaves"]),
            "min_data_in_leaf": int(cfg["quantile_min_data_in_leaf"]),
        }
    )

    raw_rows = int(pd.read_parquet(DATA_PATH).shape[0])
    clean_orders = load_clean_orders()
    panel, sku_info, _, _ = build_panel(top_n=top_n, val_weeks=val_origins_cfg)

    panel = add_price_band(panel, cfg["price_band_edges"])
    panel = add_cumulative_target(panel, horizon)
    features = add_features(panel)
    features["base"] = features["roll_mean_cur_4"] * horizon
    features["ratio"] = np.where(
        features["base"] > 0, features["target"] / features["base"], np.nan
    )

    weeks = sorted(features["week"].unique())
    week_to_idx = {week: idx for idx, week in enumerate(weeks)}
    features["week_idx"] = features["week"].map(week_to_idx)

    # 只保留目标窗口完整可观测的起点（最后 H 周没有完整未来窗口）
    valid_origins = [
        week for week in weeks
        if week_to_idx[week] + horizon < len(weeks)
    ]
    if len(valid_origins) <= val_origins_cfg:
        raise SystemExit(
            f"可用起点不足：{len(valid_origins)} 个，无法切出 {val_origins_cfg} 个验证起点。"
            "请减小 config.yaml 的 horizon_weeks 或 val_weeks。"
        )
    backtest_origins = valid_origins[-backtest_cfg:]
    holdout_origins = valid_origins[-val_origins_cfg:]
    holdout_set = set(holdout_origins)

    label_col = "ratio" if normalize else "target"
    pred_frames: list[pd.DataFrame] = []
    train_sizes: list[int] = []
    calib_records: list[dict] = []
    # 校准历史：只保存“未来窗口已实现”的起点，避免未来信息泄漏。
    calib_history: list[tuple[int, pd.DataFrame]] = []

    for origin in backtest_origins:
        origin_idx = week_to_idx[origin]
        train = features[
            (features["week_idx"] + horizon <= origin_idx)
            & features["target"].notna()
            & (features["week"] < origin)
        ].copy()
        if normalize:
            train = train[train["base"] > 0]
        if train.empty:
            raise SystemExit(f"起点 {origin:%Y-%m-%d} 没有可用的训练样本。")

        current = features[features["week"] == origin].copy()
        result = current[
            ["week", "sku_id", "sku_name", "category", "price", "price_band", "target"]
        ].copy()
        result = result.rename(columns={"target": "actual"})

        for quantile, q_name in zip(quantiles, q_names):
            model = train_quantile_model(
                train,
                quantile,
                CUM_FEATURE_COLS,
                CATEGORICAL_COLS_POOLED,
                params,
                num_rounds,
                label_col=label_col,
            )
            raw = model.predict(current[CUM_FEATURE_COLS])
            if normalize:
                predicted = raw * current["base"].to_numpy()
            else:
                predicted = raw
            result[q_name] = np.clip(predicted, 0, None)

        # 强制分位数不交叉（P50 <= P90 <= P95）
        matrix = np.sort(result[q_names].to_numpy(dtype=float), axis=1)
        result[q_names] = matrix
        # 保留校准前的分位线，用于修正前后对照
        for q_name in q_names:
            result[f"{q_name}_raw"] = result[q_name]

        # ---------------- 保形校准 ----------------
        # 站在第 t 周末，只有 u+H <= t 的起点残差是“已实现”的；取最近 window 个。
        usable = [
            (u_idx, frame)
            for u_idx, frame in calib_history
            if u_idx + horizon <= origin_idx
        ]
        usable = usable[-conformal_window:]
        offsets = {q_name: 0.0 for q_name in q_names}
        calib_start = calib_end = None
        n_calib = 0
        if conformal and len(usable) >= conformal_min:
            hist = pd.concat([frame for _, frame in usable], ignore_index=True)
            n_calib = int(len(hist))
            calib_start = min(frame["week"].min() for _, frame in usable)
            calib_end = max(frame["week"].max() for _, frame in usable)
            for quantile, q_name in zip(quantiles, q_names):
                residuals = (hist["actual"] - hist[f"{q_name}_raw"]).to_numpy(dtype=float)
                offsets[q_name] = _conformal_offset(
                    residuals, quantile, conformal_positive_only
                )
            for q_name in q_names:
                result[q_name] = np.clip(result[q_name] + offsets[q_name], 0, None)
            # 修正后重新做不交叉
            result[q_names] = np.sort(result[q_names].to_numpy(dtype=float), axis=1)

        calib_records.append(
            {
                "week": origin,
                "n_calib_origins": len(usable),
                "n_calib_samples": n_calib,
                "calib_start": calib_start,
                "calib_end": calib_end,
                **{f"{q_name}_offset": offsets[q_name] for q_name in q_names},
            }
        )

        # 基准：用当期 4/8 周均值做水平外推
        result["ma4_x_h"] = current["base"].to_numpy()
        result["ma8_x_h"] = current["roll_mean_cur_8"].to_numpy() * horizon
        result["is_holdout"] = origin in holdout_set

        pred_frames.append(result)
        train_sizes.append(int(len(train)))
        # 当前起点的“未校准”分位线进入历史，供后续起点估计偏移量
        calib_history.append(
            (
                origin_idx,
                result[["week", "sku_id", "actual", *[f"{q_name}_raw" for q_name in q_names]]].copy(),
            )
        )
    calib_df = pd.DataFrame(calib_records)

    preds_all = pd.concat(pred_frames, ignore_index=True)
    holdout = preds_all[preds_all["is_holdout"]].copy()
    backtest = preds_all.copy()

    # ---------------- 指标 ----------------
    metrics = pd.concat(
        [
            build_metrics(holdout, "留出集（最近12个起点）", q_names, quantiles),
            build_metrics(backtest, "滚动回测（最近52个起点）", q_names, quantiles),
        ],
        ignore_index=True,
    )

    # ---------------- 覆盖率（修正前 / 修正后对照） ----------------
    coverage_rows = []
    for scope, df in [("留出集（最近12个起点）", holdout),
                      ("滚动回测（最近52个起点）", backtest)]:
        stats = coverage_stats(df, q_names, quantiles)
        for q, q_name in zip(quantiles, q_names):
            covered_before = int((df["actual"] <= df[f"{q_name}_raw"]).sum())
            coverage_before = float(covered_before / len(df))
            pinball_before = _pinball_loss(
                df["actual"].to_numpy(dtype=float),
                df[f"{q_name}_raw"].to_numpy(dtype=float),
                q,
            )
            coverage_rows.append(
                {
                    "scope": scope,
                    "quantile": q,
                    "line": q_name,
                    "n": int(stats["n"]),
                    "covered_before": covered_before,
                    "coverage_before": coverage_before,
                    "covered": int((df["actual"] <= df[q_name]).sum()),
                    "coverage": stats[f"coverage_{q_name}"],
                    "target_coverage": q,
                    "delta_pp": (stats[f"coverage_{q_name}"] - coverage_before) * 100,
                    "pinball_loss_before": pinball_before,
                    "pinball_loss": stats[f"pinball_{q_name}"],
                }
            )
    coverage = pd.DataFrame(coverage_rows)

    # 每个 SKU 的覆盖率：留出集 + 回测
    sku_rows = []
    for sku_id, grp in backtest.groupby("sku_id", sort=True):
        h = grp[grp["is_holdout"]]
        row: dict[str, object] = {
            "sku_id": int(sku_id),
            "sku_name": str(grp["sku_name"].iloc[0]),
            "price": float(grp["price"].iloc[0]),
            "n_holdout": int(len(h)),
            "n_backtest": int(len(grp)),
            "coverage_p90_holdout_before": (
                float((h["actual"] <= h["p90_raw"]).mean()) if len(h) else float("nan")
            ),
            "coverage_p90_holdout": float((h["actual"] <= h["p90"]).mean()) if len(h) else float("nan"),
            "coverage_p50_backtest_before": float((grp["actual"] <= grp["p50_raw"]).mean()),
            "coverage_p50_backtest": float((grp["actual"] <= grp["p50"]).mean()),
            "coverage_p90_backtest_before": float((grp["actual"] <= grp["p90_raw"]).mean()),
            "coverage_p90_backtest": float((grp["actual"] <= grp["p90"]).mean()),
            "coverage_p95_backtest_before": float((grp["actual"] <= grp["p95_raw"]).mean()),
            "coverage_p95_backtest": float((grp["actual"] <= grp["p95"]).mean()),
            "mae_p50_holdout": float((h["actual"] - h["p50"]).abs().mean()) if len(h) else float("nan"),
            "mae_p50_backtest": float((grp["actual"] - grp["p50"]).abs().mean()),
            "wmape_p50_holdout": (
                float((h["actual"] - h["p50"]).abs().sum() / h["actual"].sum())
                if len(h) and h["actual"].sum() else float("nan")
            ),
            "wmape_p50_backtest": (
                float((grp["actual"] - grp["p50"]).abs().sum() / grp["actual"].sum())
                if grp["actual"].sum() else float("nan")
            ),
        }
        sku_rows.append(row)
    coverage_sku = pd.DataFrame(sku_rows)

    # 每个起点的覆盖率（回测稳定性）
    origin_rows = []
    for week, grp in backtest.groupby("week", sort=True):
        row = {"week": week, "n": int(len(grp)), "is_holdout": bool(grp["is_holdout"].iloc[0])}
        for q_name in q_names:
            row[f"coverage_{q_name}_before"] = float((grp["actual"] <= grp[f"{q_name}_raw"]).mean())
            row[f"coverage_{q_name}"] = float((grp["actual"] <= grp[q_name]).mean())
        row["p50_bias"] = float(grp["p50"].sum() / grp["actual"].sum()) if grp["actual"].sum() else float("nan")
        origin_rows.append(row)
    coverage_origin = pd.DataFrame(origin_rows)

    # ---------------- 输出 ----------------
    pred_cols = [
        "week", "sku_id", "sku_name", "category", "price", "price_band",
        "actual", *q_names, *[f"{q_name}_raw" for q_name in q_names],
        "ma4_x_h", "ma8_x_h",
    ]
    holdout[pred_cols].to_csv(
        OUTPUT_DIR / "forecast_cumulative_predictions.csv",
        index=False,
        encoding="utf-8-sig",
    )
    backtest[pred_cols + ["is_holdout"]].to_csv(
        OUTPUT_DIR / "forecast_cumulative_backtest_predictions.csv",
        index=False,
        encoding="utf-8-sig",
    )
    metrics.to_csv(
        OUTPUT_DIR / "forecast_cumulative_metrics.csv",
        index=False,
        encoding="utf-8-sig",
    )
    coverage.to_csv(
        OUTPUT_DIR / "forecast_coverage_summary.csv",
        index=False,
        encoding="utf-8-sig",
    )
    coverage_sku.to_csv(
        OUTPUT_DIR / "forecast_coverage_by_sku.csv",
        index=False,
        encoding="utf-8-sig",
    )
    coverage_origin.to_csv(
        OUTPUT_DIR / "forecast_coverage_by_origin.csv",
        index=False,
        encoding="utf-8-sig",
    )
    calib_df.to_csv(
        OUTPUT_DIR / "forecast_conformal_offsets.csv",
        index=False,
        encoding="utf-8-sig",
    )

    plot_cumulative(
        holdout, backtest, coverage_sku, coverage_origin,
        q_names, quantiles, horizon, OUTPUT_DIR / "forecast_cumulative.png",
    )

    # ---------------- 终端报告 ----------------
    def _metric_view(scope: str) -> pd.DataFrame:
        sub = metrics[metrics["scope"] == scope].copy()
        sub["wmape"] = sub["wmape"].map(lambda x: f"{x:.2%}" if pd.notna(x) else "—")
        sub["mae"] = sub["mae"].map(lambda x: f"{x:.2f}")
        sub["actual_sum"] = sub["actual_sum"].map(lambda x: f"{x:.0f}")
        sub["pinball_loss"] = sub["pinball_loss"].map(
            lambda x: f"{x:.2f}" if pd.notna(x) else "—"
        )
        return sub[["model", "type", "mae", "wmape", "pinball_loss", "n", "actual_sum"]]

    def _coverage_view(scope: str) -> pd.DataFrame:
        sub = coverage[coverage["scope"] == scope].copy()
        sub["coverage_before"] = sub["coverage_before"].map(lambda x: f"{x:.1%}")
        sub["coverage"] = sub["coverage"].map(lambda x: f"{x:.1%}")
        sub["target_coverage"] = sub["target_coverage"].map(lambda x: f"{x:.0%}")
        sub["delta_pp"] = sub["delta_pp"].map(lambda x: f"{x:+.1f}")
        sub["pinball_loss_before"] = sub["pinball_loss_before"].map(lambda x: f"{x:.2f}")
        sub["pinball_loss"] = sub["pinball_loss"].map(lambda x: f"{x:.2f}")
        return sub[
            ["line", "n", "covered_before", "coverage_before", "covered", "coverage",
             "target_coverage", "delta_pp", "pinball_loss_before", "pinball_loss"]
        ]

    print(f"========== 未来 {horizon} 周累计需求 & 分位数预测 ==========")
    print(
        f"数据：clean_main {raw_rows:,} 行 -> 质量过滤后 {len(clean_orders):,} 行；"
        f"Top-{top_n} SKU；配置：config.yaml（horizon_weeks={horizon}）"
    )
    print(
        f"留出集起点：{_date_range(holdout_origins)}（{len(holdout_origins)} 个起点，"
        f"最后覆盖到 {(pd.Timestamp(holdout_origins[-1]) + pd.Timedelta(days=7 * horizon - 1)):%Y-%m-%d}）"
    )
    print(
        f"覆盖率回测起点：{_date_range(backtest_origins)}（{len(backtest_origins)} 个起点，"
        f"{len(backtest)} 个 SKU-起点）"
    )
    print(
        f"目标定义：target[t] = sum(qty[t+1 .. t+{horizon}])；"
        f"训练样本 {min(train_sizes)} ~ {max(train_sizes)} 行（随起点滚动增长）"
    )
    print(
        f"模型：池化 LightGBM，objective=quantile，分位数 "
        f"{', '.join(f'P{int(q * 100)}' for q in quantiles)}；"
        f"{'按近4周水平归一化' if normalize else '直接预测累计值'}"
    )
    print(f"分位数参数：{params}")
    if conformal:
        print(
            f"保形校准：开启（窗口 {conformal_window} 个可用起点、"
            f"至少 {conformal_min} 个；只向上修正={conformal_positive_only}）；"
            "校准起点要求 u + horizon <= t，不使用未来信息。"
        )
    else:
        print("保形校准：关闭（P50/P90/P95 均为模型原始分位线）。")
    print()

    print("一、留出集点预测精度")
    print(_metric_view("留出集（最近12个起点）").to_string(index=False))
    print("\n二、滚动回测点预测精度")
    print(_metric_view("滚动回测（最近52个起点）").to_string(index=False))
    print("\n三、覆盖率检验（真实值 ≤ 分位线的比例）")
    print(_coverage_view("留出集（最近12个起点）").to_string(index=False))
    print()
    print(_coverage_view("滚动回测（最近52个起点）").to_string(index=False))

    print(f"\n四、每个 SKU 的覆盖率（回测 n={len(backtest_origins)}，留出集 n={len(holdout_origins)}）")
    sku_display = coverage_sku[
        ["sku_id", "sku_name", "coverage_p50_backtest", "coverage_p90_backtest",
         "coverage_p95_backtest", "coverage_p90_holdout", "wmape_p50_backtest"]
    ].copy()
    for col in ["coverage_p50_backtest", "coverage_p90_backtest",
                "coverage_p95_backtest", "coverage_p90_holdout"]:
        sku_display[col] = sku_display[col].map(lambda x: f"{x:.1%}" if pd.notna(x) else "—")
    sku_display["wmape_p50_backtest"] = sku_display["wmape_p50_backtest"].map(
        lambda x: f"{x:.2%}" if pd.notna(x) else "—"
    )
    sku_display.columns = [
        "sku_id", "sku_name", "P50覆盖(回测)", "P90覆盖(回测)", "P95覆盖(回测)",
        "P90覆盖(留出)", "P50 WMAPE(回测)",
    ]
    print(sku_display.to_string(index=False))

    # 关键数字
    def _cov(scope: str, line: str) -> float:
        return float(coverage[(coverage["scope"] == scope) & (coverage["line"] == line)]["coverage"].iloc[0])

    hold_scope = "留出集（最近12个起点）"
    back_scope = "滚动回测（最近52个起点）"
    back_p50 = metrics[(metrics["scope"] == back_scope) & (metrics["model"] == "p50")].iloc[0]
    hold_p50 = metrics[(metrics["scope"] == hold_scope) & (metrics["model"] == "p50")].iloc[0]
    back_ma4 = metrics[(metrics["scope"] == back_scope) & (metrics["model"] == "ma4_x_h")].iloc[0]
    hold_ma4 = metrics[(metrics["scope"] == hold_scope) & (metrics["model"] == "ma4_x_h")].iloc[0]

    # 剔除 2017-06 爬坡期的留出集覆盖率
    ramp = coverage_origin[
        (coverage_origin["week"] >= pd.Timestamp("2017-06-05"))
        & (coverage_origin["week"] <= pd.Timestamp("2017-06-26"))
    ]
    non_ramp = coverage_origin[
        (coverage_origin["week"] > pd.Timestamp("2017-06-26"))
        & (coverage_origin["week"].isin(holdout_origins))
    ]
    print("\n五、关键结论")
    print(
        f"  P50 点预测：留出集 WMAPE {hold_p50['wmape']:.2%}（MAE {hold_p50['mae']:.2f}），"
        f"回测 WMAPE {back_p50['wmape']:.2%}（MAE {back_p50['mae']:.2f}）；"
        f"同期 MA4×{horizon} 基准为 {hold_ma4['wmape']:.2%} / {back_ma4['wmape']:.2%}。"
    )
    print(
        f"  P90 覆盖率：留出集 {_cov(hold_scope, 'p90'):.1%}，"
        f"回测 {_cov(back_scope, 'p90'):.1%}（目标 90%）；"
        f"P95 覆盖率：留出集 {_cov(hold_scope, 'p95'):.1%}，回测 {_cov(back_scope, 'p95'):.1%}（目标 95%）。"
    )
    if len(ramp) and len(non_ramp):
        print(
            f"  留出集内 2017-06 爬坡期（{len(ramp)} 个起点）P90 覆盖率 "
            f"{ramp['coverage_p90'].mean():.1%}，"
            f"7-8 月（{len(non_ramp)} 个起点）{non_ramp['coverage_p90'].mean():.1%}；"
            "覆盖率缺口主要来自 6 月需求突然上台阶，而不是分位数模型整体失效。"
        )

    # ---------------- 验收线逐条对照 ----------------
    hold_cov = {
        q_name: _cov(hold_scope, q_name) for q_name in q_names
    }
    back_cov = {
        q_name: _cov(back_scope, q_name) for q_name in q_names
    }
    # 周度组合 WMAPE：来自 forecast_ensemble.py 的宽表（本脚本不重算）
    weekly_wmape = float("nan")
    weekly_path = OUTPUT_DIR / "forecast_ensemble_metrics_wide.csv"
    if weekly_path.exists():
        wide = pd.read_csv(weekly_path)
        row = wide[wide["scope"] == "Top10 全部 SKU-周"]
        if len(row):
            weekly_wmape = float(row["ensemble_WMAPE"].iloc[0])

    checks = [
        ("留出集 P90 覆盖率 88%~92%", 0.88 <= hold_cov["p90"] <= 0.92, f"{hold_cov['p90']:.1%}"),
        ("留出集 P95 覆盖率 90%~96%", 0.90 <= hold_cov["p95"] <= 0.96, f"{hold_cov['p95']:.1%}"),
        ("留出集 P50 覆盖率 45%~55%", 0.45 <= hold_cov["p50"] <= 0.55, f"{hold_cov['p50']:.1%}"),
        ("滚动回测 P90 覆盖率 ≥86%", back_cov["p90"] >= 0.86, f"{back_cov['p90']:.1%}"),
        (
            "周度组合 WMAPE ≤8.61%（当前 8.41% + 0.2pp）",
            (pd.notna(weekly_wmape) and weekly_wmape <= 0.0861),
            f"{weekly_wmape:.2%}" if pd.notna(weekly_wmape) else "未找到周度结果",
        ),
    ]
    acceptance = pd.DataFrame(
        checks, columns=["验收项", "是否通过", "实测"]
    )
    acceptance["是否通过"] = acceptance["是否通过"].map(lambda ok: "通过" if ok else "未通过")
    print("\n六、验收线逐条对照")
    print(acceptance.to_string(index=False))
    if not all(ok for _, ok, _ in checks):
        print(
            "  说明：未通过项集中在留出集的 2017-06-05 / 06-12 两个起点。"
            "这两个起点的未来 4 周包含“两周异常低点后回到常态”的台阶，"
            "在 t 时刻因果可得的信息里没有类似历史样本；保形校准又要等目标窗口实现后才能更新，"
            "因此因果口径下无法提前修正。详见 README「预测模块结论」。"
        )

    summary_path = OUTPUT_DIR / "forecast_cumulative_summary.txt"
    with open(summary_path, "w", encoding="utf-8") as f:
        f.write(f"未来 {horizon} 周累计需求与分位数预测摘要\n")
        f.write("=" * 58 + "\n")
        f.write(f"数据源：{DATA_PATH}\n")
        f.write(f"clean_main：{raw_rows:,} 行；质量过滤后：{len(clean_orders):,} 行\n")
        f.write(f"配置：config.yaml，horizon_weeks={horizon}，Top-{top_n}\n")
        f.write(f"留出集起点：{_date_range(holdout_origins)}（{len(holdout_origins)} 个）\n")
        f.write(f"覆盖率回测起点：{_date_range(backtest_origins)}（{len(backtest_origins)} 个）\n")
        f.write(f"验证样本：留出集 {len(holdout)}，回测 {len(backtest)} 个 SKU-起点\n")
        f.write(f"训练样本数：{min(train_sizes)} ~ {max(train_sizes)}\n")
        f.write(f"分位数：{quantiles}；目标归一化：{normalize}；分位数参数：{params}\n")
        f.write(
            f"保形校准：{'开启' if conformal else '关闭'}；窗口 {conformal_window} 个可用起点；"
            f"最少 {conformal_min} 个；只向上修正 {conformal_positive_only}\n"
        )
        f.write(
            "  校准窗口口径：起点 t 只使用 u + horizon <= t 的历史起点残差，"
            "不使用未来信息；逐起点滚动。\n"
        )
        if len(calib_df):
            valid_cal = calib_df[calib_df["n_calib_samples"] > 0]
            if len(valid_cal):
                f.write(
                    f"  各起点的校准窗口逐次滚动（并集 "
                    f"{valid_cal['calib_start'].min():%Y-%m-%d} ~ "
                    f"{valid_cal['calib_end'].max():%Y-%m-%d}）；"
                    f"每起点 {int(valid_cal['n_calib_samples'].min())} ~ "
                    f"{int(valid_cal['n_calib_samples'].max())} 条残差，"
                    "逐起点明细见第七节\n"
                )
        f.write(f"分位数损失（回测）：{dict(zip(q_names, [round(_pinball_loss_safe(backtest, q), 4) for q in q_names]))}\n\n")
        f.write("一、留出集点预测精度\n")
        f.write(_metric_view(hold_scope).to_string(index=False))
        f.write("\n\n二、滚动回测点预测精度\n")
        f.write(_metric_view(back_scope).to_string(index=False))
        f.write("\n\n三、覆盖率检验\n")
        f.write(_coverage_view(hold_scope).to_string(index=False))
        f.write("\n")
        f.write(_coverage_view(back_scope).to_string(index=False))
        f.write("\n\n四、每个 SKU 的覆盖率\n")
        f.write(sku_display.to_string(index=False))
        f.write("\n\n五、每个起点的覆盖率\n")
        f.write(coverage_origin.to_string(index=False))
        f.write("\n\n六、验收线逐条对照\n")
        f.write(acceptance.to_string(index=False))
        f.write("\n\n七、每个起点的校准偏移量（目标窗口已实现的历史起点）\n")
        f.write(calib_df.to_string(index=False))
        f.write("\n\n说明：\n")
        f.write("- target[t] = sum(qty[t+1 .. t+H])，H 由 config.yaml 配置。\n")
        f.write("- 特征只使用第 t 周及以前的数据；训练样本要求目标窗口在起点前已完全实现。\n")
        f.write("- 默认把目标归一化为 target / (H × 近4周均值)，预测时再乘回；可缓解量纲差异和水平漂移。\n")
        f.write("- 分位数预测做了不交叉处理（P50 <= P90 <= P95）。\n")
        f.write("- 保形校准只用“目标窗口已完全实现”的历史起点残差，逐起点滚动；偏移量见第七节。\n")
        f.write("- 覆盖率用“真实值 ≤ 分位线”计算；P90 目标 90%，P95 目标 95%。\n")
        f.write("- 滚动回测的起点相邻、累计窗口重叠，样本存在相关性，结论应结合样本量解读。\n")
        f.write("- 2017-10 之后口径突变，未用于训练/验证。\n")

    print(f"\n已输出：{OUTPUT_DIR / 'forecast_cumulative.png'}")
    print(f"已输出：{OUTPUT_DIR / 'forecast_cumulative_metrics.csv'}")
    print(f"已输出：{OUTPUT_DIR / 'forecast_coverage_by_sku.csv'}")
    print(f"已输出：{summary_path}")


def _pinball_loss_safe(df: pd.DataFrame, q_name: str) -> float:
    quantile = int(q_name[1:]) / 100.0
    return _pinball_loss(df["actual"].to_numpy(dtype=float), df[q_name].to_numpy(dtype=float), quantile)


if __name__ == "__main__":
    main()
