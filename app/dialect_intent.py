"""方言意图解析 + 替换回退检查（第 9 步）。

这一层做两件事：
1. 把一句话解析成结构化意图 JSON（intent / store_id / product_id / qty / ratio / confidence）；
2. 词表归一化之后做回退检查：原文与归一化文本各解析一次，若意图不一致且
   归一化版本的置信度没有提高，就回退用原文，避免“词表把句子改坏”。

不做的事：
- 不判断用户说的是哪门方言（方言种类不交给模型）；
- 不调用业务工具，工具调用仍由 agent.run_turn 负责。

用法：
    from dialect_intent import parse_intent, resolve_text, validate_intent
"""

from __future__ import annotations

import json
import re
from typing import Any

import requests

API_URL = "https://api.deepseek.com/chat/completions"
MODEL_NAME = "deepseek-chat"
REQUEST_TIMEOUT = (5, 30)
MAX_TOKENS = 300

INTENT_VALUES = [
    "get_forecast",            # 查未来销量/预测
    "get_inventory_status",    # 查库存、临期
    "recommend_replenishment",  # 该补多少、补货建议
    "list_risk_skus",          # 哪些货要补、列清单
    "simulate_what_if",        # 不按建议/按比例/订货量变化会怎样
    "out_of_scope",            # 与补货无关
    "chitchat",                # 寒暄、感慨，没有明确诉求
    "unknown",                 # 太模糊、听不懂
]

INTENT_PROMPT = """你是补货助手的意图解析器。只输出一个 JSON 对象，不要解释、不要 Markdown。

可选 intent：
- get_forecast：问未来几天/几周卖多少、预测销量
- get_inventory_status：问库存、在途、临期
- recommend_replenishment：问该补多少、补货建议
- list_risk_skus：问哪些货要补、要一份风险清单
- simulate_what_if：问不订货/只订一半/按建议/1.5 倍订会怎样
- out_of_scope：与补货、库存无关
- chitchat：寒暄、天气、感慨，没有明确业务诉求
- unknown：听不出要做什么

槽位规则（非常重要）：
1. store_id、product_id 必须是用户句子里明确出现的数字，禁止猜测、禁止用默认值；
   没出现就填 null。只有店号没有商品号（或反过来）同样填 null。
2. ratio 是相对“按建议订量”的下单比例：完全不订/不下单=0，一半=0.5，
   按建议=1，一点五倍/1.5 倍=1.5，两倍=2。没提比例填 null。
3. qty 是用户直接说的订货件数（例如“订 20 件”），没提填 null。
4. confidence 是 0 到 1 的小数，表示你对 intent 判断的把握。

输出字段固定为：
{"intent": "...", "store_id": null, "product_id": null, "qty": null, "ratio": null, "confidence": 0.0}
"""

_SLOT_FIELDS = ("store_id", "product_id", "qty", "ratio")


class IntentError(RuntimeError):
    """意图解析失败（网络、HTTP、JSON 格式）。"""


