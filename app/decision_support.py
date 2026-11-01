"""决策支持：三方案对比、决策留痕与偏差校准（第 13 / 15 步）。

设计约束（重要）：
1. 本模块只做纯计算与文件读写，不 import streamlit，便于脱离界面单测。
2. 三方案沿用 ``decision()`` 的参数语义——只动效期上限倍数 ``cap_multiplier``，
   不动触发线 ROP_P90。基准版的订货量必须与 04 表 ``suggest_qty`` 逐行相等，
   否则说明口径已经漂移，``verify_base_against_snapshot()`` 会报错。
3. 偏差校准**不修改**主配置 ``config/freshretail_inventory.yaml``。
   主配置是所有交付表的生成依据，改它会立刻让 5 张交付表和 4 个 α 基线的
   MD5 全部失效。这里只写 ``config/freshretail_inventory_overrides.yaml``，
   并明确标注"需重跑脚本才生效"，页面上的数字在重跑前保持不变。
"""

from __future__ import annotations

import math
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

EPS = 1e-9

# 三方案：以配置倍数为基准，上下各挪一档（0.1），正好落在 config 的
# cap_sweep_multipliers 网格上。**不要用固定的 0.7 当"收紧一档"**：
# 实测 5 天档 0.7 倍的成本是 1234.19，比 1.0 倍的 917.50 高 34.5%，
# 那是因为订太少导致缺货惩罚（5 元/件）盖过了报损节省。只能叫"收紧"，
# 不能暗示省钱。
SCHEME_SPECS: tuple[tuple[str, float, str], ...] = (
    ("收紧一档", -0.1, "效期上限比配置低一档：订货更少、报损更低，缺货更多"),
    ("当前配置", 0.0, "config 当前值，交付表 04 用的就是这一档"),
    ("放宽一档", 0.1, "效期上限比配置高一档：缺货更少，报损上升"),
)

DECISION_FIELDS: tuple[str, ...] = (
    "decided_at",
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
    "gap_qty",
    "gap_pct",
    "operator",
    "note",
)

PARAM_CHANGE_FIELDS: tuple[str, ...] = (
    "changed_at",
    "param",
    "old_value",
    "new_value",
    "basis",
    "evidence_n",
    "mean_gap_pct",
    "source_file",
)


def round_up_qty(qty: float, pack: int, min_qty: int) -> int:
    """缺口向上取整到包装倍数，并保证不低于最小订货量。

    与 ``app/freshretail_inventory.round_up_qty`` 逐字一致；单测里对拍过。
    """
    if qty <= EPS:
        return 0
    pack = max(int(pack), 1)
    rounded = math.ceil(qty / pack - EPS) * pack
    if rounded < min_qty:
        rounded = math.ceil(min_qty / pack) * pack
    return int(rounded)


def scheme_multipliers(base_multiplier: float) -> tuple[tuple[str, float, str], ...]:
    """以配置倍数为基准，上下各挪一档；基准版永远等于 config 当前值。"""
    base = float(base_multiplier)
    out = []
    for name, offset, note in SCHEME_SPECS:
        if abs(offset) < EPS:
            value = base
        else:
            value = round(min(max(base + offset, 0.5), 1.5), 2)
        out.append((name, float(value), note))
    return tuple(out)


def scheme_quantities(
    row: Mapping[str, Any],
    sellable: float,
    base_multiplier: float,
    pack: int = 1,
    min_qty: int = 1,
) -> dict[str, dict[str, Any]]:
    """按三个效期倍数分别算修正目标与订货量。

    口径与补货快照（04 表 ``build_snapshot``）一致，**这一点与 ``decision()``
    不同，务必注意**：

      - 闸门是**目标水位**，不是 ROP。快照里 ``on_hand >= target`` 记为"正常"档
        且订货量为 0；``on_hand`` 落在 ROP 之上但目标之下时（"计划补货"档）
        **仍然下单**。用 ROP 做闸门会让这 45 行全部错误地变成 0 件。
      - 上限：``min(target, multiplier × 保质期内可售量)``；
      - 订货：``(target_eff - on_hand)`` 向上取整，缺口 ≤ 0 时为 0。
    """
    on_hand = float(row["on_hand"])
    rop = float(row["rop_p90"])
    target = float(row["target_level"])
    sellable = max(float(sellable), 0.0)

    result: dict[str, dict[str, Any]] = {}
    for name, mult, note in scheme_multipliers(base_multiplier):
        cap = mult * sellable
        target_eff = min(target, cap)
        gap = target_eff - on_hand
        qty = round_up_qty(gap, pack, min_qty)
        result[name] = {
            "multiplier": mult,
            "cap": cap,
            "target_eff": target_eff,
            "gap": gap,
            "qty": qty,
            "below_rop": on_hand < rop,
            "binding": cap < target,  # 效期上限是否真的卡住了目标水位
            "note": note,
        }
    return result


