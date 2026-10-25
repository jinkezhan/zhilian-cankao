"""智能问答页：基于模型产物的规则演示（未接入 LLM）。"""

from __future__ import annotations

import re

import pandas as pd
import streamlit as st

from components import page_header
from data_loader import (
    load_context,
    load_forecast_predictions,
    load_inventory_replenishment,
    load_policy_simulation,
    load_sku_selection,
)


def _find_sku(question: str, inventory: pd.DataFrame) -> int | None:
    valid = {str(int(value)) for value in inventory["sku_id"].dropna().unique()}
    for token in re.findall(r"(?<!\d)(\d{2,5})(?!\d)", question):
        if token in valid:
            return int(token)
    return None


def _inventory_answer(row: pd.Series) -> str:
    return (
        f"**{int(row['sku_id'])} · {row['sku_name']}**\n\n"
        f"- 可用库存：{row['available_inventory']:,.1f} 件\n"
        f"- 再订货点：{row['reorder_point']:,.1f} 件\n"
        f"- 安全库存：{row['safety_stock']:,.1f} 件\n"
        f"- 目标水位：{row['target_level']:,.1f} 件\n"
        f"- 建议订货量：**{int(row['recommended_order_qty']):,} 件**\n"
        f"- 紧急度：**{row['urgency']}**；漂移状态：{row['drift_status']}\n\n"
        f"补货依据：{row['reason']}"
    )


def _forecast_answer(
    sku_id: int,
    predictions: pd.DataFrame,
    selection: pd.DataFrame,
    horizon: int,
) -> str:
    latest_week = predictions["week"].max()
    latest = predictions[
        (predictions["week"] == latest_week) & (predictions["sku_id"] == sku_id)
    ]
    if latest.empty:
        return f"没有找到 SKU {sku_id} 的最新预测数据。"
    row = latest.iloc[0]

    wmape_text = ""
    selected = selection[selection["sku_id"] == sku_id]
    if not selected.empty and pd.notna(selected["select_WMAPE"].iloc[0]):
        wmape_text = (
            f"\n- 该 SKU 选择模型回测 WMAPE："
            f"{float(selected['select_WMAPE'].iloc[0]):.2%}"
        )

    actual_text = (
        f"{row['actual']:,.0f} 件" if pd.notna(row["actual"]) else "尚未实现"
    )
    return (
        f"**{sku_id} · {row['sku_name']}**\n\n"
        f"最新起点 **{latest_week:%Y-%m-%d}**，未来 **{horizon} 周**累计需求：\n"
        f"- P50：**{row['p50']:,.0f} 件**\n"
        f"- P90：**{row['p90']:,.0f} 件**\n"
        f"- P95：**{row['p95']:,.0f} 件**\n"
        f"- 对应窗口实际值：{actual_text}"
        f"{wmape_text}\n\n"
        "库存模块使用 P90 提前期需求计算再订货点。"
    )


def _top_demand_answer(predictions: pd.DataFrame, horizon: int) -> str:
    latest_week = predictions["week"].max()
    latest = predictions[predictions["week"] == latest_week].sort_values(
        "p50", ascending=False
    )
    top = latest.head(3)
    lines = [
        f"- **{int(row.sku_id)} · {row.sku_name}**：P50 {row.p50:,.0f} 件，"
        f"P90 {row.p90:,.0f} 件"
        for row in top.itertuples(index=False)
    ]
    return (
        f"最新起点 {latest_week:%Y-%m-%d} 的未来 {horizon} 周累计需求最高 SKU：\n\n"
        + "\n".join(lines)
        + "\n\n可以继续问「365 未来需求如何？」查看单个 SKU。"
    )


def _inventory_list_answer(inventory: pd.DataFrame) -> str:
    action = inventory[inventory["urgency"].isin(["紧急", "建议补货"])].sort_values(
        "recommended_order_qty", ascending=False
    )
    if action.empty:
        return "当前所有 SKU 都处于正常水位，没有需要立即补货的物料。"
    lines = [
        f"- **{int(row.sku_id)} · {row.sku_name}**：可用库存 "
        f"{row.available_inventory:,.0f} / 再订货点 {row.reorder_point:,.0f}，"
        f"建议订货 **{int(row.recommended_order_qty):,} 件**（{row.urgency}）"
        for row in action.itertuples(index=False)
    ]
    return (
        f"当前有 **{len(action)} 个 SKU** 需要补货：\n\n"
        + "\n".join(lines)
        + "\n\n完整计算依据见「补货建议」页。"
    )


def _drift_answer(inventory: pd.DataFrame) -> str:
    drift = inventory[inventory["drift_status"] != "正常"]
    if drift.empty:
        return f"当前 {len(inventory)} 个 SKU 的漂移状态均为「正常」，暂无需要重估的预测。"
    lines = [
        f"- **{int(row.sku_id)} · {row.sku_name}**：{row.drift_status}，"
        f"{row.drift_detail or '无详细说明'}"
        for row in drift.sort_values("sku_id").itertuples(index=False)
    ]
    return "检测到以下预测漂移：\n\n" + "\n".join(lines)


