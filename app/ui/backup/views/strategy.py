"""策略对比页：三种补货策略的仿真结果。"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from components import (
    card_head,
    insight,
    kpi_row,
    page_header,
    render_altair,
    render_table,
    strategy_bar_chart,
)
from data_loader import load_policy_simulation


def _row_for(frame: pd.DataFrame, strategy: str) -> pd.Series | None:
    matched = frame[frame["strategy"].astype(str) == strategy]
    return None if matched.empty else matched.iloc[0]


def render() -> None:
    simulation = load_policy_simulation()

    if simulation.empty:
        page_header(
            "POLICY SIMULATION",
            "策略对比",
            "三种补货策略的仿真结果。",
        )
        st.info("暂无策略仿真数据。")
        return

    first = simulation.iloc[0]
    n_weeks = int(first["n_weeks"]) if "n_weeks" in simulation.columns else 0
    n_skus = int(first["n_skus"]) if "n_skus" in simulation.columns else 0
    period_start = first.get("period_start", "")
    period_end = first.get("period_end", "")

    page_header(
        "POLICY SIMULATION",
        "策略对比",
        f"在相同需求、初始库存与提前期下，对比经验规则、移动平均与分位数 ROP 三种补货策略；"
        f"仿真周期 {period_start} 至 {period_end}，覆盖 {n_skus} 个 SKU。",
    )

    best_fill = simulation.loc[simulation["fill_rate"].idxmax()]
    lowest_cost = simulation.loc[simulation["total_cost"].idxmin()]

    kpi_row(
        [
            {
                "label": "仿真周期",
                "value": f"{n_weeks}",
                "unit": "周",
                "note": f"{n_skus} 个 SKU · {period_start} 至 {period_end}",
            },
            {
                "label": "最高满足率",
                "value": f"{best_fill['fill_rate'] * 100:.2f}",
                "unit": "%",
                "note": str(best_fill["strategy_label"]),
                "accent": "success",
            },
            {
                "label": "最低总成本",
                "value": f"¥{lowest_cost['total_cost']:,.0f}",
                "unit": "",
                "note": str(lowest_cost["strategy_label"]),
                "accent": "warning",
            },
        ]
    )

    display = simulation[
        [
            "strategy_label",
            "fill_rate",
            "avg_inventory",
            "total_cost",
            "stockout_weeks",
            "n_orders",
        ]
    ].rename(
        columns={
            "strategy_label": "策略",
            "fill_rate": "满足率",
            "avg_inventory": "平均库存",
            "total_cost": "总成本（元）",
            "stockout_weeks": "缺货周数",
            "n_orders": "订单数",
        }
    )
    display["满足率 (%)"] = display["满足率"] * 100
    display = display.drop(columns=["满足率"])[
        [
            "策略",
            "满足率 (%)",
            "平均库存",
            "总成本（元）",
            "缺货周数",
            "订单数",
        ]
    ]

    with st.container(border=True):
        card_head(
            "三策略对比",
            "满足率越高越好；平均库存与总成本越低越好。指标来自 "
            "output/inventory_policy_simulation.csv。",
        )
        render_table(
            display,
            integer_columns=("缺货周数", "订单数"),
        )

    with st.container(border=True):
        card_head(
            "关键指标对比",
            "左：订单满足率；右：仿真期总成本（订单成本 + 持有成本 + 缺货成本）。",
        )
        left, right = st.columns(2)
        with left:
            render_altair(
                strategy_bar_chart(
                    simulation,
                    field="fill_rate",
                    title="满足率",
                    axis_format=".1%",
                    label_format=".1%",
                )
            )
        with right:
            render_altair(
                strategy_bar_chart(
                    simulation,
                    field="total_cost",
                    title="总成本（元）",
                    axis_format=",.0f",
                    label_format=",.0f",
                )
            )

    row_c = _row_for(simulation, "C")
    row_b = _row_for(simulation, "B")
    row_a = _row_for(simulation, "A")
    if row_c is not None and row_b is not None and row_a is not None:
        insight(
            f"从仿真结果看，<b>{row_c['strategy_label']}</b> 的满足率为 "
            f"<b>{row_c['fill_rate']:.2%}</b>，与 {row_b['strategy_label']} 基本持平；"
            f"平均库存 <b>{row_c['avg_inventory']:,.1f}</b> 件，低于 "
            f"{row_b['strategy_label']} 的 {row_b['avg_inventory']:,.1f} 件；"
            f"总成本 <b>¥{row_c['total_cost']:,.0f}</b>，也低于 "
            f"¥{row_b['total_cost']:,.0f}。"
            f"{row_a['strategy_label']}的总成本最低（¥{row_a['total_cost']:,.0f}），"
            f"但缺货周数为 {int(row_a['stockout_weeks'])} 周、满足率仅 "
            f"{row_a['fill_rate']:.2%}。"
        )
