"""把原始 xlsx 一次性转成 parquet 缓存，后续读取从 ~80 秒降到 ~2 秒。

用法：
    .venv\\Scripts\\python.exe app\\prepare_data.py
    .venv\\Scripts\\python.exe app\\prepare_data.py --force   # 强制重建缓存

产出：
    data/clean_raw.parquet  全量数据（含 2017-10 之后的结构断层部分）
    data/clean_main.parquet 主分析窗口 2015-01-01 ~ 2017-09-30
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

BASE_DIR = Path(__file__).resolve().parents[1]
RAW_XLSX = Path(r"D:\ai-race data\DataCoSupplyChainDataset.xlsx")
DATA_DIR = BASE_DIR / "data"
CACHE_ALL = DATA_DIR / "clean_raw.parquet"
CACHE_MAIN = DATA_DIR / "clean_main.parquet"

ORDER_DATE = "order date (DateOrders)"
SHIP_DATE = "shipping date (DateOrders)"
DATE_COLS = [ORDER_DATE, SHIP_DATE]
MAIN_START = "2015-01-01"
MAIN_END = "2017-09-30"


def parse_date_columns(df: pd.DataFrame) -> pd.DataFrame:
    """日期列转成真正的 datetime，原始文本另存为 *_raw，方便溯源。"""
    for col in DATE_COLS:
        if col not in df.columns:
            continue
        raw = df[col].astype("string")
        df[f"{col}_raw"] = raw
        df[col] = pd.to_datetime(raw, format="%m/%d/%Y %H:%M", errors="coerce")
        bad = df[col].isna().sum() - raw.isna().sum()
        print(f"  {col}: 解析失败 {bad:,} 行")
    return df


def harmonize_object_columns(df: pd.DataFrame) -> pd.DataFrame:
    """把混类型的 object 列整理干净，否则写 parquet 会报 ArrowTypeError。

    规则：能整体转成数字的转数字，否则统一转成字符串。
    """
    converted: list[str] = []
    stringified: list[str] = []
    for col in df.columns[df.dtypes == "object"]:
        coerced = pd.to_numeric(df[col], errors="coerce")
        if coerced.notna().sum() == df[col].notna().sum():
            df[col] = coerced
            converted.append(col)
        else:
            df[col] = df[col].astype("string")
            stringified.append(col)
    if converted:
        print(f"  转数值：{', '.join(converted)}")
    if stringified:
        print(f"  转文本：{', '.join(stringified)}")
    return df


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true", help="忽略已有缓存，强制重建")
    args = parser.parse_args()

    DATA_DIR.mkdir(parents=True, exist_ok=True)

    if CACHE_ALL.exists() and CACHE_MAIN.exists() and not args.force:
        print(f"缓存已存在，跳过：{CACHE_ALL.name}、{CACHE_MAIN.name}（如需重建加 --force）")
        return 0

    if not RAW_XLSX.exists():
        print(f"[错误] 找不到原始数据：{RAW_XLSX}")
        return 1

    print(f"读取 xlsx：{RAW_XLSX}（约 1 分钟，仅此一次）")
    df = pd.read_excel(RAW_XLSX, engine="openpyxl")
    print(f"读取完成：{df.shape[0]:,} 行 × {df.shape[1]} 列")
    df = parse_date_columns(df)
    df = harmonize_object_columns(df)

    df.to_parquet(CACHE_ALL, index=False)
    print(f"已写出全量缓存：{CACHE_ALL}")

    dates = pd.to_datetime(df[ORDER_DATE], errors="coerce")
    mask = (dates >= MAIN_START) & (dates <= MAIN_END)
    main_df = df.loc[mask].copy()
    main_df.to_parquet(CACHE_MAIN, index=False)
    print(f"主分析窗口 {MAIN_START} ~ {MAIN_END}：{main_df.shape[0]:,} 行 -> {CACHE_MAIN.name}")

    check = pd.read_parquet(CACHE_MAIN)
    print(f"回读校验：{check.shape[0]:,} 行 × {check.shape[1]} 列")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
