"""需求预测共用模块：数据清洗、周度面板、基线、特征与评估指标。

约定：
- 只读取 data/clean_main.parquet（2015-01-01 ~ 2017-09-30）。
- 预测颗粒度：周 × Top10 SKU；周以周一为起点。
- 只用完整的自然周，剔除窗口首尾不完整的周。
- clean_main 仍含少量字段错位行，预测前再做一次质量校验。
"""

from __future__ import annotations

import re
from copy import deepcopy
from pathlib import Path

import numpy as np
import pandas as pd

BASE_DIR = Path(__file__).resolve().parents[1]
DATA_PATH = BASE_DIR / "data" / "clean_main.parquet"
CONFIG_PATH = BASE_DIR / "config.yaml"
OUTPUT_DIR = BASE_DIR / "output"
MODEL_DIR = BASE_DIR / "models"

ORDER_DATE = "order date (DateOrders)"
SHIP_DATE = "shipping date (DateOrders)"
QTY = "Order Item Quantity"
SKU_ID = "Product Card Id"
SKU_NAME = "Product Name"
CATEGORY = "Category Name"
PRODUCT_PRICE = "Product Price"

SHIPPING_MODES = ("Standard Class", "First Class", "Second Class", "Same Day")
TOP_N = 10
VAL_WEEKS = 12

MODEL_LABELS = {
    "ma4": "移动平均(4周)",
    "ma8": "移动平均(8周)",
    "sn": "季节朴素(52周)",
    "naive": "朴素(上周)",
    "lgbm": "LightGBM",
    "ensemble": "组合(MA4+MA8+LGBM)",
    "select": "按SKU滚动选优",
}
MODEL_ORDER = ["ma4", "ma8", "sn", "naive", "lgbm"]

DEFAULT_CONFIG = {
    "forecast": {
        "horizon_weeks": 4,
        "top_n": 10,
        "val_weeks": 12,
        "coverage_backtest_weeks": 52,
        "quantiles": [0.5, 0.9, 0.95],
        "price_band_edges": [0, 50, 100, 250, 100000],
        "normalize_by_recent_level": True,
        "quantile_num_boost_round": 100,
        "quantile_learning_rate": 0.05,
        "quantile_num_leaves": 5,
        "quantile_min_data_in_leaf": 60,
        "conformal_calibration": True,
        "conformal_window_origins": 8,
        "conformal_positive_only": True,
        "conformal_min_origins": 4,
    }
}


# ---------------------------------------------------------------------------
# 配置读取
# ---------------------------------------------------------------------------
def _parse_scalar(text: str):
    """解析 YAML 标量子集：整数、浮点、布尔、null、引号字符串、行内列表。"""
    text = text.strip()
    if text == "":
        return None
    if len(text) >= 2 and text[0] == text[-1] and text[0] in ("'", '"'):
        return text[1:-1]
    low = text.lower()
    if low in {"true", "yes"}:
        return True
    if low in {"false", "no"}:
        return False
    if low in {"null", "none", "~"}:
        return None
    if text.startswith("[") and text.endswith("]"):
        inner = text[1:-1].strip()
        if not inner:
            return []
        return [_parse_scalar(item) for item in inner.split(",")]
    try:
        return int(text)
    except ValueError:
        pass
    try:
        return float(text)
    except ValueError:
        pass
    return text


def _read_simple_yaml(path: Path) -> dict:
    """读取本项目配置所需的 YAML 子集（两层键值 + # 注释）。

    项目 venv 未安装 PyYAML，而环境约定不允许随意装包，因此用最小解析器。
    """
    root: dict = {}
    section: dict | None = None
    # utf-8-sig 兼容 Windows 记事本/PowerShell 写出的 BOM
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = re.split(r"\s+#", raw_line, maxsplit=1)[0]
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip(" "))
        key, sep, value = line.strip().partition(":")
        if not sep:
            raise ValueError(f"无法解析配置行：{raw_line}")
        key = key.strip()
        value = value.strip()
        if indent == 0:
            if value:
                root[key] = _parse_scalar(value)
                section = None
            else:
                root[key] = {}
                section = root[key]
        else:
            if section is None:
                raise ValueError(f"配置缩进异常（缺少父级键）：{raw_line}")
            section[key] = _parse_scalar(value)
    return root


