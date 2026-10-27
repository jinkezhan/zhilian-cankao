"""FreshRetailNet-50K: 第 1 步数据核查（只读，不落盘）。

运行：.venv\\Scripts\\python.exe -I src\\freshretail\\inspect_schema.py
"""
from __future__ import annotations

import pyarrow.parquet as pq

FILES = [
    "data/freshretail/train.parquet",
    "data/freshretail/eval.parquet",
]


def main() -> None:
    for f in FILES:
        pf = pq.ParquetFile(f)
        md = pf.metadata
        print("=" * 60)
        print(f)
        print("rows:", md.num_rows, "| row_groups:", md.num_row_groups)
        print("-" * 60)
        for field in pf.schema_arrow:
            print(f"  {field.name:<24} {field.type}")
        print("-" * 60)
        # 列级统计：先看 dt 范围，避免整表加载
        for name in ("dt",):
            idx = pf.schema_arrow.get_field_index(name)
            if idx >= 0:
                col = pf.read(columns=[name]).column(name)
                print(f"  {name} min/max:", col.to_pandas().min(), col.to_pandas().max())


if __name__ == "__main__":
    main()
