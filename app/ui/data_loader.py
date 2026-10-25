"""界面数据层：只读 A 电脑模型产物（output/*.csv）。

数据接口见 app/ui/README.md：
    - forecast_cumulative_predictions.csv
    - forecast_cumulative_metrics.csv
    - forecast_selection_by_sku.csv
    - demand_weekly.csv
    - inventory_replenishment.csv
    - inventory_policy_simulation.csv

页面不直接读 CSV，也不重算模型指标；这里只做列名校验、类型转换和排序。
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import streamlit as st

OUTPUT_DIR = Path(__file__).resolve().parents[2] / "output"

FORECAST_PREDICTIONS = "forecast_cumulative_predictions.csv"
FORECAST_METRICS = "forecast_cumulative_metrics.csv"
SKU_SELECTION = "forecast_selection_by_sku.csv"
DEMAND_WEEKLY = "demand_weekly.csv"
INVENTORY_REPLENISHMENT = "inventory_replenishment.csv"
POLICY_SIMULATION = "inventory_policy_simulation.csv"


def _read_output(filename: str) -> pd.DataFrame:
    path = OUTPUT_DIR / filename
    if not path.exists():
        raise FileNotFoundError(
            f"缺少模型输出文件：{path}\n"
            "请确认 output 目录已随项目一起复制，或先运行 "
            "app/forecast_cumulative.py 与 app/inventory_policy.py 生成产物。"
        )
    return pd.read_csv(path, encoding="utf-8-sig")


def _require_columns(df: pd.DataFrame, required: set[str], name: str) -> None:
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"{name} 缺少字段：{', '.join(sorted(missing))}")


def _to_numeric(df: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    for col in columns:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


@st.cache_data(show_spinner=False)
def load_forecast_predictions() -> pd.DataFrame:
    """未来 H 周累计需求预测（含回测实际值）。"""
    df = _read_output(FORECAST_PREDICTIONS)
    required = {
        "week",
        "sku_id",
        "sku_name",
        "category",
        "price_band",
        "actual",
        "p50",
        "p90",
        "p95",
    }
    _require_columns(df, required, "forecast_cumulative_predictions")

    df["week"] = pd.to_datetime(df["week"], errors="coerce")
    df = _to_numeric(
        df,
        ["sku_id", "price", "actual", "p50", "p90", "p95", "p50_raw", "p90_raw", "p95_raw"],
    )
    for col in ("sku_name", "category", "price_band"):
        df[col] = df[col].fillna("").astype(str)

    return (
        df.dropna(subset=["week", "sku_id"])
        .sort_values(["week", "sku_id"])
        .reset_index(drop=True)
    )


@st.cache_data(show_spinner=False)
def load_forecast_metrics() -> pd.DataFrame:
    """整体回测指标（留出集 / 滚动回测）。"""
    df = _read_output(FORECAST_METRICS)
    _require_columns(df, {"scope", "model", "type", "mae", "wmape"}, "forecast_cumulative_metrics")
    df = _to_numeric(df, ["mae", "wmape", "actual_sum", "n", "pinball_loss"])
    for col in ("scope", "model", "type"):
        df[col] = df[col].fillna("").astype(str)
    return df.reset_index(drop=True)


@st.cache_data(show_spinner=False)
def load_sku_selection() -> pd.DataFrame:
    """每个 SKU 的滚动选择回测结果。"""
    df = _read_output(SKU_SELECTION)
    _require_columns(df, {"sku_id", "sku_name", "select_WMAPE"}, "forecast_selection_by_sku")
    df = _to_numeric(df, ["sku_id", "weeks", "select_MAE", "select_WMAPE"])
    df["sku_name"] = df["sku_name"].fillna("").astype(str)
    return df.dropna(subset=["sku_id"]).sort_values("sku_id").reset_index(drop=True)


@st.cache_data(show_spinner=False)
def load_demand_weekly() -> pd.DataFrame:
    """全站周度需求序列。"""
    df = _read_output(DEMAND_WEEKLY)
    _require_columns(df, {"period", "demand_qty"}, "demand_weekly")
    df["period"] = pd.to_datetime(df["period"], errors="coerce")
    df = _to_numeric(df, ["demand_qty", "order_count", "line_count"])
    return df.dropna(subset=["period"]).sort_values("period").reset_index(drop=True)


@st.cache_data(show_spinner=False)
def load_inventory_replenishment() -> pd.DataFrame:
    """库存状态与补货建议。"""
    df = _read_output(INVENTORY_REPLENISHMENT)
    required = {
        "sku_id",
        "sku_name",
        "snapshot_week",
        "horizon_weeks",
        "lead_time_weeks",
        "review_period_weeks",
        "service_level_target",
        "available_inventory",
        "safety_stock",
        "reorder_point",
        "target_level",
        "recommended_order_qty",
        "urgency",
        "drift_status",
    }
    _require_columns(df, required, "inventory_replenishment")

    df["snapshot_week"] = pd.to_datetime(df["snapshot_week"], errors="coerce")
    df = _to_numeric(
        df,
        [
            "sku_id",
            "horizon_weeks",
            "lead_time_weeks",
            "review_period_weeks",
            "service_level_target",
            "effective_service_quantile",
            "on_hand",
            "in_transit",
            "available_inventory",
            "p50_cum",
            "p90_cum",
            "p95_cum",
            "p50_lt",
            "p90_lt",
            "p95_lt",
            "service_demand_lt",
            "safety_stock",
            "reorder_point",
            "target_level",
            "gap",
            "recommended_order_qty",
            "moq",
            "pack_size",
        ],
    )
    for col in ("sku_name", "urgency", "drift_status", "drift_detail", "reason"):
        if col in df.columns:
            df[col] = df[col].fillna("").astype(str)

    return df.dropna(subset=["sku_id"]).reset_index(drop=True)


@st.cache_data(show_spinner=False)
def load_policy_simulation() -> pd.DataFrame:
    """三种补货策略的仿真结果。"""
    df = _read_output(POLICY_SIMULATION)
    required = {
        "strategy",
        "strategy_label",
        "fill_rate",
        "avg_inventory",
        "total_cost",
    }
    _require_columns(df, required, "inventory_policy_simulation")
    df = _to_numeric(
        df,
        [
            "fill_rate",
            "avg_inventory",
            "total_cost",
            "stockout_weeks",
            "n_orders",
            "n_weeks",
            "n_skus",
        ],
    )
    for col in ("strategy", "strategy_label", "period_start", "period_end"):
        if col in df.columns:
            df[col] = df[col].fillna("").astype(str)
    return df.sort_values("strategy").reset_index(drop=True)


@st.cache_data(show_spinner=False)
def load_context() -> dict[str, object]:
    """页面共享的上下文：快照周、预测跨度、提前期等。"""
    inventory = load_inventory_replenishment()
    if inventory.empty:
        raise ValueError("inventory_replenishment.csv 没有数据行。")

    first = inventory.iloc[0]
    return {
        "snapshot_week": pd.to_datetime(first["snapshot_week"]),
        "horizon_weeks": int(first["horizon_weeks"]),
        "lead_time_weeks": int(first["lead_time_weeks"]),
        "review_period_weeks": int(first["review_period_weeks"]),
        "service_level_target": float(first["service_level_target"]),
        "sku_count": int(inventory["sku_id"].nunique()),
    }