def _deep_merge(base: dict, override: dict) -> dict:
    out = deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def load_config(path: Path | None = None) -> dict:
    """读取 config.yaml，缺省项自动补默认值。"""
    cfg_path = Path(path) if path is not None else CONFIG_PATH
    cfg = deepcopy(DEFAULT_CONFIG)
    if cfg_path.exists():
        cfg = _deep_merge(cfg, _read_simple_yaml(cfg_path))
    return cfg


def forecast_config(path: Path | None = None) -> dict:
    return load_config(path)["forecast"]



def setup_plot_style() -> None:
    import matplotlib.pyplot as plt

    plt.rcParams["font.sans-serif"] = [
        "Microsoft YaHei",
        "SimHei",
        "Noto Sans CJK SC",
        "Arial Unicode MS",
    ]
    plt.rcParams["axes.unicode_minus"] = False


def load_clean_orders() -> pd.DataFrame:
    """读取主窗口数据，并过滤字段错位行。"""
    df = pd.read_parquet(DATA_PATH)
    qty = pd.to_numeric(df[QTY], errors="coerce")
    price = pd.to_numeric(df["Product Price"], errors="coerce")
    status = pd.to_numeric(df["Product Status"], errors="coerce")
    name_num = pd.to_numeric(df[SKU_NAME], errors="coerce")

    mask = (
        df[ORDER_DATE].notna()
        & df[SHIP_DATE].notna()
        & df[SKU_ID].notna()
        & df[SKU_NAME].notna()
        & name_num.isna()
        & price.notna()
        & status.isin([0, 1])
        & df["Shipping Mode"].isin(SHIPPING_MODES)
        & qty.between(1, 10)
    )
    out = df.loc[
        mask,
        [ORDER_DATE, SHIP_DATE, SKU_ID, SKU_NAME, CATEGORY, QTY, PRODUCT_PRICE, "Shipping Mode"],
    ].copy()
    out[QTY] = qty[mask].astype(float)
    out[PRODUCT_PRICE] = price[mask].astype(float)
    out[SKU_ID] = out[SKU_ID].astype(int)
    out["week"] = out[ORDER_DATE].dt.to_period("W").dt.start_time
    return out


def full_week_starts(orders: pd.DataFrame) -> list[pd.Timestamp]:
    """返回数据完整覆盖的周（周一为起点），去掉首尾不完整周。"""
    min_date = orders[ORDER_DATE].min().normalize()
    max_date = orders[ORDER_DATE].max().normalize()
    starts = pd.Series(sorted(orders["week"].unique()))
    ends = starts + pd.Timedelta(days=6)
    keep = starts[(starts >= min_date) & (ends <= max_date)]
    return [pd.Timestamp(x) for x in keep.tolist()]


def top_sku_table(orders: pd.DataFrame, n: int = TOP_N) -> pd.DataFrame:
    table = (
        orders.groupby(SKU_ID)
        .agg(
            total_qty=(QTY, "sum"),
            line_count=(QTY, "size"),
            sku_name=(SKU_NAME, "first"),
            category=(CATEGORY, "first"),
            price=(PRODUCT_PRICE, "median"),
        )
        .sort_values("total_qty", ascending=False)
    )
    table.index.name = "sku_id"
    return table.head(n)


def build_panel(
    top_n: int = TOP_N,
    val_weeks: int = VAL_WEEKS,
) -> tuple[pd.DataFrame, pd.DataFrame, list[pd.Timestamp], list[pd.Timestamp]]:
    """构建 Top-N SKU × 完整周的平衡面板（缺失周补 0）。"""
    orders = load_clean_orders()
    weeks = full_week_starts(orders)
    sku_info = top_sku_table(orders, n=top_n)
    sku_ids = sku_info.index.tolist()

    weekly = (
        orders[orders["week"].isin(weeks)]
        .groupby([SKU_ID, "week"], observed=True)[QTY]
        .sum()
        .reset_index()
        .rename(columns={SKU_ID: "sku_id"})
    )
    grid = pd.MultiIndex.from_product(
        [sku_ids, weeks], names=["sku_id", "week"]
    ).to_frame(index=False)
    panel = grid.merge(weekly, on=["sku_id", "week"], how="left")
    panel[QTY] = panel[QTY].fillna(0.0)
    panel = panel.rename(columns={QTY: "qty"})
    panel["sku_name"] = panel["sku_id"].map(sku_info["sku_name"])
    panel["category"] = panel["sku_id"].map(sku_info["category"])
    panel["price"] = panel["sku_id"].map(sku_info["price"])
    panel = panel.sort_values(["sku_id", "week"]).reset_index(drop=True)

    train_weeks = weeks[:-val_weeks]
    val_weeks_list = weeks[-val_weeks:]
    return panel, sku_info, train_weeks, val_weeks_list