def urgency_level(on_hand: float, rop: float, target: float, urgent_ratio: float = 0.5) -> str:
    """四档紧急度，与 ``freshretail_inventory.urgency_level`` 同口径。

    紧急度只用于排序与文案，**不参与订货量计算**——订货量的闸门是目标水位。
    """
    if on_hand < urgent_ratio * rop:
        return "紧急"
    if on_hand < rop:
        return "建议补货"
    if on_hand < target:
        return "计划补货"
    return "正常"


def verify_base_against_snapshot(
    rows: Iterable[Mapping[str, Any]],
    sellable_by_key: Mapping[tuple[int, int], float],
    base_multiplier: float,
    pack: int = 1,
    min_qty: int = 1,
) -> dict[str, Any]:
    """逐行核对"当前配置"是否等于补货快照的 suggest_qty。

    返回 {total, matched, mismatches:[...]}；界面在 matched != total 时降级为
    只展示推导链、不展示三方案，避免给出与交付表不一致的数字。
    """
    total = 0
    matched = 0
    mismatches: list[dict[str, Any]] = []
    for row in rows:
        key = (int(row["store_id"]), int(row["product_id"]))
        sellable = sellable_by_key.get(key)
        if sellable is None:
            continue
        total += 1
        calc = scheme_quantities(row, sellable, base_multiplier, pack, min_qty)["当前配置"]
        want = int(row["suggest_qty"])
        if calc["qty"] == want:
            matched += 1
        else:
            mismatches.append(
                {
                    "store_id": key[0],
                    "product_id": key[1],
                    "snapshot_qty": want,
                    "calculated_qty": calc["qty"],
                    "difference": calc["qty"] - want,
                }
            )
    return {"total": total, "matched": matched, "mismatches": mismatches}


def trace_steps(
    row: Mapping[str, Any],
    info: Mapping[str, Any],
    base_multiplier: float,
    lead_time: int,
    target_cover_days: int,
    urgent_ratio: float = 0.5,
) -> list[dict[str, Any]]:
    """把一次补货决策拆成四步可核对的推导链。

    措辞按真实口径写：ROP 只决定紧急度档位，订货量的闸门是目标水位。
    """
    on_hand = float(row["on_hand"])
    rop = float(row["rop_p90"])
    target = float(row["target_level"])
    qty = int(row["suggest_qty"])
    sellable = float(info["sellable"])
    cap = float(info["cap"])
    target_eff = float(info["target_eff"])
    tier = urgency_level(on_hand, rop, target, urgent_ratio)
    below_rop = on_hand < rop

    return [
        {
            "step": "1 · 紧急度分档（ROP 只用于分档）",
            "formula": (
                f"紧急：在库 < {urgent_ratio:.0%}×ROP ｜ 建议补货：在库 < ROP ｜ "
                "计划补货：在库 < 目标水位 ｜ 正常：在库 ≥ 目标水位"
            ),
            "detail": (
                f"在库 {on_hand:,.2f} 件，ROP_P90 {rop:,.2f} 件 → **{tier}**"
                + (
                    "（在库已高于 ROP，但还没到目标水位，所以仍要下单）"
                    if not below_rop and on_hand < target
                    else ""
                )
            ),
            "numbers": {"on_hand": on_hand, "rop_p90": rop, "urgency": tier},
        },
        {
            "step": "2 · 目标水位",
            "formula": f"P50 ×（提前期 {lead_time} 天 + 覆盖 {target_cover_days} 天）日累计",
            "detail": (
                f"目标水位 {target:,.2f} 件（不含效期约束，"
                f"末日之后按最后一个预测值平推）"
            ),
            "numbers": {"target_level": target},
        },
        {
            "step": "3 · 效期上限",
            "formula": "min(目标水位, 保质期内预计可售量 × 配置倍数)",
            "detail": (
                f"保质期内可售 {sellable:,.2f} 件 × 倍数 {base_multiplier:.2f} "
                f"= {cap:,.2f} 件 → 取 min 后目标 {target_eff:,.2f} 件"
                + ("（效期上限卡住了目标）" if cap < target else "（目标水位更低，效期未触发）")
            ),
            "numbers": {
                "sellable": sellable,
                "multiplier": base_multiplier,
                "cap": cap,
                "target_eff": target_eff,
            },
        },
        {
            "step": "4 · 订货量",
            "formula": "(修正目标 − 现有库存) 向上取整到包装倍数；缺口 ≤ 0 时不下单",
            "detail": (
                f"({target_eff:,.2f} − {on_hand:,.2f}) = {target_eff - on_hand:,.2f} "
                f"→ 订 **{qty}** 件"
                if target_eff - on_hand > EPS
                else f"修正目标 {target_eff:,.2f} 不高于在库 {on_hand:,.2f}，订 0 件"
            ),
            "numbers": {"gap": target_eff - on_hand, "qty": qty},
        },
    ]


