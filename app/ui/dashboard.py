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

import html
import json
import math
import sys
from pathlib import Path

import pandas as pd
import streamlit as st
import yaml

APP_DIR = Path(__file__).resolve().parents[1]
BASE_DIR = APP_DIR.parent
OUTPUT_DIR = BASE_DIR / "output"
CONFIG_PATH = BASE_DIR / "config" / "freshretail_inventory.yaml"
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

_AGENT = None


def get_agent():
    """延迟导入 Agent，让图表和库存先渲染；只在对话区真正需要时加载。"""
    global _AGENT
    if _AGENT is None:
        import agent as agent_module
        _AGENT = agent_module
    return _AGENT


# ---------------------------------------------------------------------------
# 配置与视觉规范
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="智链参谋 · 生鲜前置仓补货决策助手",
    layout="wide",
    initial_sidebar_state="collapsed",
)

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
    "SKU 145-802 该补多少？",
    "哪些货快过期了？",
]

FRESH_REPL = "freshretail_inventory_replenishment.csv"
FRESH_FORECAST = "freshretail_forecast_predictions.csv"
FRESH_COMPARISON = "freshretail_inventory_policy_comparison.csv"
FRESH_CAP_SWEEP = "freshretail_inventory_cap_sweep.csv"
FRESH_SUMMARY = "freshretail_sku_summary_all.csv"

# 四档紧急度按固定顺序排序；颜色由 CSS 的 .badge-* 类承载
URGENCY_RANK = {"紧急": 0, "建议补货": 1, "计划补货": 2, "正常": 3}

# 图表色：历史线用分类蓝；预测分位用同色系 ordinal 蓝阶
BLUE = "#2a78d6"
BLUE_LIGHT = "#86b6ef"
BLUE_DARK = "#184f95"
INK = "#0b0b0b"
SECONDARY = "#52514e"
MUTED = "#898781"
GRID = "#e1e0d9"
AXIS = "#c3c2b7"

