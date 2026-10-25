"""界面主题：制造业深蓝色系、卡片与表格样式。

样式全部注入在 Streamlit 页面内，避免依赖外部 CSS 文件。
"""

from __future__ import annotations

import streamlit as st

PRIMARY = "#1E6FB8"
NAVY = "#0B2A4A"
SUCCESS = "#2E9E5B"
WARNING = "#D98A1F"
DANGER = "#D64545"
MUTED = "#64748B"

# 策略对比配色（与 inventory_policy.py 的语义保持区分）
STRATEGY_COLORS = ["#1E6FB8", "#E8963E", "#2E9E5B"]

# 紧急度 / 漂移状态 → 徽标颜色
BADGE_CLASS = {
    "紧急": "badge-danger",
    "建议补货": "badge-warning",
    "正常": "badge-success",
    "需重估": "badge-danger",
    "预警": "badge-warning",
    "库存积压": "badge-muted",
    "偏低": "badge-warning",
    "严重不足": "badge-danger",
}

URGENCY_ORDER = {"紧急": 0, "建议补货": 1, "正常": 2}

_THEME_CSS = """
<style>
:root {
    --navy: #0B2A4A;
    --navy-deep: #081C34;
    --blue: #1E6FB8;
    --blue-light: #4FA3E8;
    --bg: #F3F7FB;
    --card: #FFFFFF;
    --border: #DCE7F3;
    --text: #1E293B;
    --muted: #64748B;
    --danger: #D64545;
    --success: #2E9E5B;
    --warning: #D98A1F;
}

html, body, [class*="css"] {
    font-family: "Microsoft YaHei", "PingFang SC", "Segoe UI", sans-serif;
}

.stApp {
    background:
        radial-gradient(1100px 520px at 88% -12%, rgba(30, 111, 184, 0.10), transparent 62%),
        radial-gradient(900px 420px at 4% 0%, rgba(11, 42, 74, 0.05), transparent 60%),
        var(--bg);
    color: var(--text);
}

[data-testid="stHeader"] {
    background: rgba(243, 247, 251, 0.72);
    backdrop-filter: blur(8px);
}

/* 主内容区宽度（兼容旧版 block-container 与新版 stMainBlockContainer） */
section.main .block-container,
[data-testid="stMainBlockContainer"] {
    max-width: 1220px;
    padding-top: 1.7rem;
    padding-bottom: 3rem;
}
[data-testid="stSidebar"] .block-container {
    padding-top: 1.2rem;
    padding-bottom: 1.2rem;
}

#MainMenu, footer, [data-testid="stDecoration"], .stDeployButton {
    display: none;
}

/* ---------- 侧边栏 ---------- */
[data-testid="stSidebar"] {
    background: linear-gradient(180deg, #0C2F53 0%, #0A2340 55%, #081C34 100%);
    border-right: 1px solid rgba(255, 255, 255, 0.06);
}
[data-testid="stSidebar"] p,
[data-testid="stSidebar"] span,
[data-testid="stSidebar"] label,
[data-testid="stSidebar"] button {
    color: #D5E4F2;
}
[data-testid="stSidebar"] .stButton > button,
[data-testid="stSidebar"] div[data-testid="stButton"] > button {
    width: 100%;
    background: rgba(255, 255, 255, 0.07);
    border: 1px solid rgba(255, 255, 255, 0.14);
    border-radius: 9px;
    color: #D5E4F2;
    font-size: 12.5px;
    padding: 0.35rem 0.6rem;
}
[data-testid="stSidebar"] .stButton > button:hover,
[data-testid="stSidebar"] div[data-testid="stButton"] > button:hover {
    background: rgba(255, 255, 255, 0.13);
    border-color: rgba(255, 255, 255, 0.28);
    color: #FFFFFF;
}
.brand { display: flex; align-items: center; gap: 12px; padding: 10px 2px 2px; }
.brand-mark {
    width: 42px; height: 42px; border-radius: 12px;
    background: linear-gradient(135deg, #1E6FB8, #4FA3E8);
    color: #FFFFFF; display: flex; align-items: center; justify-content: center;
    font-size: 13px; font-weight: 800; letter-spacing: 0.04em;
    box-shadow: 0 6px 18px rgba(30, 111, 184, 0.45);
    flex: 0 0 auto;
}
.brand-title { color: #FFFFFF !important; font-size: 17px; font-weight: 700; line-height: 1.2; }
.brand-sub { color: #7FA8CC !important; font-size: 9.5px; letter-spacing: 0.06em; margin-top: 4px; }
.side-label {
    color: #7FA8CC !important; font-size: 10.5px; font-weight: 700;
    letter-spacing: 0.16em; text-transform: uppercase;
    margin: 1.35rem 0 0.45rem 4px;
}
[data-testid="stSidebar"] [role="radiogroup"] { gap: 4px; }
[data-testid="stSidebar"] [role="radiogroup"] label {
    padding: 10px 12px; border-radius: 10px; font-size: 14.5px;
    transition: background 0.15s ease;
}
[data-testid="stSidebar"] [role="radiogroup"] label:hover {
    background: rgba(255, 255, 255, 0.07);
}
[data-testid="stSidebar"] [role="radiogroup"] label:has(input:checked) {
    background: linear-gradient(90deg, rgba(79, 163, 232, 0.30), rgba(79, 163, 232, 0.08));
    box-shadow: inset 3px 0 0 #4FA3E8;
}
.sidebar-meta {
    margin-top: 1.25rem; padding: 11px 13px;
    background: rgba(255, 255, 255, 0.045);
    border: 1px solid rgba(255, 255, 255, 0.08);
    border-radius: 10px;
}
.meta-row {
    display: flex; justify-content: space-between; align-items: baseline;
    gap: 10px; padding: 3px 0; font-size: 11.5px; color: #9CC0DF;
}
.meta-row b { color: #E6F0FA; font-weight: 600; text-align: right; }
.sidebar-foot {
    display: flex; align-items: center; gap: 8px;
    margin-top: 0.85rem; padding: 10px 12px;
    background: rgba(255, 255, 255, 0.05);
    border: 1px solid rgba(255, 255, 255, 0.08);
    border-radius: 10px; color: #9CC0DF !important; font-size: 11.5px;
}
.sidebar-foot .dot {
    width: 7px; height: 7px; border-radius: 50%;
    background: #4ADE80; box-shadow: 0 0 8px rgba(74, 222, 128, 0.8);
    flex: 0 0 auto;
}

/* ---------- 页头 ---------- */
.page-header {
    position: relative; overflow: hidden;
    background: linear-gradient(130deg, #0B2A4A 0%, #154C7D 58%, #1E6FB8 100%);
    border-radius: 18px; padding: 24px 28px; margin-bottom: 20px;
    box-shadow: 0 14px 34px rgba(11, 42, 74, 0.18);
}
.page-header::after {
    content: ""; position: absolute; right: -50px; top: -80px;
    width: 240px; height: 240px; border-radius: 50%;
    background: radial-gradient(circle, rgba(255, 255, 255, 0.15), transparent 68%);
}
.page-kicker {
    color: #8FC3ED; font-size: 11px; font-weight: 700;
    letter-spacing: 0.18em; text-transform: uppercase;
}
.page-header h1 {
    color: #FFFFFF; font-size: 27px; font-weight: 700;
    margin: 6px 0 5px; letter-spacing: 0.01em;
}
.page-header p {
    color: #BFD6EA; font-size: 13.5px; margin: 0; max-width: 820px;
}

/* ---------- 卡片 ---------- */
section.main div[data-testid="stVerticalBlockBorderWrapper"] {
    background: var(--card);
    border: 1px solid var(--border) !important;
    border-radius: 16px;
    box-shadow: 0 8px 24px rgba(11, 42, 74, 0.06);
    padding: 1.15rem 1.25rem;
}
/* 页面级 / 列级 wrapper 不是卡片，恢复透明 */
section.main [data-testid="stMainBlockContainer"] > div[data-testid="stVerticalBlockBorderWrapper"],
section.main .block-container > div[data-testid="stVerticalBlockBorderWrapper"],
section.main div[data-testid="column"] > div[data-testid="stVerticalBlockBorderWrapper"] {
    background: transparent !important;
    border: none !important;
    box-shadow: none !important;
    padding: 0 !important;
    border-radius: 0 !important;
}
.card-head { margin-bottom: 0.85rem; }
.card-title { color: #0B2A4A; font-size: 16px; font-weight: 700; }
.card-sub { color: #6B7F94; font-size: 12.5px; margin-top: 3px; line-height: 1.55; }

/* ---------- KPI ---------- */
.kpi-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(205px, 1fr));
    gap: 14px; margin: 2px 0 20px;
}
.kpi-card {
    position: relative; overflow: hidden;
    background: var(--card); border: 1px solid var(--border);
    border-radius: 16px; padding: 16px 18px 15px;
    box-shadow: 0 8px 24px rgba(11, 42, 74, 0.05);
}
.kpi-card::before {
    content: ""; position: absolute; left: 0; top: 0; bottom: 0;
    width: 4px; background: var(--blue);
}
.kpi-card.kpi-danger::before { background: var(--danger); }
.kpi-card.kpi-success::before { background: var(--success); }
.kpi-card.kpi-warning::before { background: var(--warning); }
.kpi-label { color: #6B7F94; font-size: 12.5px; font-weight: 600; }
.kpi-value {
    color: #0B2A4A; font-size: 28px; font-weight: 800;
    line-height: 1.25; margin-top: 4px; letter-spacing: -0.01em;
}
.kpi-card.kpi-danger .kpi-value { color: #B42318; }
.kpi-unit { color: #7B8FA4; font-size: 13px; font-weight: 600; margin-left: 5px; letter-spacing: 0; }
.kpi-note { color: #8A9BAE; font-size: 11.5px; margin-top: 3px; line-height: 1.45; }

/* ---------- 实体信息 ---------- */
.entity { padding: 2px 0 0 14px; border-left: 3px solid #DCE7F3; }
.entity-label { color: #8399AF; font-size: 10.5px; font-weight: 700; letter-spacing: 0.14em; text-transform: uppercase; }
.entity-name { color: #0B2A4A; font-size: 20px; font-weight: 800; margin-top: 2px; line-height: 1.3; word-break: break-word; }
.entity-meta { color: #7B8FA4; font-size: 12.5px; margin-top: 3px; line-height: 1.45; }

/* ---------- 表格 ---------- */
.table-wrap { overflow-x: auto; border: 1px solid #E7EEF7; border-radius: 12px; }
table.data-table { width: 100%; border-collapse: collapse; font-size: 13.5px; }
table.data-table thead th {
    background: linear-gradient(180deg, #0E3A63, #0B2F52);
    color: #EAF3FB; font-size: 12.5px; font-weight: 600;
    letter-spacing: 0.02em; text-align: left; padding: 11px 14px; white-space: nowrap;
}
table.data-table thead th.num { text-align: right; }
table.data-table tbody td {
    color: #334155; padding: 11px 14px; white-space: nowrap;
    border-bottom: 1px solid #EDF3F9;
}
table.data-table tbody td:first-child { color: #0B2A4A; font-weight: 600; }
table.data-table tbody tr:last-child td { border-bottom: none; }
table.data-table tbody tr:hover { background: #F6FAFE; }
table.data-table td.num { color: #0B2A4A; font-weight: 600; text-align: right; font-variant-numeric: tabular-nums; }
table.data-table td.wrap { white-space: normal; min-width: 210px; max-width: 310px; line-height: 1.55; }
.badge {
    display: inline-flex; align-items: center; gap: 6px;
    padding: 3px 10px; border-radius: 999px;
    font-size: 12px; font-weight: 600; line-height: 1.5;
}
.badge::before { content: ""; width: 6px; height: 6px; border-radius: 50%; background: currentColor; }
.badge-danger { color: #B42318; background: #FEE4E2; }
.badge-success { color: #067647; background: #DCFAE6; }
.badge-warning { color: #B54708; background: #FEF0C7; }
.badge-muted { color: #475569; background: #E2E8F0; }

/* ---------- 提示与问答 ---------- */
.notice {
    display: flex; gap: 10px; align-items: flex-start;
    background: #EEF6FF; border: 1px solid #CFE3F8; border-left: 4px solid #1E6FB8;
    border-radius: 12px; padding: 12px 16px; margin-bottom: 16px;
    color: #24527A; font-size: 13px; line-height: 1.6;
}
.notice b { color: #0B2A4A; }
.notice-icon { color: #1E6FB8; font-size: 15px; line-height: 1.45; }
.insight {
    margin-top: 14px; padding: 12px 16px;
    background: #F0F8FF; border: 1px solid #D6E7F7; border-left: 4px solid #1E6FB8;
    border-radius: 12px; color: #24527A; font-size: 13px; line-height: 1.7;
}
.insight b { color: #0B2A4A; }
[data-testid="stChatMessage"] {
    background: #FFFFFF; border: 1px solid #E2ECF6;
    border-radius: 14px; box-shadow: 0 4px 14px rgba(11, 42, 74, 0.04);
}
[data-testid="stSelectbox"] label p { color: #6B7F94; font-size: 12.5px; font-weight: 600; }
[data-testid="stExpander"] details {
    border: 1px solid var(--border); border-radius: 12px; background: #FFFFFF;
}
.caption-note { color: #7B8FA4; font-size: 12px; margin-top: 8px; line-height: 1.55; }
</style>
"""


def inject_theme() -> None:
    """把全局 CSS 注入当前页面。"""
    st.markdown(_THEME_CSS, unsafe_allow_html=True)
