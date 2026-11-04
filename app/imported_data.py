"""用户导入数据 → 完整补货分析（纯计算，不依赖 Streamlit）。

设计目标：
- 导入数据不再只是替换表格展示，而是接管 KPI、补货清单、导出和 Agent。
- 复用系统库存口径：ROP_P90、目标水位、效期上限、建议订货量、四档紧急度。
- 支持“全量覆盖”和“增量合并”（按 store_id + product_id 更新/新增）。
- 生成 Agent 需要的 3 份内存 CSV：补货快照、7 天预测、what-if 反事实。

必填字段：
    SKU（如 145-802）或 store_id + product_id
    库存、日均销量、保质期（天）、提前期（天）

可选字段：
    商品名称、P90 日销、销量标准差、临期数量、服务水平、
    目标覆盖天数、最小订货量、包装倍数

说明：
- 没有预测模型可用，7 天预测按“日均销量”平推；P90 = 日均 × (1 + z × CV)。
  CV 优先用“销量标准差 / 日均销量”，缺省 0.30；z 由服务水平反算。
- 效期上限 = 保质期内预计可售量 × config 倍数；建议订货量与系统口径一致。
- what-if 是简化回放：首日到货、日均销量平推 7 天，不含批次效期轮换。
"""

from __future__ import annotations

import hashlib
import io
import math
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from statistics import NormalDist

import numpy as np
import pandas as pd

import product_names

IMPORTED_DATA_VERSION = 2

# ---------------------------------------------------------------------------
# 表头识别
# ---------------------------------------------------------------------------
COLUMN_ALIASES: dict[str, tuple[str, ...]] = {
    "SKU": ("SKU", "SKU编码", "SKU ID", "sku_id", "商品编码", "物料编码", "店 / SKU", "序列"),
    "store_id": ("store_id", "店号", "门店号", "门店编号", "门店ID", "门店 ID", "店铺编号"),
    "product_id": ("product_id", "商品号", "商品编号", "商品ID", "商品 ID", "单品编号"),
    "库存": (
        "库存", "现有库存", "当前库存", "库存数量", "可用库存",
        "on_hand", "current_stock", "stock", "inventory",
    ),
    "日均销量": (
        "日均销量", "平均日销量", "日销量", "日均销售", "日销",
        "销量", "销售量", "销量指数",
        "daily_mean", "avg_daily_sales", "daily_sales", "sales", "demand",
    ),
    "保质期": ("保质期", "保质期天数", "效期", "shelf_life", "shelf_life_days"),
    "提前期": ("提前期", "提前期天数", "补货提前期", "订货提前期", "lead_time", "lead_time_days"),
    "商品名称": ("商品名称", "品名", "商品名", "菜品名称", "product_name", "name"),
    "P90日销": ("P90日销", "P90销量", "日销P90", "p90_daily", "p90"),
    "销量标准差": ("销量标准差", "需求标准差", "日销量标准差", "demand_std", "std", "sigma"),
    "临期数量": ("临期数量", "临期数", "临期库存", "expiring_qty", "expiring"),
    "服务水平": ("服务水平", "目标服务水平", "service_level"),
    "目标覆盖天数": ("目标覆盖天数", "覆盖天数", "target_cover_days", "cover_days"),
    "最小订货量": ("最小订货量", "起订量", "MOQ", "min_order_qty"),
    "包装倍数": ("包装倍数", "包装规格", "箱规", "order_pack_size", "pack_size"),
}

REQUIRED_FIELDS = ("库存", "日均销量", "保质期", "提前期")


def normalize_name(name: object) -> str:
    text = str(name or "").strip().lower()
    text = re.sub(r"[（(][^）)]*[）)]", "", text)
    return re.sub(r"[\s_\-/]+", "", text)


def match_columns(df: pd.DataFrame) -> dict[str, str]:
    normalized: dict[str, str] = {}
    for col in df.columns:
        normalized.setdefault(normalize_name(col), col)
    mapping: dict[str, str] = {}
    used: set[str] = set()
    for field, aliases in COLUMN_ALIASES.items():
        for alias in aliases:
            col = normalized.get(normalize_name(alias))
            if col is not None and col not in used:
                mapping[field] = col
                used.add(col)
                break
    return mapping


