"""可复用界面组件与图表。"""

from __future__ import annotations

import html
from typing import Sequence

import altair as alt
import numpy as np
import pandas as pd
import streamlit as st

from theme import (
    BADGE_CLASS,
    PRIMARY,
    STRATEGY_COLORS,
)


def shorten(text: str, limit: int = 34) -> str:
    text = str(text)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def page_header(kicker: str, title: str, subtitle: str) -> None:
    st.markdown(
        '<div class="page-header">'
        f'<div class="page-kicker">{html.escape(kicker)}</div>'
        f"<h1>{html.escape(title)}</h1>"
        f"<p>{html.escape(subtitle)}</p>"
        "</div>",
        unsafe_allow_html=True,
    )


def kpi_row(items: Sequence[dict[str, str]]) -> None:
    cards: list[str] = []
    accent_classes = {
        "danger": " kpi-danger",
        "success": " kpi-success",
        "warning": " kpi-warning",
    }
    for item in items:
        accent = accent_classes.get(item.get("accent", ""), "")
        cards.append(
            f'<div class="kpi-card{accent}">'
            f'<div class="kpi-label">{html.escape(item["label"])}</div>'
            f'<div class="kpi-value">{html.escape(item["value"])}'
            f'<span class="kpi-unit">{html.escape(item.get("unit", ""))}</span></div>'
            f'<div class="kpi-note">{html.escape(item.get("note", ""))}</div>'
            "</div>"
        )
    st.markdown('<div class="kpi-grid">' + "".join(cards) + "</div>", unsafe_allow_html=True)


def card_head(title: str, subtitle: str = "") -> None:
    sub = f'<div class="card-sub">{html.escape(subtitle)}</div>' if subtitle else ""
    st.markdown(
        f'<div class="card-head"><div class="card-title">{html.escape(title)}</div>{sub}</div>',
        unsafe_allow_html=True,
    )


def notice(text_html: str) -> None:
    st.markdown(
        f'<div class="notice"><span class="notice-icon">ⓘ</span><div>{text_html}</div></div>',
        unsafe_allow_html=True,
    )


def insight(text_html: str) -> None:
    st.markdown(f'<div class="insight">{text_html}</div>', unsafe_allow_html=True)


def render_table(
    df: pd.DataFrame,
    status_columns: Sequence[str] = (),
    wrap_columns: Sequence[str] = (),
    integer_columns: Sequence[str] = (),
) -> None:
    """渲染深蓝表头的 HTML 表格；状态列自动显示为彩色徽标。"""
    if df.empty:
        st.info("暂无数据")
        return

    status_set = set(status_columns)
    wrap_set = set(wrap_columns)
    integer_set = set(integer_columns)
    numeric_columns = {
        col for col in df.columns if pd.api.types.is_numeric_dtype(df[col])
    }

    header_cells = "".join(
        (
            f'<th class="num">{html.escape(str(col))}</th>'
            if col in numeric_columns and col not in status_set
            else f"<th>{html.escape(str(col))}</th>"
        )
        for col in df.columns
    )
    body_rows: list[str] = []

    for _, row in df.iterrows():
        cells: list[str] = []
        for col in df.columns:
            value = row[col]
            if pd.isna(value):
                cells.append("<td>—</td>")
                continue

            is_number = False
            if isinstance(value, pd.Timestamp):
                text = value.strftime("%Y-%m-%d")
            elif col in integer_set and isinstance(value, (int, float, np.integer, np.floating)):
                text = f"{int(round(float(value))):,}"
                is_number = True
            elif isinstance(value, (int, np.integer)):
                text = f"{int(value):,}"
                is_number = True
            elif isinstance(value, (float, np.floating)):
                text = f"{float(value):,.1f}"
                is_number = True
            else:
                text = str(value)

            if col in status_set and text in BADGE_CLASS:
                cells.append(
                    f'<td><span class="badge {BADGE_CLASS[text]}">'
                    f"{html.escape(text)}</span></td>"
                )
            elif is_number:
                cells.append(f'<td class="num">{html.escape(text)}</td>')
            elif col in wrap_set:
                cells.append(f'<td class="wrap">{html.escape(text)}</td>')
            else:
                cells.append(f"<td>{html.escape(text)}</td>")

        body_rows.append("<tr>" + "".join(cells) + "</tr>")

    st.markdown(
        '<div class="table-wrap"><table class="data-table"><thead><tr>'
        + header_cells
        + "</tr></thead><tbody>"
        + "".join(body_rows)
        + "</tbody></table></div>",
        unsafe_allow_html=True,
    )


def render_altair(chart: alt.Chart) -> None:
    """统一 Altair 图表的容器宽度与主题。

    theme=None 保留自定义配色；Streamlit 1.30 / 1.66 都支持
    use_container_width（新版本可能提示弃用，但不会影响渲染）。
    """
    st.altair_chart(chart, use_container_width=True, theme=None)


