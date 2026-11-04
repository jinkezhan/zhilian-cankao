"""演示用商品名称映射（生鲜品类）。

原始生鲜数据已脱敏，没有官方品名。为了让演示页面像真实门店系统，
这里把补货快照中出现的 product_id 全部映射为具体的中文生鲜名称；
未出现在映射里的 product_id 会从下面的真实品名池中稳定分配一个名字，
不会再出现「生鲜商品」「精品蔬菜-300」这类占位符。

当前补货快照有 200 条「门店 × SKU」序列，去重后是 50 个 product_id；
50 个 product_id 已全部显式映射，因此 200 行表格都有具体品名。
"""

from __future__ import annotations

import re

# product_id → 具体生鲜品名。补货快照里出现的 50 个 product_id 已全覆盖。
PRODUCT_NAMES_BY_ID: dict[int, str] = {
    4: "红富士苹果",
    6: "海南香蕉",
    19: "上海青",
    26: "本地小白菜",
    41: "黑猪梅花肉",
    70: "鲜活基围虾",
    71: "鲜活鲈鱼",
    76: "农家土鸡蛋",
    104: "沙地土豆",
    116: "水果黄瓜",
    117: "紫长茄",
    118: "螺丝椒",
    119: "西兰花",
    122: "紫甘蓝",
    127: "山东大葱",
    129: "鲜香菇",
    190: "赣南脐橙",
    191: "阳光玫瑰葡萄",
    207: "麒麟西瓜",
    215: "水蜜桃",
    267: "海南小台芒",
    290: "红心火龙果",
    291: "泰国金枕榴莲",
    292: "山东大樱桃",
    296: "有机菠菜",
    300: "水东芥菜",
    381: "紫皮洋葱",
    422: "黑猪里脊",
    467: "土猪前腿肉",
    481: "精品牛腩",
    486: "雪花肥牛卷",
    500: "内蒙古羔羊排",
    554: "三黄鸡整鸡",
    580: "老鸭",
    589: "大闸蟹",
    596: "冰鲜三文鱼",
    600: "鲜活花蛤",
    631: "冷冻带鱼段",
    635: "嫩豆腐",
    638: "老豆腐",
    666: "金针菇",
    691: "杏鲍菇",
    740: "东北大米",
    769: "咸鸭蛋",
    775: "鲜牛奶",
    783: "腐竹",
    802: "黑猪五花肉",
    810: "鲜活小龙虾",
    834: "新鲜鸡胸肉",
    835: "澳洲牛腱子",
}

# 未在显式映射里的 product_id，从这些真实品名中按编号稳定分配。
FALLBACK_NAMES: tuple[str, ...] = (
    # 蔬菜
    "菜心", "芥蓝", "娃娃菜", "空心菜", "苋菜", "茼蒿", "韭菜", "香菜",
    "小葱", "生姜", "大蒜", "小米辣", "线椒", "彩椒", "西红柿", "胡萝卜",
    "白萝卜", "青萝卜", "莴笋", "芦笋", "秋葵", "毛豆", "豌豆苗", "甜豆",
    "荷兰豆", "四季豆", "豇豆", "西葫芦", "苦瓜", "丝瓜", "冬瓜", "南瓜",
    "莲藕", "山药", "芋头", "鲜玉米", "白玉菇", "蟹味菇", "海带结",
    # 水果
    "砂糖橘", "沃柑", "库尔勒香梨", "砀山酥梨", "徐香猕猴桃", "红提",
    "夏黑葡萄", "巨峰葡萄", "蓝莓", "草莓", "圣女果", "哈密瓜", "网纹瓜",
    "菠萝", "凤梨", "柚子", "柠檬", "青提", "杨梅", "荔枝", "龙眼",
    "枇杷", "李子", "杏", "石榴", "柿子", "无花果", "百香果", "牛油果",
    "椰子",
    # 肉禽
    "土猪后腿肉", "猪肋排", "猪筒骨", "猪蹄", "猪肝", "猪腰", "牛里脊",
    "牛霖肉", "牛尾", "羊腿肉", "羊蝎子", "鸡翅中", "鸡腿", "鸭腿",
    "鸭翅", "鹌鹑", "乳鸽",
    # 水产
    "鲜活多宝鱼", "鲜活鲫鱼", "鲜活草鱼", "鲜活黄骨鱼", "冰鲜鳕鱼",
    "冰鲜金枪鱼", "鲜活生蚝", "鲜活扇贝", "鲜鱿鱼", "墨鱼仔", "虾仁",
    "蟹柳",
    # 蛋奶豆制品与干货
    "皮蛋", "酸奶", "豆皮", "千张", "豆干", "油豆腐", "五常大米", "小米",
    "糯米", "红豆", "绿豆", "花生米", "木耳", "银耳", "红枣", "枸杞",
    "莲子", "百合",
)

# 完整品名池：50 个显式映射 + 兜底品名。保证任何 product_id 都能拿到具体名字。
NAME_POOL: tuple[str, ...] = tuple(PRODUCT_NAMES_BY_ID.values()) + FALLBACK_NAMES

