"""智链参谋 · Streamlit 演示看板（第 6 步 / 生鲜看板第五步）。

顶部数据源切换：
- 生鲜前置仓（默认）：只读 output/freshretail_* 五张表；
- 制造企业（保底）：沿用 DataCo 原有逻辑。

页面只读 CSV，不重算 SS / ROP / 订货量；生鲜版的“效期修正目标”按与
Agent 相同的口径现算：min(目标水位, 保质期内预计可售量 × config 倍数)。
Agent 对话复用 app/agent.py 的 run_turn()，不修改其对外接口。

运行：
    .venv\\Scripts\\python.exe -m streamlit run app\\ui\\dashboard.py
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import html
import io
import json
import linecache
import math
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components
import yaml

APP_DIR = Path(__file__).resolve().parents[1]
BASE_DIR = APP_DIR.parent
OUTPUT_DIR = BASE_DIR / "output"
CONFIG_PATH = BASE_DIR / "config" / "freshretail_inventory.yaml"
USERS_CONFIG_PATH = BASE_DIR / "config" / "users.yaml"
ASSET_DIR = Path(__file__).resolve().parent / "assets"
ROLE_LABELS = {"admin": "管理员", "planner": "计划员", "viewer": "查看者"}
SESSION_HOURS = 8
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import decision_support  # noqa: E402 - 溯源三方案，纯计算模块（不依赖 Streamlit）
import product_names  # noqa: E402 - 商品名称映射（演示用）
import report_export  # noqa: E402 - Excel / PDF 日报导出

_AGENT = None


def get_agent():
    """延迟导入 Agent，让图表和库存先渲染；只在对话区真正需要时加载。"""
    global _AGENT
    if _AGENT is None:
        import agent as agent_module
        _AGENT = agent_module
    return _AGENT


def _system_prompt(dataset: str) -> str:
    """基础系统提示词；生鲜数据源追加商品名 → SKU 映射，便于按品名提问。"""
    prompt = get_agent().SYSTEM_PROMPT
    if dataset == "freshretail":
        prompt = prompt + "\n\n" + product_names.agent_prompt_block()
    return prompt


# ---------------------------------------------------------------------------
# 配置与视觉规范
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="智链参谋 · 生鲜前置仓补货决策助手",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ---------------------------------------------------------------------------
# 公网部署：隐藏 Streamlit Cloud 的 Manage app / 开发者徽章
# 只匹配 Streamlit 自带元素，不影响自定义顶栏、页脚和全局 AI 悬浮球。
# set_page_config 必须是第一条 Streamlit 命令，所以这段紧跟在它后面。
# ---------------------------------------------------------------------------
_HIDE_STREAMLIT_CHROME = """
<style>
  #MainMenu, footer { visibility: hidden !important; }
  [data-testid="stHeader"],
  [data-testid="stToolbar"],
  [data-testid="stDecoration"],
  [data-testid="stStatusWidget"],
  [data-testid="stAppDeployButton"],
  [data-testid="stDeployButton"],
  [data-testid="manage-app-button"],
  .stDeployButton {
    display: none !important;
    visibility: hidden !important;
  }
  /* Streamlit Cloud 右下角可能出现的 viewer badge / 品牌标识 */
  #viewerBadge,
  [data-testid="viewerBadge"],
  [class*="viewerBadge"],
  [class*="ViewerBadge"] {
    display: none !important;
    visibility: hidden !important;
  }
