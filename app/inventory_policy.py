"""库存优化模块：把未来 H 周累计分位数预测转成补货建议，并用历史需求做策略仿真。

输入（只读，不修改预测模块）：
- output/forecast_cumulative_predictions.csv      最新起点（2017-08-21）的 P50/P90/P95
- output/forecast_cumulative_backtest_predictions.csv  52 个起点的 walk-forward 预测，
  用于 2017-01 ~ 2017-09 的逐周仿真和漂移监测
- data/clean_main.parquet                          按 SKU 的周需求（经 forecast_common.build_panel）
- output/demand_weekly.csv                         全量合计周需求，用于口径校验
- config.yaml 的 forecast / inventory 段

补货口径：
    提前期需求 Pxx_LT = 未来 H 周累计 Pxx × (lead_time_weeks / H)
    安全库存 SS       = P90_LT - P50_LT          （用分位数差，不另造正态假设）
    再订货点 ROP      = P50_LT + SS              （= P90_LT）
    目标水位 S        = P90_cum × (lead_time_weeks + review_period_weeks) / H
    建议订货量        = max(0, S - 可用库存)，向上取整到 pack_size，且不低于 MOQ
    紧急度            = 可用库存 < ROP 且 < 0.5×SS → 紧急；可用库存 < ROP → 建议补货；否则正常

周转口径（单 SKU 量纲，避免 Top-N 合计 ÷ 单 SKU 库存的 10 倍误差）：
    平均单 SKU 已满足件数 = total_served / n_skus
    平均单 SKU 库存       = avg_inventory（状态表按 SKU-周平铺，均值即单 SKU 周平均库存）
    turnover_period       = (total_served / n_skus) / avg_inventory
    turnover_annualized   = turnover_period × (52 / n_weeks)

仿真三种策略（同一份需求、同一初始库存、同一提前期/检查周期/MOQ/pack）：
    A 经验规则：补到 initial_inventory_weeks 周的平均销量（不看预测）
    B 移动平均：补到 1.2 × MA4 × (lead_time + review_period)
    C 本方案：分位数 ROP + 安全库存 + 漂移监测（检出漂移时目标分位 P90→P95）

用法：
    .venv\\Scripts\\python.exe app\\inventory_policy.py
    .venv\\Scripts\\python.exe app\\inventory_policy.py --config output\\demo_config.yaml
    .venv\\Scripts\\python.exe app\\inventory_policy.py --sku 365

输出：
    output/inventory_replenishment.csv
    output/inventory_policy_simulation.csv
    output/inventory_policy_simulation_states.csv
    output/inventory_simulation.png
    output/inventory_policy_summary.txt
"""

from __future__ import annotations

import argparse
import math
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import numpy as np
import pandas as pd

from forecast_common import (
    DATA_PATH,
    OUTPUT_DIR,
    build_panel,
    load_config,
    setup_plot_style,
)

HOLDOUT_PATH = OUTPUT_DIR / "forecast_cumulative_predictions.csv"
BACKTEST_PATH = OUTPUT_DIR / "forecast_cumulative_backtest_predictions.csv"
DEMAND_WEEKLY_PATH = OUTPUT_DIR / "demand_weekly.csv"

SIM_START = pd.Timestamp("2017-01-02")
SIM_END = pd.Timestamp("2017-09-18")

# 与 config.yaml 的 inventory 段一一对应；config 缺项时用这里的默认值。
INVENTORY_DEFAULTS: dict[str, object] = {
    "lead_time_weeks": 4,
    "service_level_target": 0.90,
    "review_period_weeks": 1,
    "order_cost": 500.0,
    "holding_cost_per_unit_week": 0.5,
    "stockout_cost_per_unit": 10.0,
    "moq": 10,
    "pack_size": 1,
    "initial_inventory_weeks": 4,
    "drift_monitor_enabled": True,
    "drift_monitor_window_weeks": 4,
    "drift_monitor_threshold_pct": 0.05,
    "drift_monitor_consecutive_weeks": 3,
    "drift_escalate_quantile": True,
}

# 图表配色：dataviz 参考调色板的前三个分类槽位（已用 validate_palette.js 校验）
STRATEGY_COLORS = {
    "A": "#2a78d6",
    "B": "#eb6834",
    "C": "#1baf7a",
}
STRATEGY_LABELS = {
    "A": "A 经验规则",
    "B": "B MA4×1.2",
    "C": "C 分位数 ROP",
}
INK = "#0b0b0b"
INK_SECONDARY = "#52514e"
MUTED = "#898781"
GRID = "#e1e0d9"
SURFACE = "#fcfcfb"


# ---------------------------------------------------------------------------
# 配置与工具
# ---------------------------------------------------------------------------
def load_inventory_config(path: str | None = None) -> tuple[dict, dict, Path]:
    cfg_path = Path(path) if path else None
    cfg = load_config(cfg_path)
    fc = dict(cfg["forecast"])
    inv = dict(INVENTORY_DEFAULTS)
    inv.update(cfg.get("inventory", {}) or {})

    # 类型规整与边界校验
    inv["lead_time_weeks"] = int(inv["lead_time_weeks"])
    inv["review_period_weeks"] = int(inv["review_period_weeks"])
    inv["moq"] = max(int(inv["moq"]), 1)
    inv["pack_size"] = max(int(inv["pack_size"]), 1)
    inv["service_level_target"] = float(inv["service_level_target"])
    if not (0.80 <= inv["service_level_target"] <= 0.95):
        raise SystemExit(
            f"service_level_target={inv['service_level_target']} 超出 [0.80, 0.95]；"
            "预测只提供 P50/P90/P95，不要外推。"
        )
    if inv["lead_time_weeks"] < 1 or inv["review_period_weeks"] < 1:
        raise SystemExit("lead_time_weeks 和 review_period_weeks 必须 >= 1。")
    return fc, inv, cfg_path