def inspect_columns(mapping: dict[str, str]) -> list[str]:
    """返回缺失的必填项说明；空列表表示可以导入。"""
    missing: list[str] = []
    has_key = (
        "SKU" in mapping
        or ("store_id" in mapping and "product_id" in mapping)
    )
    if not has_key:
        missing.append("SKU（店号-商品号）或 store_id + product_id")
    for field in REQUIRED_FIELDS:
        if field not in mapping:
            missing.append(field)
    return missing


def read_uploaded_table(uploaded) -> pd.DataFrame:
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


# ---------------------------------------------------------------------------
# 字段清洗
# ---------------------------------------------------------------------------
def _num(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce")


def _defaults(config: dict) -> dict:
    rep = (config or {}).get("replenishment", {}) or {}
    shelf = (config or {}).get("shelf_life", {}) or {}
    urg = (config or {}).get("urgency", {}) or {}
    return {
        "lead": float(rep.get("lead_time_days", 1)),
        "cover": float(rep.get("target_cover_days", 7)),
        "service": float(rep.get("service_level", 0.90)),
        "pack": int(rep.get("order_pack_size", 1)),
        "min_qty": int(rep.get("min_order_qty", 1)),
        "shelf": float(shelf.get("base_days", 5)),
        "multiplier": float(rep.get("expiry_cap_multiplier", 1.0)),
        "urgent_ratio": float(urg.get("urgent_rop_ratio", 0.5)),
        "default_cv": 0.30,
    }


def parse_sku(value: object) -> tuple[float, float]:
    text = str(value or "").strip().upper().replace("SKU", "").strip()
    for sep in ("-", "_", "/", "—", "–"):
        if sep in text:
            left, right = text.split(sep, 1)
            try:
                return float(int(left.strip())), float(int(right.strip()))
            except (TypeError, ValueError):
                return float("nan"), float("nan")
    return float("nan"), float("nan")


def _round_up(qty: float, pack: int, min_qty: int) -> int:
    pack = max(int(pack), 1)
    min_qty = max(int(min_qty), 1)
    if qty <= 1e-9:
        return 0
    rounded = math.ceil(qty / pack - 1e-9) * pack
    if rounded < min_qty:
        rounded = math.ceil(min_qty / pack) * pack
    return int(rounded)


def prepare_frame(
    df: pd.DataFrame, mapping: dict[str, str], config: dict
) -> tuple[pd.DataFrame, list[str]]:
    """把用户表清洗成 canonical 列；返回 (canonical, 提示信息)。"""
    defaults = _defaults(config)
    issues: list[str] = []
    out = pd.DataFrame(index=df.index)

    for field in (
        "SKU", "store_id", "product_id", "库存", "日均销量", "保质期", "提前期",
        "商品名称", "P90日销", "销量标准差", "临期数量", "服务水平",
        "目标覆盖天数", "最小订货量", "包装倍数",
    ):
        if field in mapping:
            out[field] = df[mapping[field]]

    # 门店 + 商品号：优先显式两列，缺失时从 SKU 解析。
    store = _num(out["store_id"]) if "store_id" in out else pd.Series(np.nan, index=df.index)
    pid = _num(out["product_id"]) if "product_id" in out else pd.Series(np.nan, index=df.index)
    if "SKU" in out:
        parsed = out["SKU"].astype(str).apply(parse_sku)
        sku_store = parsed.apply(lambda x: x[0])
        sku_pid = parsed.apply(lambda x: x[1])
        store = store.fillna(sku_store)
        pid = pid.fillna(sku_pid)

    canonical = pd.DataFrame(
        {
            "store_id": _num(store),
            "product_id": _num(pid),
            "product_name": (
                out["商品名称"].astype(str).str.strip()
                if "商品名称" in out
                else ""
            ),
            "on_hand": _num(out["库存"]) if "库存" in out else np.nan,
            "daily_mean": _num(out["日均销量"]) if "日均销量" in out else np.nan,
            "shelf_life_days": _num(out["保质期"]) if "保质期" in out else np.nan,
            "lead_time_days": _num(out["提前期"]) if "提前期" in out else np.nan,
            "p90_daily": _num(out["P90日销"]) if "P90日销" in out else np.nan,
            "demand_std": _num(out["销量标准差"]) if "销量标准差" in out else np.nan,
            "expiring_qty": _num(out["临期数量"]) if "临期数量" in out else np.nan,
            "service_level": _num(out["服务水平"]) if "服务水平" in out else np.nan,
            "target_cover_days": (
                _num(out["目标覆盖天数"]) if "目标覆盖天数" in out else np.nan
            ),
            "min_order_qty": _num(out["最小订货量"]) if "最小订货量" in out else np.nan,
            "order_pack_size": _num(out["包装倍数"]) if "包装倍数" in out else np.nan,
        }
    )

    # 保质期 / 提前期允许为空：计算时用系统默认值，合并时保留旧值。
    shelf_na = int(canonical["shelf_life_days"].isna().sum())
    if shelf_na:
        issues.append(
            f"有 {shelf_na} 行未填保质期，计算时按系统默认值 {defaults['shelf']:g} 天。"
        )
    lead_na = int(canonical["lead_time_days"].isna().sum())
    if lead_na:
        issues.append(
            f"有 {lead_na} 行未填提前期，计算时按系统默认值 {defaults['lead']:g} 天。"
        )

    before = len(canonical)
    key_ok = canonical["store_id"].notna() & canonical["product_id"].notna()
    required_ok = canonical["on_hand"].notna() & canonical["daily_mean"].notna()
    canonical = canonical[key_ok & required_ok].copy()
    dropped = before - len(canonical)
    if dropped:
        issues.append(f"有 {dropped} 行缺少门店/商品号/库存/日均销量，已跳过。")
    if canonical.empty:
        return canonical, issues

    canonical["store_id"] = canonical["store_id"].astype(int)
    canonical["product_id"] = canonical["product_id"].astype(int)
    canonical["on_hand"] = canonical["on_hand"].clip(lower=0.0)
    canonical["daily_mean"] = canonical["daily_mean"].clip(lower=0.0)
    canonical["shelf_life_days"] = canonical["shelf_life_days"].clip(lower=1).round()
    canonical["lead_time_days"] = canonical["lead_time_days"].clip(lower=0).round()
    canonical["expiring_qty"] = canonical["expiring_qty"].clip(lower=0.0)
    canonical["service_level"] = canonical["service_level"].clip(0.5, 0.999)
    canonical["target_cover_days"] = canonical["target_cover_days"].clip(lower=0).round()
    canonical["min_order_qty"] = canonical["min_order_qty"].clip(lower=1).round()
    canonical["order_pack_size"] = canonical["order_pack_size"].clip(lower=1).round()

    # 空品名转为 NaN，持续合并时不会覆盖已有的商品名称。
    if "product_name" in canonical.columns:
        cleaned_names = canonical["product_name"].astype(str).str.strip()
        canonical["product_name"] = cleaned_names.mask(
            cleaned_names.isin(["", "<NA>", "nan", "None", "NaN"]), np.nan
        )

    if "商品名称" not in mapping:
        issues.append("未提供商品名称，计算时使用演示品名映射补全。")

    # 同一 SKU 重复时保留最后一行（持续导入时后导入覆盖先导入）。
    dup = canonical.duplicated(["store_id", "product_id"], keep="last")
    if dup.any():
        issues.append(f"有 {int(dup.sum())} 行门店+商品号重复，已保留最后一行。")
        canonical = canonical[~dup]

    canonical = canonical.sort_values(["store_id", "product_id"]).reset_index(drop=True)
    return canonical, issues


def merge_frames(existing: pd.DataFrame, new: pd.DataFrame) -> pd.DataFrame:
    """按 (store_id, product_id) 合并：新数据覆盖旧值，新数据为空的字段保留旧值。"""
    if existing is None or existing.empty:
        return new.copy().reset_index(drop=True)
    if new is None or new.empty:
        return existing.copy().reset_index(drop=True)
    old = existing.set_index(["store_id", "product_id"])
    fresh = new.set_index(["store_id", "product_id"])
    merged = fresh.combine_first(old).reset_index()
    return merged.sort_values(["store_id", "product_id"]).reset_index(drop=True)


# ---------------------------------------------------------------------------
# 计算
# ---------------------------------------------------------------------------
@dataclass
class ImportedDataset:
    repl: pd.DataFrame
    forecast: pd.DataFrame
    caps: dict[tuple[int, int], dict]
    what_if_detail: pd.DataFrame
    what_if_summary: pd.DataFrame
    names: dict[tuple[int, int], str]
    snapshot: str
    signature: str
    warnings: list[str] = field(default_factory=list)


def _z_value(service_level: pd.Series) -> pd.Series:
    dist = NormalDist()
    return service_level.apply(lambda x: dist.inv_cdf(float(x)))


def build_dataset(
    canonical: pd.DataFrame,
    config: dict,
    snapshot: str | None = None,
) -> ImportedDataset:
    if canonical is None or canonical.empty:
        raise ValueError("导入数据为空。")
    defaults = _defaults(config)
    df = canonical.copy().reset_index(drop=True)
    snapshot_date = (
        pd.Timestamp(snapshot).normalize()
        if snapshot
        else pd.Timestamp(date.today())
    )
    snapshot_text = snapshot_date.strftime("%Y-%m-%d")

    blank = pd.Series(np.nan, index=df.index)

    def _num_col(column: str) -> pd.Series:
        return (
            pd.to_numeric(df[column], errors="coerce")
            if column in df.columns
            else blank
        )

    avg = _num_col("daily_mean")
    on_hand = _num_col("on_hand")
    shelf = _num_col("shelf_life_days").fillna(defaults["shelf"]).clip(lower=1)
    lead = _num_col("lead_time_days").fillna(defaults["lead"]).clip(lower=0)
    cover = _num_col("target_cover_days").fillna(defaults["cover"]).clip(lower=0)
    service = _num_col("service_level").fillna(defaults["service"]).clip(0.5, 0.999)
    pack = (
        _num_col("order_pack_size").fillna(defaults["pack"]).clip(lower=1).round().astype(int)
    )
    min_qty = (
        _num_col("min_order_qty").fillna(defaults["min_qty"]).clip(lower=1).round().astype(int)
    )
    expiring = _num_col("expiring_qty").fillna(0.0).clip(lower=0.0)

    p90 = _num_col("p90_daily")
    std = _num_col("demand_std")
    cv = std / avg.replace(0, np.nan)
    cv = cv.fillna(defaults["default_cv"]).clip(lower=0.0)
    z = _z_value(service)
    p90_est = avg * (1.0 + z * cv)
    p90 = p90.where(p90.notna(), p90_est)
    p90 = np.maximum(p90, avg)

    rop = p90 * lead
    target = avg * (lead + cover)
    sellable = avg * shelf
    cap = defaults["multiplier"] * sellable
    target_eff = np.minimum(target, cap)
    gap = np.maximum(0.0, target_eff - on_hand)

    raw_qty = np.ceil(gap / pack - 1e-9) * pack
    raw_qty = np.where(raw_qty < min_qty, np.ceil(min_qty / pack) * pack, raw_qty)
    suggest = np.where(gap <= 1e-9, 0, raw_qty).astype(int)

    ratio = defaults["urgent_ratio"]
    urgency = np.select(
        [on_hand < ratio * rop, on_hand < rop, on_hand < target_eff],
        ["紧急", "建议补货", "计划补货"],
        default="正常",
    )

    name_column = (
        df["product_name"]
        if "product_name" in df.columns
        else pd.Series("", index=df.index)
    )
    names = []
    for store, pid, name in zip(df["store_id"], df["product_id"], name_column):
        text = "" if name is None or pd.isna(name) else str(name).strip()
        if not text or text.lower() == "nan":
            text = product_names.product_name(store, pid)
        names.append(text)

    repl = pd.DataFrame(
        {
            "store_id": df["store_id"].astype(int),
            "product_id": df["product_id"].astype(int),
            "product_name": names,
            "shelf_life_days": shelf.astype(int),
            "on_hand": on_hand.round(2),
            "rop_p90": rop.round(2),
            "target_level": target.round(2),
            "suggest_qty": suggest,
            "urgency": urgency,
            "expiring_qty": expiring.round(2),
            "daily_mean": avg.round(2),
            "p90_daily": p90.round(2),
            "lead_time_days": lead.astype(int),
            "target_cover_days": cover.astype(int),
            "service_level": service,
            "order_pack_size": pack.astype(int),
            "min_order_qty": min_qty.astype(int),
            "cap_qty": cap.round(2),
            "target_eff": target_eff.round(2),
        }
    )

    caps: dict[tuple[int, int], dict] = {}
    for row in repl.itertuples():
        key = (int(row.store_id), int(row.product_id))
        caps[key] = {
            "sellable": float(row.cap_qty / max(defaults["multiplier"], 1e-9)),
            "cap": float(row.cap_qty),
            "target_eff": float(row.target_eff),
        }

    horizon = int(
        ((config or {}).get("what_if", {}) or {}).get("horizon_days", 7) or 7
    )
    dates = [
        (snapshot_date - timedelta(days=horizon - 1 - i)).strftime("%Y-%m-%d")
        for i in range(horizon)
    ]
    rows: list[dict] = []
    for row in repl.itertuples():
        for i, dt in enumerate(dates, 1):
            rows.append(
                {
                    "store_id": int(row.store_id),
                    "product_id": int(row.product_id),
                    "dt": dt,
                    "horizon": i,
                    "y_actual": float(row.daily_mean),
                    "y_pred_p50": float(row.daily_mean),
                    "y_pred_p90": float(row.p90_daily),
                    "stockout_rate": 0.0,
                }
            )
    forecast = pd.DataFrame(rows)

    detail, summary = build_what_if(repl, config, horizon=horizon)
    signature = hashlib.md5(
        canonical.to_csv(index=False).encode("utf-8")
    ).hexdigest()[:12]
    warnings: list[str] = []
    if (repl["lead_time_days"] == 0).any():
        warnings.append("存在提前期为 0 的序列，ROP_P90 会为 0。")
    if (repl["daily_mean"] == 0).any():
        warnings.append("存在日均销量为 0 的序列，目标水位和预测均为 0。")

    return ImportedDataset(
        repl=repl,
        forecast=forecast,
        caps=caps,
        what_if_detail=detail,
        what_if_summary=summary,
        names={(int(s), int(p)): n for s, p, n in zip(repl.store_id, repl.product_id, repl.product_name)},
        snapshot=snapshot_text,
        signature=signature,
        warnings=warnings,
    )


def build_what_if(
    repl: pd.DataFrame, config: dict, horizon: int = 7
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """简化 what-if：首日到货，日均销量平推 7 天，不含批次效期轮换。"""
    wi = (config or {}).get("what_if", {}) or {}
    scenarios = [float(x) for x in (wi.get("scenarios") or [0.0, 0.5, 0.75, 1.0, 1.25, 1.5, 2.0])]
    labels = [str(x) for x in (wi.get("scenario_labels") or [])]
    if len(labels) < len(scenarios):
        labels += [f"{x:.2f} 倍" for x in scenarios[len(labels):]]
    costs = (config or {}).get("costs", {}) or {}
    stockout_penalty = float(costs.get("stockout_penalty_per_unit", 5.0))
    holding_rate = float(costs.get("holding_cost_rate_per_day", 0.02))
    unit_cost = float(costs.get("unit_cost_normalized", 1.0))

    base_ratio = 1.0 if 1.0 in scenarios else min(scenarios, key=lambda x: abs(x - 1.0))
    base_costs: dict[tuple[int, int], float] = {}
    rows: list[dict] = []

    for row in repl.itertuples():
        key = (int(row.store_id), int(row.product_id))
        demand = float(row.daily_mean)
        total_demand = demand * horizon
        on_hand = float(row.on_hand)
        pack = int(row.order_pack_size)
        min_qty = int(row.min_order_qty)
        base_qty = int(row.suggest_qty)

        per_ratio: dict[float, dict] = {}
        for ratio, label in zip(scenarios, labels):
            order_qty = 0 if ratio <= 1e-9 else _round_up(base_qty * ratio, pack, min_qty)
            available = on_hand + order_qty
            sold = min(available, total_demand)
            unmet = max(0.0, total_demand - sold)
            ending = max(0.0, available - total_demand)
            fill = sold / total_demand if total_demand > 1e-9 else 1.0
            avg_inv = (available + ending) / 2.0
            total_cost = stockout_penalty * unmet + holding_rate * unit_cost * avg_inv * horizon
            per_ratio[ratio] = {
                "scenario_order_qty": order_qty,
                "unmet_units": round(unmet, 2),
                "spoiled_units": 0.0,
                "ending_on_hand": round(ending, 2),
                "fill_rate": round(fill, 6),
                "total_cost": round(total_cost, 4),
            }
        base_cost = per_ratio.get(base_ratio, {}).get("total_cost", 0.0)
        if key not in base_costs:
            base_costs[key] = base_cost
        for ratio, label in zip(scenarios, labels):
            item = per_ratio[ratio]
            rows.append(
                {
                    "store_id": key[0],
                    "product_id": key[1],
                    "product_name": str(row.product_name),
                    "daily_mean": demand,
                    "scenario_ratio": ratio,
                    "scenario_label": label,
                    "base_order_qty": base_qty,
                    **item,
                    "delta_cost_vs_base": round(item["total_cost"] - base_cost, 4),
                }
            )

    detail = pd.DataFrame(rows)
    if detail.empty:
        return detail, pd.DataFrame()

    summary_rows = []
    base_total = float(
        detail[detail["scenario_ratio"].astype(float) == base_ratio]["total_cost"].sum()
    )
    for ratio, label in zip(scenarios, labels):
        sub = detail[detail["scenario_ratio"].astype(float) == ratio]
        demand_total = float((sub["daily_mean"].astype(float) * horizon).sum())
        sold_total = float(
            (sub["fill_rate"].astype(float) * sub["daily_mean"].astype(float) * horizon).sum()
        )
        total_cost = float(sub["total_cost"].sum())
        summary_rows.append(
            {
                "scenario_ratio": ratio,
                "scenario_label": label,
                "总缺货件": round(float(sub["unmet_units"].sum()), 2),
                "总报损件": round(float(sub["spoiled_units"].sum()), 2),
                "平均满足率": round(sold_total / demand_total, 6) if demand_total > 0 else 1.0,
                "总成本": round(total_cost, 2),
                "相对基线成本差": round(total_cost - base_total, 2),
            }
        )
    return detail, pd.DataFrame(summary_rows)


def materialize_agent_files(dataset: ImportedDataset, directory: Path) -> Path:
    """把导入结果写成 FreshDataStore 能读的 3 份 CSV。"""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    dataset.repl.to_csv(
        directory / "freshretail_inventory_replenishment.csv",
        index=False,
        encoding="utf-8-sig",
    )
    dataset.forecast.to_csv(
        directory / "freshretail_forecast_predictions.csv",
        index=False,
        encoding="utf-8-sig",
    )
    dataset.what_if_detail.to_csv(
        directory / "freshretail_what_if.csv",
        index=False,
        encoding="utf-8-sig",
    )
    dataset.what_if_summary.to_csv(
        directory / "freshretail_what_if_summary.csv",
        index=False,
        encoding="utf-8-sig",
    )
    return directory
