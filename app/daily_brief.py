"""今日简报：把补货快照翻译成一段店长口吻的晨会开场白（第 11 步）。

分工：
    build_brief_data()  只读已有 output 表，用 pandas 把数字算死（不调 LLM、断网可跑）；
    render_template()   按日期 hash 固定选一组措辞，格式化只在这里做；
    build_brief()       总入口，按 config 的 daily_brief.mode 决定是否走 DeepSeek 润色；
    render_brief()      看板直接调用的单行入口，失败时返回空串，不让看板崩。

数字来源（每个数都能回到具体文件的具体行）：
    output/freshretail_inventory_replenishment.csv   “04 表”：on_hand / suggest_qty /
        urgency / expiring_qty 的唯一来源；
    output/freshretail_forecast_predictions.csv      “07 表”：y_pred_p50，用于算紧急条目的
        可售天数（on_hand ÷ 快照日日销 P50），以及可选的 holiday_flag / activity_flag 背景；
    config/freshretail_inventory.yaml                daily_brief.mode / timeout 与临期阈值口径。

口径说明：
    * 临期种类/件数沿用看板“临期货”版块：04 表 expiring_qty > 0 的序列数、expiring_qty 合计
      （expiring_threshold_days = 1，剩余保质期 ≤ 1 天，见 config）。
    * 可售天数 = on_hand ÷ 07 表快照日（2024-07-02）的 y_pred_p50；预测窗口末日之后按
      最后一日平推，与 freshretail_inventory.py 快照的口径一致。
    * 04/07 表当前没有 holiday_flag / activity_flag 列，因此 context_lines 为空、背景段整段
      跳过；代码保留读取分支，表里出现这两列后自动生效，不需要改模板。

运行（自检 + 打印 3 个日期的样例）：
    .venv\\Scripts\\python.exe app\\daily_brief.py
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from collections import Counter
from datetime import date as Date
from pathlib import Path

import pandas as pd
import yaml

BASE_DIR = Path(__file__).resolve().parents[1]
OUTPUT_DIR = BASE_DIR / "output"
CONFIG_PATH = BASE_DIR / "config" / "freshretail_inventory.yaml"

# 04 表 / 07 表：文件名与列名写死在这里，避免看板与脚本各写一套口径。
REPL_FILE = "freshretail_inventory_replenishment.csv"
FORECAST_FILE = "freshretail_forecast_predictions.csv"
REPL_PATH = OUTPUT_DIR / REPL_FILE
FORECAST_PATH = OUTPUT_DIR / FORECAST_FILE

WEEKDAYS = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")

API_URL = "https://api.deepseek.com/chat/completions"
MODEL_NAME = "deepseek-chat"
DEFAULT_TIMEOUT = 3.0

logger = logging.getLogger("daily_brief")


# ---------------------------------------------------------------------------
# 1. 数据计算层：纯 pandas，不调 LLM
# ---------------------------------------------------------------------------
def load_brief_config(path: Path = CONFIG_PATH) -> dict:
    """只取 daily_brief 段；缺省时按 template 模式跑。"""
    if not path.exists():
        return {"daily_brief": {"mode": "template", "timeout_seconds": DEFAULT_TIMEOUT}}
    with path.open("r", encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh) or {}
    section = cfg.get("daily_brief") or {}
    mode = str(section.get("mode", "template")).strip().lower()
    if mode not in ("template", "llm"):
        mode = "template"
    return {
        "daily_brief": {
            "mode": mode,
            "timeout_seconds": float(section.get("timeout_seconds", DEFAULT_TIMEOUT)),
        }
    }


def _sellable_days(on_hand: float, daily_p50: float) -> float | None:
    """可售天数 = 现有库存 ÷ 次日日销 P50；P50 为 0 时无法判断，返回 None。"""
    if daily_p50 is None or float(daily_p50) <= 0:
        return None
    return float(on_hand) / float(daily_p50)


def _flag_on(forecast: pd.DataFrame, day: pd.Timestamp, column: str) -> bool:
    """读取 07 表某一天的 0/1 标记；列不存在或没有该天时返回 False（不编造）。"""
    if column not in forecast.columns:
        return False
    rows = forecast[forecast["dt"] == day]
    if rows.empty:
        return False
    value = pd.to_numeric(rows[column], errors="coerce").fillna(0)
    return bool((value > 0).any())


def build_brief_data(
    output_dir: Path | None = None,
    snapshot_date: str | None = None,
) -> dict:
    """算出简报要用的全部数字；返回的数值保持原始精度，格式化交给渲染层。"""
    output_dir = Path(output_dir) if output_dir is not None else OUTPUT_DIR
    repl_path = output_dir / REPL_FILE
    forecast_path = output_dir / FORECAST_FILE
    for path in (repl_path, forecast_path):
        if not path.exists():
            raise FileNotFoundError(f"缺少 {path}，请先运行对应的生鲜脚本")

    # 04 表：每行是一条“店 × SKU”的补货快照
    repl = pd.read_csv(repl_path, encoding="utf-8-sig")
    # 07 表：200 条序列 × 7 天，只有这张表有 y_pred_p50
    forecast = pd.read_csv(forecast_path, encoding="utf-8-sig")
    forecast["dt"] = pd.to_datetime(forecast["dt"], errors="coerce")
    forecast = forecast.dropna(subset=["dt"])

    if snapshot_date is not None:
        snap = pd.Timestamp(snapshot_date)
    else:
        snap = forecast["dt"].max()
    same_day = forecast[forecast["dt"] == snap]
    if same_day.empty:
        # 传入的日期不在预测窗口内时，退回窗口末日，保证可售天数仍有来源
        snap = forecast["dt"].max()
        same_day = forecast[forecast["dt"] == snap]

    daily = (
        same_day.groupby(["store_id", "product_id"], as_index=False)["y_pred_p50"]
        .max()
        .rename(columns={"y_pred_p50": "p50_snapshot_day"})
    )
    merged = repl.merge(daily, on=["store_id", "product_id"], how="left")
    merged["sellable_days"] = [
        _sellable_days(on_hand, p50)
        for on_hand, p50 in zip(merged["on_hand"], merged["p50_snapshot_day"])
    ]

    # 紧急档：04 表 urgency == "紧急"；按可售天数升序点名，天数算不出的排最后
    urgent_rows = merged[merged["urgency"].astype(str) == "紧急"].copy()
    urgent_rows["_days_key"] = urgent_rows["sellable_days"].apply(
        lambda x: float("inf") if x is None else float(x)
    )
    urgent_rows = urgent_rows.sort_values(
        ["_days_key", "suggest_qty", "store_id", "product_id"],
        ascending=[True, False, True, True],
    )
    urgent_list = [
        {
            "store_id": int(row.store_id),
            "product_id": int(row.product_id),
            "suggest_qty": int(row.suggest_qty),
            "sellable_days": (
                None if pd.isna(row.sellable_days) else float(row.sellable_days)
            ),
        }
        for row in urgent_rows.head(2).itertuples()
    ]

    # 临期：沿用看板“临期货”版块口径，04 表 expiring_qty > 0
    expiring = pd.to_numeric(merged["expiring_qty"], errors="coerce").fillna(0.0)
    near_expiry_kinds = int((expiring > 0).sum())
    near_expiry_units = float(expiring[expiring > 0].sum())

    # 04 表 urgency 分档计数（“建议补货 / 计划补货”是表里现成的档位）
    urgency = merged["urgency"].astype(str)
    suggest_total = int((urgency == "建议补货").sum())
    plan_total = int((urgency == "计划补货").sum())

    # 背景段：次日是否周末/节假日、是否促销。07 表目前没有这两列 -> 空列表。
    # 两个标记同时为 1 时合并成一句，避免把简报撑出 150 字上限。
    next_day = snap + pd.Timedelta(days=1)
    tags: list[str] = []
    if _flag_on(forecast, next_day, "holiday_flag"):
        tags.append("周末或节假日")
    if _flag_on(forecast, next_day, "activity_flag"):
        tags.append("促销")
    context_lines: list[str] = []
    if tags:
        context_lines.append(f"明天是{'、'.join(tags)}，预测已按客流调过")

    return {
        "snapshot_date": snap.strftime("%Y-%m-%d"),
        "urgent_count": int(len(urgent_rows)),
        "urgent_list": urgent_list,
        "near_expiry_kinds": near_expiry_kinds,
        "near_expiry_units": near_expiry_units,
        "suggest_total": suggest_total,
        "plan_total": plan_total,
        "context_lines": context_lines,
    }


# ---------------------------------------------------------------------------
# 2. 模板渲染：每段 2~3 个变体，按“日期 + 段名”做 md5 轮换（同一天固定）
# ---------------------------------------------------------------------------
GREETING_PRESS = (
    "老板早，今天 {n} 条掉到紧急线，得抓紧。",
    "老板早，今天不太轻松，{n} 条紧急的先处理。",
    "早上好老板，今天 {n} 条真急，先排出去。",
)
GREETING_CALM = (
    "老板早上好，今天整体还算稳，没有紧急的货。",
    "老板早，今天盘子还算稳，按平时节奏走。",
)
URGENT_TWO = (
    "{s1} 店 {p1} 号只剩 {d1} 天量，今早补 {q1} 件；"
    "{s2} 店 {p2} 号剩 {d2} 天，补 {q2} 件。",
    "{s1} 店 {p1} 号只剩 {d1} 天，补 {q1} 件；"
    "{s2} 店 {p2} 号剩 {d2} 天，补 {q2} 件。",
    "{s1}-{p1} 只剩 {d1} 天量，今早补 {q1} 件；"
    "{s2}-{p2} 剩 {d2} 天，补 {q2} 件。",
)
URGENT_ONE = (
    "最急的是 {s1} 店 {p1} 号，只剩 {d1} 天，今早补 {q1} 件。",
    "{s1}-{p1} 只剩 {d1} 天量，今早补 {q1} 件。",
)
URGENT_NONE = (
    "今天没有掉到紧急线的，按建议补货的节奏走就行。",
    "紧急档今天空着，不用抢时间，按表补就好。",
)
EXPIRY = (
    "临期 {k} 种、约 {u} 件，能打折的抓紧出手。",
    "另外 {k} 种快到期，共约 {u} 件，尽早打折。",
    "还有 {k} 种临期、约 {u} 件，别压到报废。",
)
EXPIRY_NONE = (
    "今天没有临期货，这块可以放心。",
    "临期这块今天空的，不用安排促销。",
)
CONTEXT = (
    "顺便说一句：{lines}。",
    "对了，{lines}。",
)
CLOSING = (
    "其余 {n} 条建议在表里，{m} 条计划慢慢排，拿不准的问我。",
    "剩下 {n} 条建议、{m} 条计划都在表里，不急的照表走，有疑问喊我。",
    "其余 {n} 条建议在表里，另外 {m} 条计划补货慢慢排，拿不准的问我。",
)
# 背景段出现时改用紧凑句式，保证整段仍落在 80~150 字内。
GREETING_PRESS_COMPACT = "老板早，今天 {n} 条紧急。"
GREETING_CALM_COMPACT = "老板早，今天还算稳。"
CLOSING_COMPACT = "其余 {n} 条建议在表里，拿不准的问我。"


def _pick(variants: tuple[str, ...], date_key: str, section: str) -> str:
    """同一天同一段固定选同一个变体；用 md5 而不是内置 hash（后者每次进程随机）。"""
    digest = hashlib.md5(f"{date_key}|{section}".encode("utf-8")).hexdigest()
    return variants[int(digest, 16) % len(variants)]


def fmt_days(days: float | None) -> str:
    """可售天数保留 1 位小数；算不出来的说“说不准”，不猜。"""
    if days is None:
        return "说不准"
    if float(days) < 0.05:
        return "不到 0.1"
    return f"{float(days):.1f}"


def fmt_qty(qty: int) -> str:
    return f"{int(qty)}"


def fmt_units(units: float) -> str:
    """临期件数保留 2 位小数（与报告里的 147.42 件一致，不做整数约化）。"""
    return f"{float(units):,.2f}"


def date_label(day: Date) -> str:
    return f"{day.month} 月 {day.day} 日 · {WEEKDAYS[day.weekday()]}"


def render_template(data: dict, day: Date | None = None) -> str:
    """把 build_brief_data() 的原始数字拼成 80~150 字的五段式简报。"""
    if day is None:
        day = pd.Timestamp(data["snapshot_date"]).date()
    date_key = day.isoformat()

    urgent_n = int(data["urgent_count"])
    urgent_items = data["urgent_list"]
    has_context = bool(data["context_lines"])

    if urgent_n > 0:
        greeting = (
            GREETING_PRESS_COMPACT.format(n=urgent_n)
            if has_context
            else _pick(GREETING_PRESS, date_key, "greeting").format(n=urgent_n)
        )
    else:
        greeting = (
            GREETING_CALM_COMPACT
            if has_context
            else _pick(GREETING_CALM, date_key, "greeting")
        )

    if len(urgent_items) >= 2:
        first, second = urgent_items[0], urgent_items[1]
        urgent_text = _pick(URGENT_TWO, date_key, "urgent").format(
            s1=first["store_id"], p1=first["product_id"],
            d1=fmt_days(first["sellable_days"]), q1=fmt_qty(first["suggest_qty"]),
            s2=second["store_id"], p2=second["product_id"],
            d2=fmt_days(second["sellable_days"]), q2=fmt_qty(second["suggest_qty"]),
        )
    elif len(urgent_items) == 1:
        first = urgent_items[0]
        urgent_text = _pick(URGENT_ONE, date_key, "urgent").format(
            s1=first["store_id"], p1=first["product_id"],
            d1=fmt_days(first["sellable_days"]), q1=fmt_qty(first["suggest_qty"]),
        )
    else:
        urgent_text = _pick(URGENT_NONE, date_key, "urgent")

    if data["near_expiry_kinds"] > 0:
        expiry_text = _pick(EXPIRY, date_key, "expiry").format(
            k=int(data["near_expiry_kinds"]),
            u=fmt_units(data["near_expiry_units"]),
        )
    else:
        expiry_text = _pick(EXPIRY_NONE, date_key, "expiry")

    paragraphs = [greeting, urgent_text, expiry_text]

    if data["context_lines"]:
        context_text = _pick(CONTEXT, date_key, "context").format(
            lines="；".join(data["context_lines"])
        )
        paragraphs.append(context_text)

    closing = (
        CLOSING_COMPACT.format(n=int(data["suggest_total"]))
        if has_context
        else _pick(CLOSING, date_key, "closing").format(
            n=int(data["suggest_total"]), m=int(data["plan_total"])
        )
    )
    paragraphs.append(closing)

    body = "".join(paragraphs)
    return f"{date_label(day)}\n\n{body}"


# ---------------------------------------------------------------------------
# 3. LLM 润色层（可选，默认 template；校验不过一律回退模板稿）
# ---------------------------------------------------------------------------
SYSTEM_PROMPT = (
    "你是「智链参谋」生鲜补货看板的早间简报润色助手。"
    "只允许调整措辞和语序，数字与商品名一字不改。"
    "必须原样保留所有阿拉伯数字、小数、店号、SKU 号、单位；"
    "不得新增、删除、约化、四舍五入或改写任何数字，不得增加新事实或新建议。"
    "只输出润色后的简报正文，不要解释，不要 Markdown，不要加引号。"
)

_NUM_RE = re.compile(r"\d+(?:\.\d+)?")
_PAIR_RE = re.compile(r"(\d+)\s*[-－—]\s*(\d+)")
_ASSOC_RE = re.compile(r"(?<!\d)(\d+)\s*店[^。；，,\n]{0,12}?(\d+)\s*号")
# 只认"数量词 + 量词"形态的中文数字，避免"一律/一旦"这类词被误当数字
_CN_NUM_RE = re.compile(r"([零一二三四五六七八九十百千两]{1,4})(?=[件种条天个家店号箱包])")


def _norm_number(raw: str) -> str:
    """把 6 与 6.0 视为同一个数；其余原样保留小数位。"""
    try:
        value = float(raw)
    except ValueError:
        return raw
    if value.is_integer():
        return str(int(value))
    return f"{value:.6f}".rstrip("0").rstrip(".")


def extract_numbers(text: str) -> Counter:
    """数字多重集（含阿拉伯数字与量词前的中文数字）。

    用多重集而非集合：避免"把两个 6 换成 8 和 4"这类换序蒙过去；
    纳入中文数字：避免"补十件"绕过校验。
    """
    text = text or ""
    counter: Counter = Counter(_norm_number(x) for x in _NUM_RE.findall(text))
    for raw in _CN_NUM_RE.findall(text):
        value = _cn_to_number(raw)
        if value is not None:
            counter[_norm_number(str(value))] += 1
    return counter


def _cn_to_number(raw: str) -> int | None:
    """把 零/一/两/十/百 组成的数量词转成整数；无法解析返回 None。"""
    digits = {
        "零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
        "五": 5, "六": 6, "七": 7, "八": 8, "九": 9,
    }
    units = {"十": 10, "百": 100, "千": 1000}
    if not raw:
        return None
    total, current = 0, 0
    for ch in raw:
        if ch in digits:
            current = digits[ch]
        elif ch in units:
            total += (current or 1) * units[ch]
            current = 0
        else:
            return None
    return total + current


def extract_pairs(text: str) -> set[tuple[int, int]]:
    return {(int(a), int(b)) for a, b in _PAIR_RE.findall(text or "")}


def _pair_anchors(text: str) -> list[tuple[int, int, int]]:
    """文本中所有"店号-SKU"锚点（含 145-802 与 145 店…802 号两种写法），按位置排序。"""
    found: list[tuple[int, int, int]] = []
    for m in _PAIR_RE.finditer(text):
        found.append((m.start(), int(m.group(1)), int(m.group(2))))
    for m in _ASSOC_RE.finditer(text):
        found.append((m.start(), int(m.group(1)), int(m.group(2))))
    found.sort()
    dedup: list[tuple[int, int, int]] = []
    for item in found:
        if not dedup or item[0] != dedup[-1][0]:
            dedup.append(item)
    return dedup


def number_manifest(data: dict) -> dict:
    """发给 LLM 的数字清单；与模板渲染用同一套格式化函数，避免两边对不上。"""
    return {
        "紧急条数": int(data["urgent_count"]),
        "紧急条目": [
            {
                "店号": int(x["store_id"]),
                "SKU": int(x["product_id"]),
                "建议量": fmt_qty(x["suggest_qty"]),
                "可售天数": fmt_days(x["sellable_days"]),
            }
            for x in data["urgent_list"]
        ],
        "临期": {
            "种类": int(data["near_expiry_kinds"]),
            "件数": fmt_units(data["near_expiry_units"]),
        },
        "建议补货条数": int(data["suggest_total"]),
        "计划补货条数": int(data["plan_total"]),
    }


def _pairs_preserved(template: str, polished: str, data: dict) -> tuple[bool, str]:
    """店号-SKU 组合校验：显式 145-802 形式与“145 店 … 802 号”关联都必须保住。"""
    expected = extract_pairs(template)
    got = extract_pairs(polished)
    if expected and got != expected:
        return False, f"店号-SKU 组合变了：{sorted(expected)} -> {sorted(got)}"
    for item in data["urgent_list"]:
        store, sku = int(item["store_id"]), int(item["product_id"])
        hyphen = re.search(rf"(?<!\d){store}\s*[-－—]\s*{sku}(?!\d)", polished)
        association = re.search(
            rf"(?<!\d){store}\s*店[^。；，,\n]{{0,12}}?{sku}\s*号", polished
        )
        if not (hyphen or association):
            return False, f"店号-SKU 关联丢失：{store} 店 {sku} 号"
        # 数字与 SKU 的绑定校验：把文本按"出现顺序"切成每段 = 该 SKU 到下一个 SKU 之间，
        # 要求该段里出现它的建议量。防止两个 SKU 的补货量互换后整体数字多重集仍"对得上"。
        qty = _norm_number(str(item["suggest_qty"]))
        anchors = _pair_anchors(polished)
        bound = False
        for idx, (start, a_store, a_sku) in enumerate(anchors):
            if (a_store, a_sku) != (store, sku):
                continue
            start = anchors[idx][0]
            end = anchors[idx + 1][0] if idx + 1 < len(anchors) else len(polished)
            segment = polished[start:end]
            prefix = polished[max(0, start - 20) : start]
            if qty in extract_numbers(segment) or qty in extract_numbers(prefix):
                bound = True
                break
        if not bound:
            return False, f"店号-SKU 与建议量不再对应：{store}-{sku} 附近找不到 {qty}"
    return True, ""


def validate_polish(template: str, polished: str, data: dict) -> tuple[bool, str]:
    """润色稿校验：数字集合 + 店号-SKU 组合 + 非空。任何一项不过都回退。"""
    if not (polished or "").strip():
        return False, "润色稿为空"
    tpl_numbers = extract_numbers(template)
    out_numbers = extract_numbers(polished)
    if tpl_numbers != out_numbers:
        missing = sorted((tpl_numbers - out_numbers).elements())
        extra = sorted((out_numbers - tpl_numbers).elements())
        return False, f"数字多重集不一致：缺 {missing}，多 {extra}"
    return _pairs_preserved(template, polished, data)


def _streamlit_secret(name: str) -> str:
    try:
        import streamlit as st

        # 本地没有 secrets.toml 时，st.secrets.get() 会先渲染红色错误框；
        # load_if_toml_exists() 只返回 False，不弹错。
        if not st.secrets.load_if_toml_exists():
            return ""
        value = st.secrets.get(name, "")
        return str(value).strip() if value is not None else ""
    except Exception:
        return ""


def _load_api_key() -> str:
    """DEEPSEEK_API_KEY：环境变量 > Streamlit secrets > 本地 .env。"""
    key = os.environ.get("DEEPSEEK_API_KEY", "").strip() or _streamlit_secret(
        "DEEPSEEK_API_KEY"
    )
    if key:
        return key
    env_path = BASE_DIR / ".env"
    if not env_path.exists():
        return ""
    try:
        for raw in env_path.read_text(encoding="utf-8-sig").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            name, _, value = line.partition("=")
            if name.strip() == "DEEPSEEK_API_KEY":
                return value.strip().strip('"').strip("'")
    except OSError:
        return ""
    return ""


def _call_deepseek(messages: list[dict], timeout: float) -> str:
    import requests  # 延迟导入：template 模式不依赖 requests / 网络

    key = _load_api_key()
    if not key:
        raise RuntimeError("未配置 DEEPSEEK_API_KEY")
    resp = requests.post(
        API_URL,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json={
            "model": MODEL_NAME,
            "messages": messages,
            "temperature": 0.3,
            "max_tokens": 400,
            "stream": False,
        },
        timeout=timeout,
    )
    if resp.status_code >= 400:
        raise RuntimeError(f"HTTP {resp.status_code}")
    data = resp.json()
    choices = data.get("choices") or []
    if not choices:
        raise RuntimeError("响应缺少 choices")
    return str((choices[0].get("message") or {}).get("content") or "")


def polish_with_llm(
    template_body: str,
    data: dict,
    timeout: float = DEFAULT_TIMEOUT,
    completer=None,
) -> tuple[str, dict]:
    """返回 (最终正文, 元信息)。润色失败/校验不过时正文就是模板稿。"""
    meta = {"mode": "llm", "polished": False, "fallback": True, "reason": None}
    completer = completer or _call_deepseek
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                "模板稿：\n" + template_body
                + "\n\n数字清单（必须原样保留）：\n"
                + json.dumps(number_manifest(data), ensure_ascii=False)
            ),
        },
    ]
    try:
        polished = completer(messages, timeout).strip()
    except Exception as exc:  # 网络/超时/无密钥，一律回退
        meta["reason"] = f"LLM 调用失败：{type(exc).__name__}: {exc}"
        logger.warning("daily_brief 回退模板：%s", meta["reason"])
        return template_body, meta

    ok, reason = validate_polish(template_body, polished, data)
    if not ok:
        meta["reason"] = reason
        logger.warning("daily_brief 回退模板：%s", reason)
        return template_body, meta

    meta.update({"polished": True, "fallback": False})
    return polished, meta


# ---------------------------------------------------------------------------
# 4. 总入口
# ---------------------------------------------------------------------------
def build_brief(
    date: str | Date | None = None,
    mode: str | None = None,
    data: dict | None = None,
    output_dir: Path | None = None,
    completer=None,
    timeout: float | None = None,
) -> dict:
    """算数 -> 模板 -> （可选）LLM 润色，返回 text / data / mode / fallback 元信息。"""
    if mode is None:
        mode = load_brief_config()["daily_brief"]["mode"]
    mode = "llm" if str(mode).lower() == "llm" else "template"

    if data is None:
        data = build_brief_data(
            output_dir=output_dir,
            snapshot_date=date if isinstance(date, str) else None,
        )

    if isinstance(date, str):
        day = pd.Timestamp(date).date()
    elif isinstance(date, Date):
        day = date
    else:
        day = pd.Timestamp(data["snapshot_date"]).date()

    template_text = render_template(data, day)
    header, _, body = template_text.partition("\n\n")

    if mode == "template":
        return {
            "text": template_text,
            "data": data,
            "day": day,
            "mode": "template",
            "fallback": False,
            "fallback_reason": None,
            "template_body": body,
        }

    if timeout is None:
        timeout = load_brief_config()["daily_brief"]["timeout_seconds"]
    polished, meta = polish_with_llm(body, data, timeout=timeout, completer=completer)
    return {
        "text": f"{header}\n\n{polished}",
        "data": data,
        "day": day,
        "mode": "llm",
        "fallback": meta["fallback"],
        "fallback_reason": meta["reason"],
        "template_body": body,
    }


def render_brief(date: str | Date | None = None, mode: str | None = None) -> str:
    """看板调用：只返回正文；文件缺失等异常返回空串，绝不让页面崩。"""
    try:
        return build_brief(date=date, mode=mode)["text"]
    except Exception as exc:  # noqa: BLE001 - 看板兜底
        logger.warning("daily_brief 生成失败：%s", exc)
        return ""


def main() -> int:
    """自检：打印快照日与 3 个日期的完整样例（模板模式，断网可跑）。"""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    data = build_brief_data()
    print("数据字段：")
    print(json.dumps(data, ensure_ascii=False, indent=2))
    for offset in (0, 1, 2):
        day = pd.Timestamp(data["snapshot_date"]) + pd.Timedelta(days=offset)
        brief = build_brief(date=day.date().isoformat(), mode="template", data=data)
        body = brief["template_body"]
        print(f"\n===== {brief['text']} =====")
        print(f"正文字数：{len(body)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