def quantile_at(p50: float, p90: float, p95: float, q: float) -> float:
    """在已有的 P50/P90/P95 之间取分位值。

    0.90 / 0.95 直接命中；0.80~0.90 和 0.90~0.95 之间线性插值。
    这是对已训练分位线的近似，不重新训练模型，也不外推。
    """
    points = [(0.50, p50), (0.90, p90), (0.95, p95)]
    if q <= points[0][0]:
        return float(p50)
    if q >= points[-1][0]:
        return float(p95)
    for (q0, v0), (q1, v1) in zip(points, points[1:]):
        if q0 <= q <= q1:
            w = (q - q0) / (q1 - q0)
            return float(v0 + w * (v1 - v0))
    return float(p95)


def round_order(gap: float, moq: int, pack_size: int) -> int:
    """缺口 -> 建议订货量：向上取整到 pack_size，且不低于 MOQ。"""
    if gap <= 1e-9:
        return 0
    pack_size = max(int(pack_size), 1)
    moq = max(int(moq), 1)
    qty = math.ceil(gap / pack_size - 1e-9) * pack_size
    min_qty = math.ceil(moq / pack_size) * pack_size
    return int(max(qty, min_qty))


def urgency_level(available: float, rop: float, safety_stock: float) -> str:
    if available < rop and available < 0.5 * safety_stock:
        return "紧急"
    if available < rop:
        return "建议补货"
    return "正常"


def detect_drift(
    history: pd.DataFrame,
    window_weeks: int,
    consecutive_weeks: int,
    threshold_pct: float,
) -> tuple[bool, str, float, int]:
    """判断最近若干已实现起点是否存在连续同向偏差。

    history: 该 SKU 的已实现起点，列含 actual、p50，按 week 升序。
    规则：最近连续 consecutive_weeks 个起点的 (actual-p50)/p50 同向，
          且这段的平均绝对偏差 >= threshold_pct。
    """
    if history.empty:
        return False, "无已实现历史", float("nan"), 0
    h = history.tail(max(int(window_weeks), 1)).copy()
    if len(h) < int(consecutive_weeks):
        return False, "已实现历史不足", float("nan"), len(h)
    rel = ((h["actual"] - h["p50"]) / h["p50"].clip(lower=1.0)).to_numpy(dtype=float)
    signs = np.sign(rel)
    last_sign = signs[-1]
    run = 1
    for i in range(len(signs) - 2, -1, -1):
        if signs[i] == last_sign and signs[i] != 0:
            run += 1
        else:
            break
    run = min(run, len(h))
    mean_abs = float(np.abs(rel[-run:]).mean())
    # 只对“实际持续高于预测”（低估）升级：缺货成本高于持有成本，方向性升级避免无谓库存。
    flagged = bool(
        last_sign > 0 and run >= int(consecutive_weeks) and mean_abs >= threshold_pct
    )
    direction = "实际持续高于预测（低估）" if last_sign > 0 else "实际持续低于预测（高估）"
    detail = f"最近 {run} 周{direction}，平均偏差 {mean_abs:.1%}"
    return flagged, detail, mean_abs, run


# ---------------------------------------------------------------------------
# 预测/漂移查表
# ---------------------------------------------------------------------------
def build_forecast_lookup(
    backtest: pd.DataFrame, sim_weeks: list[pd.Timestamp], skus: list[int]
) -> dict[tuple[int, pd.Timestamp], tuple[float, float, float]]:
    """每个 SKU 在仿真周上的 4 周累计分位线；缺失周沿用最近一次预测。"""
    lookup: dict[tuple[int, pd.Timestamp], tuple[float, float, float]] = {}
    sim_index = pd.DatetimeIndex(sim_weeks)
    for sku in skus:
        sub = backtest[backtest["sku_id"] == sku].set_index("week").sort_index()
        if sub.empty:
            continue
        full = sub.reindex(sub.index.union(sim_index)).sort_index().ffill()
        for week in sim_weeks:
            row = full.loc[week]
            if pd.notna(row["p50"]):
                lookup[(int(sku), week)] = (
                    float(row["p50"]),
                    float(row["p90"]),
                    float(row["p95"]),
                )
    return lookup


def build_drift_lookup(
    backtest: pd.DataFrame,
    sim_weeks: list[pd.Timestamp],
    skus: list[int],
    horizon: int,
    inv: dict,
) -> dict[tuple[int, pd.Timestamp], tuple[bool, str, float, int]]:
    """每个 SKU 在每个仿真周上的漂移状态（只用 u+H<=t 的已实现起点）。"""
    lookup: dict[tuple[int, pd.Timestamp], tuple[bool, str, float, int]] = {}
    if not bool(inv["drift_monitor_enabled"]):
        for sku in skus:
            for week in sim_weeks:
                lookup[(int(sku), week)] = (False, "监测关闭", float("nan"), 0)
        return lookup
    window = int(inv["drift_monitor_window_weeks"])
    consecutive = int(inv["drift_monitor_consecutive_weeks"])
    threshold = float(inv["drift_monitor_threshold_pct"])
    for sku in skus:
        sub = backtest[backtest["sku_id"] == sku].sort_values("week")
        realized_week = sub["week"] + pd.Timedelta(weeks=horizon)
        for week in sim_weeks:
            hist = sub[realized_week <= week]
            lookup[(int(sku), week)] = detect_drift(hist, window, consecutive, threshold)
    return lookup


