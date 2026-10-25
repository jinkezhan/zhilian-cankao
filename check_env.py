"""环境自检脚本：一条命令确认运行环境是否健康。

用法（必须用项目内的解释器，不要用全局 python）：
    .venv\\Scripts\\python.exe check_env.py

检查项：
  1. 解释器是否为项目 .venv（避免误用系统 Python / MSYS2 Python）
  2. 依赖是否齐全、版本是否达标
  3. 数据文件是否存在
"""

from __future__ import annotations

import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATA_CANDIDATES = [
    Path(r"D:\ai-race data\DataCoSupplyChainDataset.xlsx"),
    BASE_DIR / "data" / "DataCoSupplyChainDataset.xlsx",
    BASE_DIR / "data" / "clean_raw.parquet",
    BASE_DIR / "data" / "clean_raw.csv",
]

REQUIRED = {
    "pandas": "2.0",
    "numpy": "1.24",
    "matplotlib": "3.7",
    "openpyxl": "3.1",
    "sklearn": "1.3",
    "lightgbm": "4.0",
    "streamlit": "1.30",
}


def main() -> int:
    problems: list[str] = []

    print(f"解释器 : {sys.executable}")
    print(f"版本   : {sys.version.split()[0]}")
    in_venv = Path(sys.prefix).name == ".venv"
    print(f"虚拟环境: {'是' if in_venv else '否  <-- 请改用 .venv\\\\Scripts\\\\python.exe'}")
    if not in_venv:
        problems.append("未在项目 .venv 中运行")

    print("\n依赖：")
    for name, minimum in REQUIRED.items():
        try:
            mod = __import__(name)
            ver = getattr(mod, "__version__", "?")
            print(f"  [OK]   {name:<12} {ver}")
        except Exception as exc:  # noqa: BLE001
            print(f"  [缺失] {name:<12} ({exc.__class__.__name__})")
            problems.append(f"缺少 {name}")

    print("\n数据文件：")
    found = None
    for path in DATA_CANDIDATES:
        if path.exists():
            size_mb = path.stat().st_size / 1024 / 1024
            print(f"  [找到] {path}  ({size_mb:.1f} MB)")
            found = path
            break
    if found is None:
        print("  [未找到] 请把 DataCoSupplyChainDataset.xlsx 放到 D:\\ai-race data\\ 或 data\\ 下")
        problems.append("数据文件缺失")

    print("\n结论：" + ("环境健康，可以开工" if not problems else "存在问题 -> " + "；".join(problems)))
    return 0 if not problems else 1


if __name__ == "__main__":
    raise SystemExit(main())