</style>
"""
st.markdown(_HIDE_STREAMLIT_CHROME, unsafe_allow_html=True)

FRESH_LABEL = "生鲜前置仓（默认）"
DATACO_LABEL = "制造企业（保底）"

SNAPSHOT_FALLBACK = "—"
# 生鲜展示口径：底层是 FreshRetailNet 归一化销量指数，不是真实 kg/份。
FRESH_UNIT = "kg/份"
QUICK_QUESTIONS = [
    "哪些 SKU 需要马上补货？",
    "SKU 365 该补多少？",
    "SKU 977 有数据吗？",
]
FRESH_QUICK_QUESTIONS = [
    "哪些 SKU 最紧急？",
    "黑猪五花肉（145-802）该补多少？",
    "哪些货快过期了？",
]

# 方言输入：下拉选项 + 内置小词表（不依赖缺失的 dialect_glossary.yaml）
DIALECT_OPTIONS = {
    "普通话 / 关闭": "none",
    "自动识别": "auto",
    "桂柳话": "guiliu",
    "川渝话": "chuanyu",
    "粤语": "yue",
}
DIALECT_WORDS: dict[str, dict[str, str]] = {
    "guiliu": {
        "包谷": "玉米",
        "苞谷": "玉米",
        "洋芋": "土豆",
        "番茄": "西红柿",
        "包心菜": "卷心菜",
    },
    "chuanyu": {
        "包谷": "玉米",
        "苞谷": "玉米",
        "洋芋": "土豆",
        "海椒": "辣椒",
        "藤藤菜": "空心菜",
    },
    "yue": {
        "粟米": "玉米",
        "薯仔": "土豆",
        "番茄": "西红柿",
        "青椒": "辣椒",
    },
}

FRESH_REPL = "freshretail_inventory_replenishment.csv"
FRESH_FORECAST = "freshretail_forecast_predictions.csv"
FRESH_COMPARISON = "freshretail_inventory_policy_comparison.csv"
FRESH_CAP_SWEEP = "freshretail_inventory_cap_sweep.csv"
FRESH_SUMMARY = "freshretail_sku_summary_all.csv"
FRESH_WHATIF = "freshretail_what_if.csv"
FRESH_WHATIF_SUMMARY = "freshretail_what_if_summary.csv"
FRESH_ALPHA_TERMINAL = "alpha_sweep_terminal.csv"
FRESH_METRICS = "freshretail_forecast_metrics.csv"

# 四档紧急度按固定顺序排序；颜色由 CSS 的 .badge-* 类承载
URGENCY_RANK = {"紧急": 0, "建议补货": 1, "计划补货": 2, "正常": 3}

# 图表色：历史线用分类蓝；预测分位用同色系 ordinal 蓝阶
BLUE = "#2a78d6"
BLUE_LIGHT = "#86b6ef"
BLUE_DARK = "#184f95"
INK = "#0b0b0b"
SECONDARY = "#52514e"
MUTED = "#6b7f94"
GRID = "#DCE7F3"
AXIS = "#c3c2b7"

# 注意：以下 CSS 是完整多行字符串，首尾三引号分别在下一行和 </style> 之后
# （约第 1207 行）。第 1183 行位于字符串内部，切勿在这里补三引号。
CSS = """
<style>
:root { color-scheme: light; }
.stApp { background: #f8f9fa; color: #1f2937; overflow-x: hidden; }
html, body, [class*="css"] {
  font-family: system-ui, -apple-system, "Segoe UI", "Microsoft YaHei", sans-serif;
}
.block-container { padding-top: 1.2rem; padding-bottom: 2rem; max-width: 1480px; }
/* 主体占满全宽；顶部只留顶栏的 60px，Banner 直接贴住顶栏 */
section.main .block-container {
  max-width: 100%;
  padding: 60px 32px 96px;
}
/* 制造企业（保底）模式没有顶栏，不需要预留 60px */
.stApp:not(:has(.top-navbar)) section.main .block-container { padding-top: 1.2rem; }
section.main { scroll-behavior: smooth; }
#section-overview,
#section-inventory,
#section-forecast,
#section-agent { scroll-margin-top: 78px; }
#MainMenu, footer { visibility: hidden; }
[data-testid="stHeader"] { display: none; }
[data-testid="stToolbar"], [data-testid="stDecoration"] { visibility: hidden; }

.brand { color: #0b0b0b; font-size: 2rem; font-weight: 700; letter-spacing: -0.02em; line-height: 1.15; }
.subtitle { color: #52514e; font-size: 0.95rem; margin-top: 2px; }
.snapshot {
  display: inline-block; background: #ffffff; border: 1px solid #E1EAF4;
  border-radius: 999px; padding: 6px 12px; color: #5f6f82;
  font-size: 0.85rem; white-space: nowrap;
}
.topbar-line { border-top: 1px solid #E1EAF4; margin: 6px 0 18px; }

.reco-card {
  background: #ffffff; border: 1px solid #E1EAF4;
  border-radius: 16px; padding: 18px 18px 16px; margin: 10px 0 14px;
  box-shadow: 0 6px 20px rgba(11, 42, 74, .04);
}
.reco-label { color: #52514e; font-size: 0.85rem; }
.reco-value {
  font-size: 2.7rem; font-weight: 700; line-height: 1.05;
  color: #0b0b0b; margin-top: 2px;
}
.reco-unit { font-size: 1rem; font-weight: 500; color: #52514e; margin-left: 4px; }
.badge {
  display: inline-block; padding: 4px 10px; border-radius: 999px;
  font-size: 0.8rem; font-weight: 600; border: 1px solid transparent; margin-top: 8px;
}
.badge-urgent { background: #fdecec; color: #b3261e; border-color: #f3c4c0; }
.badge-advised { background: #fff3e8; color: #b45309; border-color: #f3d3b5; }
.badge-planned { background: #eef4fd; color: #1c5cab; border-color: #cde2fb; }
.badge-normal { background: #eaf7ea; color: #0c6b0c; border-color: #c9e8c9; }
.reco-reason { color: #52514e; font-size: 0.86rem; line-height: 1.55; margin-top: 10px; }
.reco-meta { color: #6b7f94; font-size: 0.8rem; margin-top: 6px; }

.repl-table { width: 100%; border-collapse: collapse; font-size: 0.82rem; color: #0b0b0b; }
.repl-table th {
  text-align: right; font-weight: 600; color: #52514e; background: #f0efec;
  padding: 7px 8px; border-bottom: 1px solid #DCE7F3;
}
.repl-table td {
  text-align: right; padding: 7px 8px; border-bottom: 1px solid #f0efec;
  font-variant-numeric: tabular-nums;
}
.repl-table th:first-child, .repl-table td:first-child { text-align: left; }
.repl-table tr:last-child td { border-bottom: none; }
.repl-table .badge { margin-top: 0; padding: 2px 8px; font-size: 0.75rem; }
.repl-table tr.row-expiring td { background: #fff8ea; }
.tag-expiring {
  display: inline-block; margin-left: 6px; padding: 1px 7px; border-radius: 999px;
  background: #fff3e8; color: #b45309; border: 1px solid #f3d3b5;
  font-size: 0.7rem; font-weight: 600;
}

.metric-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin: 4px 0 6px; }
.metric-card {
  background: #ffffff; border: 1px solid #E1EAF4;
  border-radius: 14px; padding: 12px 14px;
  box-shadow: 0 4px 14px rgba(11, 42, 74, .035);
}
.metric-label { color: #52514e; font-size: 0.82rem; }
.metric-value {
  color: #0b0b0b; font-size: 1.55rem; font-weight: 600;
  line-height: 1.2; margin-top: 2px;
}
.metric-unit { font-size: 0.9rem; font-weight: 500; color: #52514e; margin-left: 3px; }

.kpi-grid {
  display: grid; grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 12px; margin: 6px 0 10px;
}
.kpi-card {
  background: #ffffff; border: 1px solid #E1EAF4;
  border-radius: 16px; padding: 16px 16px 14px;
  box-shadow: 0 6px 20px rgba(11, 42, 74, .04);
}
.kpi-label { color: #52514e; font-size: 0.85rem; }
.kpi-value {
  color: #0b0b0b; font-size: 1.5rem; font-weight: 700;
  line-height: 1.28; margin-top: 5px;
}
.kpi-value-sm { font-size: 1.2rem; }
.kpi-vs { color: #6b7f94; font-size: 0.9rem; font-weight: 500; margin: 0 4px; }
.kpi-unit { font-size: 0.95rem; font-weight: 500; color: #52514e; margin-left: 3px; }
.kpi-sub { color: #52514e; font-size: 0.85rem; margin-top: 5px; }
.kpi-source { color: #6b7f94; font-size: 0.75rem; margin-top: 9px; }

/* ---------- 今日概览：Banner 下方的纯白指标卡 ---------- */
.overview-dock {
  margin: 44px 0 18px;
}
.ov-grid {
  display: grid;
  grid-template-columns: 1.35fr repeat(3, minmax(0, 1fr));
  gap: 14px;
}
.ov-card {
  padding: 18px 20px 16px;
  border: 1px solid #E1EAF4;
  border-radius: 16px;
  background: #ffffff;
  box-shadow: 0 6px 18px rgba(11, 42, 74, 0.05);
  transition: transform .16s ease, box-shadow .16s ease;
}
.ov-card:hover {
  transform: translateY(-1px);
  box-shadow: 0 10px 24px rgba(11, 42, 74, 0.08);
}
.ov-card.ov-primary {
  padding: 20px 22px 18px;
  border-color: #F1D4D0;
  background: linear-gradient(180deg, #FFFFFF 0%, #FFF7F6 100%);
}
.ov-primary .ov-value { font-size: 2.9rem; }
.ov-primary .ov-label { color: #B42318; }
.ov-primary .ov-unit { color: #B42318; }
.ov-primary .ov-sub { color: #8A5A55; }
.ov-primary .ov-dot {
  background: #D64545;
  box-shadow: 0 0 0 3px rgba(214, 69, 69, .12);
}
.ov-label {
  display: flex; align-items: center; gap: 7px;
  color: #5f6f82; font-size: 12.5px; font-weight: 600;
  letter-spacing: .02em;
}
.ov-dot {
  width: 6px; height: 6px; border-radius: 50%;
  background: #1E6FB8; flex: 0 0 auto;
}
.ov-value {
  margin-top: 8px;
  color: #0b2a4a;
  font-size: 2.3rem;
  font-weight: 800;
  line-height: 1.08;
  letter-spacing: -0.03em;
  font-variant-numeric: tabular-nums;
}
.ov-unit {
  margin-left: 5px;
  color: #6b7f94;
  font-size: .95rem;
  font-weight: 600;
  letter-spacing: 0;
}
.ov-sub { margin-top: 7px; color: #6b7f94; font-size: 11.5px; line-height: 1.5; }
.ov-alert .ov-label, .ov-alert .ov-value { color: #b3261e; }
.ov-alert .ov-dot { background: #d64545; box-shadow: 0 0 0 3px rgba(214, 69, 69, .12); }
.ov-warn .ov-label, .ov-warn .ov-value { color: #b45309; }
.ov-warn .ov-dot { background: #d98a1f; box-shadow: 0 0 0 3px rgba(217, 138, 31, .14); }
.ov-foot { margin: 12px 0 0; color: #6b7f94; font-size: 11.5px; }
.ov-vs { color: #9AA9B8; font-weight: 500; margin: 0 4px; }
.import-banner {
  display: flex; align-items: center; gap: 8px;
  margin: 4px 0 14px; padding: 10px 14px;
  border: 1px solid #CFE3F8; border-radius: 12px;
  background: #EEF6FF; color: #24527A;
  font-size: 12.5px; font-weight: 600;
}
.import-dot {
  width: 7px; height: 7px; border-radius: 50%;
  background: #1E6FB8; box-shadow: 0 0 8px rgba(30, 111, 184, .6);
  flex: 0 0 auto;
}
.import-hint { margin-left: auto; color: #8399af; font-size: 11px; font-weight: 500; }
@media (max-width: 1100px) {
  .ov-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}
@media (max-width: 640px) {
  .overview-dock { margin: 28px 0 28px; }
  .ov-grid { grid-template-columns: 1fr; gap: 12px; }
  .ov-value { font-size: 2rem; }
}

.chart-svg { margin: 4px 0 2px; }
.chart-svg svg { width: 100%; height: auto; display: block; }

[data-testid="stMetric"] {
  background: #ffffff;
  border: 1px solid #E1EAF4;
  border-left: 4px solid #1E6FB8;
  border-radius: 16px;
  padding: 16px 18px;
  box-shadow: 0 6px 20px rgba(11, 42, 74, .05);
}
[data-testid="stMetricLabel"] { color: #5f6f82; font-size: 0.82rem; font-weight: 600; }
[data-testid="stMetricValue"] { color: #0b2a4a; font-weight: 800; }
[data-testid="stChatMessage"] { background: #ffffff; }

/* 隐藏彩蛋：右上角极小齿轮，不抢视觉 */
div[data-testid="stVerticalBlock"]:has(> div[data-testid="element-container"] .source-egg-anchor)
  div[data-testid="stButton"] button {
  min-height: 18px;
  height: 18px;
  width: 20px;
  padding: 0;
  border: none;
  background: transparent;
  color: #b8b6ad;
  font-size: 12px;
  line-height: 1;
  opacity: 0.28;
  box-shadow: none;
  transition: opacity 0.2s ease;
}
div[data-testid="stVerticalBlock"]:has(> div[data-testid="element-container"] .source-egg-anchor)
  div[data-testid="stButton"] button p {
  margin: 0;
  font-size: 12px;
  line-height: 1;
}
div[data-testid="stVerticalBlock"]:has(> div[data-testid="element-container"] .source-egg-anchor)
  div[data-testid="stButton"] button:hover {
  opacity: 0.9;
  background: transparent;
  border: none;
  color: #52514e;
}

/* ---------- 顶部固定导航 ---------- */
.top-navbar {
  position: fixed;
  top: 0;
  left: 0;
  width: 100%;
  height: 60px;
  /* 高于 Streamlit 侧栏（999991），顶栏才能真正浮在所有内容之上 */
  z-index: 1000000;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 24px;
  padding: 0 28px;
  box-sizing: border-box;
  color: #ffffff;
  background:
    linear-gradient(90deg,
      rgba(7, 20, 38, 0.97) 0%,
      rgba(11, 42, 74, 0.94) 55%,
      rgba(16, 64, 110, 0.92) 100%);
  border-bottom: 1px solid rgba(255, 255, 255, 0.08);
  box-shadow: 0 6px 22px rgba(3, 13, 28, 0.22);
  backdrop-filter: blur(14px) saturate(1.2);
  font-family: "Microsoft YaHei", "PingFang SC", "Segoe UI", sans-serif;
}
.nav-left { display: flex; align-items: center; gap: 10px; }
.nav-logo {
  width: 30px; height: 30px; border-radius: 9px;
  display: flex; align-items: center; justify-content: center;
  background: linear-gradient(135deg, #1E6FB8, #4FA3E8);
  color: #fff; font-size: 10px; font-weight: 800; letter-spacing: .04em;
  box-shadow: 0 4px 12px rgba(30, 111, 184, .4);
}
.nav-brand {
  color: #ffffff; font-size: 15px; font-weight: 700;
  letter-spacing: .04em; white-space: nowrap;
}
.nav-links {
  position: absolute;
  left: 50%;
  top: 50%;
  transform: translate(-50%, -50%);
  display: flex; align-items: center; justify-content: center;
  gap: 4px; min-width: 0;
}
.nav-links a {
  color: rgba(255, 255, 255, 0.78);
  text-decoration: none;
  font-size: 13.5px;
  font-weight: 500;
  padding: 8px 14px;
  border-radius: 8px;
  white-space: nowrap;
  transition: color .15s ease, background .15s ease, text-shadow .15s ease;
}
.nav-links a:hover {
  color: #ffffff;
  background: rgba(255, 255, 255, 0.08);
  text-shadow: 0 0 12px rgba(143, 212, 255, 0.8);
}
.nav-links a.active {
  color: #ffffff;
  background: rgba(143, 212, 255, 0.12);
  box-shadow: inset 0 0 0 1px rgba(143, 212, 255, 0.22);
}
.nav-more {
  display: none;
  position: absolute;
  left: 50%;
  top: 50%;
  transform: translate(-50%, -50%);
  z-index: 1000001;
}
.nav-more summary {
  list-style: none;
  color: rgba(255, 255, 255, 0.78);
  font-size: 13.5px;
  font-weight: 500;
  padding: 8px 14px;
  border-radius: 8px;
  cursor: pointer;
  white-space: nowrap;
}
.nav-more summary::-webkit-details-marker { display: none; }
.nav-more[open] summary {
  color: #ffffff;
  background: rgba(255, 255, 255, 0.08);
}
.nav-more-panel {
  position: absolute;
  top: 44px;
  left: 50%;
  transform: translateX(-50%);
  min-width: 150px;
  padding: 6px;
  border-radius: 12px;
  border: 1px solid rgba(255, 255, 255, 0.12);
  background: #0C2F53;
  box-shadow: 0 14px 34px rgba(3, 13, 28, 0.40);
}
.nav-more-panel a {
  display: block;
  padding: 8px 10px;
  border-radius: 8px;
  color: #D5E4F2;
  text-decoration: none;
  font-size: 13px;
  white-space: nowrap;
}
.nav-more-panel a:hover {
  color: #ffffff;
  background: rgba(255, 255, 255, 0.08);
}
.nav-right {
  display: flex; align-items: center; justify-content: flex-end;
  gap: 10px;
  flex: 0 0 auto;
}
.nav-store {
  display: inline-flex; align-items: center; gap: 8px;
  height: 32px; padding: 0 10px 0 12px;
  border-radius: 10px;
  background: rgba(255, 255, 255, 0.07);
  border: 1px solid rgba(255, 255, 255, 0.13);
  backdrop-filter: blur(8px);
  transition: background .15s ease, border-color .15s ease, box-shadow .15s ease;
}
.nav-store:hover {
  background: rgba(255, 255, 255, 0.11);
  border-color: rgba(143, 212, 255, 0.34);
}
.nav-store:focus-within {
  border-color: rgba(143, 212, 255, 0.55);
  box-shadow: 0 0 0 3px rgba(143, 212, 255, 0.12);
}
.nav-store-label {
  color: rgba(255, 255, 255, 0.52);
  font-size: 10.5px; letter-spacing: .06em; white-space: nowrap;
}
.nav-store-control { position: relative; display: inline-flex; align-items: center; }
.nav-store-value {
  color: #ffffff; font-size: 12.5px; font-weight: 600;
  white-space: nowrap; max-width: 150px;
  overflow: hidden; text-overflow: ellipsis;
}
.nav-store-value::before {
  content: "";
  display: inline-block;
  width: 6px; height: 6px;
  margin-right: 6px;
  border-radius: 50%;
  background: #4ADE80;
  box-shadow: 0 0 6px rgba(74, 222, 128, .7);
  vertical-align: 1px;
}
.nav-store-select {
  appearance: none; -webkit-appearance: none;
  max-width: 138px;
  padding: 0 16px 0 0;
  border: 0; outline: none; background: transparent;
  color: #ffffff; font-family: inherit;
  font-size: 12.5px; font-weight: 600;
  cursor: pointer;
}
.nav-store-select option { background: #0d2a49; color: #ffffff; }
.nav-store-caret {
  position: absolute; right: 0; top: 50%;
  transform: translateY(-50%);
  color: #8fd4ff; font-size: 9px; line-height: 1;
  pointer-events: none;
}
.nav-status {
  display: inline-flex; align-items: center; gap: 7px;
  color: rgba(255, 255, 255, 0.76);
  font-size: 12.5px; white-space: nowrap;
}
.nav-dot {
  width: 7px; height: 7px; border-radius: 50%;
  background: #4ADE80;
  box-shadow: 0 0 8px rgba(74, 222, 128, .8);
}
.nav-date {
  color: rgba(255, 255, 255, 0.58);
  font-size: 11.5px; letter-spacing: .04em; white-space: nowrap;
}
.nav-auth-label {
  color: rgba(255, 255, 255, 0.86);
  font-size: 12px; font-weight: 600; white-space: nowrap;
}
.nav-auth-slot { width: 34px; height: 34px; flex: 0 0 auto; }
.nav-bell-slot { width: 34px; height: 34px; flex: 0 0 auto; }

/* ---------- 顶栏模拟登录（Streamlit 1.30 无 st.popover，用固定浮层实现） ---------- */
section.main .block-container div[data-testid="element-container"]:has(.nav-auth-anchor),
section.main .block-container div[data-testid="element-container"]:has(.auth-panel-anchor) {
  position: absolute;
  width: 0;
  height: 0;
  overflow: visible;
}
section.main .block-container div[data-testid="element-container"]:has(> div[data-testid="stMarkdown"] .nav-auth-anchor),
section.main .block-container div[data-testid="element-container"]:has(> div[data-testid="stMarkdown"] .auth-panel-anchor) {
  display: none;
}
div[data-testid="stVerticalBlock"]:has(> div[data-testid="element-container"] > div[data-testid="stMarkdown"] .nav-auth-anchor) {
  position: fixed !important;
  top: 13px;
  right: 28px;
  width: 34px;
  height: 34px;
  z-index: 1000002;
  padding: 0;
  margin: 0;
  gap: 0;
  background: transparent;
}
div[data-testid="stVerticalBlock"]:has(> div[data-testid="element-container"] > div[data-testid="stMarkdown"] .nav-auth-anchor) div[data-testid="stButton"] {
  width: 34px;
  margin: 0;
  padding: 0;
}
div[data-testid="stVerticalBlock"]:has(> div[data-testid="element-container"] > div[data-testid="stMarkdown"] .nav-auth-anchor) button {
  width: 34px !important;
  height: 34px !important;
  min-height: 34px !important;
  padding: 0 !important;
  border-radius: 50% !important;
  border: 1px solid rgba(255, 255, 255, 0.35) !important;
  background: linear-gradient(135deg, #1E6FB8, #4FA3E8) !important;
  color: #ffffff !important;
  box-shadow: 0 4px 12px rgba(3, 13, 28, 0.35) !important;
  transition: transform .15s ease, box-shadow .15s ease;
}
div[data-testid="stVerticalBlock"]:has(> div[data-testid="element-container"] > div[data-testid="stMarkdown"] .nav-auth-anchor) button p {
  color: #ffffff !important;
  font-size: 14px !important;
  font-weight: 700 !important;
  line-height: 1 !important;
}
div[data-testid="stVerticalBlock"]:has(> div[data-testid="element-container"] > div[data-testid="stMarkdown"] .nav-auth-anchor) button:hover {
  transform: translateY(-1px);
  box-shadow: 0 6px 16px rgba(3, 13, 28, 0.45) !important;
  background: linear-gradient(135deg, #2479c4, #5fb0f0) !important;
}
div[data-testid="stVerticalBlock"]:has(> div[data-testid="element-container"] > div[data-testid="stMarkdown"] .auth-panel-anchor) {
  position: fixed !important;
  top: 68px;
  right: 28px;
  width: 300px;
  z-index: 1000003;
  padding: 18px 18px 16px;
  border-radius: 16px;
  border: 1px solid #E1EAF4;
  background: #ffffff;
  box-shadow: 0 18px 44px rgba(11, 42, 74, 0.18);
  gap: 8px;
}
div[data-testid="stVerticalBlock"]:has(> div[data-testid="element-container"] > div[data-testid="stMarkdown"] .auth-panel-anchor) > div[data-testid="element-container"],
div[data-testid="stVerticalBlock"]:has(> div[data-testid="element-container"] > div[data-testid="stMarkdown"] .auth-panel-anchor) > div[data-testid="stForm"],
div[data-testid="stVerticalBlock"]:has(> div[data-testid="element-container"] > div[data-testid="stMarkdown"] .auth-panel-anchor) div[data-testid="stButton"],
div[data-testid="stVerticalBlock"]:has(> div[data-testid="element-container"] > div[data-testid="stMarkdown"] .auth-panel-anchor) div[data-testid="stFormSubmitButton"] {
  width: 100% !important;
  max-width: 100% !important;
  box-sizing: border-box;
}
div[data-testid="stVerticalBlock"]:has(> div[data-testid="element-container"] > div[data-testid="stMarkdown"] .auth-panel-anchor)::before {
  content: "";
  position: absolute;
  top: -7px;
  right: 13px;
  width: 12px;
  height: 12px;
  background: #ffffff;
  border-left: 1px solid #E1EAF4;
  border-top: 1px solid #E1EAF4;
  transform: rotate(45deg);
}
div[data-testid="stVerticalBlock"]:has(> div[data-testid="element-container"] > div[data-testid="stMarkdown"] .auth-panel-anchor) div[data-testid="stForm"] {
  border: 0 !important;
  padding: 0 !important;
  background: transparent !important;
}
div[data-testid="stVerticalBlock"]:has(> div[data-testid="element-container"] > div[data-testid="stMarkdown"] .auth-panel-anchor) div[data-testid="stFormSubmitButton"] button {
  background: #0B2A4A !important;
  border: 1px solid #0B2A4A !important;
  color: #ffffff !important;
}
div[data-testid="stVerticalBlock"]:has(> div[data-testid="element-container"] > div[data-testid="stMarkdown"] .auth-panel-anchor) div[data-testid="stFormSubmitButton"] button p {
  color: #ffffff !important;
}
div[data-testid="stVerticalBlock"]:has(> div[data-testid="element-container"] > div[data-testid="stMarkdown"] .auth-panel-anchor) div[data-testid="stFormSubmitButton"] button:hover {
  background: #123a63 !important;
  border-color: #123a63 !important;
}
.auth-panel-title { color: #0B2A4A; font-size: 15px; font-weight: 700; letter-spacing: .01em; }
.auth-user-row { display: flex; align-items: center; gap: 10px; margin: 12px 0 4px; }
.auth-avatar {
  width: 38px; height: 38px; border-radius: 50%;
  display: flex; align-items: center; justify-content: center;
  background: linear-gradient(135deg, #0B2A4A, #1E6FB8);
  color: #ffffff; font-size: 13px; font-weight: 700;
  box-shadow: 0 4px 12px rgba(30, 111, 184, .25);
}
.auth-user-name { color: #0B2A4A; font-size: 13.5px; font-weight: 700; }
.auth-user-meta { color: #6b7f94; font-size: 11.5px; margin-top: 2px; }
.auth-panel-note { color: #6b7f94; font-size: 11.5px; margin: 6px 0 10px; }

/* ---------- 预警中心（顶栏铃铛 + 固定浮层） ---------- */
section.main .block-container div[data-testid="element-container"]:has(> div[data-testid="stMarkdown"] .notif-bell-anchor),
section.main .block-container div[data-testid="element-container"]:has(> div[data-testid="stMarkdown"] .notif-panel-anchor) {
  display: none;
}
div[data-testid="stVerticalBlock"]:has(> div[data-testid="element-container"] > div[data-testid="stMarkdown"] .notif-bell-anchor) {
  position: fixed !important;
  top: 13px;
  right: 72px;
  width: 34px;
  height: 34px;
  z-index: 1000002;
  padding: 0;
  margin: 0;
  gap: 0;
  background: transparent;
}
div[data-testid="stVerticalBlock"]:has(> div[data-testid="element-container"] > div[data-testid="stMarkdown"] .notif-bell-anchor) div[data-testid="stButton"] {
  width: 34px;
  margin: 0;
  padding: 0;
}
div[data-testid="stVerticalBlock"]:has(> div[data-testid="element-container"] > div[data-testid="stMarkdown"] .notif-bell-anchor) button {
  width: 34px !important;
  height: 34px !important;
  min-height: 34px !important;
  padding: 0 !important;
  border-radius: 50% !important;
  border: 1px solid rgba(255, 255, 255, 0.32) !important;
  background: rgba(255, 255, 255, 0.10) !important;
  color: #ffffff !important;
  box-shadow: 0 4px 12px rgba(3, 13, 28, 0.28) !important;
}
div[data-testid="stVerticalBlock"]:has(> div[data-testid="element-container"] > div[data-testid="stMarkdown"] .notif-bell-anchor) button p {
  color: #ffffff !important;
  font-size: 13px !important;
  line-height: 1 !important;
}
div[data-testid="stVerticalBlock"]:has(> div[data-testid="element-container"] > div[data-testid="stMarkdown"] .notif-bell-anchor) button:hover {
  background: rgba(255, 255, 255, 0.18) !important;
}
.notif-badge {
  position: fixed;
  top: 8px;
  right: 76px;
  z-index: 1000003;
  min-width: 16px;
  height: 16px;
  padding: 0 4px;
  border-radius: 8px;
  background: #D64545;
  color: #ffffff;
  font-size: 10px;
  font-weight: 700;
  line-height: 16px;
  text-align: center;
  box-shadow: 0 0 0 2px #0B2A4A;
}
div[data-testid="stVerticalBlock"]:has(> div[data-testid="element-container"] > div[data-testid="stMarkdown"] .notif-panel-anchor) {
  position: fixed !important;
  top: 68px;
  right: 72px;
  width: 330px;
  max-height: 70vh;
  overflow-y: auto;
  z-index: 1000003;
  padding: 16px 18px 18px;
  border-radius: 16px;
  border: 1px solid #E1EAF4;
  background: #ffffff;
  box-shadow: 0 18px 44px rgba(11, 42, 74, 0.18);
  gap: 8px;
}
div[data-testid="stVerticalBlock"]:has(> div[data-testid="element-container"] > div[data-testid="stMarkdown"] .notif-panel-anchor) > div[data-testid="element-container"],
div[data-testid="stVerticalBlock"]:has(> div[data-testid="element-container"] > div[data-testid="stMarkdown"] .notif-panel-anchor) div[data-testid="stButton"] {
  width: 100% !important;
  max-width: 100% !important;
  box-sizing: border-box;
}

/* ---------- 全局问 AI 浮层（固定右下角，不打断页面） ---------- */
section.main .block-container div[data-testid="element-container"]:has(> div[data-testid="stMarkdown"] .global-ai-toggle-anchor),
section.main .block-container div[data-testid="element-container"]:has(> div[data-testid="stMarkdown"] .global-ai-panel-anchor) {
  display: none;
}
div[data-testid="stVerticalBlock"]:has(> div[data-testid="element-container"] > div[data-testid="stMarkdown"] .global-ai-toggle-anchor) {
  position: fixed !important;
  right: 24px;
  bottom: 24px;
  width: 62px;
  height: 62px;
  z-index: 1000004;
  padding: 0;
  margin: 0;
  gap: 0;
  background: transparent;
  /* 拖拽球是可见入口；这个原生按钮只保留给 JS 触发，不显示 */
  opacity: 0 !important;
  pointer-events: none !important;
}
div[data-testid="stVerticalBlock"]:has(> div[data-testid="element-container"] > div[data-testid="stMarkdown"] .global-ai-toggle-anchor) div[data-testid="stButton"] {
  width: 62px;
  margin: 0;
  padding: 0;
}
div[data-testid="stVerticalBlock"]:has(> div[data-testid="element-container"] > div[data-testid="stMarkdown"] .global-ai-toggle-anchor) button {
  width: 62px !important;
  height: 62px !important;
  min-height: 62px !important;
  padding: 0 !important;
  border-radius: 50% !important;
  border: 1px solid rgba(126, 206, 255, 0.35) !important;
  background:
    radial-gradient(circle at 32% 24%,
      rgba(255,255,255,.98) 0%,
      rgba(177,235,255,.92) 7%,
      rgba(84,187,246,.76) 18%,
      rgba(30,111,184,.72) 38%,
      rgba(7,31,58,.98) 68%,
      #02070f 100%) !important;
  color: #eaf8ff !important;
  box-shadow:
    0 12px 34px rgba(0,8,24,.45),
    0 0 0 1px rgba(126,206,255,.32),
    0 0 26px rgba(30,111,184,.5) !important;
  transition: transform .18s ease, box-shadow .18s ease;
}
div[data-testid="stVerticalBlock"]:has(> div[data-testid="element-container"] > div[data-testid="stMarkdown"] .global-ai-toggle-anchor) button p {
  color: #eaf8ff !important;
  font-size: 22px !important;
  font-weight: 600 !important;
  line-height: 1 !important;
  text-shadow: 0 0 12px rgba(125, 215, 255, .9);
}
div[data-testid="stVerticalBlock"]:has(> div[data-testid="element-container"] > div[data-testid="stMarkdown"] .global-ai-toggle-anchor) button:hover {
  transform: scale(1.06);
  box-shadow:
    0 14px 38px rgba(0,8,24,.52),
    0 0 0 1px rgba(126,206,255,.45),
    0 0 34px rgba(64,162,235,.68) !important;
}
div[data-testid="stVerticalBlock"]:has(> div[data-testid="element-container"] > div[data-testid="stMarkdown"] .global-ai-panel-anchor) {
  position: fixed !important;
  right: 24px;
  bottom: 98px;
  width: 390px;
  max-width: calc(100vw - 48px);
  max-height: min(72vh, 620px);
  overflow-y: auto;
  z-index: 1000004;
  padding: 16px 18px 18px;
  border-radius: 18px;
  border: 1px solid #dce7f3;
  background: #ffffff;
  box-shadow: 0 22px 54px rgba(11, 42, 74, 0.22);
  gap: 10px;
}
div[data-testid="stVerticalBlock"]:has(> div[data-testid="element-container"] > div[data-testid="stMarkdown"] .global-ai-panel-anchor) > div[data-testid="element-container"],
div[data-testid="stVerticalBlock"]:has(> div[data-testid="element-container"] > div[data-testid="stMarkdown"] .global-ai-panel-anchor) > div[data-testid="stForm"],
div[data-testid="stVerticalBlock"]:has(> div[data-testid="element-container"] > div[data-testid="stMarkdown"] .global-ai-panel-anchor) div[data-testid="stButton"],
div[data-testid="stVerticalBlock"]:has(> div[data-testid="element-container"] > div[data-testid="stMarkdown"] .global-ai-panel-anchor) div[data-testid="stFormSubmitButton"] {
  width: 100% !important;
  max-width: 100% !important;
  box-sizing: border-box;
}
div[data-testid="stVerticalBlock"]:has(> div[data-testid="element-container"] > div[data-testid="stMarkdown"] .global-ai-panel-anchor) div[data-testid="stForm"] {
  border: 0 !important;
  padding: 0 !important;
  background: transparent !important;
}
div[data-testid="stVerticalBlock"]:has(> div[data-testid="element-container"] > div[data-testid="stMarkdown"] .global-ai-panel-anchor) div[data-testid="stButton"] button {
  height: 32px;
  min-height: 32px;
  padding: 0 8px;
  font-size: 12px;
}
div[data-testid="stVerticalBlock"]:has(> div[data-testid="element-container"] > div[data-testid="stMarkdown"] .global-ai-panel-anchor) [data-testid="stChatMessage"] {
  background: #f8fbfe !important;
  border: 1px solid #e7eff8 !important;
  box-shadow: none !important;
  padding: 8px 10px !important;
}
div[data-testid="stVerticalBlock"]:has(> div[data-testid="element-container"] > div[data-testid="stMarkdown"] .global-ai-panel-anchor) [data-testid="chatAvatarIcon-user"],
div[data-testid="stVerticalBlock"]:has(> div[data-testid="element-container"] > div[data-testid="stMarkdown"] .global-ai-panel-anchor) [data-testid="chatAvatarIcon-assistant"] {
  background: linear-gradient(135deg, #1E6FB8, #4FA3E8) !important;
  color: #ffffff !important;
}

@media (max-width: 1020px) {
  .nav-links { display: none; }
  .nav-more { display: block; }
  .nav-store-label { display: none; }
  .nav-store-select { max-width: 120px; }
}
@media (max-width: 900px) {
  .top-navbar { padding: 0 16px; gap: 12px; }
  .nav-links { display: none; }
  div[data-testid="stVerticalBlock"]:has(> div[data-testid="element-container"] > div[data-testid="stMarkdown"] .nav-auth-anchor),
  div[data-testid="stVerticalBlock"]:has(> div[data-testid="element-container"] > div[data-testid="stMarkdown"] .auth-panel-anchor) {
    right: 16px;
  }
}
@media (max-width: 640px) {
  .nav-date { display: none; }
  .nav-auth-label { display: none; }
  div[data-testid="stVerticalBlock"]:has(> div[data-testid="element-container"] > div[data-testid="stMarkdown"] .auth-panel-anchor) {
    width: calc(100vw - 32px);
    max-width: 300px;
    right: 16px;
  }
}

/* ---------- 全宽头部：顶栏与 Banner 无缝衔接 ---------- */
/* 这些零高度元素（样式注入、滚顶脚本、固定顶栏本身）移出文档流，
   避免它们在流式布局里撑出 16px 的 gap，让 Banner 成为第一个内容块。 */
section.main .block-container div[data-testid="element-container"]:has(> div[data-testid="stMarkdown"] style),
section.main .block-container div[data-testid="element-container"]:has(> iframe[height="0"]):has(+ div[data-testid="stHorizontalBlock"] .source-egg-anchor) {
  position: absolute;
  width: 0;
  height: 0;
  overflow: hidden;
}
section.main .block-container div[data-testid="element-container"]:has(.top-navbar) {
  position: absolute;
  width: 100%;
  height: 0;
}
/* Banner 抵消主体左右内边距，铺满整个视口；顶部圆角去掉，紧贴顶栏 */
section.main .block-container div[data-testid="element-container"]:has(> iframe[height="400"]) {
  width: auto;
  margin-left: -32px;
  margin-right: -32px;
}
section.main .block-container iframe[data-testid="stIFrame"][height="400"] {
  display: block;
  width: 100% !important;
}
/* 隐藏的数据源彩蛋齿轮改为固定悬浮，不占用 Banner 高度 */
div[data-testid="stHorizontalBlock"]:has(.source-egg-anchor) {
  position: fixed !important;
  top: 68px;
  right: 18px;
  width: 24px !important;
  z-index: 1000001;
  margin: 0 !important;
  padding: 0 !important;
  gap: 0 !important;
}
div[data-testid="stHorizontalBlock"]:has(.source-egg-anchor) > div[data-testid="column"]:first-child {
  display: none;
}
div[data-testid="stHorizontalBlock"]:has(.source-egg-anchor) > div[data-testid="column"]:last-child {
  width: 24px !important;
  flex: 0 0 24px !important;
  min-width: 0 !important;
}

/* ---------- 侧边栏（已由顶栏替代，彻底隐藏） ---------- */
[data-testid="stSidebar"],
[data-testid="collapsedControl"] { display: none !important; }
.side-brand {
  display: flex; align-items: center; gap: 11px;
  padding: 8px 2px 14px;
}
.side-brand-mark {
  width: 42px; height: 42px; border-radius: 12px;
  background: linear-gradient(135deg, #0B2A4A, #1E6FB8);
  color: #fff; display: flex; align-items: center; justify-content: center;
  font-size: 12px; font-weight: 800; letter-spacing: .05em;
  box-shadow: 0 6px 16px rgba(30, 111, 184, .28);
}
.side-brand-title { color: #0B2A4A; font-size: 16px; font-weight: 800; line-height: 1.2; }
.side-brand-sub { color: #6b7f94; font-size: 10.5px; margin-top: 3px; letter-spacing: .04em; }
.side-nav-label {
  color: #8399af; font-size: 10.5px; font-weight: 700;
  letter-spacing: .16em; text-transform: uppercase;
  margin: 2px 0 8px 4px;
}
.side-nav { display: flex; flex-direction: column; gap: 5px; }
.side-nav a {
  display: flex; align-items: center; gap: 12px;
  color: #24384d; text-decoration: none;
  font-size: 14.5px; font-weight: 600;
  padding: 11px 12px; border-radius: 11px;
  transition: background .15s ease, color .15s ease;
}
.side-nav a:hover { background: #eef5fd; color: #0B2A4A; }
.side-nav .side-icon {
  width: 25px; text-align: center; font-size: 20px; line-height: 1;
  flex: 0 0 auto;
}
.side-nav .side-text { font-size: 14.5px; letter-spacing: .01em; }

/* ---------- 网站封底 ---------- */
.site-footer {
  position: relative;
  width: 100vw;
  margin: 44px calc(50% - 50vw) 0;
  padding: 40px 0;
  box-sizing: border-box;
  color: #ffffff;
  text-align: center;
  border-top: 1px solid rgba(255, 255, 255, 0.10);
  background:
    radial-gradient(ellipse at 18% 0%, rgba(60, 148, 226, 0.16), transparent 46%),
    linear-gradient(135deg, #0f2027 0%, #203a43 48%, #2c5364 100%);
  box-shadow: inset 0 1px 0 rgba(143, 212, 255, 0.08);
}
.site-footer::before {
  content: "";
  position: absolute;
  top: -1px;
  left: 14%;
  right: 14%;
  height: 1px;
  background: linear-gradient(
    90deg,
    transparent 0%,
    rgba(143, 212, 255, 0.42) 50%,
    transparent 100%
  );
  pointer-events: none;
}
.site-footer .footer-line1 {
  color: #ffffff;
  font-size: 16px;
  font-weight: 700;
  line-height: 1.8;
  letter-spacing: 1px;
}
.site-footer .footer-line2 {
  color: #a0aec0;
  font-size: 12px;
  line-height: 1.8;
  letter-spacing: 1px;
  margin-top: 6px;
}

/* ---------- 生鲜首页 Banner ---------- */
.fresh-hero {
  position: relative;
  overflow: hidden;
  border-radius: 22px;
  min-height: 380px;
  margin: 2px 0 20px;
  background: #071b32;
  box-shadow: 0 18px 45px rgba(7, 27, 50, .18);
}
.fresh-hero-media {
  position: absolute;
  inset: 0;
  display: grid;
  grid-template-columns: repeat(3, 1fr);
}
.fresh-hero-panel { overflow: hidden; }
.fresh-hero-panel img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
  transform: scale(1.02);
}
.fresh-hero-shade {
  position: absolute;
  inset: 0;
  background:
    linear-gradient(90deg,
      rgba(3, 14, 28, .95) 0%,
      rgba(4, 18, 35, .84) 42%,
      rgba(5, 22, 42, .40) 72%,
      rgba(5, 22, 42, .16) 100%),
    linear-gradient(0deg, rgba(2, 10, 22, .58), transparent 58%);
}
.fresh-hero-copy {
  position: relative;
  z-index: 2;
  max-width: 780px;
  padding: 46px 46px;
  color: #fff;
}
.fresh-hero-kicker {
  color: #8fd4ff;
  font-size: 10.5px;
  font-weight: 700;
  letter-spacing: .18em;
  text-transform: uppercase;
  margin-bottom: 12px;
}
.fresh-hero-copy h1 {
  color: #ffffff;
  font-size: clamp(24px, 2.4vw, 34px);
  line-height: 1.28;
  font-weight: 800;
  letter-spacing: .01em;
  margin: 0 0 12px;
}
.fresh-hero-copy p {
  color: #d6e8f7;
  font-size: 14.5px;
  line-height: 1.8;
  margin: 0;
  max-width: 700px;
}
@media (max-width: 760px) {
  .fresh-hero { min-height: 420px; }
  .fresh-hero-media { grid-template-columns: 1fr; }
  .fresh-hero-panel:nth-child(2),
  .fresh-hero-panel:nth-child(3) { display: none; }
  .fresh-hero-copy { padding: 30px 24px; }
  .fresh-hero-copy h1 { font-size: 24px; }
}

/* ---------- 数据新鲜度 ---------- */
.data-status {
  display: flex; justify-content: flex-end; align-items: center;
  margin: -6px 2px 18px;
  color: #6b7f94; font-size: 11.5px; letter-spacing: .01em;
}
.data-status-chip {
  display: inline-flex; align-items: center; gap: 7px;
  padding: 5px 11px;
  border: 1px solid #E1EAF4;
  border-radius: 999px;
  background: #F6FAFE;
}
.data-status .dot {
  width: 6px; height: 6px; border-radius: 50%;
  background: #2E9E5B; box-shadow: 0 0 6px rgba(46, 158, 91, .45);
  flex: 0 0 auto;
}
.data-status.warn .dot {
  background: #D98A1F; box-shadow: 0 0 6px rgba(217, 138, 31, .45);
}
.data-status b { color: #6b7f94; font-weight: 600; }
section.main .block-container div[data-testid="element-container"]:has(> div[data-testid="stMarkdown"] .data-refresh-anchor) {
  display: none;
}
div[data-testid="stVerticalBlock"]:has(> div[data-testid="element-container"] > div[data-testid="stMarkdown"] .data-refresh-anchor) div[data-testid="stButton"] button {
  height: 30px;
  min-height: 30px;
  padding: 0 10px;
  font-size: 12px;
  border-radius: 8px;
}
section.main .block-container div[data-testid="element-container"]:has(> div[data-testid="stMarkdown"] .pager-anchor) {
  display: none;
}
div[data-testid="stVerticalBlock"]:has(> div[data-testid="element-container"] > div[data-testid="stMarkdown"] .pager-anchor) {
  background: #F8FAFD;
  border: 1px solid #E1EAF4;
  border-radius: 12px;
  padding: 8px 12px 10px;
  margin: 8px 0 4px;
}
div[data-testid="stVerticalBlock"]:has(> div[data-testid="element-container"] > div[data-testid="stMarkdown"] .pager-anchor) div[data-testid="stButton"] button {
  height: 34px;
  min-height: 34px;
  border-radius: 8px;
  font-size: 12.5px;
}
div[data-testid="stVerticalBlock"]:has(> div[data-testid="element-container"] > div[data-testid="stMarkdown"] .pager-anchor) [data-testid="stSelectbox"] [data-baseweb="select"] {
  min-height: 34px;
}

/* ---------- 导出按钮：与工具条同一套视觉 ---------- */
div[data-testid="stDownloadButton"] button {
  height: 34px;
  min-height: 34px;
  padding: 0 14px;
  border-radius: 9px;
  border: 1px solid #DCE7F3;
  background: #FFFFFF;
  color: #1E6FB8;
  font-size: 12.5px;
  font-weight: 600;
  box-shadow: 0 1px 2px rgba(11, 42, 74, .04);
  transition: background .15s ease, border-color .15s ease, color .15s ease;
}
div[data-testid="stDownloadButton"] button:hover {
  border-color: #9CC0DF;
  background: #F6FAFE;
  color: #185A96;
}

</style>
"""
st.markdown(CSS, unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# 数据加载（只读、缓存）
# ---------------------------------------------------------------------------
# Streamlit Cloud 热更新时，linecache 可能仍缓存着截断的旧源码；
# inspect.getsource() 会因此抛 EOF in multi-line string。先清空再装饰第一个
# 缓存函数，强制它重新读取当前完整文件。
linecache.clearcache()


@st.cache_data(show_spinner=False, ttl=300)
def load_csv(name: str) -> pd.DataFrame | None:
    path = OUTPUT_DIR / name
    if not path.exists():
        return None
    try:
        return pd.read_csv(path)
    except Exception:
        return None


@st.cache_data(show_spinner=False, ttl=300)
def load_fresh_config() -> dict:
    if not CONFIG_PATH.exists():
        return {}
    try:
        with CONFIG_PATH.open("r", encoding="utf-8") as fh:
            return yaml.safe_load(fh) or {}
    except Exception:
        return {}


def _agent_backup(agent) -> dict:
    """留存 DataCo 版工具集（use_dataset 不可逆）。

    页面脚本每次 rerun 都会重新执行，模块级变量会重置；因此把备份挂在
    agent 模块对象上，保证同一进程内只捕获一次、且捕获的是原始 DataCo 工具集。
    """
    backup = getattr(agent, "_ZHILIAN_DATACO_BACKUP", None)
    if backup is None:
        backup = {
            name: getattr(agent, name)
            for name in ("DataStore", "SYSTEM_PROMPT", "TOOLS", "TOOL_FUNCS", "summarize_result")
        }
        setattr(agent, "_ZHILIAN_DATACO_BACKUP", backup)
    return backup


def activate_agent_dataset(agent, dataset: str) -> None:
    """按当前页面数据源切换 agent 的全局工具/提示词，支持来回切换。"""
    backup = _agent_backup(agent)
    if dataset == "freshretail":
        from freshretail_agent.tools_freshretail import (
            FRESH_SYSTEM_PROMPT,
            FRESH_TOOL_FUNCS,
            FRESH_TOOLS,
            FreshDataStore,
            summarize_fresh_result,
        )

        agent.DataStore = FreshDataStore
        agent.SYSTEM_PROMPT = FRESH_SYSTEM_PROMPT
        agent.TOOLS = FRESH_TOOLS
        agent.TOOL_FUNCS = FRESH_TOOL_FUNCS
        agent.summarize_result = summarize_fresh_result
    else:
        for name, value in backup.items():
            setattr(agent, name, value)


@st.cache_resource(show_spinner=False)
def get_dataco_store():
    agent = get_agent()
    backup = _agent_backup(agent)
    return backup["DataStore"]()


@st.cache_resource(show_spinner=False)
def get_fresh_store():
    from freshretail_agent.tools_freshretail import FreshDataStore

    return FreshDataStore()


def _as_date(df: pd.DataFrame, column: str) -> pd.DataFrame:
    out = df.copy()
    out[column] = pd.to_datetime(out[column], errors="coerce")
    return out


def snapshot_week(repl: pd.DataFrame | None, forecast: pd.DataFrame | None) -> str:
    if repl is not None and not repl.empty and "snapshot_week" in repl.columns:
        return str(repl["snapshot_week"].iloc[0])
    if forecast is not None and not forecast.empty and "week" in forecast.columns:
        weeks = pd.to_datetime(forecast["week"], errors="coerce").dropna()
        if not weeks.empty:
            return weeks.max().strftime("%Y-%m-%d")
    return SNAPSHOT_FALLBACK


def sku_options(repl: pd.DataFrame | None, forecast: pd.DataFrame | None) -> list[int]:
    ids: set[int] = set()
    for df in (repl, forecast):
        if df is not None and not df.empty and "sku_id" in df.columns:
            ids.update(int(x) for x in pd.to_numeric(df["sku_id"], errors="coerce").dropna())
    return sorted(ids)


def sku_name_map(repl: pd.DataFrame | None, forecast: pd.DataFrame | None) -> dict[int, str]:
    names: dict[int, str] = {}
    for df in (forecast, repl):
        if df is None or df.empty or "sku_id" not in df.columns or "sku_name" not in df.columns:
            continue
        for _, row in df.drop_duplicates("sku_id").iterrows():
            names.setdefault(int(row["sku_id"]), str(row["sku_name"]))
    return names


def badge_class(urgency: str) -> str:
    return {
        "紧急": "badge-urgent",
        "建议补货": "badge-advised",
        "计划补货": "badge-planned",
        "正常": "badge-normal",
    }.get(urgency, "badge-normal")


# ---------------------------------------------------------------------------
# 顶部栏
# ---------------------------------------------------------------------------
def render_header(snapshot: str) -> None:
    c1, c2, c3 = st.columns([5.6, 2.0, 2.4])
    with c1:
        st.markdown(
            """
            <div class="brand">智链参谋</div>
            <div class="subtitle">面向中小制造企业的供应链补货决策助手</div>
            """,
            unsafe_allow_html=True,
        )
    with c2:
        st.markdown(
            f'<div class="snapshot">快照周 {html.escape(snapshot)}</div>',
            unsafe_allow_html=True,
        )
    with c3:
        if st.button("上传企业数据（即将开放）", type="secondary", use_container_width=True):
            st.session_state["upload_notice"] = True
    st.markdown('<div class="topbar-line"></div>', unsafe_allow_html=True)
    if st.session_state.get("upload_notice"):
        st.info("该功能将在区域赛版本开放")


# ---------------------------------------------------------------------------
# 生鲜版：数据口径
# ---------------------------------------------------------------------------
def series_label(store_id: int, product_id: int) -> str:
    return f"SKU {int(store_id)}-{int(product_id)}"


def fresh_snapshot_date(forecast: pd.DataFrame | None, config: dict) -> str:
    if forecast is not None and not forecast.empty and "dt" in forecast.columns:
        dates = pd.to_datetime(forecast["dt"], errors="coerce").dropna()
        if not dates.empty:
            return dates.max().strftime("%Y-%m-%d")
    sim_cfg = (config or {}).get("simulation", {}) or {}
    return str(sim_cfg.get("snapshot_date") or SNAPSHOT_FALLBACK)


def fresh_shelf_options(repl: pd.DataFrame | None, config: dict) -> list[int]:
    shelf_cfg = (config or {}).get("shelf_life", {}) or {}
    options = shelf_cfg.get("options_days")
    if isinstance(options, list) and options:
        return sorted({int(x) for x in options})
    if repl is not None and not repl.empty and "shelf_life_days" in repl.columns:
        return sorted({int(x) for x in repl["shelf_life_days"].dropna().unique()})
    return []


def compute_expiry_caps(
    repl: pd.DataFrame | None, forecast: pd.DataFrame | None, config: dict
) -> dict[tuple[int, int], dict]:
    """与 Agent 的 target_eff 口径一致：min(目标水位, 保质期内预计可售量 × 倍数)。"""
    caps: dict[tuple[int, int], dict] = {}
    if repl is None or repl.empty or forecast is None or forecast.empty:
        return caps
    rep_cfg = (config or {}).get("replenishment", {}) or {}
    multiplier = float(rep_cfg.get("expiry_cap_multiplier", 1.0))
    lead_time = int(rep_cfg.get("lead_time_days", 0))
    sim_cfg = (config or {}).get("simulation", {}) or {}
    snapshot = str(sim_cfg.get("snapshot_date", ""))

    fc = _as_date(forecast, "dt")
    groups = {
        (int(key[0]), int(key[1])): group.sort_values("dt")
        for key, group in fc.groupby(["store_id", "product_id"], sort=False)
    }
    for row in repl.itertuples():
        key = (int(row.store_id), int(row.product_id))
        group = groups.get(key)
        if group is None or group.empty:
            continue
        dates = group["dt"].dt.strftime("%Y-%m-%d").tolist()
        idx = dates.index(snapshot) if snapshot in dates else len(dates) - 1
        arrival = min(idx + lead_time, len(dates) - 1)
        p50 = group["y_pred_p50"].astype(float).tolist()
        shelf_life = int(row.shelf_life_days)
        sellable = 0.0
        for i in range(arrival, arrival + shelf_life):
            sellable += p50[i] if i < len(p50) else p50[-1]
        cap_qty = multiplier * sellable
        caps[key] = {
            "sellable": sellable,
            "cap": cap_qty,
            "target_eff": min(float(row.target_level), cap_qty),
        }
    return caps


def fresh_kpi_values(
    repl: pd.DataFrame, comparison: pd.DataFrame | None
) -> dict[str, float | int]:
    urgency_counts = repl["urgency"].value_counts()
    expiring = pd.to_numeric(repl["expiring_qty"], errors="coerce").fillna(0.0)
    values: dict[str, float | int] = {
        "series": int(len(repl)),
        "urgent": int(urgency_counts.get("紧急", 0)),
        "advised": int(urgency_counts.get("建议补货", 0)),
        "expiring_qty": float(expiring[expiring > 0].sum()),
        "expiring_series": int((expiring > 0).sum()),
        "short_shelf": None,
        "spoilage_c": None,
        "spoilage_b": None,
    }
    if comparison is not None and not comparison.empty:
        short_shelf = int(comparison["shelf_life_days"].min())
        values["short_shelf"] = short_shelf
        for policy, key in (("C", "spoilage_c"), ("B", "spoilage_b")):
            rows = comparison[
                (comparison["policy"].astype(str) == policy)
                & (comparison["shelf_life_days"] == short_shelf)
            ]
            if not rows.empty:
                values[key] = float(rows.iloc[0]["spoilage_rate"])
    return values


def log_fresh_kpi(values: dict[str, float | int], snapshot: str) -> None:
    """在 Streamlit 进程控制台打印一份计算值，供人工核对。"""
    if st.session_state.get("fresh_kpi_logged"):
        return
    st.session_state["fresh_kpi_logged"] = True
    c = values.get("spoilage_c")
    b = values.get("spoilage_b")
    print(
        "[生鲜看板 KPI] "
        f"快照={snapshot} 在管序列={values['series']} "
        f"紧急={values['urgent']} 建议补货={values['advised']} "
        f"临期数量={float(values['expiring_qty']):.2f} "
        f"临期序列={values['expiring_series']} "
        f"短保档={values.get('short_shelf')}天 "
        f"报损C={c:.4%} 报损B={b:.4%}"
        if c is not None and b is not None
        else "[生鲜看板 KPI] 策略对比表缺失"
    )


# ---------------------------------------------------------------------------
# 生鲜版：顶部栏 + KPI
# ---------------------------------------------------------------------------
@st.cache_data(show_spinner=False)
def _fresh_hero_html() -> str:
    """生鲜首页三图 Banner；图片统一裁切为 960×720 后嵌入。"""
    panels = []
    for index, filename in enumerate(("hero_1.jpg", "hero_2.jpg", "hero_3.jpg"), 1):
        encoded = base64.b64encode((ASSET_DIR / filename).read_bytes()).decode("ascii")
        panels.append(
            '<div class="fresh-hero-panel">'
            f'<img src="data:image/jpeg;base64,{encoded}" alt="生鲜前置仓场景 {index}">'
            "</div>"
        )

    return (
        '<div class="fresh-hero">'
        '<div class="fresh-hero-media">' + "".join(panels) + "</div>"
        '<div class="fresh-hero-shade"></div>'
        '<div class="fresh-hero-copy">'
        '<div class="fresh-hero-kicker">FRESH SUPPLY CHAIN · AI REPLENISHMENT</div>'
        "<h1>智链参谋 · 生鲜前置仓 AI 补货决策系统</h1>"
        "<p>数据驱动源头直采，算法锁定新鲜每一刻。"
        "让每一份农产品，都能以最新鲜的状态极速送达。</p>"
        "</div>"
        "</div>"
    )


@st.cache_data(show_spinner=False)
def _fresh_hero_carousel_html(
    snapshot: str,
    data_label: str,
    urgent: int = 0,
    expiring: int = 0,
    suggest_total: int = 0,
) -> str:
    """生鲜首页三图轮播 Banner（纯 HTML/CSS/JS，无第三方库）。"""
    slides = []
    dots = []
    for index, filename in enumerate(("photoa.jpg", "photob.jpg", "photoc.jpg")):
        encoded = base64.b64encode((ASSET_DIR / filename).read_bytes()).decode("ascii")
        active = " active" if index == 0 else ""
        slides.append(
            f'<div class="hero-slide{active}">'
            f'<img src="data:image/jpeg;base64,{encoded}" alt="生鲜前置仓场景 {index + 1}">'
            "</div>"
        )
        dots.append(
            f'<button class="hero-dot{" active" if index == 0 else ""}" '
            f'type="button" aria-label="切换到第 {index + 1} 张"></button>'
        )

    template = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  * { box-sizing: border-box; }
  html, body {
    margin: 0;
    padding: 0;
    width: 100%;
    height: 100%;
    background: #f8f9fa;
    font-family: "Microsoft YaHei", "PingFang SC", "Segoe UI", sans-serif;
    overflow: hidden;
  }
  .hero-carousel {
    position: relative;
    width: 100%;
    height: 340px;
    overflow: hidden;
    border-radius: 0;
    background: #071b32;
  }
  .hero-slide {
    position: absolute;
    inset: 0;
    opacity: 0;
    transition: opacity .9s ease;
    z-index: 0;
  }
  .hero-slide.active { opacity: 1; }
  .hero-slide img {
    width: 100%;
    height: 100%;
    object-fit: cover;
    display: block;
    transform: scale(1.02);
    filter: saturate(1.08) brightness(1.03);
    transform-origin: center center;
  }
  .hero-slide.active img {
    animation: heroKenBurns 6.5s ease-out forwards;
  }
  @keyframes heroKenBurns {
    from { transform: scale(1.02) translate3d(0, 0, 0); }
    to { transform: scale(1.10) translate3d(-0.8%, -0.8%, 0); }
  }
  .hero-shade {
    position: absolute;
    inset: 0;
    z-index: 1;
    background: linear-gradient(
      180deg,
      rgba(0, 0, 0, 0.10) 0%,
      rgba(0, 0, 0, 0.22) 42%,
      rgba(0, 0, 0, 0.62) 100%
    );
  }
  .hero-copy {
    position: absolute;
    inset: 0;
    z-index: 2;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    text-align: center;
    padding: 30px 42px 54px;
  }
  .hero-kicker {
    color: #8fd4ff;
    font-size: 10.5px;
    font-weight: 700;
    letter-spacing: .2em;
    text-transform: uppercase;
    margin-bottom: 13px;
    text-shadow: 0 1px 8px rgba(0, 0, 0, 0.35);
  }
  .hero-copy h1 {
    color: #ffffff;
    font-size: clamp(22px, 2.2vw, 30px);
    line-height: 1.28;
    font-weight: 800;
    letter-spacing: .01em;
    margin: 0 0 10px;
    max-width: 980px;
    text-shadow: 0 2px 10px rgba(0, 0, 0, 0.50);
  }
  .hero-copy p {
    color: rgba(255, 255, 255, 0.85);
    font-size: 14px;
    line-height: 1.7;
    font-weight: 400;
    margin: 0;
    max-width: 880px;
    text-shadow: 0 1px 8px rgba(0, 0, 0, 0.45);
  }
  .hero-action {
    margin-top: 18px;
    display: flex;
    align-items: center;
    justify-content: center;
    flex-wrap: wrap;
    gap: 8px;
    padding: 9px 12px 9px 16px;
    border-radius: 999px;
    border: 1px solid rgba(255, 255, 255, 0.18);
    background: rgba(7, 27, 50, 0.42);
    backdrop-filter: blur(10px);
    -webkit-backdrop-filter: blur(10px);
    box-shadow: 0 8px 24px rgba(0, 0, 0, 0.20);
    color: rgba(255, 255, 255, 0.92);
    font-size: 13px;
    line-height: 1.4;
  }
  .hero-action b {
    color: #ffffff;
    font-weight: 700;
    font-variant-numeric: tabular-nums;
  }
  .hero-action-sep { color: rgba(255, 255, 255, 0.34); }
  .hero-cta {
    height: 32px;
    padding: 0 14px;
    border: 0;
    border-radius: 999px;
    background: #1E6FB8;
    color: #ffffff;
    font-family: inherit;
    font-size: 12.5px;
    font-weight: 700;
    cursor: pointer;
    box-shadow: 0 4px 12px rgba(30, 111, 184, 0.40);
    transition: background .15s ease, transform .15s ease;
  }
  .hero-cta:hover {
    background: #2479c4;
    transform: translateY(-1px);
  }
  .hero-meta {
    position: absolute;
    right: 30px;
    bottom: 26px;
    z-index: 4;
    display: flex;
    align-items: center;
    justify-content: flex-end;
    flex-wrap: wrap;
    gap: 8px;
    max-width: calc(100% - 60px);
    pointer-events: none;
  }
  .hero-pill {
    display: inline-flex;
    align-items: center;
    gap: 7px;
    padding: 7px 12px;
    border-radius: 999px;
    background: rgba(7, 27, 50, 0.44);
    border: 1px solid rgba(255, 255, 255, 0.22);
    color: rgba(255, 255, 255, 0.92);
    font-size: 11.5px;
    font-weight: 500;
    letter-spacing: .01em;
    white-space: nowrap;
    backdrop-filter: blur(10px) saturate(1.2);
    -webkit-backdrop-filter: blur(10px) saturate(1.2);
    box-shadow: 0 6px 18px rgba(3, 13, 28, 0.20);
  }
  .hero-pill-dot {
    width: 6px; height: 6px; border-radius: 50%;
    background: #4ade80;
    box-shadow: 0 0 8px rgba(74, 222, 128, 0.8);
    flex: 0 0 auto;
  }
  .hero-dots {
    position: absolute;
    left: 50%;
    bottom: 26px;
    transform: translateX(-50%);
    z-index: 3;
    display: flex;
    align-items: center;
    gap: 8px;
  }
  .hero-dot {
    width: 8px;
    height: 8px;
    padding: 0;
    border: 1px solid rgba(255, 255, 255, .65);
    border-radius: 999px;
    background: rgba(255, 255, 255, .25);
    cursor: pointer;
    transition: width .25s ease, background .25s ease, border-color .25s ease;
  }
  .hero-dot.active {
    width: 22px;
    background: #8fd4ff;
    border-color: #8fd4ff;
  }
  @media (max-width: 760px) {
    .hero-carousel { height: 320px; }
    .hero-copy { padding: 22px 18px 48px; }
    .hero-copy h1 { font-size: 20px; }
    .hero-copy p { font-size: 12.5px; }
    .hero-kicker { font-size: 9px; }
    .hero-action { font-size: 11.5px; gap: 6px; padding: 8px 10px 8px 12px; }
    .hero-cta { height: 30px; font-size: 11.5px; }
    .hero-meta {
      right: 16px; bottom: 18px;
      flex-direction: column; align-items: flex-end; gap: 6px;
      max-width: calc(100% - 32px);
    }
    .hero-pill { font-size: 10px; padding: 5px 9px; }
    .hero-dots { left: 18px; bottom: 18px; transform: none; }
  }
  @media (prefers-reduced-motion: reduce) {
    .hero-slide.active img { animation: none; transform: scale(1.02); }
    .hero-slide { transition: none; }
  }
</style>
</head>
<body>
  <div class="hero-carousel" id="fresh-hero-carousel">
    __SLIDES__
    <div class="hero-shade"></div>
    <div class="hero-copy">
      <div class="hero-kicker">FRESH SUPPLY CHAIN · AI REPLENISHMENT</div>
      <h1>智链参谋 · 生鲜前置仓 AI 补货决策系统</h1>
      <p>数据驱动源头直采 · 算法锁定新鲜每一刻</p>
      <div class="hero-action">
        <span><b>__URGENT__</b> 条紧急</span>
        <span class="hero-action-sep">·</span>
        <span><b>__EXPIRING__</b> 种临期</span>
        <span class="hero-action-sep">·</span>
        <span>建议补货 <b>__SUGGEST__</b> 件</span>
        <button class="hero-cta" id="hero-cta" type="button">查看补货清单</button>
      </div>
    </div>
    <div class="hero-meta">
      <span class="hero-pill"><span class="hero-pill-dot"></span>补货快照 __SNAPSHOT__</span>
      <span class="hero-pill">数据：__DATA_LABEL__</span>
    </div>
    <div class="hero-dots" id="hero-dots" role="tablist" aria-label="Banner 轮播">
      __DOTS__
    </div>
  </div>
  <script>
    (function () {
      var slides = Array.prototype.slice.call(document.querySelectorAll(".hero-slide"));
      var dots = Array.prototype.slice.call(document.querySelectorAll(".hero-dot"));
      var current = 0;
      var timer = null;
      var interval = 3500;

      function show(next) {
        if (!slides.length) return;
        slides[current].classList.remove("active");
        dots[current].classList.remove("active");
        current = (next + slides.length) % slides.length;
        slides[current].classList.add("active");
        dots[current].classList.add("active");
      }

      function nextSlide() {
        show(current + 1);
      }

      function start() {
        stop();
        timer = window.setInterval(nextSlide, interval);
      }

      function stop() {
        if (timer !== null) {
          window.clearInterval(timer);
          timer = null;
        }
      }

      dots.forEach(function (dot, index) {
        dot.addEventListener("click", function () {
          show(index);
          start();
        });
      });

      var cta = document.getElementById("hero-cta");
      if (cta) {
        cta.addEventListener("click", function () {
          try {
            var el = window.parent.document.getElementById("section-inventory");
            if (el) el.scrollIntoView({ behavior: "smooth", block: "start" });
          } catch (e) {}
        });
      }

      var root = document.getElementById("fresh-hero-carousel");
      root.addEventListener("mouseenter", stop);
      root.addEventListener("mouseleave", start);
      document.addEventListener("visibilitychange", function () {
        if (document.hidden) stop();
        else start();
      });

      start();
    })();
  </script>
</body>
</html>"""
    return (
        template.replace("__SLIDES__", "".join(slides))
        .replace("__DOTS__", "".join(dots))
        .replace("__SNAPSHOT__", html.escape(str(snapshot)))
        .replace("__DATA_LABEL__", html.escape(str(data_label)))
        .replace("__URGENT__", f"{int(urgent):,}")
        .replace("__EXPIRING__", f"{int(expiring):,}")
        .replace("__SUGGEST__", f"{int(suggest_total):,}")
    )


def render_fresh_header(snapshot: str) -> None:
    c1, c2, c3 = st.columns([6.0, 2.0, 2.4])
    with c1:
        st.caption("生鲜前置仓 · 每日补货决策")
    with c2:
        st.markdown(
            f'<div class="snapshot">补货快照 {html.escape(snapshot)}</div>',
            unsafe_allow_html=True,
        )
    with c3:
        st.markdown(
            '<div class="snapshot">数据：FreshRetailNet-50K（品类已脱敏）</div>',
            unsafe_allow_html=True,
        )
    st.markdown('<div class="topbar-line"></div>', unsafe_allow_html=True)


def _kpi_card(label: str, value: str, sub: str, source: str) -> str:
    return (
        '<div class="kpi-card">'
        f'<div class="kpi-label">{html.escape(label)}</div>'
        f'<div class="kpi-value">{value}</div>'
        f'<div class="kpi-sub">{sub}</div>'
        f'<div class="kpi-source">{html.escape(source)}</div>'
        "</div>"
    )


def fresh_model_wmape(metrics: pd.DataFrame | None) -> float | None:
    if metrics is None or metrics.empty or "wmape" not in metrics.columns:
        return None
    models = metrics["model"].astype(str)
    scopes = metrics["scope"].astype(str)
    rows = metrics[(models == "lgbm_a") & (scopes == "全部")]
    if rows.empty:
        rows = metrics[models.str.startswith("lgbm_a")].head(1)
    if rows.empty:
        return None
    value = pd.to_numeric(rows.iloc[0]["wmape"], errors="coerce")
    return None if pd.isna(value) else float(value)


def render_fresh_kpi(
    values: dict[str, float | int],
    snapshot: str,
    metrics: pd.DataFrame | None = None,
) -> None:
    wmape = fresh_model_wmape(metrics)
    accuracy = (1.0 - wmape) * 100 if wmape is not None else None
    expiring_qty = float(values["expiring_qty"])

    series = int(values.get("series", 0) or 0)
    urgent = int(values.get("urgent", 0) or 0)
    urgent_share = urgent / series if series else 0.0
    cards = [
        {
            "label": "紧急缺货数",
            "value": f"{urgent:,}",
            "unit": "个",
            "sub": f"占全部序列 {urgent_share:.1%} · 需立即补货",
            "tone": "ov-alert",
            "primary": True,
            "hint": "紧急度 = 紧急的序列数，需立即确认补货",
        },
        {
            "label": "总 SKU 数",
            "value": f"{series:,}",
            "unit": "个",
            "sub": "纳入分析的店 × SKU 序列",
            "tone": "",
            "hint": "当前纳入分析的店 × SKU 序列数",
        },
        {
            "label": "临期预警数",
            "value": f"{int(values['expiring_series']):,}",
            "unit": "个",
            "sub": f"涉及临期 {expiring_qty:,.1f} {FRESH_UNIT}",
            "tone": "ov-warn",
            "hint": f"涉及临期数量 {expiring_qty:,.2f} {FRESH_UNIT}",
        },
        {
            "label": "模型预测准确率",
            "value": f"{accuracy:.1f}" if accuracy is not None else "—",
            "unit": "%" if accuracy is not None else "",
            "sub": (
                f"1 − WMAPE · WMAPE {wmape:.1%}"
                if wmape is not None
                else "预测精度指标暂缺"
            ),
            "tone": "",
            "hint": (
                f"1 − WMAPE；WMAPE {wmape:.2%}（lgbm_a / 全部）"
                if wmape is not None
                else "未找到预测精度指标"
            ),
        },
    ]

    cards_html = []
    for card in cards:
        tone = f" {card['tone']}" if card["tone"] else ""
        primary = " ov-primary" if card.get("primary") else ""
        cards_html.append(
            f'<article class="ov-card{tone}{primary}" '
            f'title="{html.escape(card["hint"])}">'
            f'<div class="ov-label"><span class="ov-dot"></span>'
            f'{html.escape(card["label"])}</div>'
            f'<div class="ov-value">{html.escape(card["value"])}'
            f'<span class="ov-unit">{html.escape(card["unit"])}</span></div>'
            f'<div class="ov-sub">{html.escape(card["sub"])}</div>'
            "</article>"
        )

    foot_parts = []
    short_shelf = values.get("short_shelf")
    c_rate = values.get("spoilage_c")
    b_rate = values.get("spoilage_b")
    if short_shelf is not None and c_rate is not None and b_rate is not None:
        foot_parts.append(
            f"短保报损对比（{int(short_shelf)} 天档）：本方案 "
            f"{float(c_rate):.2%} <span class=\"ov-vs\">vs</span> 传统补货 "
            f"{float(b_rate):.2%}"
        )
    foot = (
        '<div class="ov-foot">' + " · ".join(foot_parts) + "</div>" if foot_parts else ""
    )

    st.markdown(
        '<section class="overview-dock" id="section-overview" '
        f'data-snapshot="{html.escape(str(snapshot))}">'
        '<div class="ov-grid">'
        + "".join(cards_html)
        + "</div>"
        + foot
        + "</section>",
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------------------
# 生鲜版：补货清单
# ---------------------------------------------------------------------------
def _normalize_search(raw: str) -> str:
    q = str(raw or "").strip()
    if q.upper().startswith("SKU"):
        q = q[3:]
    return q.replace(" ", "").replace("-", "")


def _fresh_inventory_table_html(display: pd.DataFrame) -> str:
    """卡片式补货表格：行间距、状态色、表头点击排序。"""
    numeric_columns = {
        col for col in display.columns if pd.api.types.is_numeric_dtype(display[col])
    }
    badge_classes = {
        "紧急": "badge-danger",
        "建议补货": "badge-warning",
        "计划补货": "badge-info",
        "正常": "badge-success",
    }

    # 首列与两个状态列使用固定比例，其余数值列均分剩余宽度。
    # 导入数据列数较少时，首列适当加宽，避免数值列被拉得过大。
    column_count = len(display.columns)
    if column_count == 1:
        first_width = 100
    elif column_count == 2:
        first_width = 50
    elif column_count <= 4:
        first_width = 32
    else:
        first_width = 23
    fixed_widths = {
        "店 / SKU": first_width,
        "商品（门店-编号）": first_width,
        "紧急度": 13,
        "临期": 8,
    }
    present_fixed = {
        col: width for col, width in fixed_widths.items() if col in display.columns
    }
    flexible_columns = [
        col for col in display.columns if col not in present_fixed
    ]
    flexible_width = (
        (100 - sum(present_fixed.values())) / len(flexible_columns)
        if flexible_columns
        else 0
    )
    colgroup = "".join(
        f'<col style="width: {present_fixed.get(col, flexible_width):.2f}%">'
        for col in display.columns
    )

    headers = []
    for col in display.columns:
        col_type = "num" if col in numeric_columns else "text"
        header_text = str(col).replace(f"（{FRESH_UNIT}）", "")
        headers.append(
            f'<th scope="col" data-type="{col_type}">'
            f"{html.escape(header_text)}</th>"
        )

    rows = []
    for _, row in display.iterrows():
        is_expiring = str(row.get("临期", "")) == "是"
        row_class = ' class="row-expiring"' if is_expiring else ""
        cells = []
        for col in display.columns:
            value = row[col]
            if col in numeric_columns:
                number = float(value)
                text = (
                    f"{int(round(number)):,}"
                    if str(col).startswith("建议订货")
                    else f"{number:,.2f}"
                )
                cells.append(
                    f'<td class="num" data-sort="{number}">{html.escape(text)}</td>'
                )
                continue

            text = html.escape(str(value))
            if col == "紧急度":
                badge = badge_classes.get(str(value), "badge-muted")
                cells.append(
                    f'<td data-sort="{text}">'
                    f'<span class="badge {badge}">{text}</span></td>'
                )
            elif col == "临期":
                if is_expiring:
                    cells.append(
                        '<td data-sort="1">'
                        '<span class="badge badge-expiring">临期</span></td>'
                    )
                else:
                    cells.append(
                        '<td data-sort="0">'
                        '<span class="expiry-ok" title="未临期">✓</span></td>'
                    )
            elif col in ("店 / SKU", "商品（门店-编号）"):
                cells.append(
                    f'<td class="sku" data-sort="{text}">{text}</td>'
                )
            else:
                cells.append(f'<td data-sort="{text}">{text}</td>')

        rows.append(f"<tr{row_class}>" + "".join(cells) + "</tr>")

    template = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  * { box-sizing: border-box; }
  html, body {
    margin: 0; padding: 0; width: 100%; height: 100%;
    overflow-x: hidden;
    background: #f8f9fa;
    font-family: "Microsoft YaHei", "PingFang SC", "Segoe UI", sans-serif;
    color: #334155;
  }
  .table-wrap {
    display: flex;
    flex-direction: column;
    width: 100%;
    height: 100%;
  }
  .table-scroll {
    flex: 1 1 auto;
    min-height: 0;
    width: 100%;
    overflow-y: auto;
    overflow-x: hidden;
    border-radius: 14px;
    padding: 0;
  }
  table.fresh-card-table {
    width: 100%;
    min-width: 0;
    table-layout: fixed;
    border-collapse: separate;
    border-spacing: 0 8px;
    font-size: 12.5px;
  }
  thead th {
    position: sticky;
    top: 0;
    z-index: 5;
    background: linear-gradient(180deg, #0E3A63, #0B2F52);
    background-clip: padding-box;
    color: #EAF3FB;
    font-size: 11.5px;
    font-weight: 600;
    letter-spacing: .02em;
    text-align: left;
    white-space: nowrap;
    padding: 11px 11px;
    cursor: pointer;
    user-select: none;
  }
  thead th[data-type="num"] { text-align: right; }
  thead th:first-child { border-radius: 10px 0 0 10px; }
  thead th:last-child { border-radius: 0 10px 10px 0; }
  thead th.sorted-asc::after { content: " ▲"; color: #8FD4FF; font-size: 10px; }
  thead th.sorted-desc::after { content: " ▼"; color: #8FD4FF; font-size: 10px; }
  thead th:focus-visible {
    outline: 2px solid #8FD4FF;
    outline-offset: -2px;
  }
  tbody td {
    background: #FFFFFF;
    padding: 12px 11px;
    white-space: nowrap;
    border-top: 1px solid #E8EFF7;
    border-bottom: 1px solid #E8EFF7;
    vertical-align: middle;
  }
  tbody td:first-child {
    border-left: 1px solid #E8EFF7;
    border-radius: 10px 0 0 10px;
  }
  tbody td:last-child {
    border-right: 1px solid #E8EFF7;
    border-radius: 0 10px 10px 0;
  }
  tbody tr.row-expiring td { background: #FFF7E8; }
  tbody tr:hover td { background: #F6FAFE; }
  td.num { text-align: right; font-variant-numeric: tabular-nums; color: #0B2A4A; font-weight: 600; }
  td.sku { text-align: left; color: #0B2A4A; font-weight: 700; }
  .badge {
    display: inline-flex; align-items: center; gap: 6px;
    padding: 5px 12px; border-radius: 999px;
    font-size: 12px; font-weight: 700; line-height: 1.4;
  }
  .badge::before {
    content: ""; width: 6px; height: 6px; border-radius: 50%;
    background: currentColor;
  }
  .badge-danger { color: #B42318; background: #FEE4E2; }
  .badge-success { color: #067647; background: #DCFAE6; }
  .badge-warning { color: #B54708; background: #FEF0C7; }
  .badge-info { color: #1C5CAB; background: #EEF4FD; }
  .badge-expiring { color: #B45309; background: #FFF3E8; }
  .badge-muted { color: #475569; background: #E2E8F0; }
  .expiry-ok { color: #2E9E5B; font-size: 13px; font-weight: 700; }
  @media (max-width: 900px) {
    table.fresh-card-table { font-size: 11.5px; }
    thead th { padding: 9px 6px; font-size: 10.5px; }
    tbody td { padding: 10px 6px; }
    .badge { padding: 4px 8px; font-size: 11px; }
    .badge::before { width: 5px; height: 5px; }
    /* 列宽由服务端按实际列数写入 colgroup，窄屏不再硬编码列序。 */
  }
</style>
</head>
<body>
  <div class="table-wrap">
    <div class="table-scroll">
      <table class="fresh-card-table" id="fresh-card-table">
        <colgroup>__COLS__</colgroup>
        <thead><tr>__HEADERS__</tr></thead>
        <tbody>__ROWS__</tbody>
      </table>
    </div>
  </div>
  <script>
    (function () {
      var table = document.getElementById("fresh-card-table");
      var tbody = table.querySelector("tbody");
      var headers = Array.prototype.slice.call(table.querySelectorAll("thead th"));
      var sortIndex = -1;
      var sortDirection = 1;

      headers.forEach(function (th, index) {
        th.setAttribute("tabindex", "0");
        th.setAttribute("aria-sort", "none");

        function applySort() {
          if (sortIndex === index) sortDirection *= -1;
          else { sortIndex = index; sortDirection = 1; }
          headers.forEach(function (h) {
            h.classList.remove("sorted-asc", "sorted-desc");
            h.setAttribute("aria-sort", "none");
          });
          th.classList.add(sortDirection === 1 ? "sorted-asc" : "sorted-desc");
          th.setAttribute(
            "aria-sort",
            sortDirection === 1 ? "ascending" : "descending"
          );
          var rows = Array.prototype.slice.call(tbody.querySelectorAll("tr"));
          var numeric = th.getAttribute("data-type") === "num";
          rows.sort(function (a, b) {
            var av = a.children[index].getAttribute("data-sort") || "";
            var bv = b.children[index].getAttribute("data-sort") || "";
            if (numeric) {
              return (parseFloat(av) - parseFloat(bv)) * sortDirection;
            }
            return String(av).localeCompare(String(bv), "zh-CN") * sortDirection;
          });
          rows.forEach(function (row) { tbody.appendChild(row); });
        }

        th.addEventListener("click", applySort);
        th.addEventListener("keydown", function (event) {
          if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            applySort();
          }
        });
      });

    })();
  </script>
</body>
</html>"""
    return (
        template.replace("__HEADERS__", "".join(headers))
        .replace("__ROWS__", "".join(rows))
        .replace("__COLS__", colgroup)
    )


# ---------------------------------------------------------------------------
# 底部数据导入（CSV / Excel）
# ---------------------------------------------------------------------------
_IMPORT_REQUIRED_FIELDS = ("SKU", "库存", "销量")
_IMPORT_COLUMN_ALIASES: dict[str, tuple[str, ...]] = {
    "SKU": ("SKU", "sku_id", "商品编码", "物料编码", "店 / SKU", "序列"),
    "库存": (
        "库存",
        "现有库存",
        "当前库存",
        "库存数量",
        "on_hand",
        "current_stock",
        "stock",
        "inventory",
    ),
    "销量": (
        "销量",
        "销售量",
        "日均销量",
        "销量指数",
        "sales",
        "sales_qty",
        "demand",
        "actual_qty",
        "qty",
    ),
    "建议订货": ("建议订货", "建议补货量", "补货量", "suggest_qty", "order_qty"),
    "紧急度": ("紧急度", "库存状态", "状态", "urgency", "priority"),
    "临期数量": ("临期数量", "临期数", "expiring_qty", "expiring"),
}


def _normalize_import_name(name: object) -> str:
    text = str(name or "").strip().lower()
    text = re.sub(r"[（(][^）)]*[）)]", "", text)
    return re.sub(r"[\s_\-/]+", "", text)


def _match_import_columns(df: pd.DataFrame) -> dict[str, str]:
    normalized: dict[str, str] = {}
    for col in df.columns:
        normalized.setdefault(_normalize_import_name(col), col)
    mapping: dict[str, str] = {}
    used: set[str] = set()
    for field, aliases in _IMPORT_COLUMN_ALIASES.items():
        for alias in aliases:
            col = normalized.get(_normalize_import_name(alias))
            if col is not None and col not in used:
                mapping[field] = col
                used.add(col)
                break
    return mapping


def _read_uploaded_table(uploaded) -> pd.DataFrame:
    data = uploaded.getvalue()
    name = str(uploaded.name).lower()
    if name.endswith(".csv"):
        last_error: Exception | None = None
        for encoding in ("utf-8-sig", "gbk", "utf-8"):
            try:
                return pd.read_csv(io.BytesIO(data), encoding=encoding)
            except UnicodeDecodeError as exc:
                last_error = exc
        raise ValueError("CSV 编码无法识别，请另存为 UTF-8 后重试。") from last_error
    if name.endswith(".xlsx"):
        return pd.read_excel(io.BytesIO(data), engine="openpyxl")
    raise ValueError("仅支持 CSV / XLSX 文件。")


def _prepare_import_frame(
    df: pd.DataFrame, mapping: dict[str, str]
) -> pd.DataFrame:
    canonical = pd.DataFrame({field: df[col] for field, col in mapping.items()})
    present_required = [
        field for field in _IMPORT_REQUIRED_FIELDS if field in canonical.columns
    ]
    if not present_required:
        return canonical.iloc[0:0]
    cleaned = canonical.dropna(subset=present_required).copy()
    for field in ("库存", "销量", "建议订货", "临期数量"):
        if field in cleaned.columns:
            cleaned[field] = pd.to_numeric(cleaned[field], errors="coerce")
    numeric_required = [
        field for field in ("库存", "销量") if field in cleaned.columns
    ]
    if numeric_required:
        cleaned = cleaned.dropna(subset=numeric_required)
    return cleaned.reset_index(drop=True)


def _store_import(
    frame: pd.DataFrame, partial: bool, missing: list[str], filename: str
) -> None:
    st.session_state["imported_table"] = frame.copy()
    st.session_state["imported_meta"] = {
        "partial": partial,
        "missing": list(missing),
        "filename": filename,
        "imported_at": pd.Timestamp.now().strftime("%Y-%m-%d %H:%M"),
    }
    st.session_state["use_imported_data"] = True


def _render_imported_inventory(imported: pd.DataFrame) -> None:
    display = pd.DataFrame()
    if "SKU" in imported.columns:
        display["店 / SKU"] = imported["SKU"].astype(str)
    else:
        display["店 / SKU"] = [f"行 {index + 1}" for index in range(len(imported))]
    if "库存" in imported.columns:
        display["现有库存"] = (
            pd.to_numeric(imported["库存"], errors="coerce").fillna(0).round(2)
        )
    if "销量" in imported.columns:
        display["销量"] = (
            pd.to_numeric(imported["销量"], errors="coerce").fillna(0).round(2)
        )
    if "建议订货" in imported.columns:
        display["建议订货"] = (
            pd.to_numeric(imported["建议订货"], errors="coerce")
            .fillna(0)
            .round(0)
            .astype(int)
        )
    if "紧急度" in imported.columns:
        display["紧急度"] = imported["紧急度"].astype(str)
    if "临期数量" in imported.columns:
        qty = pd.to_numeric(imported["临期数量"], errors="coerce").fillna(0)
        display["临期"] = ["是" if value > 0 else "" for value in qty]

    st.markdown(
        '<div class="import-banner"><span class="import-dot"></span>'
        f"当前清单：自定义导入数据 · {len(imported):,} 行"
        '<span class="import-hint">KPI / 预测仍使用系统快照</span></div>',
        unsafe_allow_html=True,
    )
    table_height = min(560, 150 + 46 * len(display))
    components.html(_fresh_inventory_table_html(display), height=table_height)
    if st.button("恢复系统数据", key="restore_system_data"):
        st.session_state["use_imported_data"] = False
        st.rerun()


def _render_data_import() -> None:
    with st.expander("📁 上传自有数据（支持 CSV / Excel）", expanded=False):
        st.caption(
            "至少需要 SKU、库存、销量三列；支持 CSV / XLSX，"
            "自动识别常见中英文列名与带单位的表头。"
        )
        uploaded = st.file_uploader(
            "选择文件", type=["csv", "xlsx"], key="dashboard_import_file"
        )
        if uploaded is not None:
            try:
                raw_df = _read_uploaded_table(uploaded)
            except Exception as exc:
                st.error(f"文件读取失败：{exc}")
                raw_df = None
            if raw_df is not None:
                if raw_df.empty:
                    st.warning("文件没有数据行。")
                else:
                    st.markdown("**前 5 行预览**")
                    st.dataframe(
                        raw_df.head(5), use_container_width=True, hide_index=True
                    )
                    mapping = _match_import_columns(raw_df)
                    missing = [
                        field
                        for field in _IMPORT_REQUIRED_FIELDS
                        if field not in mapping
                    ]
                    recognized = list(mapping.keys())
                    if missing:
                        st.warning(
                            "缺少必需列：" + "、".join(missing) + "。"
                            "已识别列："
                            + "、".join(recognized if recognized else ["无"])
                            + "。请检查表头命名。"
                        )
                        if st.button("尝试部分导入", key="partial_import_button"):
                            partial_df = _prepare_import_frame(raw_df, mapping)
                            if partial_df.empty:
                                st.error(
                                    "没有可导入的完整行：已识别字段中存在空值。"
                                )
                            else:
                                _store_import(
                                    partial_df,
                                    partial=True,
                                    missing=missing,
                                    filename=uploaded.name,
                                )
                                st.rerun()
                    else:
                        st.success("列校验通过：SKU、库存、销量均已识别。")
                        if st.button(
                            "替换当前看板数据",
                            type="primary",
                            key="replace_data_button",
                        ):
                            full_df = _prepare_import_frame(raw_df, mapping)
                            if full_df.empty:
                                st.error(
                                    "必需字段存在空值，没有可导入的完整行。"
                                )
                            else:
                                _store_import(
                                    full_df,
                                    partial=False,
                                    missing=[],
                                    filename=uploaded.name,
                                )
                                st.rerun()

        imported = st.session_state.get("imported_table")
        if imported is not None:
            meta = st.session_state.get("imported_meta", {})
            st.success(
                f"当前会话已载入自定义数据：{len(imported):,} 行"
                + ("（部分导入）" if meta.get("partial") else "")
            )
            if meta.get("missing"):
                st.caption(
                    "缺失字段："
                    + "、".join(meta["missing"])
                    + "；这些列在导入数据中为空。"
                )
            if meta.get("filename"):
                st.caption(
                    f"来源文件：{meta['filename']} · {meta.get('imported_at', '')}"
                )
            st.dataframe(
                imported.head(10), use_container_width=True, hide_index=True
            )
            if st.button("清除导入数据", key="clear_imported_data"):
                for key in (
                    "imported_table",
                    "imported_meta",
                    "use_imported_data",
                ):
                    st.session_state.pop(key, None)
                st.rerun()


# ---------------------------------------------------------------------------
# 决策溯源（功能 2）：表格下方 selectbox 选序列 + 三个 tab
# 独立代码块，不改动上方表格、轮播图与顶栏；只读已加载的数据与 output 表。
# ---------------------------------------------------------------------------
def _trace_whatif_table(key: tuple[int, int]) -> None:
    """② 不照做会怎样：优先看这条序列的明细，缺失时退回全局汇总。"""
    detail = load_csv(FRESH_WHATIF)
    summary = load_csv(FRESH_WHATIF_SUMMARY)
    labels: dict[float, str] = {}
    if summary is not None and not summary.empty:
        labels = {
            float(row.scenario_ratio): str(row.scenario_label)
            for row in summary.itertuples()
        }

    sub = None
    if detail is not None and not detail.empty:
        sub = detail[
            (pd.to_numeric(detail["store_id"], errors="coerce") == key[0])
            & (pd.to_numeric(detail["product_id"], errors="coerce") == key[1])
        ]

    if sub is None or sub.empty:
        st.info("这条序列没有 what-if 明细，只能看全局口径。")
        if summary is None or summary.empty:
            return
        table = summary.copy()
        source_note = "全部 200 条序列合计"
    else:
        table = sub.copy()
        source_note = f"仅 {series_label(*key)} 这一条序列"

    table = table.sort_values("scenario_ratio")
    label_column = [
        labels.get(float(ratio), f"{float(ratio):.2f} 倍")
        for ratio in table["scenario_ratio"]
    ]

    if "scenario_order_qty" in table.columns:
        display = pd.DataFrame(
            {
                "下法": label_column,
                "下单量": table["scenario_order_qty"].astype(float).round(0).astype(int),
                "缺货件": table["unmet_units"].astype(float).round(2),
                "报损件": table["spoiled_units"].astype(float).round(2),
                "满足率": [
                    f"{float(x):.2%}" for x in table["fill_rate"].astype(float)
                ],
                "相对按建议的成本差": [
                    f"{float(x):+.2f}"
                    for x in table["delta_cost_vs_base"].astype(float)
                ],
            }
        )
    else:
        display = pd.DataFrame(
            {
                "下法": label_column,
                "总缺货件": table["总缺货件"].astype(float).round(2),
                "总报损件": table["总报损件"].astype(float).round(2),
                "平均满足率": [
                    f"{float(x):.2%}" for x in table["平均满足率"].astype(float)
                ],
                "相对基线成本差": [
                    f"{float(x):+.2f}" for x in table["相对基线成本差"].astype(float)
                ],
            }
        )

    st.caption(f"口径：{source_note}，7 天窗口，需求按实测回放；基线是「按建议」。")
    st.dataframe(display, hide_index=True, use_container_width=True)
    st.caption(
        "两点如实说明：① 0.75 倍档的报损可能高于按建议档（批次轮换的次生效应），"
        "不是必然单调；② 多订到 1.5 倍以上时缺货几乎不再下降、报损却大幅上升，"
        "说明瓶颈在效期而不是订货量。"
    )


def _trace_alpha(shelf_life: int) -> None:
    """③ 分位凭什么定在这：α 扫描的期末总成本曲线。"""
    term = load_csv(FRESH_ALPHA_TERMINAL)
    if term is None or term.empty:
        st.info("没有 α 扫描结果表，跳过这一页。")
        return

    shelf_options = sorted(
        {int(x) for x in term["shelf_life_days"].dropna().unique().tolist()}
    )
    use_shelf = shelf_life if shelf_life in shelf_options else shelf_options[0]
    if use_shelf != shelf_life:
        st.caption(
            f"α 扫描只跑了 {' / '.join(f'{s} 天' for s in shelf_options)} 两档；"
            f"当前序列是 {shelf_life} 天档，其结论不外推。"
        )

    base = term[
        (term["shelf_life_days"].astype(int) == use_shelf)
        & (term["basis"].astype(str) == "no_settle")
    ].sort_values("alpha")
    if base.empty:
        st.info("这一档没有 no_settle 口径的结果。")
        return

    st.markdown(f"**{use_shelf} 天档：安全库存分位 α 与期末总成本**")
    chart = pd.DataFrame(
        {
            "期末总成本（缺货 5 + 报损 1 + 持有）": base["total_cost"]
            .astype(float)
            .tolist()
        },
        index=[f"{float(a):.2f}" for a in base["alpha"].tolist()],
    )
    st.line_chart(chart, height=260)
    st.caption(
        "横轴是触发线用的分位 α，纵轴是 7 天期末结算总成本。"
        "0.90 以上曲线进入平台区，相邻档差的中位数已接近单步噪声，"
        "因此只报落点区间、不宣称单点最优。"
    )
    st.markdown(
        "- 0.90 以上**不可辨识**：符号检验 p=0.6562（3 天）/ 0.1094（5 天），均 > 0.05，"
        "只报区间 [0.90, 0.96] / [0.90, 0.98]。\n"
        "- 名义报童临界分位 0.833，比区间最低点高 2.66% / 4.28%，"
        "所以**83% 偏低**、调到 0.90 是真收益。\n"
        "- α 作用在**触发线**，不改变每次订多少——"
        "这条口径差异在报告 1 节已写明，不要把它说成「验证了报童公式」。"
    )


def render_trace_section(
    repl: pd.DataFrame,
    caps: dict[tuple[int, int], dict],
    config: dict,
) -> None:
    """表格下方的溯源区：选序列 -> 三个分页展示计算依据。"""
    if repl is None or repl.empty:
        return

    rep_cfg = (config or {}).get("replenishment", {}) or {}
    base_multiplier = float(rep_cfg.get("expiry_cap_multiplier", 1.0))
    lead_time = int(rep_cfg.get("lead_time_days", 1))
    cover_days = int(rep_cfg.get("target_cover_days", 7))
    pack = int(rep_cfg.get("order_pack_size", 1))
    min_qty = int(rep_cfg.get("min_order_qty", 1))
    urgent_ratio = float(
        ((config or {}).get("urgency", {}) or {}).get("urgent_rop_ratio", 0.5)
    )

    st.divider()
    st.markdown(
        '<div id="section-trace" style="scroll-margin-top: 80px;"></div>',
        unsafe_allow_html=True,
    )
    st.markdown("#### 决策溯源")
    st.caption(
        "选择一条序列：① 订货量怎么算出来的；② 不照做会怎样；"
        "③ 安全库存分位凭什么定在这。"
    )

    rows_by_key = {
        (int(row.store_id), int(row.product_id)): row for row in repl.itertuples()
    }
    options = list(rows_by_key)

    def _label(key: tuple[int, int]) -> str:
        tier = str(getattr(rows_by_key[key], "urgency", ""))
        label = f"{product_names.product_name(*key)} · {series_label(*key)}"
        return f"{label} · {tier}" if tier else label

    # 用选项指纹做 key，保留同一组选项下的选中状态。
    option_sig = abs(hash(tuple(options))) % 1_000_000
    select_key = f"fresh_trace_select_{option_sig}"
    deep_sku = _parse_sku_param()
    if deep_sku is None:
        st.session_state.pop("fresh_deep_link_applied", None)
    elif deep_sku in rows_by_key:
        if st.session_state.get("fresh_deep_link_applied") != deep_sku:
            st.session_state[select_key] = deep_sku
            st.session_state["fresh_deep_link_applied"] = deep_sku
    else:
        st.warning(
            f"深链接中的 SKU {deep_sku[0]}-{deep_sku[1]} 不在当前数据中。"
        )
    choice = st.selectbox(
        "选择要溯源的序列",
        options=options,
        index=None,
        format_func=_label,
        placeholder="选择店 × SKU（默认不展开）",
        key=select_key,
    )
    if choice is None:
        return

    record = rows_by_key[choice]
    key = (int(record.store_id), int(record.product_id))
    row = {
        "store_id": key[0],
        "product_id": key[1],
        "shelf_life_days": int(record.shelf_life_days),
        "on_hand": float(record.on_hand),
        "rop_p90": float(record.rop_p90),
        "target_level": float(record.target_level),
        "suggest_qty": int(record.suggest_qty),
        "urgency": str(record.urgency),
    }
    cap_info = caps.get(key)

    st.markdown(
        f"**{series_label(*key)}** · 建议订货 **{row['suggest_qty']:,}** 件"
        f"（紧急度 {row['urgency']}）"
    )
    with st.expander("复制 SKU 深链接"):
        components.html(
            _share_view_html(
                f"sku={key[0]}-{key[1]}",
                "SKU 深链接 · 复制后打开会直接定位到该 SKU 的决策溯源",
            ),
            height=100,
        )

    # 行内问 AI：只针对当前选中的这条序列，不影响下方聊天区。
    target_eff = (
        float(cap_info["target_eff"]) if cap_info else float(row["target_level"])
    )
    agent = get_agent()
    api_key = agent.load_api_key()
    ai_key = f"trace_ai_{key[0]}_{key[1]}"
    ask_col, hint_col = st.columns([1.15, 4.0])
    with ask_col:
        asked = st.button(
            "问 AI 解释",
            key=f"trace_ask_{key[0]}_{key[1]}",
            use_container_width=True,
            disabled=not api_key,
        )
    with hint_col:
        st.caption(
            "让 Agent 结合库存、ROP 与效期数据解释这条建议；回答只基于补货数据。"
            if api_key
            else "未配置 DEEPSEEK_API_KEY，暂时无法调用。"
        )

    if asked:
        question = (
            f"{series_label(*key)}：现有库存 {row['on_hand']:.2f}，"
            f"ROP_P90 {row['rop_p90']:.2f}，效期修正目标 {target_eff:.2f}，"
            f"建议订货 {row['suggest_qty']}。请解释这个订货量是怎么定的，"
            "并说明不按建议执行的风险。"
        )
        with st.spinner("Agent 正在核对数据..."):
            try:
                activate_agent_dataset(agent, "freshretail")
                answer, tools = agent.run_turn(
                    [
                        {"role": "system", "content": _system_prompt("freshretail")},
                        {"role": "user", "content": question},
                    ],
                    get_fresh_store(),
                    api_key,
                    echo=False,
                )
            except agent.AgentError as exc:
                answer, tools = str(exc), []
            except Exception:
                answer, tools = "调用失败，请稍后重试；页面数据不受影响。", []
        st.session_state[ai_key] = {
            "answer": answer or "（模型未返回内容）",
            "tools": tools,
        }

    cached_ai = st.session_state.get(ai_key)
    if cached_ai:
        with st.container(border=True):
            st.markdown("**AI 解读**")
            st.markdown(cached_ai.get("answer", ""))
            if cached_ai.get("tools"):
                with st.expander("查看工具调用"):
                    _tool_call_markdown(cached_ai["tools"])

    tab_chain, tab_whatif, tab_alpha = st.tabs(
        ["① 这个数怎么算的", "② 不照做会怎样", "③ 分位凭什么定在这"]
    )

    with tab_chain:
        if cap_info is None:
            st.warning("这条序列在 7 天预测表里找不到对应日期，无法展开推导链。")
        else:
            steps = decision_support.trace_steps(
                row,
                cap_info,
                base_multiplier,
                lead_time,
                cover_days,
                urgent_ratio,
            )
            for step in steps:
                st.markdown(f"**{step['step']}** ｜ `{step['formula']}`")
                st.markdown(step["detail"])

            scheme = decision_support.scheme_quantities(
                row,
                float(cap_info["sellable"]),
                base_multiplier,
                pack,
                min_qty,
            )
            base_ok = scheme["当前配置"]["qty"] == row["suggest_qty"]
            if not base_ok:
                st.info(
                    "三方案自检未通过（当前配置版与补货快照的建议量不一致），"
                    "这里只展示推导链，不展示另外两种下法。"
                )
            else:
                st.markdown("")
                st.markdown(
                    "**三种下法对比**（只改效期上限倍数，触发线 ROP_P90 不动）"
                )
                base_qty = scheme["当前配置"]["qty"]
                compare_rows = []
                for name in ("收紧一档", "当前配置", "放宽一档"):
                    item = scheme[name]
                    delta = item["qty"] - base_qty
                    compare_rows.append(
                        {
                            "方案": name,
                            "效期倍数": round(item["multiplier"], 2),
                            "效期上限": round(item["cap"], 2),
                            "修正目标": round(item["target_eff"], 2),
                            "订货量": item["qty"],
                            "相对当前": "—" if delta == 0 else f"{delta:+d} 件",
                        }
                    )
                st.dataframe(
                    pd.DataFrame(compare_rows),
                    hide_index=True,
                    use_container_width=True,
                )
                st.caption(
                    "当前配置版必须与补货快照的建议量相等，否则整列降级。"
                    "收紧一档少订、缺货变多；放宽一档多订、报损上升。"
                )

    with tab_whatif:
        _trace_whatif_table(key)

    with tab_alpha:
        _trace_alpha(row["shelf_life_days"])


DECISION_ITEMS_PATH = OUTPUT_DIR / "decision_items.csv"
DECISION_BATCHES_PATH = OUTPUT_DIR / "decision_batches.csv"
_DECISION_ITEM_FIELDS = (
    "batch_id",
    "created_at",
    "snapshot_date",
    "store_id",
    "product_id",
    "shelf_life_days",
    "urgency",
    "on_hand",
    "rop_p90",
    "suggest_qty",
    "applied_ratio",
    "applied_qty",
    "status",
    "operator",
    "note",
)
_DECISION_BATCH_FIELDS = (
    "batch_id",
    "action",
    "status",
    "at",
    "operator",
    "item_count",
    "total_qty",
    "note",
)


def _empty_decision_items() -> pd.DataFrame:
    return pd.DataFrame(columns=_DECISION_ITEM_FIELDS)


def _load_decision_items() -> pd.DataFrame:
    if not DECISION_ITEMS_PATH.exists():
        return _empty_decision_items()
    try:
        frame = pd.read_csv(DECISION_ITEMS_PATH, encoding="utf-8-sig")
    except Exception:
        return _empty_decision_items()
    for column in _DECISION_ITEM_FIELDS:
        if column not in frame.columns:
            frame[column] = ""
    return frame[list(_DECISION_ITEM_FIELDS)]


def _save_decision_items(frame: pd.DataFrame) -> None:
    DECISION_ITEMS_PATH.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(DECISION_ITEMS_PATH, index=False, encoding="utf-8-sig")


def _load_batch_events() -> pd.DataFrame:
    if not DECISION_BATCHES_PATH.exists():
        return pd.DataFrame(columns=_DECISION_BATCH_FIELDS)
    try:
        return pd.read_csv(DECISION_BATCHES_PATH, encoding="utf-8-sig")
    except Exception:
        return pd.DataFrame(columns=_DECISION_BATCH_FIELDS)


def _append_batch_event(
    batch_id: str,
    action: str,
    status: str,
    operator: str,
    item_count: int,
    total_qty: int,
    note: str = "",
) -> None:
    row = {
        "batch_id": batch_id,
        "action": action,
        "status": status,
        "at": pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S"),
        "operator": operator,
        "item_count": int(item_count),
        "total_qty": int(total_qty),
        "note": note,
    }
    decision_support.append_rows(
        DECISION_BATCHES_PATH, [row], _DECISION_BATCH_FIELDS
    )


def _new_batch_id() -> str:
    stamp = pd.Timestamp.now().strftime("%Y%m%d%H%M%S")
    return f"{stamp}-{hashlib.md5(os.urandom(4)).hexdigest()[:4]}"


def _latest_batch(
    items: pd.DataFrame,
) -> tuple[str, pd.DataFrame, str] | None:
    if items is None or items.empty:
        return None
    batch_id = str(items["batch_id"].astype(str).max())
    sub = items[items["batch_id"].astype(str) == batch_id]
    if sub.empty:
        return None
    return batch_id, sub, str(sub["status"].iloc[-1])


def _update_batch_status(
    batch_id: str,
    new_status: str,
    operator: str,
    action: str,
) -> int:
    frame = _load_decision_items()
    mask = frame["batch_id"].astype(str) == batch_id
    if not mask.any():
        return 0
    frame.loc[mask, "status"] = new_status
    _save_decision_items(frame)
    sub = frame[mask]
    total = int(
        pd.to_numeric(sub["applied_qty"], errors="coerce").fillna(0).sum()
    )
    _append_batch_event(
        batch_id,
        action,
        new_status,
        operator,
        len(sub),
        total,
        note=f"{batch_id} -> {new_status}",
    )
    return len(sub)


def render_order_panel(view: pd.DataFrame, snapshot: str) -> None:
    """逐 SKU 补货单：加入 → 确认 → 审批 → 下单 → 到货，支持导出与撤销。"""
    st.divider()
    st.markdown("#### 补货单与审批")
    st.caption(
        "逐 SKU 加入补货单 → 确认 → 审批 → 下单 → 到货；"
        "支持导出采购单与撤销，所有动作写入批次审计。"
    )
    if view is None or view.empty:
        st.info("当前筛选没有可加入补货单的序列。")
        return

    can_write = _can_write_decisions()
    can_approve = _current_role() == "admin"
    operator = str(st.session_state.get("auth_account", "店长") or "店长")
    ratio_labels = {
        0.5: "半量（0.5 倍）",
        0.75: "七五折（0.75 倍）",
        1.0: "按建议（1.0 倍）",
        1.25: "加一成（1.25 倍）",
        1.5: "加五成（1.5 倍）",
    }
    c_ratio, c_meta = st.columns([1.2, 2.6])
    ratio = c_ratio.selectbox(
        "本次下单比例",
        options=[1.0, 0.75, 1.25, 0.5, 1.5],
        format_func=lambda x: ratio_labels.get(x, f"{x:.2f} 倍"),
        key="order_ratio",
    )
    c_meta.caption(
        f"操作人：{operator} · 当前角色：{_role_label(_current_role())}"
    )
    if not can_write:
        st.caption("查看者只能查看批次与导出采购单；加入/确认需要计划员或管理员。")

    rows_by_key = {
        (int(row.store_id), int(row.product_id)): row
        for row in view.itertuples()
        if int(row.suggest_qty) > 0
    }
    if not rows_by_key:
        st.info("当前筛选里没有建议订货量大于 0 的序列。")
        return

    cart: dict[str, dict] = st.session_state.setdefault("order_cart", {})

    def add_to_cart(key: tuple[int, int], row) -> int:
        applied = int(math.floor(int(row.suggest_qty) * float(ratio) + 0.5))
        cart[f"{key[0]}-{key[1]}"] = {
            "store_id": key[0],
            "product_id": key[1],
            "shelf_life_days": int(row.shelf_life_days),
            "urgency": str(row.urgency),
            "on_hand": float(row.on_hand),
            "rop_p90": float(row.rop_p90),
            "suggest_qty": int(row.suggest_qty),
            "applied_ratio": float(ratio),
            "applied_qty": applied,
        }
        return applied

    add_col, add_btn = st.columns([3.2, 1.0])
    selected = add_col.selectbox(
        "选择 SKU 加入补货单",
        options=list(rows_by_key),
        index=None,
        format_func=lambda key: (
            f"{product_names.product_name(*key)} · {series_label(*key)} · "
            f"{rows_by_key[key].urgency} · 建议 {int(rows_by_key[key].suggest_qty)}"
        ),
        placeholder="选择一条序列",
        key="order_add_sku",
    )
    if add_btn.button(
        "加入补货单",
        key="order_add_btn",
        use_container_width=True,
        disabled=not can_write or selected is None,
    ) and selected is not None:
        applied = add_to_cart(selected, rows_by_key[selected])
        st.session_state["order_cart"] = cart
        st.toast(f"已加入 {series_label(*selected)}：{applied} 件")
        st.rerun()

    bulk_pick_col, bulk_add_col = st.columns([3.2, 1.0])
    bulk_pick = bulk_pick_col.multiselect(
        "批量选择 SKU（可多选）",
        options=list(rows_by_key),
        format_func=lambda key: (
            f"{product_names.product_name(*key)} · {series_label(*key)} · "
            f"{rows_by_key[key].urgency}"
        ),
        placeholder="选择多条序列，再点批量加入",
        key="order_bulk_pick",
    )
    if bulk_add_col.button(
        f"批量加入所选（{len(bulk_pick)} 条）",
        key="order_bulk_add",
        use_container_width=True,
        disabled=not can_write or not bulk_pick,
    ):
        for key in bulk_pick:
            add_to_cart(key, rows_by_key[key])
        st.session_state["order_cart"] = cart
        st.toast(f"已加入 {len(bulk_pick)} 条序列")
        st.rerun()

    bulk_col, clear_col = st.columns([3.2, 1.0])
    if bulk_col.button(
        f"整单加入当前筛选（{len(rows_by_key)} 条）",
        key="order_add_all",
        use_container_width=True,
        disabled=not can_write,
    ):
        for key, row in rows_by_key.items():
            add_to_cart(key, row)
        st.session_state["order_cart"] = cart
        st.toast(f"已加入 {len(rows_by_key)} 条序列")
        st.rerun()
    if clear_col.button(
        "清空补货单",
        key="order_clear",
        use_container_width=True,
        disabled=not cart,
    ):
        st.session_state["order_cart"] = {}
        st.rerun()

    cart = st.session_state.get("order_cart", {})
    if cart:
        cart_rows = [
            {
                "商品（门店-编号）": product_names.sku_display(
                    int(item["store_id"]), int(item["product_id"])
                ),
                "建议量": int(item["suggest_qty"]),
                "下单量": int(item["applied_qty"]),
                "比例": f"{float(item['applied_ratio']):.2f}",
            }
            for item in cart.values()
        ]
        st.dataframe(
            pd.DataFrame(cart_rows), hide_index=True, use_container_width=True
        )
        cart_keys = list(cart)
        remove_sig = abs(hash(tuple(cart_keys))) % 1_000_000
        remove_col, confirm_col = st.columns([1.0, 2.6])
        remove_key = remove_col.selectbox(
            "移除一项",
            options=cart_keys,
            index=None,
            format_func=lambda name: (
                f"{product_names.product_name(*[int(part) for part in name.split('-')])} · "
                f"{series_label(*[int(part) for part in name.split('-')])}"
            ),
            placeholder="选择要移除的商品",
            key=f"order_remove_{remove_sig}",
        )
        if remove_col.button(
            "移除",
            key="order_remove_btn",
            use_container_width=True,
            disabled=not can_write or remove_key is None,
        ) and remove_key is not None:
            cart.pop(remove_key, None)
            st.session_state["order_cart"] = cart
            st.rerun()

        total_qty = int(sum(int(item["applied_qty"]) for item in cart.values()))
        if confirm_col.button(
            f"确认补货单（{len(cart)} 条 / {total_qty} 件）",
            type="primary",
            key="order_confirm",
            use_container_width=True,
            disabled=not can_write,
        ):
            batch_id = _new_batch_id()
            created_at = pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S")
            rows = []
            for item in cart.values():
                rows.append(
                    {
                        "batch_id": batch_id,
                        "created_at": created_at,
                        "snapshot_date": snapshot,
                        "store_id": int(item["store_id"]),
                        "product_id": int(item["product_id"]),
                        "shelf_life_days": int(item["shelf_life_days"]),
                        "urgency": str(item["urgency"]),
                        "on_hand": float(item["on_hand"]),
                        "rop_p90": float(item["rop_p90"]),
                        "suggest_qty": int(item["suggest_qty"]),
                        "applied_ratio": float(item["applied_ratio"]),
                        "applied_qty": int(item["applied_qty"]),
                        "status": "已确认",
                        "operator": operator,
                        "note": "逐 SKU 补货单",
                    }
                )
            existing = _load_decision_items()
            combined = pd.concat(
                [existing, pd.DataFrame(rows)], ignore_index=True
            )
            _save_decision_items(combined)
            _append_batch_event(
                batch_id,
                "confirm",
                "已确认",
                operator,
                len(rows),
                total_qty,
                note="创建补货单",
            )
            st.session_state["order_cart"] = {}
            st.session_state["last_order_batch"] = batch_id
            st.success(f"已创建批次 {batch_id}：{len(rows)} 条 / {total_qty} 件")
            st.rerun()
    else:
        st.caption("补货单为空。")

    items = _load_decision_items()
    latest = _latest_batch(items)
    if latest is not None:
        batch_id, sub, status = latest
        total = int(
            pd.to_numeric(sub["applied_qty"], errors="coerce").fillna(0).sum()
        )
        st.markdown(
            f"**最近批次** `{batch_id}` · 状态 **{status}** · "
            f"{len(sub)} 条 / {total:,} 件"
        )
        action_cols = st.columns([1.1, 1.1, 1.1, 1.5])
        if can_approve:
            if status == "已确认":
                if action_cols[0].button(
                    "审批通过",
                    key=f"order_approve_{batch_id}",
                    use_container_width=True,
                ):
                    _update_batch_status(
                        batch_id, "已审批", operator, "approve"
                    )
                    st.rerun()
            elif status == "已审批":
                if action_cols[0].button(
                    "标记已下单",
                    key=f"order_place_{batch_id}",
                    use_container_width=True,
                ):
                    _update_batch_status(
                        batch_id, "已下单", operator, "order"
                    )
                    st.rerun()
            elif status == "已下单":
                if action_cols[0].button(
                    "标记已到货",
                    key=f"order_receive_{batch_id}",
                    use_container_width=True,
                ):
                    _update_batch_status(
                        batch_id, "已到货", operator, "receive"
                    )
                    st.rerun()
            if status != "已撤销":
                if action_cols[1].button(
                    "撤销该批次",
                    key=f"order_undo_{batch_id}",
                    use_container_width=True,
                ):
                    _update_batch_status(
                        batch_id, "已撤销", operator, "undo"
                    )
                    st.rerun()
        elif status != "已撤销":
            action_cols[0].caption("审批 / 下单 / 到货 / 撤销仅管理员可用。")

        export = pd.DataFrame(
            {
                "批次": sub["batch_id"],
                "门店": sub["store_id"],
                "SKU": sub["product_id"],
                "保质期(天)": sub["shelf_life_days"],
                "紧急度": sub["urgency"],
                "建议量": sub["suggest_qty"],
                "下单量": sub["applied_qty"],
                "状态": sub["status"],
                "操作人": sub["operator"],
                "时间": sub["created_at"],
            }
        )
        action_cols[3].download_button(
            "导出采购单",
            data=export.to_csv(index=False).encode("utf-8-sig"),
            file_name=f"采购单_{batch_id}.csv",
            mime="text/csv",
            key="order_export",
            use_container_width=True,
        )

    events = _load_batch_events()
    if not items.empty or not events.empty:
        with st.expander("批次历史与操作记录"):
            if not items.empty:
                history = (
                    items.assign(
                        applied_qty=pd.to_numeric(
                            items["applied_qty"], errors="coerce"
                        ).fillna(0)
                    )
                    .groupby("batch_id", as_index=False)
                    .agg(
                        status=("status", "last"),
                        item_count=("product_id", "count"),
                        total_qty=("applied_qty", "sum"),
                        operator=("operator", "last"),
                        created_at=("created_at", "last"),
                    )
                    .sort_values("batch_id", ascending=False)
                    .head(6)
                )
                st.markdown("**最近批次**")
                st.dataframe(
                    history, hide_index=True, use_container_width=True
                )
            if not events.empty:
                st.markdown("**操作记录**")
                st.dataframe(
                    events.tail(12).iloc[::-1],
                    hide_index=True,
                    use_container_width=True,
                )


def _query_value(name: str, default: str = "") -> str:
    """兼容 Streamlit 新旧 query_params API，统一返回字符串。"""
    value = st.query_params.get(name, default)
    if isinstance(value, list):
        return value[0] if value else default
    return default if value is None else str(value)


def _parse_sku_param() -> tuple[int, int] | None:
    """解析 ?sku=145-802 / SKU 145-802 / 145_802 形式的深链接。"""
    raw = _query_value("sku").strip()
    if not raw:
        return None
    raw = re.sub(r"^SKU\s*", "", raw, flags=re.IGNORECASE).strip()
    match = re.match(r"^(\d+)\s*[-_/ ]\s*(\d+)$", raw)
    if not match:
        return None
    return int(match.group(1)), int(match.group(2))


def _share_view_html(
    query: str,
    hint: str = "分享链接 · 复制后在新标签页打开，即可恢复当前筛选",
) -> str:
    """分享卡片：把 query 拼成完整 URL，支持一键复制。"""
    return f"""
    <div style="font-family:'Microsoft YaHei','PingFang SC','Segoe UI',sans-serif;
                color:#0b0b0b;background:#FFFFFF;border:1px solid #DCE7F3;
                border-radius:16px;padding:12px 14px;">
      <div style="font-size:11.5px;color:#6b7f94;margin-bottom:8px;">
        {html.escape(hint)}
      </div>
      <div style="display:flex;gap:8px;align-items:center;">
        <input id="share-url" readonly
               style="flex:1;min-width:0;height:32px;padding:0 10px;
                      border:1px solid #DCE7F3;border-radius:8px;
                      background:#ffffff;color:#0b0b0b;font-size:12px;">
        <button id="share-copy" type="button"
                style="height:32px;padding:0 14px;border:0;border-radius:8px;
                       background:#1E6FB8;color:#ffffff;font-size:12px;
                       font-weight:600;cursor:pointer;">复制</button>
      </div>
      <div id="share-tip" style="font-size:11px;color:#6b7f94;margin-top:6px;">
        点击“复制”，或手动选中链接复制
      </div>
    </div>
    <script>
      (function () {{
        var qs = {json.dumps(query)};
        var base = window.parent.location.origin + window.parent.location.pathname;
        var url = base + (qs ? "?" + qs : "");
        var input = document.getElementById("share-url");
        var tip = document.getElementById("share-tip");
        input.value = url;
        document.getElementById("share-copy").addEventListener("click", function () {{
          input.focus();
          input.select();
          var ok = false;
          try {{ ok = document.execCommand("copy"); }} catch (e) {{}}
          if (navigator.clipboard) {{
            navigator.clipboard.writeText(url).then(function () {{
              tip.textContent = "已复制到剪贴板";
            }}).catch(function () {{
              tip.textContent = ok ? "已复制到剪贴板" : "请手动选中链接复制";
            }});
          }} else {{
            tip.textContent = ok ? "已复制到剪贴板" : "请手动选中链接复制";
          }}
        }});
      }})();
    </script>
    """


SAVED_VIEWS_PATH = OUTPUT_DIR / "saved_views.json"


def _read_saved_views_doc() -> dict:
    if not SAVED_VIEWS_PATH.exists():
        return {"users": {}}
    try:
        payload = json.loads(SAVED_VIEWS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"users": {}}
    if not isinstance(payload, dict):
        return {"users": {}}
    users = payload.get("users")
    if isinstance(users, dict):
        return {"users": users}
    legacy = payload.get("views")
    if isinstance(legacy, dict):
        return {"users": {}, "legacy": legacy}
    return {"users": {}}


def _load_saved_views(username: str) -> dict[str, dict]:
    """读取当前账号的个人视图；旧版全局视图首次访问时迁移到该账号。"""
    username = str(username or "").strip()
    if not username:
        return {}
    doc = _read_saved_views_doc()
    users = doc.get("users") or {}
    own = users.get(username)
    if isinstance(own, dict):
        return {
            str(name): item
            for name, item in own.items()
            if isinstance(item, dict)
        }
    legacy = doc.get("legacy")
    if isinstance(legacy, dict) and not users:
        migrated = {
            str(name): item
            for name, item in legacy.items()
            if isinstance(item, dict)
        }
        _write_saved_views(migrated, username)
        return migrated
    return {}


def _write_saved_views(views: dict[str, dict], username: str) -> None:
    username = str(username or "").strip()
    if not username:
        return
    doc = _read_saved_views_doc()
    users = dict(doc.get("users") or {})
    users[username] = dict(views)
    SAVED_VIEWS_PATH.parent.mkdir(parents=True, exist_ok=True)
    SAVED_VIEWS_PATH.write_text(
        json.dumps({"users": users}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _save_named_view(name: str, params: dict, username: str) -> str:
    if not str(username or "").strip():
        return "请先登录再保存视图。"
    clean = str(name or "").strip()[:40]
    if not clean:
        return "请先填写视图名称。"
    views = _load_saved_views(username)
    updated = clean in views
    views[clean] = dict(params)
    _write_saved_views(views, username)
    return f"{'已更新' if updated else '已保存'}视图「{clean}」。"


def _delete_named_view(name: str, username: str) -> str:
    if not str(username or "").strip():
        return "请先登录再删除视图。"
    views = _load_saved_views(username)
    if name not in views:
        return "视图不存在。"
    del views[name]
    _write_saved_views(views, username)
    return f"已删除视图「{name}」。"


def _sanitize_view_params(
    params: dict,
    store_options: list[int],
    shelf_options: list[int],
) -> dict:
    """载入前按当前数据校验，避免历史视图里的门店/档位已不存在。"""
    try:
        store = int(params.get("store", 0) or 0)
    except (TypeError, ValueError):
        store = 0
    if store not in store_options:
        store = 0

    urgency = [u for u in params.get("urgency", []) if u in URGENCY_RANK]
    shelf: list[int] = []
    for token in params.get("shelf", []) or []:
        try:
            value = int(token)
        except (TypeError, ValueError):
            continue
        if value in shelf_options and value not in shelf:
            shelf.append(value)

    return {
        "store": store,
        "urgency": urgency,
        "shelf": shelf,
        "expiring": bool(params.get("expiring", False)),
        "q": str(params.get("q", "") or ""),
    }


def _apply_saved_view(params: dict) -> None:
    """载入已保存视图：回调在脚本重跑前写入各筛选控件的状态。"""
    st.session_state["fresh_store_filter"] = int(params.get("store", 0) or 0)
    st.session_state["fresh_urgency_filter_v2"] = list(
        params.get("urgency", []) or []
    )
    st.session_state["fresh_shelf_filter_v2"] = [
        int(x) for x in (params.get("shelf", []) or [])
    ]
    st.session_state["fresh_expiring_only"] = bool(params.get("expiring", False))
    st.session_state["fresh_sku_search"] = str(params.get("q", "") or "")


def _delete_saved_view_callback(name: str) -> None:
    username = str(st.session_state.get("auth_account", "") or "")
    st.session_state["saved_view_notice"] = _delete_named_view(name, username)


def _clear_fresh_filters() -> None:
    """清空补货清单的全部筛选条件；作为按钮回调，在脚本重跑前执行。"""
    st.session_state["fresh_store_filter"] = 0
    st.session_state["fresh_urgency_filter_v2"] = []
    st.session_state["fresh_shelf_filter_v2"] = []
    st.session_state["fresh_expiring_only"] = False
    st.session_state["fresh_sku_search"] = ""


def _reset_fresh_page() -> None:
    st.session_state["fresh_page"] = 1


def _go_to_page(page: int) -> None:
    st.session_state["fresh_page"] = int(page)


def _pager_tokens(current: int, total: int, window: int = 5) -> list:
    """生成 1 … 4 5 6 … 25 这类页码，始终保留首尾页。"""
    total = max(1, int(total))
    current = max(1, min(int(current), total))
    if total <= window + 2:
        return list(range(1, total + 1))
    if current <= window - 1:
        return [*range(1, window + 1), "…", total]
    if current >= total - window + 2:
        return [1, "…", *range(total - window + 1, total + 1)]
    return [1, "…", current - 1, current, current + 1, "…", total]


def _render_pagination(
    total_rows: int, total_pages: int, current_page: int, page_size: int
) -> None:
    """表格下方的数字分页器：浅色工具条 + 原生按钮。"""
    with st.container():
        st.markdown('<span class="pager-anchor"></span>', unsafe_allow_html=True)
        _pager_controls(total_rows, total_pages, current_page, page_size)


def _pager_controls(
    total_rows: int, total_pages: int, current_page: int, page_size: int
) -> None:
    tokens = _pager_tokens(current_page, total_pages)
    # 末尾留一格，避免右下角悬浮球压住"每页条数"下拉框
    widths = [0.55] + [0.55] * len(tokens) + [0.55, 2.2, 1.15, 0.9]
    cols = st.columns(widths)
    cols[0].button(
        "‹",
        key="fresh_page_prev",
        on_click=_go_to_page,
        args=(current_page - 1,),
        disabled=current_page <= 1,
        use_container_width=True,
        help="上一页",
    )
    for offset, token in enumerate(tokens, 1):
        col = cols[offset]
        if token == "…":
            col.markdown(
                '<div style="display:flex;align-items:center;'
                "justify-content:center;height:34px;color:inherit;"
                'opacity:.5;font-size:13px;">…</div>',
                unsafe_allow_html=True,
            )
        else:
            col.button(
                str(token),
                key=f"fresh_page_btn_{token}",
                on_click=_go_to_page,
                args=(int(token),),
                type="primary" if int(token) == current_page else "secondary",
                use_container_width=True,
            )
    next_index = 1 + len(tokens)
    cols[next_index].button(
        "›",
        key="fresh_page_next",
        on_click=_go_to_page,
        args=(current_page + 1,),
        disabled=current_page >= total_pages,
        use_container_width=True,
        help="下一页",
    )
    info_col = cols[next_index + 1]
    info_col.markdown(
        '<div style="display:flex;align-items:center;height:34px;'
        "font-size:12px;color:inherit;opacity:.65;\">"
        f"第 {current_page} / {total_pages} 页 · 共 {total_rows} 条</div>",
        unsafe_allow_html=True,
    )
    size_options = [8, 16, 32]
    cols[next_index + 2].selectbox(
        "每页",
        options=size_options,
        index=size_options.index(page_size) if page_size in size_options else 0,
        format_func=lambda value: f"{value} 条/页",
        label_visibility="collapsed",
        key="fresh_page_size",
        on_change=_reset_fresh_page,
        help="每页显示条数",
    )


@st.cache_data(show_spinner=False, ttl=300)
def _cached_reports(
    display: pd.DataFrame,
    repl: pd.DataFrame,
    snapshot: str,
    values: dict,
    filter_note: str,
) -> tuple[bytes, bytes | None]:
    excel = report_export.build_excel_report(
        display, repl, snapshot, values, filter_note
    )
    try:
        pdf = report_export.build_pdf_report(
            display, repl, snapshot, values, filter_note
        )
    except Exception:
        pdf = None
    return excel, pdf


def render_fresh_replenishment(
    repl: pd.DataFrame,
    caps: dict[tuple[int, int], dict],
    cap_sweep: pd.DataFrame | None,
    config: dict,
    snapshot: str,
    values: dict,
) -> None:
    st.markdown("### 库存健康与补货清单")
    imported = st.session_state.get("imported_table")
    if imported is not None and st.session_state.get("use_imported_data"):
        _render_imported_inventory(imported)
        _render_data_import()
        return
    st.caption(
        f"共 {len(repl):,} 条序列；库存、订货与紧急度来自补货快照 {snapshot}，页面不重算。"
    )

    rep_cfg = (config or {}).get("replenishment", {}) or {}
    exp_threshold = int(rep_cfg.get("expiring_threshold_days", 1))

    f_store, f1, f2, f3, f4 = st.columns([1.15, 1.15, 1.0, 0.85, 1.35])
    store_options = sorted(
        {int(x) for x in pd.to_numeric(repl["store_id"], errors="coerce").dropna()}
    )
    # 分享链接恢复：首次进入会话时把 URL 参数写进 session_state；
    # 之后始终以 session_state 为准，避免改变控件参数导致选中状态被重置。
    try:
        shared_store = int(_query_value("store", "0") or 0)
    except ValueError:
        shared_store = 0
    if shared_store not in store_options:
        shared_store = 0
    st.session_state.setdefault("fresh_store_filter", shared_store)
    store_sel = f_store.selectbox(
        "门店",
        options=[0, *store_options],
        format_func=lambda s: "全部门店" if s == 0 else f"{s} 号店",
        help="仅筛选下方补货清单、溯源与留痕；顶部 KPI 仍为全局总览。",
        key="fresh_store_filter",
    )
    urgency_options = list(URGENCY_RANK.keys())
    shared_urgency = [
        item for item in _query_value("urgency").split(",") if item in URGENCY_RANK
    ]
    st.session_state.setdefault("fresh_urgency_filter_v2", shared_urgency)
    urgency_sel = f1.multiselect(
        "紧急度（多选）",
        options=urgency_options,
        placeholder="全部紧急度",
        help="不选择任何标签时展示全部数据",
        key="fresh_urgency_filter_v2",
    )
    shelf_options = fresh_shelf_options(repl, config)
    shared_shelf: list[int] = []
    for token in _query_value("shelf").split(","):
        try:
            value = int(token)
        except ValueError:
            continue
        if value in shelf_options and value not in shared_shelf:
            shared_shelf.append(value)
    st.session_state.setdefault("fresh_shelf_filter_v2", shared_shelf)
    shelf_sel = f2.multiselect(
        "保质期档",
        options=shelf_options,
        placeholder="全部保质期",
        help="不选择任何标签时展示全部数据",
        format_func=lambda d: f"{int(d)} 天",
        key="fresh_shelf_filter_v2",
    )
    st.session_state.setdefault(
        "fresh_expiring_only",
        _query_value("expiring").lower() in ("1", "true", "yes"),
    )
    expiring_only = f3.checkbox("临期仅看", key="fresh_expiring_only")
    st.session_state.setdefault("fresh_sku_search", _query_value("q"))
    search_raw = f4.text_input(
        "按 SKU 搜索",
        placeholder="例如 145-802",
        key="fresh_sku_search",
    )

    share_params: dict[str, str] = {}
    if store_sel:
        share_params["store"] = str(store_sel)
    if urgency_sel:
        share_params["urgency"] = ",".join(urgency_sel)
    if shelf_sel:
        share_params["shelf"] = ",".join(str(int(x)) for x in shelf_sel)
    if expiring_only:
        share_params["expiring"] = "1"
    if search_raw.strip():
        share_params["q"] = search_raw.strip()
    share_query = urlencode(share_params)
    filter_parts = []
    if store_sel:
        filter_parts.append(f"门店 {store_sel} 号店")
    if urgency_sel:
        filter_parts.append("紧急度 " + "/".join(urgency_sel))
    if shelf_sel:
        filter_parts.append(
            "保质期 " + "/".join(f"{int(x)} 天" for x in shelf_sel)
        )
    if expiring_only:
        filter_parts.append("仅看临期")
    if search_raw.strip():
        filter_parts.append(f"搜索 {search_raw.strip()}")
    filter_note = (
        " · ".join(filter_parts) if filter_parts else "全部门店 / 全部紧急度"
    )
    view_params = {
        "store": int(store_sel),
        "urgency": list(urgency_sel),
        "shelf": [int(x) for x in shelf_sel],
        "expiring": bool(expiring_only),
        "q": search_raw.strip(),
    }

    view = repl.copy()
    view["_label"] = [
        series_label(s, p) for s, p in zip(view["store_id"], view["product_id"])
    ]
    if store_sel:
        view = view[pd.to_numeric(view["store_id"], errors="coerce") == store_sel]
    # 空选择 = 不过滤，保留全部；只有用户主动选的标签才参与过滤。
    if urgency_sel:
        view = view[view["urgency"].isin(urgency_sel)]
    if shelf_sel:
        view = view[view["shelf_life_days"].isin(shelf_sel)]
    if expiring_only:
        view = view[pd.to_numeric(view["expiring_qty"], errors="coerce").fillna(0) > 0]
    query = _normalize_search(search_raw)
    if query:
        keys = view["_label"].str.replace("SKU ", "", regex=False).str.replace(
            "-", "", regex=False
        )
        names = view.assign(
            _name=[
                product_names.product_name(s, p)
                for s, p in zip(view["store_id"], view["product_id"])
            ]
        )["_name"]
        view = view[
            keys.str.contains(query, case=False, regex=False)
            | names.str.contains(query, case=False, regex=False)
        ]

    view = view.assign(
        _rank=view["urgency"].map(lambda x: URGENCY_RANK.get(str(x), 9)),
        _expiring=pd.to_numeric(view["expiring_qty"], errors="coerce").fillna(0.0),
    ).sort_values(
        ["_rank", "_expiring", "suggest_qty"], ascending=[True, False, False]
    )

    if view.empty:
        has_normal = bool((repl["urgency"].astype(str) == "正常").any())
        if "正常" in urgency_sel and not has_normal:
            st.info(
                "当前批次没有正常档序列（效期约束把目标水位收紧后，在库普遍低于目标）。"
                "可以改选紧急 / 建议补货 / 计划补货查看需要订货的序列。"
            )
        else:
            st.info(
                "当前筛选条件下没有匹配的序列。可以放宽紧急度或保质期筛选，"
                "或清空搜索词后重试。"
            )
        st.button(
            "清除全部筛选",
            key="fresh_clear_filters_empty",
            on_click=_clear_fresh_filters,
        )
        _render_data_import()
        _render_cap_sweep(cap_sweep, config, repl)
        return

    records = []
    for row in view.itertuples():
        key = (int(row.store_id), int(row.product_id))
        cap_info = caps.get(key)
        target_eff = (
            float(cap_info["target_eff"]) if cap_info else float(row.target_level)
        )
        expiring = float(row.expiring_qty)
        records.append(
            {
                "商品（门店-编号）": product_names.sku_display(
                    row.store_id, row.product_id
                ),
                f"现有库存（{FRESH_UNIT}）": round(float(row.on_hand), 2),
                f"ROP_P90（{FRESH_UNIT}）": round(float(row.rop_p90), 2),
                f"效期修正目标（{FRESH_UNIT}）": round(target_eff, 2),
                f"建议订货（{FRESH_UNIT}）": int(row.suggest_qty),
                "紧急度": str(row.urgency),
                f"临期数量（{FRESH_UNIT}）": round(expiring, 2),
                "临期": "是" if expiring > 0 else "",
            }
        )

    display = pd.DataFrame(records)

    page_size = int(st.session_state.get("fresh_page_size", 8) or 8)
    if page_size not in (8, 16, 32):
        page_size = 8
    total_rows = len(display)
    total_pages = max(1, math.ceil(total_rows / page_size))
    signature = (
        int(store_sel),
        tuple(urgency_sel),
        tuple(int(x) for x in shelf_sel),
        bool(expiring_only),
        search_raw.strip(),
    )
    if st.session_state.get("fresh_page_signature") != signature:
        st.session_state["fresh_page"] = 1
        st.session_state["fresh_page_signature"] = signature
    current_page = min(
        max(1, int(st.session_state.get("fresh_page", 1) or 1)),
        total_pages,
    )
    st.session_state["fresh_page"] = current_page
    start = (current_page - 1) * page_size
    page_display = display.iloc[start : start + page_size]

    if len(page_display) <= 8:
        table_height = min(560, 50 + 62 * len(page_display))
    else:
        table_height = 600
    components.html(
        _fresh_inventory_table_html(page_display), height=table_height
    )
    if total_pages > 1:
        _render_pagination(total_rows, total_pages, current_page, page_size)
    caption_col, export_col = st.columns([4.6, 1.0])
    with caption_col:
        st.caption(
            f"当前筛选 {len(view):,} / {len(repl):,} 条；"
            f"第 {current_page} / {total_pages} 页（本页 {len(page_display)} 条）。"
            f"默认按紧急度、临期数量降序。"
            f"库存与订货单位：{FRESH_UNIT}；表头排序作用于当前页，翻页用数字按钮。"
            f"临期 = 剩余保质期 ≤ {exp_threshold} 天；相关行浅橙高亮，"
            f"橙色“临期”标签表示需关注，绿色 ✓ 表示未临期。"
        )
    with export_col:
        st.download_button(
            "导出 CSV",
            data=display.to_csv(index=False).encode("utf-8-sig"),
            file_name=f"补货清单_{snapshot}.csv",
            mime="text/csv",
            key="fresh_export_view",
            use_container_width=True,
        )
    with st.expander("分享与保存视图"):
        components.html(_share_view_html(share_query), height=100)
        st.divider()
        username = str(st.session_state.get("auth_account", "") or "")
        can_save = _can_save_views()
        can_delete = can_save  # 个人视图：计划员/管理员可管理自己的视图
        if not st.session_state.get("auth_logged_in"):
            st.caption("保存 / 删除视图需要登录（计划员或管理员）；载入仅限当前账号的个人视图。")
        elif not can_save:
            st.caption("当前角色为查看者：保存 / 删除需要计划员或管理员权限。")
        if username:
            st.caption(f"当前账号：{username} · 仅显示你的个人视图")
        with st.form("save_view_form", clear_on_submit=True):
            new_name = st.text_input(
                "视图名称",
                placeholder="例如：145 号店紧急清单",
                key="saved_view_name_input",
            )
            saved = st.form_submit_button(
                "保存当前视图",
                disabled=not can_save,
                use_container_width=True,
            )
        if saved:
            st.session_state["saved_view_notice"] = _save_named_view(
                new_name, view_params, username
            )

        notice = st.session_state.pop("saved_view_notice", None)
        if notice:
            st.success(notice)

        saved_views = _load_saved_views(username)
        if saved_views:
            names = list(saved_views)
            view_sig = abs(hash(tuple(names))) % 1_000_000
            picked = st.selectbox(
                "已保存视图",
                options=names,
                key=f"saved_view_pick_{view_sig}",
            )
            sanitized = _sanitize_view_params(
                saved_views[picked], store_options, shelf_options
            )
            load_col, delete_col, _ = st.columns([1, 1, 3])
            load_col.button(
                "载入",
                key=f"saved_view_load_{view_sig}",
                on_click=_apply_saved_view,
                args=(sanitized,),
                use_container_width=True,
            )
            delete_col.button(
                "删除",
                key=f"saved_view_delete_{view_sig}",
                on_click=_delete_saved_view_callback,
                args=(picked,),
                disabled=not can_delete,
                use_container_width=True,
            )
        else:
            st.caption("还没有保存的视图；保存后会写在本机 output/saved_views.json。")

    # 日报按需生成：首屏和普通筛选不再跑 Excel/PDF，避免白屏卡顿。
    report_sig = (snapshot, filter_note, len(display), len(repl))
    if st.session_state.get("fresh_report_sig") != report_sig:
        st.session_state["fresh_report_sig"] = report_sig
        st.session_state["fresh_report_ready"] = False

    report_col, report_hint = st.columns([1.2, 3.0])
    with report_col:
        generate_report = st.button(
            "生成 Excel / PDF 日报",
            key="fresh_generate_reports",
            use_container_width=True,
            help="按当前筛选生成带格式日报，首次约 1–3 秒",
        )
    with report_hint:
        st.caption("日报只包含当前筛选结果；生成后可直接下载 Excel/PDF。")
    if generate_report:
        st.session_state["fresh_report_ready"] = True

    if st.session_state.get("fresh_report_ready"):
        with st.spinner("正在生成日报..."):
            excel_bytes, pdf_bytes = _cached_reports(
                display, repl, snapshot, values, filter_note
            )
        report_left, report_right = st.columns(2)
        with report_left:
            st.download_button(
                "导出 Excel 日报",
                data=excel_bytes,
                file_name=f"补货日报_{snapshot}.xlsx",
                mime=(
                    "application/vnd.openxmlformats-officedocument."
                    "spreadsheetml.sheet"
                ),
                key="report_excel",
                use_container_width=True,
            )
        with report_right:
            if pdf_bytes:
                st.download_button(
                    "导出 PDF 日报",
                    data=pdf_bytes,
                    file_name=f"补货日报_{snapshot}.pdf",
                    mime="application/pdf",
                    key="report_pdf",
                    use_container_width=True,
                )
            else:
                st.caption("PDF 生成暂不可用")
        st.caption("筛选条件变化后需要重新生成日报。")

    render_trace_section(repl, caps, config)
    render_order_panel(view, snapshot)
    _render_data_import()
    _render_cap_sweep(cap_sweep, config, repl)


def _render_cap_sweep(
    cap_sweep: pd.DataFrame | None, config: dict, repl: pd.DataFrame
) -> None:
    if cap_sweep is None or cap_sweep.empty:
        return
    shelf_options = fresh_shelf_options(repl, config)
    if not shelf_options:
        return
    base_shelf = int(
        ((config or {}).get("shelf_life", {}) or {}).get("base_days", shelf_options[0])
    )
    sub = cap_sweep[
        (cap_sweep["policy"].astype(str) == "C")
        & (cap_sweep["shelf_life_days"] == base_shelf)
    ].sort_values("multiplier")
    if sub.empty:
        return
    with st.expander(f"效期倍数扫描（策略 C · {base_shelf} 天保质期）"):
        st.caption(
            "倍数越小越省货、报损越低，但缺货更多；默认倍数由扫描规则自动选出，"
            "正式参数表到位后只需改 config 重跑。"
        )
        display = pd.DataFrame(
            {
                "效期倍数": sub["multiplier"].map(lambda x: f"{float(x):.2f}"),
                "满足率": sub["fill_rate"].map(lambda x: f"{float(x):.2%}"),
                "报损率": sub["spoilage_rate"].map(lambda x: f"{float(x):.2%}"),
                f"日均在库（{FRESH_UNIT}）": sub["avg_on_hand"].map(lambda x: f"{float(x):,.2f}"),
                "总成本": sub["total_cost"].map(lambda x: f"{float(x):,.2f}"),
            }
        )
        st.dataframe(display, hide_index=True, use_container_width=True)


# ---------------------------------------------------------------------------
# 生鲜版：未来 7 天日销预测
# ---------------------------------------------------------------------------
def _svg_fresh_forecast(group: pd.DataFrame, shelf_life: int) -> str:
    """未来 N 天 P50 / P90 折线 + 实测点。"""
    width, left, right, top, bottom = 720, 52, 24, 48, 36
    plot_w = width - left - right
    panel_h = 300
    plot_h = panel_h - top - bottom
    group = group.sort_values("dt")
    dates = pd.to_datetime(group["dt"]).dt.strftime("%m-%d").tolist()
    p50 = group["y_pred_p50"].astype(float).tolist()
    p90 = group["y_pred_p90"].astype(float).tolist()
    actual = group["y_actual"].astype(float).tolist()
    n = len(dates)
    y_max = _nice_max(p50 + p90 + actual)

    def sx(i: int) -> float:
        return left + plot_w * i / max(n - 1, 1)

    def sy(value: float) -> float:
        return top + plot_h * (1 - value / y_max)

    parts = ["<g>"]
    parts.append(
        f'<text x="16" y="20" font-size="12" font-weight="600" fill="{INK}">'
        f"未来 {n} 天日销预测（{FRESH_UNIT}）· 保质期 {int(shelf_life)} 天</text>"
    )
    legend = [("P50 预测", BLUE), ("P90 预测", BLUE_LIGHT), ("实测", INK)]
    lx = left + plot_w - 250
    for label, color in legend:
        parts.append(
            f'<line x1="{lx}" y1="16" x2="{lx + 18}" y2="16" stroke="{color}" '
            f'stroke-width="2"/>'
        )
        if label == "实测":
            parts.append(
                f'<circle cx="{lx + 9}" cy="16" r="3.5" fill="{color}"/>'
            )
        parts.append(
            f'<text x="{lx + 24}" y="20" font-size="10" fill="{SECONDARY}">{label}</text>'
        )
        lx += 82

    for k in range(5):
        value = y_max * k / 4
        y = sy(value)
        parts.append(
            f'<line x1="{left}" y1="{y:.1f}" x2="{left + plot_w}" '
            f'y2="{y:.1f}" stroke="{GRID}" stroke-width="1"/>'
        )
        parts.append(
            f'<text x="{left - 8}" y="{y + 3:.1f}" text-anchor="end" '
            f'font-size="10" fill="{MUTED}">{value:,.0f}</text>'
        )
    axis_y = top + plot_h
    for i, label in enumerate(dates):
        parts.append(
            f'<text x="{sx(i):.1f}" y="{axis_y + 18}" text-anchor="middle" '
            f'font-size="10" fill="{MUTED}">{label}</text>'
        )
    parts.append(
        f'<line x1="{left}" y1="{axis_y}" x2="{left + plot_w}" '
        f'y2="{axis_y}" stroke="{AXIS}" stroke-width="1"/>'
    )

    for values, color, width_ in ((p90, BLUE_LIGHT, 2.0), (p50, BLUE, 2.4)):
        points = " ".join(f"{sx(i):.1f},{sy(v):.1f}" for i, v in enumerate(values))
        parts.append(
            f'<polyline points="{points}" fill="none" stroke="{color}" '
            f'stroke-width="{width_}" stroke-linecap="round" stroke-linejoin="round"/>'
        )
    for i, (a, p5, p9) in enumerate(zip(actual, p50, p90)):
        parts.append(
            f'<circle cx="{sx(i):.1f}" cy="{sy(a):.1f}" r="4" fill="{INK}" '
            f'stroke="#f9f9f7" stroke-width="1.5"><title>'
            f"{dates[i]}：实测 {a:,.2f} {FRESH_UNIT} / P50 {p5:,.2f} / P90 {p9:,.2f}"
            "</title></circle>"
        )
    parts.append("</g>")
    return (
        f'<svg viewBox="0 0 {width} {panel_h}" xmlns="http://www.w3.org/2000/svg" '
        f'role="img" aria-label="7 天日销预测图" '
        f'style="width:100%;height:auto;display:block;">'
        + "".join(parts)
        + "</svg>"
    )


def build_fresh_advice(
    row: pd.Series,
    cap_info: dict | None,
    shelf_life: int,
    urgent_ratio: float,
) -> str:
    qty = int(row["suggest_qty"])
    urgency = str(row["urgency"])
    on_hand = float(row["on_hand"])
    rop = float(row["rop_p90"])
    target = float(row["target_level"])
    expiring = float(row["expiring_qty"])
    cap_qty = float(cap_info["cap"]) if cap_info else target

    if qty <= 0:
        text = (
            f"今天不用订货：现有库存 {on_hand:.2f} {FRESH_UNIT} 已达到目标水位 "
            f"{target:.2f} {FRESH_UNIT}。"
        )
    elif urgency == "紧急":
        text = (
            f"建议今天订 {qty} {FRESH_UNIT}：现有库存 {on_hand:.2f} {FRESH_UNIT} 低于紧急线"
            f"（ROP_P90 {rop:.2f} {FRESH_UNIT} 的 {urgent_ratio:.0%}），"
            f"效期上限 {cap_qty:.2f} {FRESH_UNIT}，订 {qty} {FRESH_UNIT} 不超过效期约束。"
        )
    elif urgency == "建议补货":
        text = (
            f"建议今天订 {qty} {FRESH_UNIT}：现有库存 {on_hand:.2f} {FRESH_UNIT} 低于 ROP_P90 "
            f"{rop:.2f} {FRESH_UNIT}，且效期上限 {cap_qty:.2f} {FRESH_UNIT}，"
            f"订 {qty} {FRESH_UNIT} 不超过效期约束。"
        )
    else:
        text = (
            f"建议今天订 {qty} {FRESH_UNIT}：低于目标水位，且效期上限 "
            f"{cap_qty:.2f} {FRESH_UNIT}，订 {qty} {FRESH_UNIT} 可在 "
            f"{int(shelf_life)} 天内售完。"
        )
    if expiring > 0:
        text += f" 另有 {expiring:.2f} {FRESH_UNIT} 临期，建议优先促销。"
    return text


def render_fresh_detail(
    repl: pd.DataFrame,
    forecast: pd.DataFrame | None,
    summary: pd.DataFrame | None,
    config: dict,
    caps: dict[tuple[int, int], dict],
    snapshot: str,
) -> None:
    st.markdown("### 未来 7 天日销预测")
    st.caption("选择一条门店 × SKU 序列，查看未来 7 天日销预测与补货详情。")
    ranked = repl.assign(
        _rank=repl["urgency"].map(lambda x: URGENCY_RANK.get(str(x), 9)),
        _expiring=pd.to_numeric(repl["expiring_qty"], errors="coerce").fillna(0.0),
    ).sort_values(["_rank", "_expiring"], ascending=[True, False])
    keys = [
        (int(s), int(p))
        for s, p in zip(ranked["store_id"], ranked["product_id"])
    ]
    selected = st.selectbox(
        "选择 SKU（门店-商品）",
        options=keys,
        index=0,
        format_func=lambda key: (
            f"{product_names.product_name(key[0], key[1])} · "
            f"{series_label(key[0], key[1])}"
        ),
        key="fresh_detail_series",
    )

    row = repl[
        (repl["store_id"].astype(int) == selected[0])
        & (repl["product_id"].astype(int) == selected[1])
    ].iloc[0]
    key = selected
    cap_info = caps.get(key)
    target_eff = (
        float(cap_info["target_eff"]) if cap_info else float(row["target_level"])
    )
    shelf_life = int(row["shelf_life_days"])
    rep_cfg = (config or {}).get("replenishment", {}) or {}
    multiplier = float(rep_cfg.get("expiry_cap_multiplier", 1.0))
    urgent_ratio = float(
        ((config or {}).get("urgency", {}) or {}).get("urgent_rop_ratio", 0.5)
    )

    group = None
    if forecast is not None and not forecast.empty:
        group = forecast[
            (forecast["store_id"].astype(int) == selected[0])
            & (forecast["product_id"].astype(int) == selected[1])
        ].copy()
        if not group.empty:
            group = _as_date(group, "dt").sort_values("dt")

    left, right = st.columns([3, 2], gap="large")
    with left:
        if group is not None and not group.empty:
            svg = _svg_fresh_forecast(group, shelf_life)
            st.markdown(f'<div class="chart-svg">{svg}</div>', unsafe_allow_html=True)
            dmin = group["dt"].min().strftime("%Y-%m-%d")
            dmax = group["dt"].max().strftime("%Y-%m-%d")
            st.caption(
                f"横轴 {dmin} ~ {dmax}；蓝线为 P50 / P90 预测，黑点为当日实测销量。"
            )
        else:
            st.warning("缺少该序列的 7 天预测数据，请先运行生鲜预测脚本。")

    with right:
        on_hand = float(row["on_hand"])
        rop = float(row["rop_p90"])
        suggest = int(row["suggest_qty"])
        expiring = float(row["expiring_qty"])
        st.markdown(
            f"""
            <div class="metric-grid">
              <div class="metric-card">
                <div class="metric-label">现有库存</div>
                <div class="metric-value">{on_hand:,.2f}<span class="metric-unit">{FRESH_UNIT}</span></div>
              </div>
              <div class="metric-card">
                <div class="metric-label">ROP_P90</div>
                <div class="metric-value">{rop:,.2f}<span class="metric-unit">{FRESH_UNIT}</span></div>
              </div>
              <div class="metric-card">
                <div class="metric-label">效期修正目标</div>
                <div class="metric-value">{target_eff:,.2f}<span class="metric-unit">{FRESH_UNIT}</span></div>
              </div>
              <div class="metric-card">
                <div class="metric-label">建议订货</div>
                <div class="metric-value">{suggest:,}<span class="metric-unit">{FRESH_UNIT}</span></div>
              </div>
              <div class="metric-card">
                <div class="metric-label">临期数量</div>
                <div class="metric-value">{expiring:,.2f}<span class="metric-unit">{FRESH_UNIT}</span></div>
              </div>
              <div class="metric-card">
                <div class="metric-label">保质期档</div>
                <div class="metric-value">{shelf_life}<span class="metric-unit">天</span></div>
              </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    urgency = str(row["urgency"])
    advice = build_fresh_advice(row, cap_info, shelf_life, urgent_ratio)
    cap_display = float(cap_info["cap"]) if cap_info else float(row["target_level"])
    st.markdown(
        f"""
        <div class="reco-card">
          <div class="reco-label">今天的补货建议 · {html.escape(product_names.sku_display(*selected))}</div>
          <div class="reco-value">{int(row['suggest_qty']):,}<span class="reco-unit">{FRESH_UNIT}</span></div>
          <span class="badge {badge_class(urgency)}">{html.escape(urgency)}</span>
          <div class="reco-reason">{html.escape(advice)}</div>
          <div class="reco-meta">
            效期上限 {cap_display:,.2f} {FRESH_UNIT} · 配置倍数 {multiplier:.2f} ·
            效期修正目标 {target_eff:,.2f} {FRESH_UNIT}
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    if summary is not None and not summary.empty:
        sub = summary[
            (summary["store_id"].astype(int) == selected[0])
            & (summary["product_id"].astype(int) == selected[1])
        ]
        if not sub.empty:
            s = sub.iloc[0]
            st.caption(
                f"序列历史汇总（官方脱敏口径）：{int(s['coverage_days'])} 天累计销量 "
                f"{float(s['total_sale']):,.2f} {FRESH_UNIT}，日变异系数 {float(s['cv']):.3f}。"
            )

    if group is not None and not group.empty:
        dmin = group["dt"].min().strftime("%Y-%m-%d")
        dmax = group["dt"].max().strftime("%Y-%m-%d")
        st.caption(
            f"数据来源：补货快照 {snapshot}（库存 / 订货）；7 天预测表 {dmin} ~ {dmax}"
            f"（P50 / P90 / 实测）。效期上限 = 保质期内预计可售量 × 配置倍数 {multiplier:.2f}。"
        )
    else:
        st.caption(f"数据来源：补货快照 {snapshot}。")


# ---------------------------------------------------------------------------
# 左侧：需求预测（DataCo 保底）
# ---------------------------------------------------------------------------
def _nice_max(
    values: list[float], intervals: int = 4, headroom: float = 1.05
) -> float:
    peak = max(values) * headroom if values else 1.0
    if peak <= 0:
        return 1.0
    raw_step = peak / intervals
    magnitude = 10 ** math.floor(math.log10(raw_step))
    for factor in (1, 1.5, 2, 2.5, 5, 7.5, 10):
        step = magnitude * factor
        if step >= raw_step:
            return float(step * intervals)
    return float(magnitude * 10 * intervals)


def _svg_history(hist: pd.DataFrame, offset_y: int = 0) -> str:
    width, left, right, top, bottom = 720, 52, 24, 28, 34
    plot_w = width - left - right
    panel_h = 230
    plot_h = panel_h - top - bottom
    dates = hist["week"].dt.strftime("%Y-%m-%d").tolist()
    values = hist["demand"].astype(float).tolist()
    n = len(values)
    y_max = _nice_max(values)

    def sx(i: int) -> float:
        return left + plot_w * i / max(n - 1, 1)

    def sy(v: float) -> float:
        return offset_y + top + plot_h * (1 - v / y_max)

    parts = ["<g>"]
    parts.append(
        f'<text x="16" y="{offset_y + 18}" font-size="12" font-weight="600" '
        f'fill="{INK}">历史周需求</text>'
    )
    for k in range(5):
        value = y_max * k / 4
        y = sy(value)
        parts.append(
            f'<line x1="{left}" y1="{y:.1f}" x2="{left + plot_w}" '
            f'y2="{y:.1f}" stroke="{GRID}" stroke-width="1"/>'
        )
        parts.append(
            f'<text x="{left - 8}" y="{y + 3:.1f}" text-anchor="end" '
            f'font-size="10" fill="{MUTED}">{value:,.0f}</text>'
        )
    axis_y = offset_y + top + plot_h
    tick_idx = sorted({round(i * (n - 1) / 5) for i in range(6)})
    for i in tick_idx:
        parts.append(
            f'<text x="{sx(i):.1f}" y="{axis_y + 18}" text-anchor="middle" '
            f'font-size="10" fill="{MUTED}">{dates[i][5:]}</text>'
        )
    parts.append(
        f'<line x1="{left}" y1="{axis_y}" x2="{left + plot_w}" '
        f'y2="{axis_y}" stroke="{AXIS}" stroke-width="1"/>'
    )
    mid_y = offset_y + top + plot_h / 2
    parts.append(
        f'<text x="16" y="{mid_y:.1f}" text-anchor="middle" font-size="10" '
        f'fill="{SECONDARY}" transform="rotate(-90 16 {mid_y:.1f})">件 / 周</text>'
    )
    points = " ".join(f"{sx(i):.1f},{sy(v):.1f}" for i, v in enumerate(values))
    parts.append(
        f'<polyline points="{points}" fill="none" stroke="{BLUE}" '
        f'stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>'
    )
    for i, value in enumerate(values):
        parts.append(
            f'<circle cx="{sx(i):.1f}" cy="{sy(value):.1f}" r="6" '
            f'fill="transparent"><title>{dates[i]}：{value:,.0f} 件</title></circle>'
        )
    last_x, last_y = sx(n - 1), sy(values[-1])
    parts.append(
        f'<circle cx="{last_x:.1f}" cy="{last_y:.1f}" r="4" fill="{BLUE}" '
        f'stroke="#f9f9f7" stroke-width="2"/>'
    )
    parts.append(
        f'<text x="{last_x - 10:.1f}" y="{last_y - 12:.1f}" text-anchor="end" '
        f'font-size="11" font-weight="600" fill="{INK}">{values[-1]:,.0f}</text>'
    )
    parts.append("</g>")
    return "".join(parts)


def _rounded_bar(
    x: float, y: float, w: float, h: float, r: float, color: str, title: str
) -> str:
    r = max(0.0, min(r, h / 2, w / 2))
    base = y + h
    path = (
        f"M {x:.1f},{base:.1f} L {x:.1f},{y + r:.1f} "
        f"Q {x:.1f},{y:.1f} {x + r:.1f},{y:.1f} "
        f"L {x + w - r:.1f},{y:.1f} Q {x + w:.1f},{y:.1f} {x + w:.1f},{y + r:.1f} "
        f"L {x + w:.1f},{base:.1f} Z"
    )
    return f'<path d="{path}" fill="{color}"><title>{title}</title></path>'


def _svg_forecast(values: tuple[float, float, float], offset_y: int = 0) -> str:
    width, left, right, top, bottom = 720, 52, 24, 30, 34
    plot_w = width - left - right
    panel_h = 200
    plot_h = panel_h - top - bottom
    y_max = _nice_max(list(values))
    labels = ["P50", "P90", "P95"]
    colors = [BLUE_LIGHT, BLUE, BLUE_DARK]
    centers = [left + plot_w * (i + 0.5) / 3 for i in range(3)]

    def sy(v: float) -> float:
        return offset_y + top + plot_h * (1 - v / y_max)

    axis_y = offset_y + top + plot_h
    parts = ["<g>"]
    parts.append(
        f'<text x="16" y="{offset_y + 18}" font-size="12" font-weight="600" '
        f'fill="{INK}">未来 4 周累计需求</text>'
    )
    for k in range(5):
        value = y_max * k / 4
        y = sy(value)
        parts.append(
            f'<line x1="{left}" y1="{y:.1f}" x2="{left + plot_w}" '
            f'y2="{y:.1f}" stroke="{GRID}" stroke-width="1"/>'
        )
        parts.append(
            f'<text x="{left - 8}" y="{y + 3:.1f}" text-anchor="end" '
            f'font-size="10" fill="{MUTED}">{value:,.0f}</text>'
        )
    parts.append(
        f'<line x1="{left}" y1="{axis_y}" x2="{left + plot_w}" '
        f'y2="{axis_y}" stroke="{AXIS}" stroke-width="1"/>'
    )
    mid_y = offset_y + top + plot_h / 2
    parts.append(
        f'<text x="16" y="{mid_y:.1f}" text-anchor="middle" font-size="10" '
        f'fill="{SECONDARY}" transform="rotate(-90 16 {mid_y:.1f})">件 / 4 周</text>'
    )
    for center, label, color, value in zip(centers, labels, colors, values):
        bar_w = 24
        bar_y = sy(value)
        bar_h = axis_y - bar_y
        parts.append(
            _rounded_bar(
                center - bar_w / 2, bar_y, bar_w, bar_h, 4, color,
                f"{label}：{value:,.2f} 件",
            )
        )
        parts.append(
            f'<text x="{center:.1f}" y="{bar_y - 8:.1f}" text-anchor="middle" '
            f'font-size="11" font-weight="600" fill="{INK}">{value:,.2f}</text>'
        )
        parts.append(
            f'<text x="{center:.1f}" y="{axis_y + 18}" text-anchor="middle" '
            f'font-size="11" fill="{SECONDARY}">{label}</text>'
        )
    parts.append("</g>")
    return "".join(parts)


def _build_chart_svg(
    hist: pd.DataFrame | None, values: tuple[float, float, float] | None
) -> str:
    panels: list[str] = []
    offset = 0
    if hist is not None and not hist.empty:
        panels.append(_svg_history(hist, offset))
        offset += 230 + 26
    if values is not None:
        panels.append(_svg_forecast(values, offset))
        offset += 200
    if not panels:
        return ""
    return (
        f'<svg viewBox="0 0 720 {offset}" xmlns="http://www.w3.org/2000/svg" '
        f'role="img" aria-label="需求预测图" '
        f'style="width:100%;height:auto;display:block;">'
        + "".join(panels)
        + "</svg>"
    )


def render_forecast(
    sku: int,
    sku_names: dict[int, str],
    forecast: pd.DataFrame | None,
    states: pd.DataFrame | None,
    demand_weekly: pd.DataFrame | None,
) -> None:
    st.markdown("### 需求预测")
    name = sku_names.get(sku, "")
    st.markdown(f"**SKU {sku}** · {html.escape(name) if name else '—'}")

    forecast_row = None
    category = "—"
    if forecast is not None and not forecast.empty:
        fc = _as_date(forecast, "week")
        latest = fc["week"].max()
        rows = fc[(fc["week"] == latest) & (fc["sku_id"] == sku)]
        if not rows.empty:
            forecast_row = rows.iloc[0]
            category = str(forecast_row.get("category", "—"))
    st.caption(f"品类：{category} · 预测口径：未来 4 周累计需求")

    # 历史周需求：来自仿真状态表的策略 C 实际 demand 列
    hist = None
    if states is not None and not states.empty:
        sub = states[(states["strategy"] == "C") & (states["sku_id"] == sku)]
        if not sub.empty:
            hist = _as_date(sub[["week", "demand"]], "week").sort_values("week")

    values = None
    if forecast_row is not None:
        values = (
            float(forecast_row["p50"]),
            float(forecast_row["p90"]),
            float(forecast_row["p95"]),
        )

    if hist is None or hist.empty:
        st.warning("数据文件缺失，请先运行对应脚本（inventory_policy_simulation_states.csv）")
    if values is None:
        st.warning("数据文件缺失，请先运行对应脚本（forecast_cumulative_predictions.csv）")

    svg = _build_chart_svg(
        hist if hist is not None and not hist.empty else None, values
    )
    if svg:
        st.markdown(f'<div class="chart-svg">{svg}</div>', unsafe_allow_html=True)

    total_latest = None
    if demand_weekly is not None and not demand_weekly.empty:
        dw = _as_date(demand_weekly, "period").sort_values("period")
        if not dw.empty:
            total_latest = float(dw["demand_qty"].iloc[-1])
    extra = f"全量周需求（所有 SKU）最新 {total_latest:,.0f} 件。" if total_latest else ""
    st.caption(
        "未来 4 周累计需求，P90 = 90% 服务水平。"
        "历史为 2017-01 ~ 2017-09 的逐周实际需求。"
        + extra
    )
    st.caption(
        "数据源：forecast_cumulative_predictions.csv、inventory_policy_simulation_states.csv、demand_weekly.csv。"
    )


# ---------------------------------------------------------------------------
# 右侧：库存状态卡 + 补货建议 + 全量补货表（DataCo 保底）
# ---------------------------------------------------------------------------
def render_inventory(
    sku: int,
    repl: pd.DataFrame | None,
    sim: pd.DataFrame | None,
) -> None:
    st.markdown("### 库存状态与补货建议")
    if repl is None or repl.empty:
        st.warning("数据文件缺失，请先运行对应脚本（inventory_replenishment.csv）")
        return

    rows = repl[repl["sku_id"] == sku]
    if rows.empty:
        st.warning(f"SKU {sku} 不在补货表中，无法显示库存状态。")
        return
    row = rows.iloc[0]

    urgency = str(row["urgency"])
    available = float(row["available_inventory"])
    rop = float(row["reorder_point"])
    safety = float(row["safety_stock"])
    target = float(row["target_level"])
    order_qty = int(row["recommended_order_qty"])
    gap = float(row["gap"])
    drift = str(row["drift_status"])

    st.markdown(
        f"""
        <div class="metric-grid">
          <div class="metric-card">
            <div class="metric-label">可用库存</div>
            <div class="metric-value">{available:,.2f}<span class="metric-unit">件</span></div>
          </div>
          <div class="metric-card">
            <div class="metric-label">ROP（再订货点）</div>
            <div class="metric-value">{rop:,.2f}<span class="metric-unit">件</span></div>
          </div>
          <div class="metric-card">
            <div class="metric-label">安全库存 SS</div>
            <div class="metric-value">{safety:,.2f}<span class="metric-unit">件</span></div>
          </div>
          <div class="metric-card">
            <div class="metric-label">目标水位</div>
            <div class="metric-value">{target:,.2f}<span class="metric-unit">件</span></div>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.caption(
        f"现有 {float(row['on_hand']):,.2f} 件 + 在途 {float(row['in_transit']):,.2f} 件"
        f" = 可用 {available:,.2f} 件。"
    )

    reason = html.escape(str(row["reason"]))
    st.markdown(
        f"""
        <div class="reco-card">
          <div class="reco-label">建议订货量</div>
          <div class="reco-value">{order_qty:,}<span class="reco-unit">件</span></div>
          <span class="badge {badge_class(urgency)}">{html.escape(urgency)}</span>
          <div class="reco-reason">{reason}</div>
          <div class="reco-meta">缺口 {gap:,.2f} 件 · 漂移状态 {html.escape(drift)}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    st.markdown("**全量补货表**")
    st.caption("按紧急度排序；颜色与 urgency 字段一致。")
    table = repl.copy()
    table["_rank"] = table["urgency"].map(lambda x: URGENCY_RANK.get(str(x), 9))
    table = table.sort_values(["_rank", "recommended_order_qty"], ascending=[True, False])

    body_rows = []
    for _, r in table.iterrows():
        u = str(r["urgency"])
        body_rows.append(
            "<tr>"
            f"<td>{int(r['sku_id'])}</td>"
            f"<td>{float(r['available_inventory']):,.2f}</td>"
            f"<td>{float(r['reorder_point']):,.2f}</td>"
            f"<td>{float(r['target_level']):,.2f}</td>"
            f"<td>{int(r['recommended_order_qty'])}</td>"
            f'<td><span class="badge {badge_class(u)}">{html.escape(u)}</span></td>'
            "</tr>"
        )

    st.markdown(
        """
        <table class="repl-table">
          <thead>
            <tr>
              <th>SKU</th><th>可用库存</th><th>ROP</th>
              <th>目标水位</th><th>建议订货</th><th>紧急度</th>
            </tr>
          </thead>
          <tbody>
        """
        + "".join(body_rows)
        + """
          </tbody>
        </table>
        """,
        unsafe_allow_html=True,
    )

    if sim is not None and not sim.empty and "strategy" in sim.columns:
        c_rows = sim[sim["strategy"] == "C"]
        if not c_rows.empty:
            c = c_rows.iloc[0]
            st.caption(
                f"策略 C 回测（2017-01 ~ 2017-09）：满足率 {float(c['fill_rate']):.2%}"
                f" · 平均库存 {float(c['avg_inventory']):,.1f} 件"
                f" · 年化周转 {float(c['turnover_annualized']):.2f} 次/年。"
            )
    st.caption("数据源：inventory_replenishment.csv、inventory_policy_simulation.csv；页面不重算指标。")


# ---------------------------------------------------------------------------
# 底部：Agent 对话（两套数据源共用框架）
# ---------------------------------------------------------------------------
def _tool_call_markdown(tools: list[dict]) -> None:
    for i, call in enumerate(tools, 1):
        st.markdown(f"**{i}. `{call.get('name', '未知工具')}`**")
        st.code(
            json.dumps(call.get("args", {}), ensure_ascii=False, indent=2),
            language="json",
        )
        if call.get("summary"):
            st.caption(str(call["summary"]))


def normalize_dialect(text: str, dialect: str) -> tuple[str, list[tuple[str, str]]]:
    """按内置词表把方言词换成普通话词；返回 (归一化文本, 实际替换列表)。"""
    if not text or dialect == "none":
        return text, []
    keys = list(DIALECT_WORDS) if dialect == "auto" else [dialect]
    mapping: dict[str, str] = {}
    for key in keys:
        mapping.update(DIALECT_WORDS.get(key, {}))

    result = text
    replacements: list[tuple[str, str]] = []
    # 长词优先，避免短词先替换后破坏长词（如“包心菜”与“包谷”）。
    for word in sorted(mapping, key=len, reverse=True):
        if word in result:
            result = result.replace(word, mapping[word])
            replacements.append((word, mapping[word]))
    return result, replacements


def _ask_agent(
    question: str,
    api_key: str,
    store,
    dataset: str,
    state_key: str,
    source_note: str | None,
    display_question: str | None = None,
    dialect_note: str | None = None,
) -> None:
    agent = get_agent()
    activate_agent_dataset(agent, dataset)
    display_key = f"chat_display_{state_key}"
    messages_key = f"agent_messages_{state_key}"
    display = st.session_state[display_key]
    messages = st.session_state[messages_key]
    display.append(
        {
            "role": "user",
            "content": display_question or question,
            "note": dialect_note,
        }
    )
    messages.append({"role": "user", "content": question})

    with st.spinner("正在查询工具并生成回答..."):
        try:
            answer, tools = agent.run_turn(messages, store, api_key, echo=False)
        except agent.AgentError as exc:
            answer = str(exc)
            tools = []
            messages.append({"role": "assistant", "content": answer})
        except Exception:
            answer = "对话调用失败，请稍后重试；图表与库存数据不受影响。"
            tools = []
            messages.append({"role": "assistant", "content": answer})

    display.append(
        {
            "role": "assistant",
            "content": answer or "（模型未返回内容）",
            "tools": tools,
            "source": source_note,
        }
    )


def _render_chat_history(display: list[dict], source_note: str | None) -> None:
    for i, message in enumerate(display):
        role = message.get("role", "assistant")
        with st.chat_message(role):
            st.markdown(message.get("content", ""))
            if role == "user" and message.get("note"):
                st.caption(message["note"])
            if role == "assistant":
                note = message.get("source") or source_note
                if note:
                    st.caption(note)
            tools = message.get("tools") or []
            if role == "assistant" and tools:
                with st.expander(
                    "查看工具调用", expanded=(i == len(display) - 1)
                ):
                    _tool_call_markdown(tools)


def render_chat(
    api_key: str,
    store,
    dataset: str,
    state_key: str,
    quick_questions: list[str],
    placeholder: str,
    source_note: str | None = None,
) -> None:
    agent = get_agent()
    activate_agent_dataset(agent, dataset)
    st.markdown("### 补货 Agent")
    st.caption("回答只基于工具返回的数据；展开每条回答可查看工具调用来源。")

    display_key = f"chat_display_{state_key}"
    messages_key = f"agent_messages_{state_key}"
    if display_key not in st.session_state:
        st.session_state[display_key] = []
    system_content = _system_prompt(dataset)
    if messages_key not in st.session_state:
        st.session_state[messages_key] = [
            {"role": "system", "content": system_content}
        ]
    else:
        existing_messages = st.session_state[messages_key]
        if existing_messages and existing_messages[0].get("role") == "system":
            existing_messages[0]["content"] = system_content

    display = st.session_state[display_key]
    # 历史消息先占位，处理完本轮问题后再填充，保证历史始终在输入框上方。
    history_slot = st.empty()

    if not api_key:
        with history_slot.container():
            _render_chat_history(display, source_note)
        st.caption("⚠️ 未配置 DEEPSEEK_API_KEY，对话功能暂不可用；图表与库存数据不受影响。")
        st.markdown('<div id="agent-chat-anchor"></div>', unsafe_allow_html=True)
        st.text_input(
            "补货 Agent 对话",
            placeholder="配置 DEEPSEEK_API_KEY 后可对话",
            disabled=True,
            key=f"agent_disabled_input_{state_key}",
            label_visibility="collapsed",
        )
        return

    quick_cols = st.columns(len(quick_questions))
    quick_question = None
    for col, label in zip(quick_cols, quick_questions):
        if col.button(
            label,
            type="secondary",
            use_container_width=True,
            key=f"quick_{state_key}_{label}",
        ):
            quick_question = label

    st.markdown('<div id="agent-chat-anchor"></div>', unsafe_allow_html=True)
    dialect_label = st.selectbox(
        "方言输入",
        options=list(DIALECT_OPTIONS),
        index=1,
        key=f"dialect_{state_key}",
        help="选方言后，提交前按内置词表换成普通话词；不额外调用模型。",
    )
    # 底部常开的普通表单：输入文字不会触发 rerun，只有点击发送才提交。
    with st.form(f"agent_form_{state_key}", clear_on_submit=True):
        prompt = st.text_input(
            "输入问题",
            placeholder=placeholder,
            label_visibility="collapsed",
            key=f"agent_prompt_{state_key}",
        )
        sent = st.form_submit_button("发送", use_container_width=True)

    question = prompt.strip() if sent and prompt.strip() else quick_question

    # 可选：?ask=... 直接带一个问题进入页面，方便演示/截图
    auto_key = f"auto_asked_{state_key}"
    if auto_key not in st.session_state:
        st.session_state[auto_key] = False
    auto_question = st.query_params.get("ask", "")
    if auto_question and not st.session_state[auto_key]:
        st.session_state[auto_key] = True
        if not question:
            question = auto_question

    if question:
        dialect = DIALECT_OPTIONS[dialect_label]
        normalized, replacements = normalize_dialect(question, dialect)
        dialect_note = None
        if replacements:
            dialect_note = "方言归一化：" + "、".join(
                f"{src}→{dst}" for src, dst in replacements
            )
        # 首屏不加载 Agent 数据仓库；只有真正提问时才加载（约 5.4MB）。
        loaded_store = store() if callable(store) else store
        _ask_agent(
            normalized,
            api_key,
            loaded_store,
            dataset,
            state_key,
            source_note,
            display_question=question,
            dialect_note=dialect_note,
        )
        display = st.session_state[display_key]

    with history_slot.container():
        _render_chat_history(display, source_note)


# ---------------------------------------------------------------------------
# 主流程：DataCo 保底
# ---------------------------------------------------------------------------
def main_dataco() -> None:
    repl = load_csv("inventory_replenishment.csv")
    forecast = load_csv("forecast_cumulative_predictions.csv")
    states = load_csv("inventory_policy_simulation_states.csv")
    demand_weekly = load_csv("demand_weekly.csv")
    sim = load_csv("inventory_policy_simulation.csv")

    render_header(snapshot_week(repl, forecast))

    options = sku_options(repl, forecast)
    if not options:
        st.error("数据文件缺失，请先运行对应脚本")
        st.divider()
        api_key = get_agent().load_api_key()
        render_chat(
            api_key,
            get_dataco_store,
            dataset="dataco",
            state_key="dataco",
            quick_questions=QUICK_QUESTIONS,
            placeholder="问补货问题，例如：SKU 365 该补多少？",
        )
        return

    names = sku_name_map(repl, forecast)
    default_index = options.index(365) if 365 in options else 0

    left, right = st.columns([3, 2], gap="large")
    with left:
        sku = st.selectbox(
            "选择 SKU",
            options=options,
            index=default_index,
            format_func=lambda x: f"{x} · {names.get(x, '')}" if names.get(x) else str(x),
        )
        render_forecast(sku, names, forecast, states, demand_weekly)
    with right:
        render_inventory(sku, repl, sim)

    st.divider()
    api_key = get_agent().load_api_key()
    render_chat(
        api_key,
        get_dataco_store,
        dataset="dataco",
        state_key="dataco",
        quick_questions=QUICK_QUESTIONS,
        placeholder="问补货问题，例如：SKU 365 该补多少？",
    )


# ---------------------------------------------------------------------------
# 主流程：生鲜前置仓（默认）
# ---------------------------------------------------------------------------
def _streamlit_secret(name: str) -> str:
    """读取 Streamlit Cloud 的 st.secrets；本地没有 secrets 时返回空串。"""
    try:
        value = st.secrets.get(name, "")
        return str(value).strip() if value is not None else ""
    except Exception:
        return ""


def _load_login_credentials() -> tuple[str, str]:
    """账号口令：环境变量 > Streamlit secrets > .env，缺省用演示口令。"""
    user = os.environ.get("APP_LOGIN_USER", "").strip() or _streamlit_secret(
        "APP_LOGIN_USER"
    )
    password = os.environ.get(
        "APP_LOGIN_PASSWORD", ""
    ).strip() or _streamlit_secret("APP_LOGIN_PASSWORD")
    env_path = BASE_DIR / ".env"
    if env_path.exists() and (not user or not password):
        try:
            for raw in env_path.read_text(encoding="utf-8-sig").splitlines():
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key == "APP_LOGIN_USER" and not user:
                    user = value
                elif key == "APP_LOGIN_PASSWORD" and not password:
                    password = value
        except OSError:
            pass
    return user or "店长", password or "zhilian2026"


def _load_users() -> dict[str, dict]:
    """读取 config/users.yaml；缺失时回退到 .env 单账号（兼容旧配置）。"""
    if USERS_CONFIG_PATH.exists():
        try:
            data = yaml.safe_load(
                USERS_CONFIG_PATH.read_text(encoding="utf-8")
            ) or {}
        except Exception:
            data = {}
        items = data.get("users") if isinstance(data, dict) else None
        users: dict[str, dict] = {}
        if isinstance(items, list):
            for item in items:
                if not isinstance(item, dict):
                    continue
                name = str(item.get("username", "")).strip()
                if not name:
                    continue
                users[name] = {
                    "role": str(item.get("role", "viewer")).strip() or "viewer",
                    "password_hash": str(item.get("password_hash", "")).strip(),
                }
        if users:
            return users
    user, password = _load_login_credentials()
    return {user: {"role": "admin", "password_hash": "", "password": password}}


def _verify_password(record: dict, password: str) -> bool:
    hashed = str(record.get("password_hash", "")).strip()
    if hashed:
        try:
            algo, iterations, salt_hex, digest_hex = hashed.split("$")
            if algo != "pbkdf2_sha256":
                return False
            salt = bytes.fromhex(salt_hex)
            expected = bytes.fromhex(digest_hex)
            actual = hashlib.pbkdf2_hmac(
                "sha256", password.encode("utf-8"), salt, int(iterations)
            )
            return hmac.compare_digest(actual, expected)
        except (ValueError, TypeError):
            return False
    plain = str(record.get("password", ""))
    return bool(plain) and hmac.compare_digest(
        password.encode("utf-8"), plain.encode("utf-8")
    )


def _authenticate(username: str, password: str) -> tuple[str, str] | None:
    record = _load_users().get(username)
    if record is None or not _verify_password(record, password):
        return None
    return username, str(record.get("role", "viewer"))


def _session_secret() -> str:
    secret = os.environ.get(
        "APP_SESSION_SECRET", ""
    ).strip() or _streamlit_secret("APP_SESSION_SECRET")
    if secret:
        return secret
    env_path = BASE_DIR / ".env"
    if env_path.exists():
        try:
            for raw in env_path.read_text(encoding="utf-8-sig").splitlines():
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                if key.strip() == "APP_SESSION_SECRET":
                    return value.strip().strip('"').strip("'")
        except OSError:
            pass
    return "zhilian-demo-session-secret"


def _issue_auth_token(username: str) -> str:
    payload = (
        base64.urlsafe_b64encode(
            json.dumps(
                {"u": username, "e": int(time.time()) + SESSION_HOURS * 3600},
                ensure_ascii=False,
            ).encode("utf-8")
        )
        .decode("ascii")
        .rstrip("=")
    )
    signature = hmac.new(
        _session_secret().encode("utf-8"),
        payload.encode("ascii"),
        hashlib.sha256,
    ).hexdigest()
    return f"{payload}.{signature}"


def _verify_auth_token(token: str) -> str | None:
    try:
        payload, signature = token.rsplit(".", 1)
        expected = hmac.new(
            _session_secret().encode("utf-8"),
            payload.encode("ascii"),
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(signature, expected):
            return None
        padded = payload + "=" * (-len(payload) % 4)
        data = json.loads(
            base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8")
        )
        if int(data.get("e", 0)) < int(time.time()):
            return None
        username = str(data.get("u", ""))
        if username not in _load_users():
            return None
        return username
    except Exception:
        return None


def _clear_auth_token() -> None:
    """退出登录：让下一次脚本开始时把地址栏 token 置空。"""
    st.session_state["auth_token_pending"] = ""


def _apply_pending_auth_token() -> None:
    """在脚本正常开始时写入 token；紧跟 st.rerun 的写入在 1.30 会丢失。"""
    pending = st.session_state.pop("auth_token_pending", None)
    if pending is None:
        return
    try:
        st.query_params["auth"] = pending
    except Exception:
        pass


def _restore_session_from_token() -> None:
    """刷新页面后用签名 token 恢复登录态；token 只含用户名与过期时间。"""
    if st.session_state.get("auth_logged_in"):
        return
    token = _query_value("auth")
    if not token:
        return
    username = _verify_auth_token(token)
    if not username:
        return
    record = _load_users().get(username, {})
    st.session_state["auth_logged_in"] = True
    st.session_state["auth_account"] = username
    st.session_state["auth_role"] = str(record.get("role", "viewer"))


def _current_role() -> str:
    return str(st.session_state.get("auth_role", "viewer") or "viewer")


def _role_label(role: str) -> str:
    return ROLE_LABELS.get(role, role)


def _can_write_decisions() -> bool:
    return _current_role() in ("admin", "planner")


def _can_save_views() -> bool:
    return _current_role() in ("admin", "planner")


def _can_delete_views() -> bool:
    return _current_role() == "admin"


def _render_auth_controls() -> None:
    """顶栏登录：固定头像按钮 + 浮层登录/账户面板。

    Streamlit 1.30 没有 st.popover，这里用固定定位的浮层复刻同样的交互。
    账号口令从 config/users.yaml 读取并校验哈希，验证通过后进入对应角色。
    """
    st.session_state.setdefault("auth_logged_in", False)
    st.session_state.setdefault("auth_panel_open", False)
    logged_in = bool(st.session_state.auth_logged_in)

    with st.container():
        clicked = st.button(
            "店" if logged_in else "👤",
            key="nav_auth_button",
            help="登录 / 账户",
        )
        st.markdown('<span class="nav-auth-anchor"></span>', unsafe_allow_html=True)
    if clicked:
        st.session_state.auth_panel_open = not bool(st.session_state.auth_panel_open)
        st.session_state.notif_open = False
        st.rerun()

    if not st.session_state.auth_panel_open:
        return

    with st.container():
        st.markdown('<span class="auth-panel-anchor"></span>', unsafe_allow_html=True)
        if logged_in:
            account = str(st.session_state.get("auth_account", "店长"))
            role = _current_role()
            role_label = _role_label(role)
            st.markdown(
                '<div class="auth-panel-title">账户</div>'
                '<div class="auth-user-row">'
                f'<span class="auth-avatar">{html.escape(account[:1])}</span>'
                "<div>"
                f'<div class="auth-user-name">{html.escape(role_label)}：'
                f"{html.escape(account)}</div>"
                f'<div class="auth-user-meta">账号：{html.escape(account)}'
                f" · 角色：{html.escape(role_label)}</div>"
                "</div></div>"
                '<div class="auth-panel-note">已通过账号口令验证</div>',
                unsafe_allow_html=True,
            )
            if st.button("退出登录", key="auth_logout", use_container_width=True):
                for key in (
                    "auth_logged_in",
                    "auth_account",
                    "auth_role",
                    "auth_panel_open",
                    "auth_username",
                    "auth_password",
                    "order_cart",
                ):
                    st.session_state.pop(key, None)
                _clear_auth_token()
                st.rerun()
        else:
            with st.form("nav_login_form", clear_on_submit=False):
                st.markdown(
                    '<div class="auth-panel-title">登录 · 智链参谋</div>',
                    unsafe_allow_html=True,
                )
                st.caption("请输入账号与口令，角色由账号决定。")
                username = st.text_input(
                    "账号", key="auth_username", placeholder="例如：店长"
                )
                password = st.text_input(
                    "口令", key="auth_password", type="password", placeholder="请输入口令"
                )
                submitted = st.form_submit_button("登录", use_container_width=True)
                if submitted:
                    auth = _authenticate(username.strip(), password)
                    if auth is not None:
                        user_name, role = auth
                        st.session_state.auth_logged_in = True
                        st.session_state.auth_account = user_name
                        st.session_state.auth_role = role
                        st.session_state.auth_panel_open = False
                        st.session_state["auth_token_pending"] = _issue_auth_token(
                            user_name
                        )
                        st.rerun()
                    else:
                        st.error("账号或口令不正确")
            if st.button("取消", key="auth_cancel", use_container_width=True):
                st.session_state.auth_panel_open = False
                st.rerun()


def render_notification_center(
    repl: pd.DataFrame | None, config: dict, snapshot: str
) -> None:
    """顶栏预警铃铛：紧急缺货 + 临期，仅在页面内提醒。"""
    st.session_state.setdefault("notif_open", False)
    if repl is None or repl.empty:
        return

    frame = repl.copy()
    urgency = frame["urgency"].astype(str)
    urgent = frame[urgency == "紧急"].copy()
    expiring = frame[
        pd.to_numeric(frame["expiring_qty"], errors="coerce").fillna(0) > 0
    ].copy()
    urgent = urgent.sort_values(
        ["suggest_qty", "on_hand"], ascending=[False, True]
    )
    expiring = expiring.sort_values("expiring_qty", ascending=False)
    count = len(urgent) + len(expiring)
    is_open = bool(st.session_state.notif_open)

    with st.container():
        st.markdown(
            '<span class="notif-bell-anchor"></span>', unsafe_allow_html=True
        )
        if st.button("🔔", key="notif_bell", help="预警中心"):
            st.session_state.notif_open = not is_open
            st.session_state.auth_panel_open = False
            st.rerun()
        if count:
            badge = str(count) if count < 100 else "99+"
            st.markdown(
                f'<span class="notif-badge">{badge}</span>',
                unsafe_allow_html=True,
            )

    if not is_open:
        return

    with st.container():
        st.markdown(
            '<span class="notif-panel-anchor"></span>', unsafe_allow_html=True
        )
        title_col, close_col = st.columns([3, 1])
        title_col.markdown("**预警中心**")
        if close_col.button("收起", key="notif_close", use_container_width=True):
            st.session_state.notif_open = False
            st.rerun()
        st.caption(f"数据截至 {snapshot} · 页面内提醒")

        st.markdown(f"**紧急缺货**（{len(urgent)} 条）")
        if urgent.empty:
            st.caption("无")
        else:
            for row in urgent.head(5).itertuples():
                st.markdown(
                    f"- **{product_names.product_name(row.store_id, row.product_id)}** · "
                    f"{series_label(int(row.store_id), int(row.product_id))} · "
                    f"在库 {float(row.on_hand):.2f} · 建议 {int(row.suggest_qty)} 件"
                )
        st.markdown(f"**临期**（{len(expiring)} 条）")
        if expiring.empty:
            st.caption("无")
        else:
            for row in expiring.head(5).itertuples():
                st.markdown(
                    f"- **{product_names.product_name(row.store_id, row.product_id)}** · "
                    f"{series_label(int(row.store_id), int(row.product_id))} · "
                    f"临期 {float(row.expiring_qty):.2f} {FRESH_UNIT}"
                )
        st.caption("仅页面内提醒；未接入企业微信/钉钉推送。")


def render_top_navbar(snapshot: str) -> None:
    """固定顶栏：品牌、页内快捷锚点与模拟登录。

    position: fixed 让它不随内容滚动；跳转目标已设置 scroll-margin-top，
    因此锚点落位时不会被 60px 的顶栏遮住。
    """
    # 顶栏展示当前系统日期；数据快照日期仍由 Hero/数据状态区单独标注。
    date_text = datetime.now().strftime("%Y-%m-%d")
    st.session_state.setdefault("auth_logged_in", False)
    if "fresh_store_filter" in st.session_state:
        store_value = int(st.session_state.get("fresh_store_filter") or 0)
    else:
        # 分享链接首次打开时，筛选控件尚未实例化，先直接用 URL 参数显示。
        try:
            store_value = int(_query_value("store", "0") or 0)
        except ValueError:
            store_value = 0
    store_text = "全部门店" if store_value == 0 else f"{store_value} 号店"
    if st.session_state.get("auth_logged_in"):
        account = str(st.session_state.get("auth_account", "店长") or "店长")
        auth_label = (
            f'<span class="nav-auth-label">{html.escape(_role_label(_current_role()))}：'
            f"{html.escape(account)}</span>"
        )
    else:
        auth_label = ""
    st.markdown(
        '<header class="top-navbar">'
        '<div class="nav-left">'
        '<span class="nav-logo">ZL</span>'
        '<span class="nav-brand">智链参谋</span>'
        "</div>"
        '<nav class="nav-links" aria-label="页面快捷导航">'
        '<a href="#section-overview">今日概览</a>'
        '<a href="#section-inventory">库存健康</a>'
        '<a href="#section-forecast">7 天预测</a>'
        '<a href="#section-agent">智能问答</a>'
        "</nav>"
        '<details class="nav-more">'
        "<summary>更多</summary>"
        '<div class="nav-more-panel">'
        '<a href="#section-overview">今日概览</a>'
        '<a href="#section-inventory">库存健康</a>'
        '<a href="#section-forecast">7 天预测</a>'
        '<a href="#section-agent">智能问答</a>'
        "</div>"
        "</details>"
        '<div class="nav-right">'
        '<span class="nav-store">'
        '<span class="nav-store-label">当前门店</span>'
        f'<span class="nav-store-value">{html.escape(store_text)}</span>'
        "</span>"
        f'<span class="nav-date">{html.escape(str(date_text))}</span>'
        + auth_label
        + '<span class="nav-bell-slot"></span>'
        + '<span class="nav-auth-slot"></span>'
        "</div>"
        "</header>",
        unsafe_allow_html=True,
    )
    _render_auth_controls()


# ---------------------------------------------------------------------------
# 今日简报：页顶晨会播报
# 独立函数，只读当前已加载的补货表（不重算、不调模型），不影响轮播图与顶栏。
# ---------------------------------------------------------------------------
def build_daily_brief(repl: pd.DataFrame | None, snapshot: str) -> str:
    """用内存里的补货表拼一句店长口吻的晨会播报；数据为空时返回空串。"""
    if repl is None or repl.empty:
        return ""

    if "urgency" in repl.columns:
        urgency = repl["urgency"].astype(str).str.strip()
    else:
        urgency = pd.Series("", index=repl.index, dtype="object")
    urgent_n = int((urgency == "紧急").sum())
    suggest_n = int((urgency == "建议补货").sum())
    plan_n = int((urgency == "计划补货").sum())

    if "expiring_qty" in repl.columns:
        expiring = pd.to_numeric(repl["expiring_qty"], errors="coerce").fillna(0.0)
    else:
        expiring = pd.Series(0.0, index=repl.index)
    expiring_kinds = int((expiring > 0).sum())
    expiring_units = float(expiring[expiring > 0].sum())

    head = (
        f"今天有 {urgent_n} 条序列掉到紧急线"
        if urgent_n
        else "今天没有掉到紧急线的货"
    )
    expiry = (
        f"临期 {expiring_kinds} 种、约 {expiring_units:,.2f} {FRESH_UNIT} 得抓紧促销"
        if expiring_kinds
        else "临期这块今天空的"
    )
    tail = f"其余 {suggest_n} 条建议补货、{plan_n} 条计划补货按表排"
    return (
        f"店长早，补货快照 {snapshot}：{head}；{expiry}；{tail}，"
        "拿不准的随时问我。"
    )


def render_daily_brief(repl: pd.DataFrame | None, snapshot: str) -> None:
    """页顶晨会播报卡片：只新增一段 HTML，不改动轮播图与顶栏布局。"""
    text = build_daily_brief(repl, snapshot)
    if not text:
        return
    st.markdown(
        '<section role="note" style="display:flex;gap:12px;align-items:flex-start;'
        "background:#FFFFFF;border:1px solid #DCE7F3;border-left:4px solid #1E6FB8;"
        "border-radius:16px;padding:14px 18px;margin:14px 0 16px;"
        'box-shadow:0 6px 18px rgba(11,42,74,.05);">'
        '<span style="font-size:18px;line-height:1.35;flex:0 0 auto;">☀</span>'
        "<div>"
        '<div style="font-size:11px;font-weight:700;letter-spacing:.12em;'
        'color:#6b7f94;text-transform:uppercase;margin-bottom:4px;">'
        "今日简报 · 晨会播报</div>"
        '<div style="font-size:14.5px;line-height:1.7;color:#0b0b0b;">'
        f"{html.escape(text)}</div>"
        "</div></section>",
        unsafe_allow_html=True,
    )


def render_data_status(
    repl: pd.DataFrame | None,
    forecast: pd.DataFrame | None,
    config: dict,
    snapshot: str,
) -> None:
    """KPI 下方一行小字：数据截止日、快照文件生成时间与序列齐全性。"""
    if repl is None or repl.empty:
        return

    paths = [OUTPUT_DIR / FRESH_REPL, OUTPUT_DIR / FRESH_FORECAST]
    stamps = [path.stat().st_mtime for path in paths if path.exists()]
    updated = (
        pd.Timestamp.fromtimestamp(max(stamps)).strftime("%m-%d %H:%M")
        if stamps
        else "未知"
    )

    expected = int(
        ((config or {}).get("validation", {}) or {}).get(
            "expected_series", len(repl)
        )
    )
    series_ok = len(repl) == expected
    forecast_ok = True
    if (
        forecast is not None
        and not forecast.empty
        and {"store_id", "product_id"} <= set(forecast.columns)
    ):
        forecast_ok = (
            forecast[["store_id", "product_id"]].drop_duplicates().shape[0] == expected
        )

    if series_ok and forecast_ok:
        detail = f"{len(repl):,} 条序列校验通过"
    elif not series_ok:
        detail = f"序列数异常（{len(repl):,} / 期望 {expected:,}）"
    else:
        detail = "预测表序列数与补货快照不一致"

    warn_class = "" if series_ok and forecast_ok else " warn"
    refreshed = st.session_state.get("fresh_data_refreshed_at")
    refresh_note = (
        f" · 页面刷新 {html.escape(str(refreshed))}" if refreshed else ""
    )
    st.markdown(
        f'<div class="data-status{warn_class}">'
        '<span class="data-status-chip">'
        '<span class="dot"></span>'
        f"<span>数据截至 <b>{html.escape(str(snapshot))}</b>"
        f" · 快照生成 {html.escape(updated)}"
        f"{refresh_note}"
        f" · {html.escape(detail)}</span>"
        "</span></div>",
        unsafe_allow_html=True,
    )


def _inject_nav_scrollspy() -> None:
    """顶栏导航随滚动高亮当前区块：只切换 class，不改任何布局。"""
    parent_js = r"""
        (function () {
          function update() {
            var links = Array.prototype.slice.call(
              document.querySelectorAll(".nav-links a")
            );
            if (!links.length) return;
            var ids = links.map(function (a) {
              return (a.getAttribute("href") || "").slice(1);
            });
            var best = null;
            var bestTop = -1e9;
            ids.forEach(function (id) {
              var el = document.getElementById(id);
              if (!el) return;
              var top = el.getBoundingClientRect().top;
              if (top <= 120 && top > bestTop) {
                bestTop = top;
                best = id;
              }
            });
            var scroller =
              document.querySelector("section.main") ||
              document.querySelector('[data-testid="stMain"]');
            if (
              scroller &&
              scroller.scrollTop + scroller.clientHeight >=
                scroller.scrollHeight - 4
            ) {
              best = ids[ids.length - 1];
            }
            if (!best) best = ids[0];
            links.forEach(function (a) {
              a.classList.toggle("active", a.getAttribute("href") === "#" + best);
            });
          }
          window.__navSpyUpdate = update;
          if (!window.__navSpyBound) {
            window.__navSpyBound = true;
            document.addEventListener("scroll", update, true);
            window.addEventListener("scroll", update, { passive: true });
            window.addEventListener("resize", update);
          }
          update();
        })();
        """
    bootstrap = (
        "<script>(function(){"
        "var doc=window.parent.document;"
        "if(!doc||!doc.body)return;"
        "if(doc.getElementById('nav-scrollspy-runtime')){"
        "if(typeof window.parent.__navSpyUpdate==='function')"
        "window.parent.__navSpyUpdate();"
        "return;"
        "}"
        "var s=doc.createElement('script');"
        "s.id='nav-scrollspy-runtime';"
        "s.textContent=" + json.dumps(parent_js) + ";"
        "(doc.body||doc.documentElement).appendChild(s);"
        "})();</script>"
    )
    components.html(bootstrap, height=0)


def main_fresh() -> None:
    repl = load_csv(FRESH_REPL)
    forecast = load_csv(FRESH_FORECAST)
    comparison = load_csv(FRESH_COMPARISON)
    cap_sweep = load_csv(FRESH_CAP_SWEEP)
    metrics = load_csv(FRESH_METRICS)
    config = load_fresh_config()
    snapshot = fresh_snapshot_date(forecast, config)
    if repl is not None and not repl.empty:
        values = fresh_kpi_values(repl, comparison)
        suggest_total = int(
            pd.to_numeric(repl["suggest_qty"], errors="coerce").fillna(0).sum()
        )
    else:
        values = {
            "series": 0,
            "urgent": 0,
            "advised": 0,
            "expiring_qty": 0.0,
            "expiring_series": 0,
            "short_shelf": None,
            "spoilage_c": None,
            "spoilage_b": None,
        }
        suggest_total = 0

    render_top_navbar(snapshot)
    render_notification_center(repl, config, snapshot)
    # 今日简报：页顶晨会播报，数字来自本次已加载的补货表。
    render_daily_brief(repl, snapshot)
    components.html(
        _fresh_hero_carousel_html(
            snapshot,
            "FreshRetailNet-50K（品类已脱敏）",
            int(values["urgent"]),
            int(values["expiring_series"]),
            suggest_total,
        ),
        height=340,
    )

    if repl is None or repl.empty:
        st.error(
            "生鲜补货快照缺失，请先运行 "
            "app/freshretail_inventory.py 生成 freshretail_inventory_replenishment.csv。"
        )
    else:
        caps = compute_expiry_caps(repl, forecast, config)
        log_fresh_kpi(values, snapshot)
        render_fresh_kpi(values, snapshot, metrics)
        status_col, refresh_col, _data_spacer = st.columns([6.0, 0.5, 0.5])
        with status_col:
            render_data_status(repl, forecast, config, snapshot)
        with refresh_col:
            st.markdown(
                '<span class="data-refresh-anchor"></span>',
                unsafe_allow_html=True,
            )
            if st.button(
                "↻",
                key="fresh_refresh_data",
                help="刷新数据：重新读取 output/ 下的 CSV 与配置",
                use_container_width=True,
            ):
                load_csv.clear()
                load_fresh_config.clear()
                get_fresh_store.clear()
                get_dataco_store.clear()
                st.session_state.pop("fresh_kpi_logged", None)
                st.session_state["fresh_data_refreshed_at"] = (
                    pd.Timestamp.now().strftime("%H:%M:%S")
                )
                st.toast("已重新读取数据文件")
                st.rerun()
        st.divider()
        st.markdown('<div id="section-inventory"></div>', unsafe_allow_html=True)
        render_fresh_replenishment(
            repl, caps, cap_sweep, config, snapshot, values
        )
        _scroll_to_deep_link_once()
        st.divider()
        st.markdown('<div id="section-forecast"></div>', unsafe_allow_html=True)
        # 5.4MB 汇总表只在预测详情区需要，放到这里加载，避免拖慢首屏。
        summary = load_csv(FRESH_SUMMARY)
        render_fresh_detail(repl, forecast, summary, config, caps, snapshot)

    st.caption("注：销量单位已作展示化处理，底层为归一化销量指数")
    st.divider()
    st.markdown('<div id="section-agent"></div>', unsafe_allow_html=True)
    api_key = get_agent().load_api_key()
    render_chat(
        api_key,
        get_fresh_store,
        dataset="freshretail",
        state_key="fresh",
        quick_questions=FRESH_QUICK_QUESTIONS,
        placeholder="问生鲜补货问题，例如：SKU 145-802 该补多少？",
        source_note=f"数据来源：补货快照 {snapshot}",
    )
    _inject_nav_scrollspy()


def _inject_floating_assistant() -> None:
    """在父页面右下角注入可拖拽的 AI 助手悬浮球。

    组件 iframe 高度为 0，不参与 Streamlit 布局；CSS/JS 全部作用于
    window.parent.document，点击后平滑滚动到补货 Agent 输入区域。
    视觉参考 transparent.png 的玻璃球高光/内旋涡，但使用蓝黑渐变重建，
    避免依赖外部图片资源。
    """
    parent_js = r"""
        (function () {
          if (document.getElementById("ai-orb-button")) return;
          var doc = document;
          if (!doc || !doc.body) return;

          var style = doc.createElement("style");
          style.id = "ai-orb-style";
          style.textContent = `
            #ai-orb-root {
              position: fixed;
              right: 26px;
              bottom: 26px;
              width: 60px;
              height: 60px;
              z-index: 9999;
              pointer-events: none;
            }
            #ai-orb-root::before {
              content: "";
              position: absolute;
              left: 50%;
              bottom: -8px;
              width: 46px;
              height: 14px;
              transform: translateX(-50%);
              border-radius: 50%;
              background: radial-gradient(ellipse at center,
                rgba(2, 8, 20, 0.45) 0%,
                rgba(2, 8, 20, 0.18) 45%,
                transparent 74%);
              filter: blur(4px);
              pointer-events: none;
            }
            #ai-orb-button {
              position: absolute;
              inset: 0;
              width: 60px;
              height: 60px;
              padding: 0;
              border: 0;
              pointer-events: auto;
              border-radius: 50%;
              z-index: 9999;
              cursor: grab;
              touch-action: none;
              user-select: none;
              display: flex;
              align-items: center;
              justify-content: center;
              overflow: hidden;
              background:
                radial-gradient(circle at 32% 24%,
                  rgba(255,255,255,.98) 0%,
                  rgba(177,235,255,.92) 7%,
                  rgba(84,187,246,.76) 18%,
                  rgba(30,111,184,.72) 38%,
                  rgba(7,31,58,.98) 68%,
                  #02070f 100%);
              box-shadow:
                0 12px 34px rgba(0,8,24,.55),
                0 0 0 1px rgba(126,206,255,.32),
                0 0 28px rgba(30,111,184,.55);
              animation: aiOrbBreathe 4.5s ease-in-out infinite;
              transition: transform .18s ease, box-shadow .18s ease;
              font-family: "Microsoft YaHei", "PingFang SC", sans-serif;
            }
            #ai-orb-button::before {
              content: "";
              position: absolute;
              inset: 7px;
              border-radius: 50%;
              background: radial-gradient(circle at 30% 25%, rgba(255,255,255,.42), transparent 42%);
              filter: blur(1px);
              pointer-events: none;
            }
            #ai-orb-button::after {
              content: "";
              position: absolute;
              width: 34px;
              height: 34px;
              left: 8px;
              bottom: 7px;
              border-radius: 50%;
              border: 2px solid rgba(109,205,255,.65);
              border-top-color: rgba(255,255,255,.95);
              transform: rotate(-35deg);
              opacity: .72;
              filter: blur(.25px);
              pointer-events: none;
            }
            #ai-orb-button:hover { transform: scale(1.06); }
            #ai-orb-button:focus-visible {
              outline: 2px solid #8fd4ff;
              outline-offset: 4px;
            }
            #ai-orb-button.dragging {
              cursor: grabbing;
              animation: none;
              transform: scale(.97);
            }
            #ai-orb-button .ai-orb-icon {
              position: relative;
              z-index: 2;
              color: #eaf8ff;
              font-size: 20px;
              line-height: 1;
              text-shadow: 0 0 12px rgba(125,215,255,.95);
              pointer-events: none;
            }
            #ai-orb-bubble {
              position: absolute;
              right: 74px;
              bottom: 0;
              z-index: 10000;
              width: 280px;
              max-width: calc(100vw - 120px);
              box-sizing: border-box;
              padding: 12px 15px;
              border-radius: 16px;
              border: 1px solid rgba(126,206,255,.35);
              background: linear-gradient(135deg, #071b32 0%, #0c3a64 55%, #155b96 100%);
              box-shadow: 0 18px 44px rgba(0,8,24,.42);
              color: #eaf7ff;
              font-family: "Microsoft YaHei", "PingFang SC", sans-serif;
              font-size: 13px;
              line-height: 1.65;
              display: flex;
              align-items: flex-start;
              gap: 8px;
              visibility: hidden;
              opacity: 0;
              transform: translateY(6px);
              pointer-events: none;
              transition: opacity .2s ease, transform .2s ease;
            }
            #ai-orb-bubble.show {
              visibility: visible;
              opacity: 1;
              transform: translateY(0);
              pointer-events: auto;
            }
            #ai-orb-root.bubble-right #ai-orb-bubble {
              right: auto;
              left: 74px;
            }
            #ai-orb-bubble .ai-orb-bubble-icon {
              color: #8fd4ff;
              font-size: 15px;
              line-height: 1.45;
              flex: 0 0 auto;
              text-shadow: 0 0 10px rgba(126,206,255,.8);
            }
            @keyframes aiOrbBreathe {
              0%, 100% {
                box-shadow:
                  0 12px 34px rgba(0,8,24,.55),
                  0 0 0 1px rgba(126,206,255,.32),
                  0 0 28px rgba(30,111,184,.55);
              }
              50% {
                box-shadow:
                  0 14px 38px rgba(0,8,24,.6),
                  0 0 0 1px rgba(126,206,255,.4),
                  0 0 38px rgba(64,162,235,.7);
              }
            }
            @media (prefers-reduced-motion: reduce) {
              #ai-orb-button { animation: none; transition: none; }
            }
          `;
          (doc.head || doc.documentElement).appendChild(style);

          var root = doc.createElement("div");
          root.id = "ai-orb-root";
          doc.body.appendChild(root);

          var orb = doc.createElement("button");
          orb.id = "ai-orb-button";
          orb.type = "button";
          orb.setAttribute("aria-label", "AI 补货助手");
          orb.innerHTML = '<span class="ai-orb-icon">✦</span>';
          root.appendChild(orb);

          var bubble = doc.createElement("div");
          bubble.id = "ai-orb-bubble";
          bubble.setAttribute("role", "status");
          bubble.setAttribute("aria-live", "polite");
          bubble.innerHTML = '<span class="ai-orb-bubble-icon">✦</span><span>你好，我是你的 AI 补货助手，有任何问题随时问我！</span>';
          root.appendChild(bubble);

          var bubbleTimer = null;
          var hideTimer = null;
          var dragging = false;
          var moved = false;
          var startX = 0;
          var startY = 0;
          var offsetX = 0;
          var offsetY = 0;
          var pointerId = null;

          function clamp(value, min, max) {
            return Math.max(min, Math.min(value, max));
          }

          function updateBubbleSide() {
            var r = root.getBoundingClientRect();
            root.classList.toggle("bubble-right", r.left < 300);
          }

          function showBubble(autoHideMs) {
            clearTimeout(bubbleTimer);
            clearTimeout(hideTimer);
            bubble.classList.add("show");
            bubble.style.visibility = "visible";
            updateBubbleSide();
            if (autoHideMs) {
              bubbleTimer = window.setTimeout(hideBubble, autoHideMs);
            }
          }

          function hideBubble() {
            clearTimeout(bubbleTimer);
            bubble.classList.remove("show");
            bubble.style.visibility = "hidden";
          }

          function openAssistant() {
            hideBubble();
            // 点击拖拽球 = 打开/收起全局 AI 面板（隐藏的 Streamlit 按钮负责状态切换）。
            var marker = doc.querySelector(".global-ai-toggle-anchor");
            var block = marker
              ? marker.closest('[data-testid="stVerticalBlock"]')
              : null;
            var toggle = block ? block.querySelector("button") : null;
            if (!toggle) {
              toggle = Array.prototype.slice
                .call(doc.querySelectorAll("button"))
                .find(function (b) {
                  var text = (b.innerText || "").trim();
                  return text === "✦" || text === "×";
                });
            }
            if (toggle) {
              toggle.click();
              return;
            }
            // 兜底：面板尚未渲染时，滚到主聊天区。
            var input = doc.querySelector('[data-testid="stForm"] input');
            var anchor = doc.getElementById("agent-chat-anchor");
            var target = input || anchor;
            if (target) {
              target.scrollIntoView({ behavior: "smooth", block: "center" });
            }
          }

          orb.addEventListener("pointerdown", function (e) {
            if (e.button !== undefined && e.button !== 0) return;
            e.preventDefault();
            var r = root.getBoundingClientRect();
            dragging = true;
            moved = false;
            startX = e.clientX;
            startY = e.clientY;
            offsetX = e.clientX - r.left;
            offsetY = e.clientY - r.top;
            pointerId = e.pointerId;
            root.style.left = r.left + "px";
            root.style.top = r.top + "px";
            root.style.right = "auto";
            root.style.bottom = "auto";
            orb.classList.add("dragging");
            try {
              orb.setPointerCapture(e.pointerId);
            } catch (err) {}
            hideBubble();
          });

          orb.addEventListener("pointermove", function (e) {
            if (!dragging) return;
            e.preventDefault();
            if (Math.abs(e.clientX - startX) > 4 || Math.abs(e.clientY - startY) > 4) {
              moved = true;
            }
            var x = clamp(
              e.clientX - offsetX,
              8,
              Math.max(8, window.innerWidth - root.offsetWidth - 8)
            );
            var y = clamp(
              e.clientY - offsetY,
              8,
              Math.max(8, window.innerHeight - root.offsetHeight - 8)
            );
            root.style.left = x + "px";
            root.style.top = y + "px";
            updateBubbleSide();
          });

          function endDrag() {
            if (!dragging) return;
            dragging = false;
            orb.classList.remove("dragging");
            try {
              if (pointerId !== null) orb.releasePointerCapture(pointerId);
            } catch (err) {}
            pointerId = null;
            if (!moved) openAssistant();
          }

          orb.addEventListener("pointerup", endDrag);
          orb.addEventListener("pointercancel", function () {
            dragging = false;
            orb.classList.remove("dragging");
            pointerId = null;
          });
          orb.addEventListener("keydown", function (e) {
            if (e.key === "Enter" || e.key === " ") {
              e.preventDefault();
              openAssistant();
            }
          });

          orb.addEventListener("mouseenter", function () {
            showBubble(0);
          });
          orb.addEventListener("mouseleave", function () {
            hideTimer = window.setTimeout(hideBubble, 350);
          });
          bubble.addEventListener("mouseenter", function () {
            clearTimeout(hideTimer);
          });
          bubble.addEventListener("mouseleave", function () {
            hideTimer = window.setTimeout(hideBubble, 250);
          });
          window.addEventListener("resize", function () {
            updateBubbleSide();
          });

          window.setTimeout(function () {
            showBubble(3000);
          }, 400);
        })();
        """

    # 把真正的前端代码注入父页面执行，而不是留在这个 components iframe 里。
    # 这样 Streamlit rerun 重建 iframe 后，悬浮球的事件监听仍然有效。
    bootstrap = (
        "<script>(function(){"
        "var doc=window.parent.document;"
        "if(!doc||!doc.body)return;"
        "if(doc.getElementById('ai-orb-button'))return;"
        "var s=doc.createElement('script');"
        "s.id='ai-orb-runtime';"
        "s.textContent=" + json.dumps(parent_js) + ";"
        "(doc.body||doc.documentElement).appendChild(s);"
        "})();</script>"
    )
    components.html(bootstrap, height=0)


def _scroll_page_to_top_once() -> None:
    """页面首次加载时强制回到顶部，避免缓存滚动位置或输入框聚焦。"""
    if st.session_state.get("_initial_scroll_fixed"):
        return
    st.session_state["_initial_scroll_fixed"] = True

    components.html(
        """
        <script>
        (function () {
          const doc = window.parent.document;
          function resetTop() {
            try {
              const main =
                doc.querySelector('section.main') ||
                doc.querySelector('[data-testid="stMain"]') ||
                doc.querySelector('[data-testid="stAppViewContainer"]');
              if (main) main.scrollTop = 0;
              if (doc.activeElement && doc.activeElement.blur) {
                doc.activeElement.blur();
              }
              window.parent.scrollTo(0, 0);
              doc.documentElement.scrollTop = 0;
              if (doc.body) doc.body.scrollTop = 0;
            } catch (e) {
              /* 父页面不可访问时静默失败 */
            }
          }
          resetTop();
          [50, 200, 500].forEach(function (ms) {
            window.setTimeout(resetTop, ms);
          });
        })();
        </script>
        """,
        height=0,
    )


def _scroll_to_deep_link_once() -> None:
    """?sku=... 首次打开时滚动到溯源区；顶部回滚脚本在 500ms，这里更晚。"""
    if not _query_value("sku"):
        return
    if st.session_state.get("_deep_link_scrolled"):
        return
    st.session_state["_deep_link_scrolled"] = True
    components.html(
        """
        <script>
        (function () {
          function go() {
            try {
              const doc = window.parent.document;
              const el = doc.getElementById("section-trace");
              if (el) el.scrollIntoView({ behavior: "smooth", block: "start" });
            } catch (e) {}
          }
          [700, 1100, 1600].forEach(function (ms) {
            window.setTimeout(go, ms);
          });
        })();
        </script>
        """,
        height=0,
    )


def _render_sidebar_nav() -> None:
    with st.sidebar:
        st.markdown(
            '<div class="side-brand">'
            '<div class="side-brand-mark">ZL</div>'
            "<div>"
            '<div class="side-brand-title">智链参谋</div>'
            '<div class="side-brand-sub">生鲜前置仓工作台</div>'
            "</div>"
            "</div>",
            unsafe_allow_html=True,
        )
        if st.session_state.get("source_mode", "fresh") == "dataco":
            st.caption("当前：制造企业（保底）模式")
            return
        st.markdown(
            '<div class="side-nav-label">工作台导航</div>'
            '<nav class="side-nav">'
            '<a href="#section-overview">'
            '<span class="side-icon">📊</span><span class="side-text">今日概览</span></a>'
            '<a href="#section-inventory">'
            '<span class="side-icon">📦</span><span class="side-text">库存健康</span></a>'
            '<a href="#section-forecast">'
            '<span class="side-icon">📈</span><span class="side-text">7 天预测</span></a>'
            '<a href="#section-agent">'
            '<span class="side-icon">💬</span><span class="side-text">补货 Agent</span></a>'
            "</nav>",
            unsafe_allow_html=True,
        )


def _render_source_egg() -> str:
    """隐藏数据源切换：右上角小齿轮连点 3 次解锁侧边栏 Toggle。"""
    st.session_state.setdefault("source_mode", "fresh")
    st.session_state.setdefault("source_egg_clicks", 0)
    st.session_state.setdefault("source_switch_visible", False)

    _, egg_col = st.columns([20, 1])
    with egg_col:
        st.markdown(
            '<span class="source-egg-anchor"></span>',
            unsafe_allow_html=True,
        )
        clicked = st.button("⚙️", key="source_egg_trigger")

    if clicked:
        st.session_state.source_egg_clicks += 1
        if (
            st.session_state.source_egg_clicks >= 3
            and not st.session_state.source_switch_visible
        ):
            st.session_state.source_switch_visible = True
            st.toast("数据源设置已解锁，请打开侧边栏")
            st.rerun()

    if st.session_state.source_switch_visible:
        with st.sidebar:
            st.markdown("### 数据源设置")
            is_dataco = st.session_state.source_mode == "dataco"
            toggled = st.toggle(
                "制造企业（保底）",
                value=is_dataco,
                key="source_mode_toggle",
                help="关闭 = 生鲜前置仓；打开 = 制造企业保底数据源",
            )
            if toggled != is_dataco:
                st.session_state.source_mode = "dataco" if toggled else "fresh"
                st.rerun()
            st.caption("默认：生鲜前置仓")

    return str(st.session_state.source_mode)


def render_global_ai(dataset: str) -> None:
    """全局问 AI 浮层：固定在右下角，输入与回答都在面板内，不跳转页面。"""
    st.session_state.setdefault("global_ai_open", False)
    st.session_state.setdefault("global_ai_messages", [])
    is_open = bool(st.session_state.global_ai_open)

    with st.container():
        st.markdown(
            '<span class="global-ai-toggle-anchor"></span>',
            unsafe_allow_html=True,
        )
        if st.button(
            "×" if is_open else "✦",
            key="global_ai_toggle",
            help="全局问 AI：不离开当前页面直接提问",
        ):
            st.session_state.global_ai_open = not is_open
            st.rerun()

    if not is_open:
        return

    with st.container():
        st.markdown(
            '<span class="global-ai-panel-anchor"></span>',
            unsafe_allow_html=True,
        )
        title_col, clear_col, close_col = st.columns([2.2, 0.9, 0.9])
        title_col.markdown("**全局问 AI**")
        if clear_col.button(
            "清空", key="global_ai_clear", use_container_width=True
        ):
            st.session_state.global_ai_messages = []
            st.rerun()
        if close_col.button(
            "收起", key="global_ai_close", use_container_width=True
        ):
            st.session_state.global_ai_open = False
            st.rerun()
        st.caption("基于当前数据源直接提问，不离开当前页面。")

        history = st.session_state.global_ai_messages
        if history:
            for message in history[-4:]:
                with st.chat_message(message["role"]):
                    st.markdown(message["content"])
                    tools = message.get("tools") or []
                    if tools:
                        with st.expander("工具调用"):
                            _tool_call_markdown(tools)
        else:
            st.caption("例如：今天最该先补哪几条？")

        with st.form("global_ai_form", clear_on_submit=True):
            prompt = st.text_input(
                "问题",
                label_visibility="collapsed",
                placeholder="问补货问题，例如：SKU 145-802 该补多少？",
            )
            sent = st.form_submit_button("发送", use_container_width=True)

        if sent and prompt.strip():
            agent = get_agent()
            api_key = agent.load_api_key()
            if not api_key:
                st.warning("未配置 DEEPSEEK_API_KEY，无法调用。")
            else:
                activate_agent_dataset(agent, dataset)
                messages = [{"role": "system", "content": _system_prompt(dataset)}]
                messages += [
                    {"role": item["role"], "content": item["content"]}
                    for item in history
                ]
                messages.append({"role": "user", "content": prompt.strip()})
                store = (
                    get_fresh_store()
                    if dataset == "freshretail"
                    else get_dataco_store()
                )
                with st.spinner("Agent 正在核对数据..."):
                    try:
                        answer, tools = agent.run_turn(
                            messages, store, api_key, echo=False
                        )
                    except agent.AgentError as exc:
                        answer, tools = str(exc), []
                    except Exception:
                        answer, tools = "调用失败，请稍后重试。", []
                history.append({"role": "user", "content": prompt.strip()})
                history.append(
                    {
                        "role": "assistant",
                        "content": answer or "（模型未返回内容）",
                        "tools": tools,
                    }
                )
                st.session_state.global_ai_messages = history
                st.rerun()


def main() -> None:
    _apply_pending_auth_token()
    _restore_session_from_token()
    _scroll_page_to_top_once()
    st.session_state.setdefault("source_mode", "fresh")
    _render_sidebar_nav()
    _agent_backup(get_agent())  # 先留存 DataCo 原工具集，支持来回切换
    source_mode = _render_source_egg()
    if source_mode == "dataco":
        main_dataco()
    else:
        main_fresh()

    st.markdown(
        '<div class="site-footer">'
        '<div class="footer-line1">智链参谋 · 生鲜前置仓 AI 补货决策系统</div>'
        '<div class="footer-line2">全国人工智能应用创新大赛</div>'
        "</div>",
        unsafe_allow_html=True,
    )
    render_global_ai("dataco" if source_mode == "dataco" else "freshretail")
    _inject_floating_assistant()


if __name__ == "__main__":
    main()