def add_baselines(panel: pd.DataFrame) -> pd.DataFrame:
    """为每周/每个 SKU 增加一步预测基线（只使用 t-1 及更早的真实值）。"""
    p = panel.sort_values(["sku_id", "week"]).copy()
    g = p.groupby("sku_id", group_keys=False)["qty"]

    p["ma4"] = g.transform(lambda s: s.shift(1).rolling(4, min_periods=4).mean())
    p["ma8"] = g.transform(lambda s: s.shift(1).rolling(8, min_periods=8).mean())
    p["naive"] = g.transform(lambda s: s.shift(1))
    p["sn"] = g.transform(lambda s: s.shift(52))
    # 季节朴素在历史不足 52 周时退化为 4 周移动平均
    p["sn"] = p["sn"].fillna(p["ma4"]).fillna(p["naive"])
    return p.reset_index(drop=True)


def add_features(panel: pd.DataFrame) -> pd.DataFrame:
    """构造滞后、滚动统计与日历特征；所有特征只使用 t-1 及更早数据。"""
    frames: list[pd.DataFrame] = []
    for _, grp in panel.groupby("sku_id", sort=False):
        grp = grp.sort_values("week").copy()
        s = grp["qty"]

        for lag in (1, 2, 3, 4, 8, 13, 26, 52):
            grp[f"lag_{lag}"] = s.shift(lag)

        shifted = s.shift(1)
        for window in (4, 8, 13, 26):
            grp[f"roll_mean_{window}"] = shifted.rolling(window, min_periods=1).mean()
            grp[f"roll_std_{window}"] = shifted.rolling(window, min_periods=2).std()
            grp[f"roll_min_{window}"] = shifted.rolling(window, min_periods=1).min()
            grp[f"roll_max_{window}"] = shifted.rolling(window, min_periods=1).max()

        # 当期水平：预测未来 H 周累计需求时，站在第 t 周末，qty[t] 已知。
        # 这些列不进入一步预测的 FEATURE_COLS，避免改变原基线口径。
        grp["lag_0"] = s
        for window in (4, 8):
            grp[f"roll_mean_cur_{window}"] = s.rolling(window, min_periods=1).mean()
            grp[f"roll_std_cur_{window}"] = s.rolling(window, min_periods=2).std()
        frames.append(grp)

    p = pd.concat(frames, ignore_index=True).sort_values(["sku_id", "week"]).reset_index(drop=True)
    iso = p["week"].dt.isocalendar()
    p["week_of_year"] = iso["week"].astype(int)
    p["month"] = p["week"].dt.month
    p["quarter"] = p["week"].dt.quarter
    p["year"] = p["week"].dt.year
    p["trend"] = ((p["week"] - p["week"].min()).dt.days // 7).astype(int)
    p["sin_woy"] = np.sin(2 * np.pi * p["week_of_year"] / 52.1775)
    p["cos_woy"] = np.cos(2 * np.pi * p["week_of_year"] / 52.1775)

    p["sku_cat"] = p["sku_id"].astype("category")
    p["category_cat"] = p["category"].astype("category")
    return p


FEATURE_COLS = [
    "sku_cat",
    "category_cat",
    "lag_1",
    "lag_2",
    "lag_3",
    "lag_4",
    "lag_8",
    "lag_13",
    "lag_26",
    "lag_52",
    "roll_mean_4",
    "roll_std_4",
    "roll_min_4",
    "roll_max_4",
    "roll_mean_8",
    "roll_std_8",
    "roll_min_8",
    "roll_max_8",
    "roll_mean_13",
    "roll_std_13",
    "roll_min_13",
    "roll_max_13",
    "roll_mean_26",
    "roll_std_26",
    "roll_min_26",
    "roll_max_26",
    "week_of_year",
    "month",
    "quarter",
    "trend",
    "sin_woy",
    "cos_woy",
]
CATEGORICAL_COLS = ["sku_cat", "category_cat"]

# 池化模型：所有 Top-N SKU 一起训练，显式加入价格与价格档位。
POOLED_FEATURE_COLS = [*FEATURE_COLS, "price", "price_band_cat"]
CATEGORICAL_COLS_POOLED = [*CATEGORICAL_COLS, "price_band_cat"]

# 累计需求模型：在池化特征上增加“当期（第 t 周）已知”的水平特征。
CUM_FEATURE_COLS = [
    *POOLED_FEATURE_COLS,
    "lag_0",
    "roll_mean_cur_4",
    "roll_std_cur_4",
    "roll_mean_cur_8",
    "roll_std_cur_8",
]


def add_price_band(panel: pd.DataFrame, edges: list[float]) -> pd.DataFrame:
    """按 Product Price 划分价格档位，作为池化模型的类别特征。"""
    p = panel.copy()
    edges = [float(x) for x in edges]
    labels = [f"{edges[i]:g}-{edges[i + 1]:g}" for i in range(len(edges) - 2)]
    labels.append(f"{edges[-2]:g}+")
    p["price_band"] = pd.cut(p["price"], bins=edges, right=False, labels=labels)
    p["price_band"] = p["price_band"].cat.add_categories(["未定价"]).fillna("未定价")
    p["price_band_cat"] = p["price_band"].astype("category")
    return p


def add_cumulative_target(panel: pd.DataFrame, horizon: int) -> pd.DataFrame:
    """目标改为“未来 H 周累计需求”：target[t] = sum(qty[t+1 .. t+H])。"""
    p = panel.sort_values(["sku_id", "week"]).copy()
    g = p.groupby("sku_id", group_keys=False)["qty"]
    parts = [g.shift(-step) for step in range(1, horizon + 1)]
    stacked = pd.concat(parts, axis=1)
    # min_count=horizon：只要未来窗口不完整就置为 NaN，不拿部分窗口当目标。
    p["target"] = stacked.sum(axis=1, min_count=horizon)
    return p.reset_index(drop=True)


# ---------------------------------------------------------------------------
# LightGBM 训练（点预测 / 分位数）
# ---------------------------------------------------------------------------
def train_point_model(
    train: pd.DataFrame,
    feature_cols: list[str],
    categorical_cols: list[str],
    params: dict,
    inner_val_weeks: int = VAL_WEEKS,
    label_col: str = "target",
) -> tuple[object, int]:
    """池化点预测模型：用训练集末尾若干周早停，再在全量训练集上重训。"""
    import lightgbm as lgb

    train_weeks = sorted(train["week"].unique())
    inner_val = set(train_weeks[-inner_val_weeks:])
    inner_train = train[~train["week"].isin(inner_val)]
    inner_valid = train[train["week"].isin(inner_val)]
    if inner_train.empty or inner_valid.empty:
        inner_train, inner_valid = train, train

    dtrain = lgb.Dataset(
        inner_train[feature_cols],
        label=inner_train[label_col],
        categorical_feature=categorical_cols,
        free_raw_data=False,
    )
    dvalid = lgb.Dataset(
        inner_valid[feature_cols],
        label=inner_valid[label_col],
        categorical_feature=categorical_cols,
        reference=dtrain,
        free_raw_data=False,
    )
    probe = lgb.train(
        params,
        dtrain,
        num_boost_round=1000,
        valid_sets=[dvalid],
        callbacks=[lgb.early_stopping(50, verbose=False), lgb.log_evaluation(0)],
    )
    best_iter = max(int(probe.best_iteration or 300), 20)
    final = lgb.train(
        params,
        lgb.Dataset(
            train[feature_cols],
            label=train[label_col],
            categorical_feature=categorical_cols,
            free_raw_data=False,
        ),
        num_boost_round=best_iter,
    )
    return final, best_iter


def train_quantile_model(
    train: pd.DataFrame,
    quantile: float,
    feature_cols: list[str],
    categorical_cols: list[str],
    params: dict,
    num_boost_round: int,
    label_col: str = "target",
):
    """训练单个分位数 LightGBM（objective=quantile, alpha=q）。"""
    import lightgbm as lgb

    q_params = dict(params)
    q_params.update(
        {
            "objective": "quantile",
            "alpha": float(quantile),
            "metric": "quantile",
        }
    )
    dtrain = lgb.Dataset(
        train[feature_cols],
        label=train[label_col],
        categorical_feature=categorical_cols,
        free_raw_data=False,
    )
    return lgb.train(q_params, dtrain, num_boost_round=num_boost_round)


def _row_metrics(actual: pd.Series, pred: pd.Series) -> tuple[float, float, float, int]:
    actual = actual.astype(float)
    pred = pred.astype(float)
    err = (actual - pred).abs()
    mae = float(err.mean()) if len(err) else float("nan")
    actual_sum = float(actual.sum())
    wmape = float(err.sum() / actual_sum) if actual_sum else float("nan")
    return mae, wmape, actual_sum, int(len(err))


def evaluate_predictions(pred_df: pd.DataFrame, models: list[str]) -> pd.DataFrame:
    """计算三个层级的 MAE / WMAPE：Top10 总量、全部 SKU-周、单 SKU。"""
    records: list[dict] = []

    def add(model: str, level: str, scope: str, actual: pd.Series, pred: pd.Series,
            sku_id: int | None = None, sku_name: str | None = None) -> None:
        mae, wmape, actual_sum, n = _row_metrics(actual, pred)
        records.append(
            {
                "model": model,
                "model_label": MODEL_LABELS.get(model, model),
                "level": level,
                "scope": scope,
                "sku_id": sku_id,
                "sku_name": sku_name,
                "mae": round(mae, 4),
                "wmape": round(wmape, 6),
                "actual_sum": round(actual_sum, 2),
                "n": n,
            }
        )

    # 1) Top10 合计（先把每个模型按周加总，再算误差）
    weekly = pred_df.groupby("week")[["actual", *models]].sum().sort_index()
    for model in models:
        add(model, "total", "Top10 合计（按周）", weekly["actual"], weekly[model])

    # 2) 全部 SKU-周
    for model in models:
        add(model, "overall", "Top10 全部 SKU-周", pred_df["actual"], pred_df[model])

    # 3) 每个 SKU
    for sku_id, grp in pred_df.groupby("sku_id", sort=False):
        sku_name = str(grp["sku_name"].iloc[0])
        for model in models:
            add(
                model,
                "sku",
                f"SKU {sku_id} · {sku_name}",
                grp["actual"],
                grp[model],
                sku_id=int(sku_id),
                sku_name=sku_name,
            )
    return pd.DataFrame(records)


def pivot_metrics(
    metrics: pd.DataFrame,
    sku_info: pd.DataFrame,
    model_order: list[str] | None = None,
) -> pd.DataFrame:
    """把长表转成宽表：每行一个 scope，每列一个模型的 MAE/WMAPE。"""
    model_order = model_order or MODEL_ORDER
    scope_order = ["Top10 合计（按周）", "Top10 全部 SKU-周"]
    for sku_id, row in sku_info.iterrows():
        scope_order.append(f"SKU {int(sku_id)} · {row['sku_name']}")

    rows = []
    for scope in scope_order:
        sub = metrics[metrics["scope"] == scope]
        if sub.empty:
            continue
        row: dict[str, object] = {"scope": scope}
        for model in model_order:
            m = sub[sub["model"] == model]
            if m.empty:
                continue
            row[f"{model}_MAE"] = float(m["mae"].iloc[0])
            row[f"{model}_WMAPE"] = float(m["wmape"].iloc[0])
        rows.append(row)
    return pd.DataFrame(rows)


def format_pivot(pivot: pd.DataFrame) -> pd.DataFrame:
    """把 WMAPE 显示成百分比，便于终端阅读。"""
    out = pivot.copy()
    for col in out.columns:
        if col.endswith("_WMAPE"):
            out[col] = out[col].map(lambda x: f"{x:.2%}" if pd.notna(x) else "—")
        elif col.endswith("_MAE"):
            out[col] = out[col].map(lambda x: f"{x:.2f}" if pd.notna(x) else "—")
    return out


def save_metrics(metrics: pd.DataFrame, pivot: pd.DataFrame, prefix: str) -> None:
    metrics.to_csv(OUTPUT_DIR / f"{prefix}_metrics_long.csv", index=False, encoding="utf-8-sig")
    pivot.to_csv(OUTPUT_DIR / f"{prefix}_metrics_wide.csv", index=False, encoding="utf-8-sig")
