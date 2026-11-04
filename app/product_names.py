"""演示用商品名称映射（按“店号-商品号”完整 SKU 标签）。

生鲜数据已脱敏，没有官方品名；这里给前 20 个核心 SKU 配上蔬菜/水果名，
仅用于界面展示和 Agent 意图解析。未命中的 SKU 统一显示“生鲜商品”，
不猜测、不编造。
"""

from __future__ import annotations

FALLBACK_PRODUCT_NAME = "生鲜商品"

# 键是界面上看到的 “店号-商品号”，例如 145-802。
PRODUCT_NAMES: dict[str, str] = {
    "145-802": "西红柿",
    "781-802": "土豆",
    "3-834": "黄瓜",
    "18-19": "胡萝卜",
    "18-117": "大白菜",
    "18-127": "青椒",
    "18-207": "茄子",
    "18-300": "西兰花",
    "18-500": "菠菜",
    "18-554": "生菜",
    "18-783": "芹菜",
    "20-835": "南瓜",
    "21-834": "冬瓜",
    "23-834": "洋葱",
    "33-589": "大蒜",
    "46-4": "苹果",
    "46-296": "香蕉",
    "50-267": "橙子",
    "57-290": "葡萄",
    "66-589": "西瓜",
}


def product_name(store_id: object, product_id: object) -> str:
    """按店号 + 商品号取展示名；找不到返回“生鲜商品”。"""
    try:
        key = f"{int(store_id)}-{int(product_id)}"
    except (TypeError, ValueError):
        key = f"{store_id}-{product_id}"
    return PRODUCT_NAMES.get(key, FALLBACK_PRODUCT_NAME)


def product_name_by_label(label: str) -> str:
    """按 “145-802” 这种标签取展示名。"""
    return PRODUCT_NAMES.get(str(label), FALLBACK_PRODUCT_NAME)


def agent_prompt_block() -> str:
    """给 Agent 的系统提示词：商品名 → SKU 映射与解析纪律。"""
    lines = [
        "【商品名称 → SKU 映射】",
        "用户只给商品名时，必须先在下列映射中换算成 SKU，再调用工具查询；"
        "不要反问用户要 SKU，也不要编造库存数字。",
    ]
    for sku, name in PRODUCT_NAMES.items():
        lines.append(f"- {name} = SKU {sku}")
    lines.append(
        "如果商品名不在映射内，请明确说“暂未映射该商品名”，"
        "并请用户提供 SKU（格式：店号-商品号），不要猜测对应关系。"
    )
    return "\n".join(lines)