# ---------------------------------------------------------------------------
# 策略参数
# ---------------------------------------------------------------------------
def policy_c_params(
    p50_cum: float, p90_cum: float, p95_cum: float, horizon: int, inv: dict,
    drift_flag: bool,
) -> dict[str, float | bool | str]:
    """策略 C 的提前期需求、SS、ROP、目标水位。"""
    lead = int(inv["lead_time_weeks"])
    review = int(inv["review_period_weeks"])
    base_target = float(inv["service_level_target"])
    escalated = bool(drift_flag and inv["drift_escalate_quantile"] and base_target < 0.95)
    effective_target = 0.95 if escalated else base_target

    lt_factor = lead / horizon
    target_factor = (lead + review) / horizon
    p50_lt = p50_cum * lt_factor
    p90_lt = p90_cum * lt_factor
    p95_lt = p95_cum * lt_factor
    p50_review = p50_cum * review / horizon
    q_lt = quantile_at(p50_lt, p90_lt, p95_lt, effective_target)
    safety_stock = q_lt - p50_lt
    rop = p50_lt + safety_stock
    target_level = quantile_at(p50_cum, p90_cum, p95_cum, effective_target) * target_factor
    return {
        "p50_lt": p50_lt,
        "p90_lt": p90_lt,
        "p95_lt": p95_lt,
        "p50_review": p50_review,
        "service_quantile": effective_target,
        "service_demand_lt": q_lt,
        "safety_stock": safety_stock,
        "reorder_point": rop,
        "target_level": target_level,
        "escalated": escalated,
        "lead_time_extrapolated": lead > horizon,
    }