# ---------------------------------------------------------------------------
# 第 15 步：决策留痕
# ---------------------------------------------------------------------------
def build_decision_rows(
    rows: Sequence[Mapping[str, Any]],
    snapshot: str,
    ratio: float,
    now: datetime | None = None,
    operator: str = "web",
    note: str = "",
) -> list[dict[str, Any]]:
    """把本次确认的整单比例展开成逐序列留痕记录。"""
    stamp = (now or datetime.now()).strftime("%Y-%m-%d %H:%M:%S")
    out: list[dict[str, Any]] = []
    for row in rows:
        suggest = int(row["suggest_qty"])
        # 不能用内置 round()：Python 3 是银行家舍入，round(4.5)=4 会让
        # 0.75 倍档的 6 件建议记成 4 件。这里按常规四舍五入。
        applied = int(math.floor(suggest * float(ratio) + 0.5))
        gap = applied - suggest
        out.append(
            {
                "decided_at": stamp,
                "snapshot_date": snapshot,
                "store_id": int(row["store_id"]),
                "product_id": int(row["product_id"]),
                "shelf_life_days": int(row["shelf_life_days"]),
                "urgency": str(row["urgency"]),
                "on_hand": round(float(row["on_hand"]), 4),
                "rop_p90": round(float(row["rop_p90"]), 4),
                "suggest_qty": suggest,
                "applied_ratio": float(ratio),
                "applied_qty": applied,
                "gap_qty": gap,
                "gap_pct": round(gap / suggest, 6) if suggest > 0 else 0.0,
                "operator": operator,
                "note": note,
            }
        )
    return out


def append_rows(path: Path, rows: Sequence[Mapping[str, Any]], fields: Sequence[str]) -> int:
    """追加写入 CSV（带 BOM，Excel 打开中文不乱码），返回写入行数。"""
    import csv

    if not rows:
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    exists = path.exists() and path.stat().st_size > 0
    with path.open("a", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(fields), extrasaction="ignore")
        if not exists:
            writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in fields})
    return len(rows)


def load_decisions(path: Path):
    import pandas as pd

    if not path.exists() or path.stat().st_size == 0:
        return None
    try:
        return pd.read_csv(path, encoding="utf-8-sig")
    except Exception:
        return None