def forecast_chart(frame: pd.DataFrame) -> alt.Chart:
    """累计需求：实际值 + P50/P90/P95 分位数预测。"""
    series_labels = {
        "actual": "实际值",
        "p50": "P50 预测",
        "p90": "P90 预测",
        "p95": "P95 预测",
    }
    domain = list(series_labels.values())
    colors = ["#1E6FB8", "#E8963E", "#F0B95C", "#C79A5A"]

    melted = frame.melt(
        id_vars=["week"],
        value_vars=list(series_labels.keys()),
        var_name="series",
        value_name="value",
    ).dropna(subset=["value"])
    melted["series"] = melted["series"].map(series_labels)

    tooltip = [
        alt.Tooltip("week:T", title="预测起点", format="%Y-%m-%d"),
        alt.Tooltip("series:N", title="序列"),
        alt.Tooltip("value:Q", title="累计需求", format=",.0f"),
    ]

    x_axis = alt.Axis(
        format="%m-%d",
        labelAngle=0,
        grid=False,
        tickCount=12,
        labelColor="#64748B",
        domainColor="#DCE7F3",
        tickColor="#DCE7F3",
    )
    y_axis = alt.Axis(
        gridColor="#E8EFF7",
        titleColor="#7B8FA4",
        labelColor="#64748B",
        format=",.0f",
        tickCount=6,
    )
    y_scale = alt.Scale(zero=False)

    lines = alt.Chart(melted).mark_line(strokeWidth=2.2).encode(
        x=alt.X("week:T", title=None, axis=x_axis),
        y=alt.Y(
            "value:Q",
            title="累计需求（件）",
            scale=y_scale,
            axis=y_axis,
        ),
        color=alt.Color(
            "series:N",
            scale=alt.Scale(domain=domain, range=colors),
            legend=alt.Legend(
                title=None,
                orient="top",
                direction="horizontal",
                labelColor="#475569",
                symbolType="stroke",
            ),
        ),
        strokeDash=alt.condition(
            alt.datum.series == "实际值",
            alt.value([1, 0]),
            alt.value([5, 4]),
        ),
        tooltip=tooltip,
    )

    points = alt.Chart(melted[melted["series"] == "实际值"]).mark_circle(
        size=36,
        color=PRIMARY,
        opacity=0.95,
    ).encode(
        x=alt.X("week:T", title=None, axis=x_axis),
        y=alt.Y(
            "value:Q",
            title="累计需求（件）",
            scale=y_scale,
            axis=y_axis,
        ),
        tooltip=tooltip,
    )

    latest_origin = frame["week"].max()
    rule = alt.Chart(pd.DataFrame({"week": [latest_origin]})).mark_rule(
        color="#94A3B8",
        strokeDash=[3, 3],
        opacity=0.9,
    ).encode(x=alt.X("week:T", title=None, axis=x_axis))

    return (
        alt.layer(lines, points, rule)
        .resolve_scale(y="shared")
        .properties(height=360)
        .configure_view(strokeWidth=0)
        .configure_axis(labelFontSize=11, titleFontSize=12, titlePadding=10)
        .configure_legend(labelFontSize=12, symbolStrokeWidth=2.2)
    )


def weekly_demand_chart(frame: pd.DataFrame) -> alt.Chart:
    """全站周度需求：面积 + 折线。"""
    base = alt.Chart(frame).encode(
        x=alt.X(
            "period:T",
            title=None,
            axis=alt.Axis(
                format="%Y-%m",
                labelAngle=0,
                tickCount=8,
                grid=False,
                labelColor="#64748B",
                domainColor="#DCE7F3",
                tickColor="#DCE7F3",
            ),
        ),
        y=alt.Y(
            "demand_qty:Q",
            title="周需求（件）",
            axis=alt.Axis(
                gridColor="#E8EFF7",
                titleColor="#7B8FA4",
                labelColor="#64748B",
                format=",.0f",
            ),
        ),
    )
    area = base.mark_area(color=PRIMARY, opacity=0.12)
    line = base.mark_line(color=PRIMARY, strokeWidth=2.2).encode(
        tooltip=[
            alt.Tooltip("period:T", title="周", format="%Y-%m-%d"),
            alt.Tooltip("demand_qty:Q", title="需求量", format=",.0f"),
        ]
    )
    return (
        alt.layer(area, line)
        .properties(height=260)
        .configure_view(strokeWidth=0)
        .configure_axis(labelFontSize=11, titleFontSize=12)
    )


def strategy_bar_chart(
    frame: pd.DataFrame,
    field: str,
    title: str,
    axis_format: str,
    label_format: str,
) -> alt.Chart:
    """三策略对比柱状图。"""
    domain = frame["strategy_label"].tolist()
    bars = alt.Chart(frame).mark_bar(
        cornerRadiusTopLeft=6,
        cornerRadiusTopRight=6,
    ).encode(
        x=alt.X(
            "strategy_label:N",
            title=None,
            sort=domain,
            axis=alt.Axis(labelAngle=0, labelColor="#475569", labelFontSize=12),
        ),
        y=alt.Y(
            f"{field}:Q",
            title=None,
            axis=alt.Axis(
                format=axis_format,
                gridColor="#E8EFF7",
                titleColor="#7B8FA4",
                labelColor="#64748B",
            ),
        ),
        color=alt.Color(
            "strategy_label:N",
            scale=alt.Scale(domain=domain, range=STRATEGY_COLORS),
            legend=None,
        ),
        tooltip=[
            alt.Tooltip("strategy_label:N", title="策略"),
            alt.Tooltip(f"{field}:Q", title=title, format=label_format),
        ],
    )
    labels = alt.Chart(frame).mark_text(
        dy=-8,
        color="#334155",
        fontSize=12,
        fontWeight="bold",
    ).encode(
        x=alt.X("strategy_label:N", sort=domain),
        y=alt.Y(f"{field}:Q"),
        text=alt.Text(f"{field}:Q", format=label_format),
    )
    return (
        alt.layer(bars, labels)
        .properties(height=270)
        .configure_view(strokeWidth=0)
        .configure_axis(labelFontSize=11, titleFontSize=12)
    )