# ---------------------------------------------------------------------------
# 策略仿真
# ---------------------------------------------------------------------------
def simulate_strategy(
    strategy: str,
    panel: pd.DataFrame,
    forecast_lookup: dict,
    drift_lookup: dict,
    inv: dict,
    horizon: int,
    sim_weeks: list[pd.Timestamp],
    last_forecast_week: pd.Timestamp,
) -> tuple[dict, pd.DataFrame, list[str]]:
    skus = sorted(int(x) for x in panel["sku_id"].unique())
    all_weeks = sorted(pd.Timestamp(x) for x in panel["week"].unique())
    demand = (
        panel.pivot(index="week", columns="sku_id", values="qty")
        .sort_index()
        .astype(float)
    )
    demand.columns = [int(c) for c in demand.columns]

    lead = int(inv["lead_time_weeks"])
    review = int(inv["review_period_weeks"])
    moq = int(inv["moq"])
    pack = int(inv["pack_size"])
    review_every = max(review, 1)

    # 同一初始库存：仿真起点之前 4 周的平均周需求 × initial_inventory_weeks
    pre_weeks = [w for w in all_weeks if w < sim_weeks[0]][-4:]
    init_avg = demand.loc[pre_weeks].mean()
    on_hand = {
        sku: float(init_avg.get(sku, 0.0)) * float(inv["initial_inventory_weeks"])
        for sku in skus
    }
    pipeline: dict[int, list[tuple[pd.Timestamp, int]]] = {sku: [] for sku in skus}

    states: list[dict] = []
    warnings: list[str] = []
    order_cost_total = 0.0
    n_orders = 0

    for wi, week in enumerate(sim_weeks):
        for sku in skus:
            # 1) 到货
            arrived = sum(qty for (arrival, qty) in pipeline[sku] if arrival <= week)
            pipeline[sku] = [(a, q) for (a, q) in pipeline[sku] if a > week]
            on_hand[sku] += arrived

            # 2) 满足本周需求（缺货不补，按丢失销售处理）
            d = float(demand.loc[week, sku]) if sku in demand.columns else 0.0
            served = min(on_hand[sku], d)
            unmet = max(d - served, 0.0)
            on_hand[sku] -= served

            # 3) 在途（本次下单前）
            in_transit = float(sum(qty for (arrival, qty) in pipeline[sku] if arrival > week))
            available = on_hand[sku] + in_transit

            # 4) 周期检查与下单
            order = 0
            rop = float("nan")
            safety_stock = float("nan")
            target_level = float("nan")
            drift_flag = False
            drift_detail = (
                "监测关闭" if strategy == "C" else "不适用（A/B 无漂移监测）"
            )
            escalated = False
            carried = week > last_forecast_week
            if wi % review_every == 0:
                if strategy in ("A", "B"):
                    hist = demand.loc[[w for w in all_weeks if w <= week], sku].tail(4)
                    ma4 = float(hist.mean()) if len(hist) else 0.0
                    if strategy == "A":
                        target_level = float(inv["initial_inventory_weeks"]) * ma4
                    else:
                        target_level = 1.2 * ma4 * (lead + review)
                    order = round_order(max(0.0, target_level - available), moq, pack)
                else:
                    key = (sku, week)
                    if key not in forecast_lookup:
                        raise SystemExit(f"SKU {sku} 在 {week:%Y-%m-%d} 没有可用预测。")
                    p50_cum, p90_cum, p95_cum = forecast_lookup[key]
                    drift_flag, drift_detail, _, _ = drift_lookup[key]
                    params = policy_c_params(
                        p50_cum, p90_cum, p95_cum, horizon, inv, drift_flag
                    )
                    rop = float(params["reorder_point"])
                    safety_stock = float(params["safety_stock"])
                    target_level = float(params["target_level"])
                    escalated = bool(params["escalated"])
                    if params["lead_time_extrapolated"]:
                        msg = (
                            f"SKU {sku}: lead_time_weeks={lead} > horizon_weeks={horizon}，"
                            "提前期需求按比例外推，已显式标记。"
                        )
                        if msg not in warnings:
                            warnings.append(msg)
                    # 周期检查 (s, S)：保护期是 lead_time + review_period。
                    # 用“可用库存 - 检查期 P50 需求 < ROP”作为触发，避免晚一周下单；
                    # ROP 本身仍按任务口径用提前期需求计算。
                    projected = available - float(params["p50_review"])
                    if projected < rop:
                        order = round_order(max(0.0, target_level - available), moq, pack)

                if order > 0:
                    pipeline[sku].append((week + pd.Timedelta(weeks=lead), int(order)))
                    n_orders += 1
                    order_cost_total += float(inv["order_cost"])

            states.append(
                {
                    "strategy": strategy,
                    "week": week,
                    "sku_id": sku,
                    "demand": d,
                    "served": served,
                    "unmet": unmet,
                    "on_hand": on_hand[sku],
                    "in_transit_before_order": in_transit,
                    "available_before_order": available,
                    "order_qty": int(order),
                    "reorder_point": rop,
                    "safety_stock": safety_stock,
                    "target_level": target_level,
                    "drift_flag": bool(drift_flag),
                    "drift_detail": drift_detail,
                    "drift_escalated": bool(escalated),
                    "forecast_carried_forward": bool(carried),
                }
            )

    states_df = pd.DataFrame(states)
    total_demand = float(states_df["demand"].sum())
    total_served = float(states_df["served"].sum())
    total_unmet = float(states_df["unmet"].sum())
    avg_inventory = float(states_df["on_hand"].mean())
    n_weeks = len(sim_weeks)
    holding_cost_total = float(inv["holding_cost_per_unit_week"]) * float(states_df["on_hand"].sum())
    stockout_cost_total = float(inv["stockout_cost_per_unit"]) * total_unmet
    total_cost = order_cost_total + holding_cost_total + stockout_cost_total
    # 周转口径：分子分母必须都是“单个 SKU”的量级。
    # states_df 每行是一个 (周, SKU)，所以 avg_inventory 已经是单 SKU 的周平均库存；
    # 分子先把 Top-N 合计已满足件数除以 n_skus，得到单 SKU 在仿真期内的平均已满足件数。
    # 若直接用合计需求 ÷ 单 SKU 库存，会把周转放大约 n_skus 倍（旧版 1436 次/年 的原因）。
    # 分子用 total_served（实际满足件数，缺货按丢失销售处理），与“库存周转 = 出库/平均库存”一致。
    n_skus = len(skus)
    avg_served_per_sku = total_served / n_skus if n_skus else float("nan")
    turnover = (
        avg_served_per_sku / avg_inventory if avg_inventory > 1e-9 else float("nan")
    )
    summary = {
        "strategy": strategy,
        "strategy_label": STRATEGY_LABELS[strategy],
        "period_start": sim_weeks[0],
        "period_end": sim_weeks[-1],
        "n_weeks": n_weeks,
        "n_skus": n_skus,
        "total_demand": total_demand,
        "total_served": total_served,
        "total_unmet": total_unmet,
        "fill_rate": total_served / total_demand if total_demand > 0 else float("nan"),
        "stockout_weeks": int((states_df["unmet"] > 1e-9).sum()),
        "stockout_calendar_weeks": int(
            states_df.loc[states_df["unmet"] > 1e-9, "week"].nunique()
        ),
        "avg_inventory": avg_inventory,
        "turnover_period": turnover,
        "turnover_annualized": turnover * 52.0 / n_weeks if pd.notna(turnover) else float("nan"),
        "n_orders": n_orders,
        "order_cost_total": order_cost_total,
        "holding_cost_total": holding_cost_total,
        "stockout_cost_total": stockout_cost_total,
        "total_cost": total_cost,
    }
    return summary, states_df, warnings


