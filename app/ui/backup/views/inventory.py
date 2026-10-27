"""库存健康页：补货点、安全库存与预测漂移。"""

from __future__ import annotations

import streamlit as st

from components import card_head, kpi_row, page_header, render_table
from data_loader import load_context, load_inventory_replenishment
from theme import URGENCY_ORDER


def render() -> None:
    context = load_context()
    inventory = load_inventory_replenishment()

    page_header(
        "INVENTORY HEALTH",
        "库存健康",
        f"库存快照 {context['snapshot_week']:%Y-%m-%d}，采购提前期 "
        f"{int(context['lead_time_weeks'])} 周；以 P90 提前期需求与安全库存识别需补货的 SKU。",
    )

    total = len(inventory)
    action = int(inventory["urgency"].isin(["紧急", "建议补货"]).sum())
    drift = int((inventory["drift_status"] != "正常").sum())

    kpi_row(
        [
            {
                "label": "监控 SKU",
                "value": f"{total}",
                "unit": "个",
                "note": "Top-N 需求 SKU",
            },
            {
                "label": "待补货",
                "value": f"{action}",
                "unit": "个",
                "note": "紧急 / 建议补货",
                "accent": "danger" if action else "success",
            },
            {
                "label": "预测漂移预警",
                "value": f"{drift}",
                "unit": "个",
                "note": "漂移状态不是「正常」",
                "accent": "danger" if drift else "success",
            },
        ]
    )

    sorted_inventory = inventory.assign(
        _order=inventory["urgency"].map(URGENCY_ORDER).fillna(99)
    ).sort_values(["_order", "available_inventory"], ascending=[True, True])

    display = sorted_inventory.rename(
        columns={
            "sku_id": "SKU",
            "sku_name": "品名",
            "available_inventory": "可用库存",
            "reorder_point": "再订货点",
            "safety_stock": "安全库存",
            "target_level": "目标水位",
            "urgency": "状态",
            "drift_status": "漂移",
        }
    )[
        [
            "SKU",
            "品名",
            "可用库存",
            "再订货点",
            "安全库存",
            "目标水位",
            "状态",
            "漂移",
        ]
    ]
    display["SKU"] = display["SKU"].astype(str)

    with st.container(border=True):
        card_head(
            "库存明细",
            "按紧急度排序；可用库存 = 现有库存 + 在途库存。"
            "所有数值来自 output/inventory_replenishment.csv，前端不重算。",
        )
        render_table(
            display,
            status_columns=("状态", "漂移"),
            wrap_columns=("品名",),
            integer_columns=(
                "可用库存",
                "再订货点",
                "安全库存",
                "目标水位",
            ),
        )
        st.markdown(
            '<div class="caption-note">紧急度规则：可用库存 &lt; 再订货点 → 建议补货；'
            "同时低于 0.5 × 安全库存 → 紧急。</div>",
            unsafe_allow_html=True,
        )

    drift_rows = inventory[inventory["drift_status"] != "正常"]
    if not drift_rows.empty:
        with st.expander("查看预测漂移详情"):
            for row in drift_rows.sort_values("sku_id").itertuples(index=False):
                st.markdown(
                    f"**{int(row.sku_id)} · {row.sku_name}**  \n"
                    f"{row.drift_detail or '暂无详细说明'}"
                )
