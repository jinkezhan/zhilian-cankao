"""智能问答页：基于模型产物的规则演示（未接入 LLM）。"""

from __future__ import annotations

import streamlit as st

from agent_tools import answer_question
from components import page_header
from data_loader import (
    load_context,
    load_forecast_predictions,
    load_inventory_replenishment,
    load_policy_simulation,
    load_sku_selection,
)


def render() -> None:
    context = load_context()
    inventory = load_inventory_replenishment()
    predictions = load_forecast_predictions()
    selection = load_sku_selection()
    simulation = load_policy_simulation()

    page_header(
        "INTELLIGENT ASSISTANT",
        "智能问答",
        "用自然语言查询预测、库存与补货建议；当前为读取模型产物的规则演示。",
    )

    st.markdown(
        '<div class="notice"><span class="notice-icon">ⓘ</span>'
        "<div><b>演示模式</b>：未接入大模型，回答由 app/ui 内置规则读取 "
        "output/*.csv 生成；不存在的 SKU 会明确报错，不会返回无关补货数据。</div></div>",
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
                answer = answer_question(
                    prompt,
                    inventory=inventory,
                    predictions=predictions,
                    selection=selection,
                    simulation=simulation,
                    context=context,
                )
            st.markdown(answer)

        st.session_state.assistant_history.append(
            {"role": "assistant", "content": answer}
        )