def detect_bias(
    decisions,
    min_n: int = 5,
    min_abs_mean: float = 0.15,
    min_same_sign: float = 0.80,
) -> dict[str, Any]:
    """检测用户上手量是否系统性偏离建议。

    三条件**同时**满足才算偏置，避免把偶发调整当成系统性行为：
      1. 有效记录 n ≥ min_n；
      2. |上手量与建议量的平均偏离比例| ≥ min_abs_mean；
      3. 同号占比 ≥ min_same_sign（只统计非零偏离）。
    返回值里带 ``triggered`` 与逐条判据，界面据此决定是否提示。
    """
    base = {
        "n": 0,
        "nonzero_n": 0,
        "mean_gap_pct": 0.0,
        "same_sign_ratio": 0.0,
        "triggered": False,
        "reasons": [],
        "sign": 0,
    }
    if decisions is None or len(decisions) == 0:
        base["reasons"].append("尚无决策记录")
        return base

    required = {"suggest_qty", "applied_qty"}
    if not required.issubset(set(decisions.columns)):
        base["reasons"].append("决策记录缺列，无法判定")
        return base

    df = decisions.copy()
    suggests = df["suggest_qty"].astype(float)
    applied = df["applied_qty"].astype(float)
    mask = suggests > 0
    df = df[mask]
    if df.empty:
        base["reasons"].append("建议量全为 0，无法计算偏离")
        return base

    gap_pct = (applied[mask] - suggests[mask]) / suggests[mask]
    nonzero = gap_pct[gap_pct.abs() > EPS]
    mean_gap = float(gap_pct.mean())
    n = int(len(gap_pct))
    nz = int(len(nonzero))
    pos = int((nonzero > 0).sum())
    neg = int((nonzero < 0).sum())
    same_sign = (max(pos, neg) / nz) if nz > 0 else 0.0
    sign = 1 if mean_gap > 0 else (-1 if mean_gap < 0 else 0)

    reasons = []
    if n < min_n:
        reasons.append(f"有效记录 {n} 条 < {min_n} 条，样本不足")
    if abs(mean_gap) < min_abs_mean:
        reasons.append(f"平均偏离 {mean_gap:+.1%}，未达 {min_abs_mean:.0%} 阈值")
    if same_sign < min_same_sign:
        reasons.append(f"同号占比 {same_sign:.0%}，未达 {min_same_sign:.0%}（调整方向不一致）")

    return {
        "n": n,
        "nonzero_n": nz,
        "mean_gap_pct": mean_gap,
        "same_sign_ratio": same_sign,
        "sign": sign,
        "positive_n": pos,
        "negative_n": neg,
        "triggered": len(reasons) == 0,
        "reasons": reasons,
    }


def calibrate_penalty(current_cu: float, mean_gap_pct: float, lo: float = 1.0, hi: float = 10.0) -> float:
    """按平均偏离比例调整缺货惩罚单价，并夹在 [lo, hi]。

    上手量长期低于建议（mean_gap_pct < 0）说明实际缺货代价比配置低，
    对应把 cu 调小；反之上调。只作为建议值，不自动写主配置。
    """
    new = float(current_cu) * (1.0 + float(mean_gap_pct))
    return round(min(max(new, lo), hi), 4)


def write_overrides(
    path: Path,
    param: str,
    new_value: float,
    old_value: float,
    basis: str,
    evidence_n: int,
    mean_gap_pct: float,
) -> None:
    """写参数覆盖文件（不碰主配置），同时在文件里说明生效方式。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    body = [
        "# 由看板「决策留痕与偏差校准」生成，请勿手改。",
        "# 主配置 config/freshretail_inventory.yaml 未被修改。",
        "# 生效方式：重跑 .venv\\Scripts\\python.exe app/freshretail_inventory.py",
        "#          （以及依赖它的导出脚本），页面上本次会话的数字保持不变。",
        f"# generated_at: {datetime.now():%Y-%m-%d %H:%M:%S}",
        f"# basis: {basis}（n={evidence_n}, 平均偏离 {mean_gap_pct:+.1%}）",
        f"# previous_value: {old_value}",
        "costs:",
        f"  {param}: {new_value}",
    ]
    path.write_text("\n".join(body) + "\n", encoding="utf-8")


def load_overrides(path: Path) -> dict[str, Any]:
    """读 override 文件，供界面提示"已有一条待生效参数"。"""
    if not path.exists():
        return {}
    try:
        import yaml

        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}