def _strategy_answer(simulation: pd.DataFrame) -> str:
    lines = [
        f"- **{row.strategy_label}**：满足率 {row.fill_rate:.2%}，"
        f"平均库存 {row.avg_inventory:,.1f} 件，总成本 ¥{row.total_cost:,.0f}，"
        f"缺货 {int(row.stockout_weeks)} 周"
        for row in simulation.itertuples(index=False)
    ]
    return (
        "三种补货策略的仿真结果：\n\n"
        + "\n".join(lines)
        + "\n\n从数据看，C 分位数 ROP 在满足率接近 B 的同时，"
        "平均库存和总成本更低；A 的总成本最低，但缺货周数明显更多。"
    )


def answer_question(question: str) -> str:
    """规则问答：所有事实都来自 output/*.csv。"""
    q = question.strip().lower()
    inventory = load_inventory_replenishment()
    predictions = load_forecast_predictions()
    selection = load_sku_selection()
    simulation = load_policy_simulation()
    context = load_context()
    horizon = int(context["horizon_weeks"])

    sku_id = _find_sku(question, inventory)
    stock_keywords = (
        "库存",
        "补货",
        "补哪些",
        "补多少",
        "订货",
        "采购",
        "缺货",
        "安全库存",
        "紧急",
    )
    forecast_keywords = ("预测", "需求", "未来", "走势", "分位")
    drift_keywords = ("漂移", "重估")
    strategy_keywords = ("策略", "对比", "成本", "满足率", "仿真")

    if sku_id is not None and any(word in q for word in stock_keywords):
        row = inventory[inventory["sku_id"] == sku_id].iloc[0]
        return _inventory_answer(row)

    if sku_id is not None and any(word in q for word in forecast_keywords):
        return _forecast_answer(sku_id, predictions, selection, horizon)

    if any(word in q for word in strategy_keywords):
        return _strategy_answer(simulation)

    if any(word in q for word in drift_keywords):
        return _drift_answer(inventory)

    if any(word in q for word in stock_keywords):
        return _inventory_list_answer(inventory)

    if any(word in q for word in forecast_keywords):
        return _top_demand_answer(predictions, horizon)

    if any(word in q for word in ("供应商", "交期", "到货")):
        return (
            f"当前模型产物中没有供应商字段。库存模块提供的采购提前期是 "
            f"**{int(context['lead_time_weeks'])} 周**；补货数量与紧急度见「补货建议」页。"
        )

    return (
        "当前为规则演示模式，我可以基于模型产物回答：\n\n"
        "- **库存**：哪些 SKU 需要补货？\n"
        "- **补货**：SKU 365 的库存和建议订货量是多少？\n"
        "- **预测**：365 未来 4 周需求如何？\n"
        "- **策略**：三种补货策略哪个更好？\n"
        "- **漂移**：哪些 SKU 的预测需要重估？\n\n"
        "_接入 AGENT_DESIGN.md 中的 LLM 对话层后，可替换为自然语言分析。_"
    )


def render() -> None:
    context = load_context()
    page_header(
        "INTELLIGENT ASSISTANT",
        "智能问答",
        "用自然语言查询预测、库存与补货建议；当前为读取模型产物的规则演示。",
    )

    st.markdown(
        '<div class="notice"><span class="notice-icon">ⓘ</span>'
        "<div><b>演示模式</b>：未接入大模型，回答由 app/ui 内置规则读取 "
        "output/*.csv 生成；真实接入方案见 AGENT_DESIGN.md。</div></div>",
        unsafe_allow_html=True,
    )

    if "assistant_history" not in st.session_state:
        st.session_state.assistant_history = [
            {
                "role": "assistant",
                "content": (
                    "你好，我是智链参谋的供应链助手。\n\n"
                    f"当前数据快照：**{context['snapshot_week']:%Y-%m-%d}**，"
                    f"预测跨度：**未来 {int(context['horizon_weeks'])} 周**。\n\n"
                    "你可以问我：\n"
                    "- 哪些 SKU 需要补货？\n"
                    "- SKU 365 的库存和建议订货量是多少？\n"
                    "- 365 未来 4 周需求如何？\n"
                    "- 三种补货策略哪个更好？"
                ),
            }
        ]

    for message in st.session_state.assistant_history:
        avatar = "🏭" if message["role"] == "assistant" else "👤"
        with st.chat_message(message["role"], avatar=avatar):
            st.markdown(message["content"])

    prompt = st.chat_input("输入问题，例如：哪些 SKU 需要补货？")
    if prompt:
        st.session_state.assistant_history.append(
            {"role": "user", "content": prompt}
        )
        with st.chat_message("user", avatar="👤"):
            st.markdown(prompt)

        with st.chat_message("assistant", avatar="🏭"):
            with st.spinner("正在读取模型产物..."):
                answer = answer_question(prompt)
            st.markdown(answer)

        st.session_state.assistant_history.append(
            {"role": "assistant", "content": answer}
        )
