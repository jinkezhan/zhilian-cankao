"""预测模块产物验收自检。"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

OUT = Path("output")
MODELS = Path("models")


def main() -> None:
    pred = pd.read_csv(OUT / "freshretail_forecast_predictions.csv", encoding="utf-8-sig")
    bdem = pd.read_csv(OUT / "freshretail_forecast_predictions_b_demand.csv", encoding="utf-8-sig")
    ab = pd.read_csv(OUT / "freshretail_forecast_predictions_ab.csv", encoding="utf-8-sig")
    metrics = pd.read_csv(OUT / "freshretail_forecast_metrics.csv", encoding="utf-8-sig")
    imp = pd.read_csv(OUT / "freshretail_forecast_importance.csv", encoding="utf-8-sig")
    diag = json.loads((OUT / "freshretail_forecast_diagnostics.json").read_text(encoding="utf-8"))
    report = (OUT / "freshretail_forecast_report.md").read_text(encoding="utf-8")

    assert len(pred) == 1400, len(pred)
    assert not pred.isna().any().any(), pred.isna().sum()
    assert pred.duplicated(["store_id", "product_id", "dt"]).sum() == 0
    assert pred.groupby("horizon").size().eq(200).all()
    cov50 = float((pred["y_actual"] <= pred["y_pred_p50"]).mean())
    cov90 = float((pred["y_actual"] <= pred["y_pred_p90"]).mean())
    assert len(bdem) == 1400 and not bdem.isna().any().any()
    assert len(ab) == 11200, len(ab)
    assert len(metrics) == 24, len(metrics)

    a = metrics[(metrics.model == "lgbm_a") & (metrics.scope == "全部")].iloc[0]
    b = metrics[(metrics.model == "lgbm_b") & (metrics.scope == "全部")].iloc[0]
    b2 = metrics[(metrics.model == "lgbm_b2") & (metrics.scope == "全部")].iloc[0]
    po = metrics[(metrics.model == "lgbm_a_poisson") & (metrics.scope == "全部")].iloc[0]
    bases = metrics[metrics.model.str.startswith("baseline") & metrics.scope.eq("全部")]
    best_base = bases.loc[bases.wmape.idxmin()]
    improvement = (best_base.wmape - a.wmape) / best_base.wmape

    assert (metrics.model == "lgbm_a_l2").sum() == 3
    assert not diag["b_decision"]["b2_bias_ok"]
    assert not diag["b_decision"]["adopted"]
    assert diag["objective_comparison"]["selected"] == "l2"
    assert diag["acceptance"]["numbers_consistent"] is True

    cats = set(imp["说明"].str.extract(r"^\[(.+?)\]")[0])
    required_cats = {"滞后", "滚动", "日历", "天气"}
    assert required_cats.issubset(cats), cats
    assert {"roll7_mean", "weekday", "avg_temperature", "lag_h"}.issubset(set(imp.feature))

    for name in ["freshretail_lgbm_p50.txt", "freshretail_lgbm_p90.txt",
                 "freshretail_lgbm_b_p50.txt", "freshretail_lgbm_b_p90.txt",
                 "freshretail_lgbm_b2_p50.txt", "freshretail_lgbm_a_poisson.txt"]:
        assert (MODELS / name).exists(), name

    # 报告与 CSV 的数字一致性（抽样关键值）
    for token in ["0.258764", "0.281547", "8.09%", "50.57%", "89.64%",
                  "+15.47%", "+5.08%", "给答辩的一句话口径",
                  "output/freshretail_forecast_metrics.csv"]:
        assert token in report, token

    print(f"predictions: {len(pred)} rows, coverage P50={cov50:.4f}, P90={cov90:.4f}")
    print(f"primary lgbm_a: WMAPE={a.wmape:.6f}, bias={a.bias:.6f}, "
          f"MAPE_med={a.mape_median:.6f}")
    print(f"B1: {b.wmape:.6f} / {b.bias:+.6f}; B2: {b2.wmape:.6f} / {b2.bias:+.6f}")
    print(f"Poisson: {po.wmape:.6f} vs L2 {a.wmape:.6f}")
    print(f"best baseline:  {best_base.model} WMAPE={best_base.wmape:.6f}; "
          f"primary improvement={improvement:.2%}")
    print(f"importance categories: {sorted(cats)}; rows={len(imp)}")
    print("model files: OK")
    assert 0.42 <= cov50 <= 0.58, cov50
    assert 0.82 <= cov90 <= 0.95, cov90
    assert improvement >= 0.05, improvement
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
