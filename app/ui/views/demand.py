"""需求预测页：累计需求回测与分位数预测。"""

from __future__ import annotations

import html

import pandas as pd
import streamlit as st

from components import (
    card_head,
    forecast_chart,
    kpi_row,
    page_header,
    render_altair,
    render_table,
    shorten,
    weekly_demand_chart,
)
from data_loader import (
    load_context,
    load_demand_weekly,
    load_forecast_metrics,
    load_forecast_predictions,
    load_sku_selection,
)


def _overall_wmape(metrics: pd.DataFrame) -> float | None:
    holdout = metrics[
        metrics["scope"].astype(str).str.startswith("留出集")
        & (metrics["model"].astype(str) == "p50")
    ]
    if holdout.empty:
        return None
    value = holdout["wmape"].iloc[0]
    return None if pd.isna(value) else float(value)


def render() -> None:
    context = load_context()
    horizon = int(context["horizon_weeks"])

    predictions = load_forecast_predictions()
    metrics = load_forecast_metrics()
    selection = load_sku_selection()
    weekly_demand = load_demand_weekly()

    page_header(
        "DEMAND FORECAST",
        "需求预测",
        f"基于 LightGBM 分位数模型，预测未来 {horizon} 周累计需求；"
        "按历史起点回测对比实际值与 P50 / P90 / P95。",
    )

    totals = (
        predictions.groupby(["sku_id", "sku_name"], as_index=False)
        .agg(actual_sum=("actual", "sum"), p50_sum=("p50", "sum"))
        .fillna(0)
    )
    totals["sort_key"] = totals["actual_sum"].where(
        totals["actual_sum"] > 0, totals["p50_sum"]
    )
    totals = totals.sort_values(["sort_key", "sku_id"], ascending=[False, True])
    options = [int(value) for value in totals["sku_id"].tolist()]
    name_map = {
        int(row.sku_id): str(row.sku_name)
        for row in totals.itertuples(index=False)
    }

    if not options:
        st.warning("当前没有可展示的 SKU 预测数据。")
        return

    # 数据重新生成后，旧的选择可能已不存在
    if st.session_state.get("demand_sku") not in options:
        st.session_state.pop("demand_sku", None)

    with st.container(border=True):
        pick_col, meta_col = st.columns([1.3, 2.7])
        with pick_col:
            selected_sku = st.selectbox(
                "SKU 筛选",
                options=options,
                format_func=lambda sku_id: (
                    f"{sku_id} · {shorten(name_map.get(sku_id, str(sku_id)), 36)}"
                ),
                key="demand_sku",
            )
        with meta_col:
            frame = predictions[
                predictions["sku_id"] == selected_sku
            ].sort_values("week")
            latest = frame.iloc[-1]
            st.markdown(
                '<div class="entity">'
                '<div class="entity-label">当前产品</div>'
                f'<div class="entity-name">{html.escape(str(latest["sku_name"]))}</div>'
                f'<div class="entity-meta">SKU {selected_sku} · '
                f'{html.escape(str(latest["category"]))} · '
                f'价格带 {html.escape(str(latest["price_band"]))} · '
                f'回测起点 {len(frame)} 个</div>'
                "</div>",
                unsafe_allow_html=True,
            )

    latest_p50 = float(latest["p50"]) if pd.notna(latest["p50"]) else None
    latest_p90 = float(latest["p90"]) if pd.notna(latest["p90"]) else None

    selected_selection = selection[selection["sku_id"] == selected_sku]
    if not selected_selection.empty and pd.notna(selected_selection["select_WMAPE"].iloc[0]):
        wmape = float(selected_selection["select_WMAPE"].iloc[0])
        selection_weeks = (
            int(selected_selection["weeks"].iloc[0])
            if "weeks" in selected_selection.columns
            and pd.notna(selected_selection["weeks"].iloc[0])
            else None
        )
        wmape_note = (
            f"该 SKU 最近 {selection_weeks} 个起点"
            if selection_weeks
            else "该 SKU 的历史回测"
        )
    else:
        wmape = _overall_wmape(metrics)
        wmape_note = "整体留出集 P50"

    if wmape is None:
        wmape_value = "—"
        wmape_accent = ""
    else:
        wmape_value = f"{wmape * 100:.1f}"
        wmape_accent = (
            "success" if wmape <= 0.12 else "warning" if wmape <= 0.18 else "danger"
        )

    kpi_row(
        [
            {
                "label": "最新起点 P50",
                "value": f"{latest_p50:,.0f}" if latest_p50 is not None else "—",
                "unit": "件",
                "note": f"{latest['week']:%Y-%m-%d} 起未来 {horizon} 周",
            },
            {
                "label": "P90 补货参考",
                "value": f"{latest_p90:,.0f}" if latest_p90 is not None else "—",
                "unit": "件",
                "note": "库存模块按 P90 计算再订货点",
            },
            {
                "label": "回测 WMAPE",
                "value": wmape_value,
                "unit": "%" if wmape is not None else "",
                "note": wmape_note,
                "accent": wmape_accent,
            },
        ]
    )

    with st.container(border=True):
        card_head(
            "累计需求：实际 vs 分位数预测",
            f"横轴为预测起点，每个点表示从该周起未来 {horizon} 周的累计需求；"
            "不同色阶虚线为 P50 / P90 / P95，灰色虚线标出最新决策起点。",
        )
        render_altair(forecast_chart(frame))

    with st.expander(f"查看全站历史周需求（{weekly_demand['period'].min():%Y-%m-%d} 至 "
                     f"{weekly_demand['period'].max():%Y-%m-%d}）"):
        render_altair(weekly_demand_chart(weekly_demand))
        st.markdown(
            '<div class="caption-note">数据来自 output/demand_weekly.csv，'
            "用于观察整体需求水平；当前页面按 SKU 展示模型回测结果。</div>",
            unsafe_allow_html=True,
        )

    with st.expander("查看整体模型回测指标"):
        holdout = metrics[
            metrics["scope"].astype(str).str.startswith("留出集")
        ].copy()
        if holdout.empty:
            st.info("暂无留出集指标。")
        else:
            display = holdout[["model", "type", "mae", "wmape"]].rename(
                columns={
                    "model": "模型",
                    "type": "类型",
                    "mae": "MAE",
                    "wmape": "WMAPE",
                }
            )
            display["WMAPE (%)"] = display["WMAPE"] * 100
            display = display.drop(columns=["WMAPE"])
            render_table(display)
            st.markdown(
                '<div class="caption-note">留出集为最近 12 个起点；'
                "指标来自 output/forecast_cumulative_metrics.csv，页面不做重算。</div>",
                unsafe_allow_html=True,
            )