# ---------------------------------------------------------------------------
# 补货建议表
# ---------------------------------------------------------------------------
def build_replenishment_table(
    snapshot_fc: pd.DataFrame,
    state_at_snapshot: pd.DataFrame,
    inv: dict,
    horizon: int,
    drift_lookup: dict,
    snapshot_week: pd.Timestamp,
) -> pd.DataFrame:
    rows: list[dict] = []
    state = state_at_snapshot.set_index("sku_id")
    for _, fc_row in snapshot_fc.sort_values("p50", ascending=False).iterrows():
        sku = int(fc_row["sku_id"])
        if sku not in state.index:
            continue
        st = state.loc[sku]
        on_hand = float(st["on_hand"])
        in_transit = float(st["in_transit_before_order"])
        available = on_hand + in_transit
        drift_flag, drift_detail, _, _ = drift_lookup[(sku, snapshot_week)]
        params = policy_c_params(
            float(fc_row["p50"]), float(fc_row["p90"]), float(fc_row["p95"]),
            horizon, inv, drift_flag,
        )
        rop = float(params["reorder_point"])
        safety_stock = float(params["safety_stock"])
        target_level = float(params["target_level"])
        gap = max(0.0, target_level - available)
        order_qty = round_order(gap, int(inv["moq"]), int(inv["pack_size"]))
        urgency = urgency_level(available, rop, safety_stock)
        quantile_label = f"P{int(round(float(params['service_quantile']) * 100))}"
        reason = (
            f"{quantile_label} 提前期需求 {float(params['service_demand_lt']):.0f}，"
            f"可用库存 {available:.0f}（现有 {on_hand:.0f} + 在途 {in_transit:.0f}），"
            f"缺口 {gap:.0f}；按 pack={int(inv['pack_size'])} 向上取整、"
            f"不低于 MOQ={int(inv['moq'])}，建议订货 {order_qty}。"
        )
        if drift_flag:
            reason += f" 漂移监测：{drift_detail}，目标水位已按 P95 重估。"
        if params["lead_time_extrapolated"]:
            reason += " 注意：提前期超过预测跨度，提前期需求为比例外推。"
        rows.append(
            {
                "sku_id": sku,
                "sku_name": str(fc_row["sku_name"]),
                "snapshot_week": snapshot_week,
                "horizon_weeks": horizon,
                "lead_time_weeks": int(inv["lead_time_weeks"]),
                "review_period_weeks": int(inv["review_period_weeks"]),
                "service_level_target": float(inv["service_level_target"]),
                "effective_service_quantile": float(params["service_quantile"]),
                "on_hand": on_hand,
                "in_transit": in_transit,
                "available_inventory": available,
                "p50_cum": float(fc_row["p50"]),
                "p90_cum": float(fc_row["p90"]),
                "p95_cum": float(fc_row["p95"]),
                "p50_lt": float(params["p50_lt"]),
                "p90_lt": float(params["p90_lt"]),
                "p95_lt": float(params["p95_lt"]),
                "service_demand_lt": float(params["service_demand_lt"]),
                "safety_stock": safety_stock,
                "reorder_point": rop,
                "target_level": target_level,
                "gap": gap,
                "recommended_order_qty": int(order_qty),
                "moq": int(inv["moq"]),
                "pack_size": int(inv["pack_size"]),
                "urgency": urgency,
                "drift_status": "需重估" if drift_flag else "正常",
                "drift_detail": drift_detail,
                "lead_time_extrapolated": bool(params["lead_time_extrapolated"]),
                "reason": reason,
            }
        )
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# 手工核对
# ---------------------------------------------------------------------------
def print_manual_check(row: pd.Series, inv: dict, horizon: int) -> list[str]:
    lead = int(inv["lead_time_weeks"])
    review = int(inv["review_period_weeks"])
    lines = [
        f"[手工核对] SKU {int(row['sku_id'])} · {row['sku_name']}",
        f"  1) 预测（未来 {horizon} 周累计）：P50={row['p50_cum']:.2f}，"
        f"P90={row['p90_cum']:.2f}，P95={row['p95_cum']:.2f}",
        f"  2) 提前期折算：lead_time={lead} 周，horizon={horizon} 周，系数={lead / horizon:.4f}",
        f"     提前期需求：P50_LT={row['p50_lt']:.2f}，P90_LT={row['p90_lt']:.2f}，"
        f"P95_LT={row['p95_lt']:.2f}",
        f"  3) 安全库存 SS = P90_LT - P50_LT = {row['p90_lt']:.2f} - "
        f"{row['p50_lt']:.2f} = {row['safety_stock']:.2f}",
        f"  4) 再订货点 ROP = P50_LT + SS = {row['p50_lt']:.2f} + "
        f"{row['safety_stock']:.2f} = {row['reorder_point']:.2f}（等于 P90_LT）",
        f"     目标水位 S = P{int(round(row['effective_service_quantile'] * 100))}_cum × "
        f"(lead_time + review) / horizon = {row['target_level']:.2f}",
        f"  5) 可用库存 = 现有 {row['on_hand']:.2f} + 在途 {row['in_transit']:.2f} = "
        f"{row['available_inventory']:.2f}",
        f"  6) 缺口 = max(0, S - 可用库存) = max(0, {row['target_level']:.2f} - "
        f"{row['available_inventory']:.2f}) = {row['gap']:.2f}",
        f"  7) 建议订货量 = 向上取整到 pack={int(row['pack_size'])}、不低于 "
        f"MOQ={int(row['moq'])} → {int(row['recommended_order_qty'])}",
        f"  8) 紧急度：可用库存 {row['available_inventory']:.2f} vs ROP "
        f"{row['reorder_point']:.2f} vs 0.5×SS {0.5 * row['safety_stock']:.2f} → {row['urgency']}",
        f"     漂移状态：{row['drift_status']}（{row['drift_detail']}）",
    ]
    return lines