CSS = """
<style>
:root { color-scheme: light; }
.stApp { background: #f9f9f7; color: #0b0b0b; }
html, body, [class*="css"] {
  font-family: system-ui, -apple-system, "Segoe UI", "Microsoft YaHei", sans-serif;
}
.block-container { padding-top: 2.2rem; padding-bottom: 3rem; max-width: 1480px; }
#MainMenu, footer { visibility: hidden; }
[data-testid="stHeader"] { display: none; }
[data-testid="stToolbar"], [data-testid="stDecoration"] { visibility: hidden; }

.brand { color: #0b0b0b; font-size: 2rem; font-weight: 700; letter-spacing: -0.02em; line-height: 1.15; }
.subtitle { color: #52514e; font-size: 0.95rem; margin-top: 2px; }
.snapshot {
  display: inline-block; background: #f0efec; border: 1px solid #e1e0d9;
  border-radius: 999px; padding: 6px 12px; color: #52514e;
  font-size: 0.85rem; white-space: nowrap;
}
.topbar-line { border-top: 1px solid #e1e0d9; margin: 6px 0 18px; }

.reco-card {
  background: #fcfcfb; border: 1px solid rgba(11,11,11,0.08);
  border-radius: 14px; padding: 18px 18px 16px; margin: 10px 0 14px;
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
.reco-meta { color: #898781; font-size: 0.8rem; margin-top: 6px; }

.repl-table { width: 100%; border-collapse: collapse; font-size: 0.82rem; color: #0b0b0b; }
.repl-table th {
  text-align: right; font-weight: 600; color: #52514e; background: #f0efec;
  padding: 7px 8px; border-bottom: 1px solid #e1e0d9;
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
  background: #fcfcfb; border: 1px solid rgba(11,11,11,0.08);
  border-radius: 12px; padding: 12px 14px;
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
  background: #fcfcfb; border: 1px solid rgba(11,11,11,0.08);
  border-radius: 14px; padding: 16px 16px 14px;
}
.kpi-label { color: #52514e; font-size: 0.85rem; }
.kpi-value {
  color: #0b0b0b; font-size: 1.5rem; font-weight: 700;
  line-height: 1.28; margin-top: 5px;
}
.kpi-value-sm { font-size: 1.2rem; }
.kpi-vs { color: #898781; font-size: 0.9rem; font-weight: 500; margin: 0 4px; }
.kpi-unit { font-size: 0.95rem; font-weight: 500; color: #52514e; margin-left: 3px; }
.kpi-sub { color: #52514e; font-size: 0.85rem; margin-top: 5px; }
.kpi-source { color: #898781; font-size: 0.75rem; margin-top: 9px; }

.chart-svg { margin: 4px 0 2px; }
.chart-svg svg { width: 100%; height: auto; display: block; }

[data-testid="stMetric"] { background: #fcfcfb; }
[data-testid="stMetricLabel"] { color: #52514e; }
[data-testid="stMetricValue"] { color: #0b0b0b; }
[data-testid="stChatMessage"] { background: #fcfcfb; }

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
</style>
"""
st.markdown(CSS, unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# 数据加载（只读、缓存）
# ---------------------------------------------------------------------------
@st.cache_data(show_spinner=False)
def load_csv(name: str) -> pd.DataFrame | None:
    path = OUTPUT_DIR / name
    if not path.exists():
        return None
    try:
        return pd.read_csv(path)
    except Exception:
        return None


@st.cache_data(show_spinner=False)
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
def render_fresh_header(snapshot: str) -> None:
    c1, c2, c3 = st.columns([6.0, 2.0, 2.4])
    with c1:
        st.markdown(
            f"""
            <div class="brand">智链参谋 · 生鲜前置仓补货决策助手</div>
            <div class="subtitle">每天告诉你，每家店每个 SKU 明天该补多少 {FRESH_UNIT}</div>
            """,
            unsafe_allow_html=True,
        )
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


def render_fresh_kpi(values: dict[str, float | int], snapshot: str) -> None:
    st.markdown("### 今日概览")
    source_snapshot = f"来源：补货快照 {snapshot}"
    short_shelf = values.get("short_shelf")
    c_rate = values.get("spoilage_c")
    b_rate = values.get("spoilage_b")

    if short_shelf is not None and c_rate is not None and b_rate is not None:
        compare_value = (
            f"{float(c_rate):.2%}<span class='kpi-vs'>vs</span>{float(b_rate):.2%}"
        )
        compare_sub = (
            f"本方案 vs 传统补货 · {int(short_shelf)} 天保质期"
        )
        compare_source = f"来源：策略对比表 · {int(short_shelf)} 天保质期"
    else:
        compare_value = "—"
        compare_sub = "策略对比表缺失"
        compare_source = "来源：策略对比表"

    cards = [
        _kpi_card(
            "在管序列",
            f"{int(values['series']):,}<span class='kpi-unit'>条</span>",
            "店 × SKU 组合",
            source_snapshot,
        ),
        _kpi_card(
            "今天要补",
            (
                f"<span class='kpi-value-sm'>紧急 {int(values['urgent'])} 条"
                f" / 建议补货 {int(values['advised'])} 条</span>"
            ),
            "按紧急度现算",
            source_snapshot,
        ),
        _kpi_card(
            "临期预警",
            f"{float(values['expiring_qty']):,.2f}<span class='kpi-unit'>{FRESH_UNIT}</span>",
            f"涉及 {int(values['expiring_series'])} 条序列",
            source_snapshot,
        ),
        _kpi_card("短保报损对比", compare_value, compare_sub, compare_source),
    ]
    st.markdown(
        '<div class="kpi-grid">' + "".join(cards) + "</div>",
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


def render_fresh_replenishment(
    repl: pd.DataFrame,
    caps: dict[tuple[int, int], dict],
    cap_sweep: pd.DataFrame | None,
    config: dict,
    snapshot: str,
) -> None:
    st.markdown("### 库存健康与补货清单")
    st.caption(
        f"共 {len(repl):,} 条序列；库存、订货与紧急度来自补货快照 {snapshot}，页面不重算。"
    )

    rep_cfg = (config or {}).get("replenishment", {}) or {}
    exp_threshold = int(rep_cfg.get("expiring_threshold_days", 1))

    f1, f2, f3, f4 = st.columns([1.4, 1.2, 0.9, 1.5])
    urgency_options = list(URGENCY_RANK.keys())
    urgency_sel = f1.multiselect(
        "紧急度（多选）",
        options=urgency_options,
        default=urgency_options,
        key="fresh_urgency_filter",
    )
    shelf_options = fresh_shelf_options(repl, config)
    shelf_sel = f2.multiselect(
        "保质期档",
        options=shelf_options,
        default=shelf_options,
        format_func=lambda d: f"{int(d)} 天",
        key="fresh_shelf_filter",
    )
    expiring_only = f3.checkbox("临期仅看", key="fresh_expiring_only")
    search_raw = f4.text_input(
        "按 SKU 搜索",
        placeholder="例如 145-802",
        key="fresh_sku_search",
    )

    view = repl.copy()
    view["_label"] = [
        series_label(s, p) for s, p in zip(view["store_id"], view["product_id"])
    ]
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
        view = view[keys.str.contains(query, case=False, regex=False)]

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
                "店 / SKU": series_label(row.store_id, row.product_id),
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

    def _urgency_style(value: object) -> str:
        styles = {
            "紧急": "color:#b3261e;background-color:#fdecec;font-weight:600",
            "建议补货": "color:#b45309;background-color:#fff3e8;font-weight:600",
            "计划补货": "color:#1c5cab;background-color:#eef4fd;font-weight:600",
            "正常": "color:#0c6b0c;background-color:#eaf7ea;font-weight:600",
        }
        return styles.get(str(value), "")

    def _expiring_style(value: object) -> str:
        try:
            expiring = float(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return ""
        return (
            "color:#b45309;background-color:#fff8ea;font-weight:600"
            if expiring > 0
            else ""
        )

    def _expiring_flag_style(value: object) -> str:
        return (
            "color:#b45309;background-color:#fff3e8;font-weight:700"
            if str(value) == "是"
            else ""
        )

    styled = (
        display.style.map(_urgency_style, subset=["紧急度"])
        .map(_expiring_style, subset=[f"临期数量（{FRESH_UNIT}）"])
        .map(_expiring_flag_style, subset=["临期"])
        .format(
            {
                f"现有库存（{FRESH_UNIT}）": "{:.2f}",
                f"ROP_P90（{FRESH_UNIT}）": "{:.2f}",
                f"效期修正目标（{FRESH_UNIT}）": "{:.2f}",
                f"建议订货（{FRESH_UNIT}）": "{:.0f}",
                f"临期数量（{FRESH_UNIT}）": "{:.2f}",
            }
        )
    )
    st.dataframe(
        styled,
        use_container_width=True,
        height=500,
        hide_index=True,
    )
    st.caption(
        f"当前显示 {len(view):,} / {len(repl):,} 条；默认按紧急度、临期数量降序。"
        "表格支持点击表头排序，最大高度 500px，可在表格内滚动。"
        f"临期 = 剩余保质期 ≤ {exp_threshold} 天，相关单元格浅黄高亮。"
    )

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
        format_func=lambda key: series_label(key[0], key[1]),
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
          <div class="reco-label">今天的补货建议 · {html.escape(series_label(*selected))}</div>
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


def _ask_agent(
    question: str,
    api_key: str,
    store,
    dataset: str,
    state_key: str,
    source_note: str | None,
) -> None:
    agent = get_agent()
    activate_agent_dataset(agent, dataset)
    display_key = f"chat_display_{state_key}"
    messages_key = f"agent_messages_{state_key}"
    display = st.session_state[display_key]
    messages = st.session_state[messages_key]
    display.append({"role": "user", "content": question})
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
    st.rerun()


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
    if messages_key not in st.session_state:
        st.session_state[messages_key] = [
            {"role": "system", "content": agent.SYSTEM_PROMPT}
        ]

    if not api_key:
        st.info("未配置 DEEPSEEK_API_KEY，对话功能暂不可用。图表与库存数据不受影响。")

    display = st.session_state[display_key]
    for i, message in enumerate(display):
        with st.chat_message(message["role"]):
            st.markdown(message["content"])
            if message["role"] == "assistant":
                note = message.get("source") or source_note
                if note:
                    st.caption(note)
            tools = message.get("tools") or []
            if message["role"] == "assistant" and tools:
                with st.expander(
                    "查看工具调用", expanded=(i == len(display) - 1)
                ):
                    _tool_call_markdown(tools)

    quick_cols = st.columns(len(quick_questions))
    quick_question = None
    for col, label in zip(quick_cols, quick_questions):
        if col.button(
            label,
            type="secondary",
            use_container_width=True,
            disabled=not api_key,
            key=f"quick_{state_key}_{label}",
        ):
            quick_question = label

    prompt = st.chat_input(
        placeholder,
        disabled=not api_key,
        key=f"agent_chat_input_{state_key}",
    )
    question = prompt or quick_question

    # 可选：?ask=... 直接带一个问题进入页面，方便演示/截图
    auto_key = f"auto_asked_{state_key}"
    if auto_key not in st.session_state:
        st.session_state[auto_key] = False
    auto_question = st.query_params.get("ask", "")
    if auto_question and not st.session_state[auto_key]:
        st.session_state[auto_key] = True
        if not question:
            question = auto_question

    if question and api_key:
        _ask_agent(question, api_key, store, dataset, state_key, source_note)


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
            get_dataco_store(),
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
        get_dataco_store(),
        dataset="dataco",
        state_key="dataco",
        quick_questions=QUICK_QUESTIONS,
        placeholder="问补货问题，例如：SKU 365 该补多少？",
    )


# ---------------------------------------------------------------------------
# 主流程：生鲜前置仓（默认）
# ---------------------------------------------------------------------------
def main_fresh() -> None:
    repl = load_csv(FRESH_REPL)
    forecast = load_csv(FRESH_FORECAST)
    comparison = load_csv(FRESH_COMPARISON)
    cap_sweep = load_csv(FRESH_CAP_SWEEP)
    summary = load_csv(FRESH_SUMMARY)
    config = load_fresh_config()
    snapshot = fresh_snapshot_date(forecast, config)

    render_fresh_header(snapshot)

    if repl is None or repl.empty:
        st.error(
            "生鲜补货快照缺失，请先运行 "
            "app/freshretail_inventory.py 生成 freshretail_inventory_replenishment.csv。"
        )
    else:
        caps = compute_expiry_caps(repl, forecast, config)
        values = fresh_kpi_values(repl, comparison)
        log_fresh_kpi(values, snapshot)
        render_fresh_kpi(values, snapshot)
        st.divider()
        render_fresh_replenishment(repl, caps, cap_sweep, config, snapshot)
        st.divider()
        render_fresh_detail(repl, forecast, summary, config, caps, snapshot)

    st.caption("注：销量单位已作展示化处理，底层为归一化销量指数")
    st.divider()
    api_key = get_agent().load_api_key()
    render_chat(
        api_key,
        get_fresh_store(),
        dataset="freshretail",
        state_key="fresh",
        quick_questions=FRESH_QUICK_QUESTIONS,
        placeholder="问生鲜补货问题，例如：SKU 145-802 该补多少？",
        source_note=f"数据来源：补货快照 {snapshot}",
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


def main() -> None:
    _agent_backup(get_agent())  # 先留存 DataCo 原工具集，支持来回切换
    source_mode = _render_source_egg()
    if source_mode == "dataco":
        main_dataco()
    else:
        main_fresh()


if __name__ == "__main__":
    main()
