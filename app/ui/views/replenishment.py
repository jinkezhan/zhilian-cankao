"""补货建议页：按目标水位计算建议订货量。"""

from __future__ import annotations

import streamlit as st

from components import card_head, kpi_row, page_header, render_table, shorten
from data_loader import load_context, load_inventory_replenishment
from theme import URGENCY_ORDER


def render() -> None:
    context = load_context()
    inventory = load_inventory_replenishment()
    lead_time = int(context["lead_time_weeks"])
    review = int(context["review_period_weeks"])
    service_target = float(context["service_level_target"])

    page_header(
        "REPLENISHMENT",
        "补货建议",
        f"按目标水位与可用库存的差额计算建议订货量；提前期 {lead_time} 周，"
        f"检查周期 {review} 周，目标服务水平 {service_target:.0%}。",
    )

    action_count = int(inventory["urgency"].isin(["紧急", "建议补货"]).sum())
    total_qty = int(inventory["recommended_order_qty"].sum())

    kpi_row(
        [
            {
                "label": "待补货 SKU",
                "value": f"{action_count}",
                "unit": "个",
                "note": "紧急 / 建议补货",
                "accent": "danger" if action_count else "success",
            },
            {
                "label": "建议订货总量",
                "value": f"{total_qty:,}",
                "unit": "件",
                "note": "全部 SKU 合计",
            },
            {
                "label": "目标服务水平",
                "value": f"{service_target * 100:.0f}",
                "unit": "%",
                "note": f"P90 提前期需求 · 提前期 {lead_time} 周",
                "accent": "success",
            },
        ]
    )

    sorted_inventory = inventory.assign(
        _order=inventory["urgency"].map(URGENCY_ORDER).fillna(99)
    ).sort_values(
        ["_order", "recommended_order_qty"],
        ascending=[True, False],
    )

    display = sorted_inventory.rename(
        columns={
            "sku_id": "SKU",
            "sku_name": "品名",
            "available_inventory": "可用库存",
            "reorder_point": "再订货点",
            "target_level": "目标水位",
            "recommended_order_qty": "建议订货量",
            "urgency": "紧急度",
            "drift_status": "漂移",
        }
    )[
        [
            "SKU",
            "品名",
            "可用库存",
            "再订货点",
            "目标水位",
            "建议订货量",
            "紧急度",
            "漂移",
        ]
    ]
    display["SKU"] = display["SKU"].astype(str)

    with st.container(border=True):
        card_head(
            "补货计划",
            "按紧急度排序。建议订货量 = max(0, 目标水位 − 可用库存)，"
            "并按 MOQ / 包装倍数取整；前端不重算。",
        )
        render_table(
            display,
            status_columns=("紧急度", "漂移"),
            wrap_columns=("品名",),
            integer_columns=(
                "可用库存",
                "再订货点",
                "目标水位",
                "建议订货量",
            ),
        )
        st.markdown(
            '<div class="caption-note">紧急度按再订货点判断（可用库存 &lt; 再订货点 → 建议补货），'
            "因此「正常」SKU 也可能因目标水位出现小额补货建议。</div>",
            unsafe_allow_html=True,
        )

    with st.expander("查看补货计算依据"):
        lines = [
            f"- **{int(row.sku_id)} · {shorten(row.sku_name, 38)}**：{row.reason}"
            for row in sorted_inventory.itertuples(index=False)
        ]
        st.markdown("\n".join(lines))
