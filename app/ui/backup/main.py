"""智链参谋 · Streamlit 界面入口。

启动（在项目根目录执行）：
    .venv\\Scripts\\python.exe -m streamlit run app/ui/main.py

数据来自 output/*.csv，页面只读，不触发模型训练。
"""

from __future__ import annotations

import sys
from pathlib import Path

# 保证从任意工作目录启动时，app/ui 下的模块都可导入
UI_DIR = Path(__file__).resolve().parent
if str(UI_DIR) not in sys.path:
    sys.path.insert(0, str(UI_DIR))

import streamlit as st

st.set_page_config(
    page_title="智链参谋 · 供应链决策工作台",
    page_icon="🏭",
    layout="wide",
    initial_sidebar_state="expanded",
)

from data_loader import load_context  # noqa: E402
from views import assistant, demand, inventory, replenishment, strategy  # noqa: E402
from theme import inject_theme  # noqa: E402

inject_theme()

_PAGES = {
    "需求预测": demand.render,
    "库存健康": inventory.render,
    "补货建议": replenishment.render,
    "策略对比": strategy.render,
    "智能问答": assistant.render,
}

_BRAND_HTML = (
    '<div class="brand">'
    '<div class="brand-mark">ZL</div>'
    "<div>"
    '<div class="brand-title">智链参谋</div>'
    '<div class="brand-sub">SUPPLY CHAIN INTELLIGENCE</div>'
    "</div>"
    "</div>"
)


def _render_sidebar() -> str:
    with st.sidebar:
        st.markdown(_BRAND_HTML, unsafe_allow_html=True)
        st.markdown('<div class="side-label">功能导航</div>', unsafe_allow_html=True)
        page = st.radio(
            "功能导航",
            list(_PAGES),
            label_visibility="collapsed",
            key="nav_page",
        )

        try:
            context = load_context()
        except Exception as exc:  # noqa: BLE001
            st.error(f"数据读取失败：{exc}")
            st.stop()

        st.markdown('<div class="side-label">数据状态</div>', unsafe_allow_html=True)
        st.markdown(
            '<div class="sidebar-meta">'
            f'<div class="meta-row"><span>库存快照</span>'
            f'<b>{context["snapshot_week"]:%Y-%m-%d}</b></div>'
            f'<div class="meta-row"><span>预测跨度</span>'
            f'<b>未来 {int(context["horizon_weeks"])} 周</b></div>'
            f'<div class="meta-row"><span>采购提前期</span>'
            f'<b>{int(context["lead_time_weeks"])} 周</b></div>'
            f'<div class="meta-row"><span>SKU</span>'
            f'<b>{int(context["sku_count"])} 个</b></div>'
            '<div class="meta-row"><span>数据源</span><b>output/*.csv</b></div>'
            "</div>",
            unsafe_allow_html=True,
        )

        if st.button("刷新数据", key="refresh_data"):
            st.cache_data.clear()
            st.rerun()

        st.markdown(
            '<div class="sidebar-foot"><span class="dot"></span>'
            "<span>模型产物 · 只读数据</span></div>",
            unsafe_allow_html=True,
        )

    return page


def main() -> None:
    page = _render_sidebar()
    _PAGES[page]()


if __name__ == "__main__":
    main()