# ---------------------------------------------------------------------------
# 图表
# ---------------------------------------------------------------------------
def plot_simulation(summary: pd.DataFrame, out_path: Path) -> None:
    setup_plot_style()
    strategies = list(summary["strategy"])
    colors = [STRATEGY_COLORS[s] for s in strategies]
    labels = [STRATEGY_LABELS[s] for s in strategies]

    fig, axes = plt.subplots(2, 2, figsize=(13, 8.5))
    fig.patch.set_facecolor(SURFACE)
    axes = axes.ravel()

    panels = [
        ("fill_rate", "满足率（fill rate）", "{:.1%}", 1.0),
        ("avg_inventory", "平均库存（件）", "{:,.0f}", None),
        ("total_cost", "总成本（元）", "{:,.0f}", None),
        ("stockout_weeks", "缺货 SKU-周数", "{:,.0f}", None),
    ]
    for ax, (col, title, fmt, upper) in zip(axes, panels):
        values = summary[col].to_numpy(dtype=float)
        bars = ax.bar(
            range(len(strategies)), values, color=colors, width=0.62,
            edgecolor=SURFACE, linewidth=2.0,
        )
        for bar, value in zip(bars, values):
            ax.text(
                bar.get_x() + bar.get_width() / 2, bar.get_height(),
                fmt.format(value), ha="center", va="bottom",
                fontsize=10, color=INK, fontweight="bold",
            )
        ax.set_facecolor(SURFACE)
        ax.set_title(title, fontsize=12, color=INK, pad=10)
        ax.set_xticks(range(len(strategies)))
        ax.set_xticklabels(labels, fontsize=9.5, color=INK_SECONDARY)
        ax.grid(axis="y", color=GRID, linewidth=0.8)
        ax.set_axisbelow(True)
        for spine in ("top", "right", "left"):
            ax.spines[spine].set_visible(False)
        ax.spines["bottom"].set_color("#c3c2b7")
        ax.tick_params(axis="both", colors=MUTED, labelsize=9)
        if col == "fill_rate":
            ax.yaxis.set_major_formatter(mticker.PercentFormatter(1.0, decimals=0))
        elif col == "total_cost":
            ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"{x:,.0f}"))
        if upper is not None:
            ax.set_ylim(0, upper * 1.12)
        else:
            ax.set_ylim(0, max(values) * 1.18 if max(values) > 0 else 1)

    fig.suptitle(
        "库存策略仿真：2017-01 ~ 2017-09，Top-10 SKU，每周滚动",
        fontsize=14, color=INK, y=0.98,
    )
    fig.text(
        0.5, 0.935,
        "同一份历史需求、同一初始库存、同一提前期/检查周期/MOQ；缺货按丢失销售处理",
        ha="center", fontsize=9.5, color=INK_SECONDARY,
    )
    fig.tight_layout(rect=(0, 0, 1, 0.92))
    fig.savefig(out_path, dpi=150, facecolor=SURFACE)
    plt.close(fig)


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(description="库存补货建议与策略仿真")
    parser.add_argument("--config", default=None, help="可选：自定义 config.yaml 路径")
    parser.add_argument("--sku", type=int, default=None, help="可选：手工核对指定 SKU")
    parser.add_argument("--tag", default="", help="可选：输出文件名后缀，便于参数对比实验")
    args = parser.parse_args()

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    fc_cfg, inv, cfg_path = load_inventory_config(args.config)
    horizon = int(fc_cfg["horizon_weeks"])
    top_n = int(fc_cfg["top_n"])

    if not HOLDOUT_PATH.exists() or not BACKTEST_PATH.exists():
        raise SystemExit(
            "缺少预测输出，请先运行：\n"
            "  .venv\\Scripts\\python.exe app\\forecast_cumulative.py"
        )
    holdout = pd.read_csv(HOLDOUT_PATH, parse_dates=["week"])
    backtest = pd.read_csv(BACKTEST_PATH, parse_dates=["week"])

    panel, sku_info, _, _ = build_panel(top_n=top_n, val_weeks=int(fc_cfg["val_weeks"]))
    skus = sorted(int(x) for x in panel["sku_id"].unique())
    all_weeks = sorted(pd.Timestamp(x) for x in panel["week"].unique())
    sim_weeks = [w for w in all_weeks if SIM_START <= w <= SIM_END]
    if not sim_weeks:
        raise SystemExit("仿真窗口内没有完整周数据。")
    last_forecast_week = pd.Timestamp(backtest["week"].max())
    snapshot_week = pd.Timestamp(holdout["week"].max())
    if snapshot_week > last_forecast_week:
        snapshot_week = last_forecast_week

    forecast_lookup = build_forecast_lookup(backtest, sim_weeks, skus)
    drift_lookup = build_drift_lookup(backtest, sim_weeks, skus, horizon, inv)

    summaries: list[dict] = []
    states_frames: list[pd.DataFrame] = []
    warnings: list[str] = []
    for strategy in ("A", "B", "C"):
        summary, states, warns = simulate_strategy(
            strategy, panel, forecast_lookup, drift_lookup, inv, horizon,
            sim_weeks, last_forecast_week,
        )
        summaries.append(summary)
        states_frames.append(states)
        warnings.extend(w for w in warns if w not in warnings)
    summary_df = pd.DataFrame(summaries)
    states_df = pd.concat(states_frames, ignore_index=True)

    # 补货建议表：策略 C 在快照周的库存状态
    snapshot_fc = holdout[holdout["week"] == snapshot_week].copy()
    if snapshot_fc.empty:
        raise SystemExit(f"留出集里没有 {snapshot_week:%Y-%m-%d} 的预测。")
    state_c = states_df[
        (states_df["strategy"] == "C") & (states_df["week"] == snapshot_week)
    ]
    if state_c.empty:
        raise SystemExit(f"策略 C 在 {snapshot_week:%Y-%m-%d} 没有仿真状态。")
    repl = build_replenishment_table(
        snapshot_fc, state_c, inv, horizon, drift_lookup, snapshot_week
    )

    # 输出（--tag 用于参数对比实验，不覆盖主结果）
    tag = f"_{args.tag}" if args.tag else ""
    repl_path = OUTPUT_DIR / f"inventory_replenishment{tag}.csv"
    sim_path = OUTPUT_DIR / f"inventory_policy_simulation{tag}.csv"
    states_path = OUTPUT_DIR / f"inventory_policy_simulation_states{tag}.csv"
    plot_path = OUTPUT_DIR / f"inventory_simulation{tag}.png"
    summary_path = OUTPUT_DIR / f"inventory_policy_summary{tag}.txt"

    repl.to_csv(repl_path, index=False, encoding="utf-8-sig")
    summary_df.to_csv(sim_path, index=False, encoding="utf-8-sig")
    states_df.to_csv(states_path, index=False, encoding="utf-8-sig")
    plot_simulation(summary_df, plot_path)

    # 口径校验：demand_weekly.csv 是全量合计，仿真面板是 Top-N
    weekly_total = float("nan")
    panel_total = float(panel[panel["week"].isin(sim_weeks)]["qty"].sum())
    panel_share = float("nan")
    if DEMAND_WEEKLY_PATH.exists():
        demand_weekly = pd.read_csv(DEMAND_WEEKLY_PATH, parse_dates=["period"])
        demand_weekly = demand_weekly[demand_weekly["period"].isin(sim_weeks)]
        weekly_total = float(demand_weekly["demand_qty"].sum())
        if weekly_total > 0:
            panel_share = panel_total / weekly_total

    # 验收判断
    row_a = summary_df[summary_df["strategy"] == "A"].iloc[0]
    row_b = summary_df[summary_df["strategy"] == "B"].iloc[0]
    row_c = summary_df[summary_df["strategy"] == "C"].iloc[0]
    fill_ok = (row_c["fill_rate"] > row_a["fill_rate"]) and (row_c["fill_rate"] > row_b["fill_rate"])
    inv_limit = 1.10 * row_b["avg_inventory"]
    inv_ok = row_c["avg_inventory"] <= inv_limit
    checks = [
        ("策略 C 满足率 > A 和 B", fill_ok,
         f"C {row_c['fill_rate']:.2%} vs A {row_a['fill_rate']:.2%} / B {row_b['fill_rate']:.2%}"),
        ("策略 C 平均库存 ≤ 1.1×B", inv_ok,
         f"C {row_c['avg_inventory']:.1f} vs 上限 {inv_limit:.1f}"),
    ]
    checks_df = pd.DataFrame(checks, columns=["验收项", "是否通过", "实测"])
    checks_df["是否通过"] = checks_df["是否通过"].map(lambda ok: "通过" if ok else "未通过")

    # 手工核对
    if args.sku is not None:
        manual = repl[repl["sku_id"] == args.sku]
        if manual.empty:
            raise SystemExit(f"补货表里没有 SKU {args.sku}。")
        manual_row = manual.iloc[0]
    else:
        manual_row = repl.iloc[0]  # 按 P50 降序，默认最大 SKU
    manual_lines = print_manual_check(manual_row, inv, horizon)

    # 终端报告
    print("========== 库存补货建议与策略仿真 ==========")
    print(f"配置：{cfg_path if cfg_path else 'config.yaml'}；horizon={horizon} 周，"
          f"lead_time={inv['lead_time_weeks']} 周，review={inv['review_period_weeks']} 周，"
          f"服务水平={inv['service_level_target']:.0%}，MOQ={inv['moq']}，pack={inv['pack_size']}")
    print(f"快照周：{snapshot_week:%Y-%m-%d}；仿真：{sim_weeks[0]:%Y-%m-%d} ~ "
          f"{sim_weeks[-1]:%Y-%m-%d}（{len(sim_weeks)} 周，{len(skus)} 个 SKU）")
    if pd.notna(weekly_total):
        print(f"口径校验：仿真期全量合计需求 {weekly_total:,.0f} 件；"
              f"Top-{top_n} 面板 {panel_total:,.0f} 件（占 {panel_share:.1%}）。"
              "demand_weekly.csv 不含 SKU 维度，按 SKU 的仿真用 clean_main 重建面板。")
    if warnings:
        print("\n[告警] 提前期超过预测跨度：")
        for w in warnings:
            print("  " + w)

    print("\n一、补货建议（策略 C，快照周 " + f"{snapshot_week:%Y-%m-%d}）")
    show_cols = [
        "sku_id", "sku_name", "available_inventory", "p50_lt", "p90_lt",
        "safety_stock", "reorder_point", "target_level", "recommended_order_qty",
        "urgency", "drift_status",
    ]
    show = repl[show_cols].copy()
    for col in ["available_inventory", "p50_lt", "p90_lt", "safety_stock",
                "reorder_point", "target_level"]:
        show[col] = show[col].map(lambda x: f"{x:,.1f}")
    show["recommended_order_qty"] = show["recommended_order_qty"].map(lambda x: f"{int(x):,}")
    show.columns = [
        "sku_id", "名称", "可用库存", "P50_LT", "P90_LT", "SS", "ROP",
        "目标水位", "建议订货", "紧急度", "漂移状态",
    ]
    print(show.to_string(index=False))

    print("\n二、策略仿真对比（2017-01 ~ 2017-09）")
    sim_show = summary_df[
        ["strategy_label", "fill_rate", "stockout_weeks", "stockout_calendar_weeks",
         "avg_inventory", "turnover_annualized", "total_cost", "n_orders", "total_unmet"]
    ].copy()
    sim_show["fill_rate"] = sim_show["fill_rate"].map(lambda x: f"{x:.2%}")
    sim_show["avg_inventory"] = sim_show["avg_inventory"].map(lambda x: f"{x:,.1f}")
    sim_show["turnover_annualized"] = sim_show["turnover_annualized"].map(lambda x: f"{x:.2f}")
    sim_show["total_cost"] = sim_show["total_cost"].map(lambda x: f"{x:,.0f}")
    sim_show["total_unmet"] = sim_show["total_unmet"].map(lambda x: f"{x:,.0f}")
    sim_show.columns = [
        "策略", "满足率", "缺货SKU-周", "缺货自然周", "平均库存", "年化周转",
        "总成本", "订单数", "缺货件数",
    ]
    print(sim_show.to_string(index=False))

    print("\n三、验收线")
    print(checks_df.to_string(index=False))

    print("\n四、手工核对（答辩公式素材）")
    for line in manual_lines:
        print(line)

    # 摘要文件
    with open(summary_path, "w", encoding="utf-8") as f:
        f.write("库存补货建议与策略仿真摘要\n")
        f.write("=" * 60 + "\n")
        f.write(f"数据源：{DATA_PATH}\n")
        f.write(f"预测输入：{HOLDOUT_PATH.name}（快照）、{BACKTEST_PATH.name}（仿真历史）\n")
        f.write(f"配置：{cfg_path if cfg_path else 'config.yaml'}\n")
        f.write(
            f"参数：horizon={horizon}，lead_time={inv['lead_time_weeks']}，"
            f"review={inv['review_period_weeks']}，service={inv['service_level_target']:.0%}，"
            f"order_cost={inv['order_cost']}，holding={inv['holding_cost_per_unit_week']}，"
            f"stockout={inv['stockout_cost_per_unit']}，MOQ={inv['moq']}，pack={inv['pack_size']}，"
            f"initial_inventory_weeks={inv['initial_inventory_weeks']}\n"
        )
        f.write(
            f"仿真：{sim_weeks[0]:%Y-%m-%d} ~ {sim_weeks[-1]:%Y-%m-%d}，"
            f"{len(sim_weeks)} 周 × {len(skus)} SKU；快照周 {snapshot_week:%Y-%m-%d}\n"
        )
        if pd.notna(weekly_total):
            f.write(
                f"口径校验：仿真期全量合计需求 {weekly_total:,.0f} 件；"
                f"Top-{top_n} 面板 {panel_total:,.0f} 件（占 {panel_share:.1%}）\n"
            )
        f.write("\n一、补货建议\n")
        f.write(repl.to_string(index=False))
        f.write("\n\n二、策略仿真对比\n")
        f.write(summary_df.to_string(index=False))
        f.write("\n\n三、验收线\n")
        f.write(checks_df.to_string(index=False))
        f.write("\n\n四、手工核对\n")
        f.write("\n".join(manual_lines) + "\n")
        f.write("\n\n假设与口径：\n")
        f.write("- 数据集没有真实库存字段，本仿真用“历史需求 + 模拟库存”做 walk-forward，不代表真实仓库执行结果。\n")
        f.write("- 需求为 Top-N SKU 的周需求，由 clean_main.parquet 重建；demand_weekly.csv 是全量合计，仅用于口径校验。\n")
        f.write(
            "- 周转口径（单 SKU 量纲）：turnover_period = (total_served / n_skus) / avg_inventory；"
            "turnover_annualized = turnover_period × (52 / n_weeks)。"
            "其中 avg_inventory 是全部 SKU-周的平均库存（即单 SKU 周平均库存）；"
            "分子用实际满足件数 total_served（缺货按丢失销售处理），不用 Top-N 合计需求。\n"
        )
        f.write("- 缺货按丢失销售处理，不补单；满足率 = 总满足件数 / 总需求件数。\n")
        f.write("- 提前期需求按未来 H 周累计分位数 × (lead_time/H) 折算；lead_time > H 时显式告警并标记外推。\n")
        f.write("- 安全库存 SS = P90_LT - P50_LT；ROP = P50_LT + SS；目标水位按 P90 覆盖 lead_time+review 折算。\n")
        f.write("- 漂移监测只用 u+H<=t 的已实现起点；只对“实际持续高于预测”（低估）升级，目标分位从 P90 升到 P95。\n")
        f.write("- 三种策略共享同一初始库存（仿真起点前 4 周平均需求 × initial_inventory_weeks）、同一 MOQ/pack。\n")
        f.write("- 策略 A 按“补到 initial_inventory_weeks 周平均销量”实现（若按字面每周固定订 4 周销量，库存无界增长，不作为可比基准）。\n")
        f.write("- 策略 B 的目标水位 = 1.2 × MA4 × (lead_time + review)。\n")
        f.write("- 策略 C 是周期检查 (s,S)：用“可用库存 − 检查期 P50 需求 < ROP”触发，补到目标水位；A/B 每周期补到目标水位。\n")
        f.write("- 2017-08-21 之后的 4 个仿真周沿用 08-21 的预测（状态表 forecast_carried_forward=True）。\n")

    print(f"\n已输出：{repl_path}")
    print(f"已输出：{sim_path}")
    print(f"已输出：{states_path}")
    print(f"已输出：{plot_path}")
    print(f"已输出：{summary_path}")


if __name__ == "__main__":
    main()