# 仅在 product_id 不是数字（脏数据）时使用，正常数据不会命中。
INVALID_PRODUCT_NAME = "未知商品"


def _as_int(value: object) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def product_name(store_id: object, product_id: object) -> str:
    """返回具体品名；同一 product_id 在所有门店保持同名。"""
    del store_id  # 品名只由 product_id 决定，门店只参与展示后缀
    pid = _as_int(product_id)
    if pid is None:
        return INVALID_PRODUCT_NAME
    curated = PRODUCT_NAMES_BY_ID.get(pid)
    if curated:
        return curated
    # 未在 CSV 里的编号：按 product_id 顺序从品名池分配，稳定且可复现。
    return NAME_POOL[pid % len(NAME_POOL)]


def sku_display(store_id: object, product_id: object) -> str:
    """表格首列展示格式：黑猪五花肉（145-802）。"""
    name = product_name(store_id, product_id)
    store = _as_int(store_id)
    pid = _as_int(product_id)
    if store is None or pid is None:
        return f"{name}（{store_id}-{product_id}）"
    return f"{name}（{store}-{pid}）"


# 回复文本里的 SKU 编号本地化：把 “SKU 145-802” 统一改写成
# “黑猪五花肉（145-802）”，保证 AI 问答区不再出现裸 SKU。
_NAME_ALT = "|".join(
    re.escape(name) for name in sorted(set(NAME_POOL), key=len, reverse=True)
)
_SKU_TOKEN_RE = re.compile(r"SKU\s*(\d{1,5})\s*-\s*(\d{1,5})", re.IGNORECASE)
_NAME_THEN_SKU_RE = re.compile(
    rf"(?P<name>{_NAME_ALT})\s*[（(]?\s*SKU\s*"
    rf"(?P<store>\d{{1,5}})\s*-\s*(?P<pid>\d{{1,5}})\s*[）)]?",
    re.IGNORECASE,
)
_SKU_THEN_NAME_RE = re.compile(
    rf"SKU\s*(?P<store>\d{{1,5}})\s*-\s*(?P<pid>\d{{1,5}})\s*"
    rf"[（(]\s*(?P<name>{_NAME_ALT})\s*[）)]",
    re.IGNORECASE,
)


def localize_sku_text(text: object) -> str:
    """把 “SKU 145-802” 统一换成 “黑猪五花肉（145-802）”。"""
    value = str(text or "")
    if not value:
        return ""

    def _name_then_sku(match: "re.Match[str]") -> str:
        return sku_display(match.group("store"), match.group("pid"))

    def _sku_then_name(match: "re.Match[str]") -> str:
        return sku_display(match.group("store"), match.group("pid"))

    def _bare_sku(match: "re.Match[str]") -> str:
        return sku_display(match.group(1), match.group(2))

    value = _SKU_THEN_NAME_RE.sub(_sku_then_name, value)
    value = _NAME_THEN_SKU_RE.sub(_name_then_sku, value)
    return _SKU_TOKEN_RE.sub(_bare_sku, value)


def product_name_by_label(label: str) -> str:
    """按 “145-802” / “SKU 145-802” / “黑猪五花肉（145-802）” 取品名。"""
    text = str(label).strip()
    if "（" in text and text.endswith("）"):
        return text.split("（", 1)[0].strip()
    text = text.replace("SKU ", "").strip()
    parts = text.split("-")
    if len(parts) == 2:
        return product_name(parts[0], parts[1])
    return text or INVALID_PRODUCT_NAME


def agent_prompt_block() -> str:
    """给 Agent 的系统提示词：具体品名 → product_id 映射与解析纪律。"""
    lines = [
        "【商品名称 → product_id 映射（演示用真实品名）】",
        "界面和对话里使用下列具体生鲜品名，不再使用「生鲜商品」等占位名。",
        "用户说品名时，先按本表换算成 product_id；门店号（store_id）必须来自"
        "用户原话或上下文，不能猜。查询时把 store_id 和 product_id 一起传给工具。",
    ]
    for pid, name in sorted(PRODUCT_NAMES_BY_ID.items()):
        lines.append(f"- {name} = product_id {pid}")
    lines.append(
        "用户只说品名、没说门店时，先说明该品名对应 product_id，并请用户补充门店"
        "（例如「黑猪五花肉（145-802）」中的 145 就是门店号），不要默认门店。"
    )
    lines.append(
        "数据中未列在上面的编号，界面会从真实品名池分配一个具体名字；"
        "如果你无法确定用户说的品名对应哪个 product_id，请让用户提供完整 SKU"
        "（店号-商品号），不要编造映射或库存数字。"
    )
    lines.append(
        "回答时一律用「具体品名（店号-商品号）」指代商品，"
        "例如：黑猪五花肉（145-802）建议补货 13 件。"
    )
    lines.append(
        "不要只输出「SKU 145-802」或裸的「145-802」；工具返回里的 sku_label 是 "
        "SKU 格式，你必须在回答里换成上面的具体品名。"
    )
    return "\n".join(lines)