def _call_json(prompt: str, text: str, api_key: str) -> dict[str, Any]:
    payload = {
        "model": MODEL_NAME,
        "messages": [
            {"role": "system", "content": prompt},
            {"role": "user", "content": text},
        ],
        "temperature": 0,
        "max_tokens": MAX_TOKENS,
        "stream": False,
        "response_format": {"type": "json_object"},
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    try:
        resp = requests.post(
            API_URL, headers=headers, json=payload, timeout=REQUEST_TIMEOUT
        )
    except requests.RequestException as exc:
        raise IntentError(f"意图解析请求失败：{type(exc).__name__}") from None

    if resp.status_code == 401:
        raise IntentError("意图解析鉴权失败（401），请检查 DEEPSEEK_API_KEY。")
    if resp.status_code == 402:
        raise IntentError("意图解析失败（402）：账户余额不足。")
    if resp.status_code == 429:
        raise IntentError("意图解析被限流（429），请稍后重试。")
    if resp.status_code >= 400:
        raise IntentError(f"意图解析失败（HTTP {resp.status_code}）。")

    try:
        data = resp.json()
        content = data["choices"][0]["message"]["content"]
    except (ValueError, KeyError, IndexError, TypeError):
        raise IntentError("意图解析响应格式异常。") from None
    return _extract_json(content)


def _extract_json(content: str) -> dict[str, Any]:
    content = (content or "").strip()
    if content.startswith("```"):
        content = re.sub(r"^```[a-zA-Z]*\s*", "", content)
        content = re.sub(r"\s*```$", "", content).strip()
    try:
        obj = json.loads(content)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", content, re.S)
        if not match:
            raise IntentError(f"意图解析返回的不是 JSON：{content[:80]}") from None
        try:
            obj = json.loads(match.group(0))
        except json.JSONDecodeError:
            raise IntentError(f"意图解析返回的不是 JSON：{content[:80]}") from None
    if not isinstance(obj, dict):
        raise IntentError("意图解析返回值必须是 JSON 对象")
    return obj


def _as_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        num = float(value)
    except (TypeError, ValueError):
        return None
    if num != num or num in (float("inf"), float("-inf")):  # NaN / inf
        return None
    return int(round(num))


def _as_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        num = float(value)
    except (TypeError, ValueError):
        return None
    if num != num or num in (float("inf"), float("-inf")):
        return None
    return num


def _coerce(obj: dict[str, Any]) -> dict[str, Any]:
    intent = str(obj.get("intent") or "unknown").strip()
    if intent not in INTENT_VALUES:
        intent = "unknown"
    confidence = _as_float(obj.get("confidence"))
    if confidence is None:
        confidence = 0.0
    confidence = min(max(confidence, 0.0), 1.0)
    return {
        "intent": intent,
        "store_id": _as_int(obj.get("store_id")),
        "product_id": _as_int(obj.get("product_id")),
        "qty": _as_int(obj.get("qty")),
        "ratio": _as_float(obj.get("ratio")),
        "confidence": confidence,
    }


def parse_intent(
    text: str,
    api_key: str,
    cache: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """解析一句话的意图。同一文本在进程内只调一次模型（temperature=0）。"""
    text = (text or "").strip()
    if not text:
        return {
            "intent": "unknown",
            "store_id": None,
            "product_id": None,
            "qty": None,
            "ratio": None,
            "confidence": 0.0,
        }
    if cache is not None and text in cache:
        return dict(cache[text])
    result = _coerce(_call_json(INTENT_PROMPT, text, api_key))
    if cache is not None:
        cache[text] = dict(result)
    return result


# ---------------------------------------------------------------------------
# 必填参数校验（规则，不交给模型）
# ---------------------------------------------------------------------------
REQUIRED_SLOTS = {
    "get_forecast": ["store_id", "product_id"],
    "get_inventory_status": ["store_id", "product_id"],
    "recommend_replenishment": ["store_id", "product_id"],
    "simulate_what_if": ["store_id", "product_id", "ratio_or_qty"],
    "list_risk_skus": [],
    "chitchat": [],
    "out_of_scope": [],
    "unknown": [],
}

SLOT_CN = {
    "store_id": "店号（store_id）",
    "product_id": "商品号（product_id）",
    "ratio_or_qty": "下单比例或订货件数（ratio / qty）",
}

NO_TOOL_INTENTS = {"chitchat", "out_of_scope", "unknown"}


def missing_slots(intent: dict[str, Any]) -> list[str]:
    """按规则返回缺失的必填槽位；不猜、不用默认值。"""
    name = intent.get("intent")
    missing: list[str] = []
    for slot in REQUIRED_SLOTS.get(name, []):
        if slot == "ratio_or_qty":
            if intent.get("ratio") is None and intent.get("qty") is None:
                missing.append(slot)
        elif intent.get(slot) is None:
            missing.append(slot)
    return missing


def validate_intent(intent: dict[str, Any]) -> dict[str, Any]:
    """返回 {ok, missing, effective_intent}。

    ok=False 时调用方必须走反问分支，禁止调用工具。
    """
    name = intent.get("intent", "unknown")
    missing = missing_slots(intent)
    if missing:
        return {"ok": False, "missing": missing, "effective_intent": "clarify"}
    return {"ok": True, "missing": [], "effective_intent": name}


def clarify_reply(
    intent: dict[str, Any],
    missing: list[str] | None = None,
    dialect_name: str | None = None,
) -> str:
    """缺参数时的反问文本：先一句口语，再一行普通话口径。不含任何数字。"""
    name = intent.get("intent")
    missing = missing or missing_slots(intent)
    missing_cn = "、".join(SLOT_CN.get(x, x) for x in missing)

    if name == "chitchat":
        return (
            "「这话我听到了哈。」\n"
            "我这边只管补货和库存；要查货的话，把店号和商品号发我。"
        )
    if name == "out_of_scope":
        return (
            "「这个不归我管哈。」\n"
            "我只负责补货与库存问题，把店号和商品号发我就能查。"
        )
    if name == "unknown" or not missing:
        return (
            "「这句我没整明白。」\n"
            "请说清楚要查哪个店、哪个商品，或者问“哪些货要补”。"
        )

    topic = {
        "get_forecast": "查预测",
        "get_inventory_status": "查库存",
        "recommend_replenishment": "算补货",
        "simulate_what_if": "做推演",
    }.get(str(name), "接着办")
    return (
        f"「要{topic}可以，但你还差{missing_cn}没说。」\n"
        f"请先提供{missing_cn}，我再给结果；缺任何一项我都不能猜。"
    )


# ---------------------------------------------------------------------------
# 替换后回退检查
# ---------------------------------------------------------------------------
def resolve_text(
    original: str,
    normalized: str,
    api_key: str,
    cache: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """比较原文与归一化文本的意图，必要时回退。

    返回：
        text            最终交给主模型/工具链的文本
        rolled_back     是否回退
        reason          回退或采用归一化的原因
        intent          最终采用的意图
        intent_original / intent_normalized
    """
    original = original or ""
    normalized = normalized or original
    info: dict[str, Any] = {
        "text": normalized,
        "rolled_back": False,
        "reason": "",
        "intent": None,
        "intent_original": None,
        "intent_normalized": None,
    }

    if normalized == original:
        info["reason"] = "no_change"
        info["intent"] = parse_intent(original, api_key, cache)
        info["intent_original"] = info["intent"]
        info["intent_normalized"] = info["intent"]
        return info

    a = parse_intent(original, api_key, cache)
    b = parse_intent(normalized, api_key, cache)
    info["intent_original"] = a
    info["intent_normalized"] = b

    if a["intent"] == b["intent"]:
        info["intent"] = b
        info["reason"] = (
            f"意图一致（{b['intent']}），采用归一化文本"
        )
        return info

    if b["confidence"] > a["confidence"]:
        info["intent"] = b
        info["reason"] = (
            f"意图改变（{a['intent']}→{b['intent']}）但置信度提高"
            f"（{a['confidence']:.2f}→{b['confidence']:.2f}），采用归一化文本"
        )
        return info

    info["text"] = original
    info["rolled_back"] = True
    info["intent"] = a
    info["reason"] = (
        f"意图改变（{a['intent']}→{b['intent']}）且置信度未提高"
        f"（{a['confidence']:.2f}→{b['confidence']:.2f}），回退原文"
    )
    return info
