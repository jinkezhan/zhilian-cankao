# 项目环境约定（Claude Code 必须遵守）

## 1. Python 解释器：只用项目内的 .venv

- **唯一允许的解释器**：`D:\mypro\zhilian-cankao\.venv\Scripts\python.exe`
- **禁止**使用 `python`、`py`、`python3`、`pip` 等裸命令。本机 `python` 会解析到 `D:\msys2\mingw64\bin\python.exe`（MSYS2 版，平台标签 `mingw_x86_64_msvcrt_gnu`，装不了 PyPI 的 win_amd64 wheel），一旦用错就会建出畸形 venv。
- **禁止**删除、重建、清空 `.venv`。环境已建好并验证通过，见下方版本清单。
- 需要装新包时：`.venv\Scripts\python.exe -m pip install <包名> -i https://pypi.tuna.tsinghua.edu.cn/simple`

## 2. 已安装依赖（已验证可导入，勿随意升级）

| 包 | 版本 |
|---|---|
| Python | 3.14.8（python.org CPython，win-amd64） |
| pandas | 3.1.0 |
| numpy | 2.5.4 |
| openpyxl | 3.1.5 |
| pyarrow | 26.0.0 |
| matplotlib | 3.11.2 |
| scikit-learn | 1.9.1 |
| lightgbm | 4.7.0 |
| streamlit | 1.66.1 |

注意 pandas 3.x 与 1.x/2.x 有行为差异（默认 string dtype、Copy-on-Write），写代码按 pandas 3 的写法，不要照抄旧教程。

## 3. 数据

- 原始数据：`D:\ai-race data\DataCoSupplyChainDataset.xlsx`（56 MB，180,519 行 × 53 列）
- **禁止每次直接读 xlsx**（一次约 80 秒）。第一次读取后转成缓存：`data/clean_raw.parquet`，之后一律读 parquet。
- **数据存在结构性断层**：2017-10 ~ 2018-01 这四个月每单仅 1 行、每行仅 1 件、且仅覆盖美国/波多黎各，统计口径与前期不同。建模与评估窗口统一用 **2015-01-01 ~ 2017-09-30**（剔除 8,550 行），不要混用。
- 数据集特征：仅约 100 个有效 SKU（`Product Card Id` 非空 150 个）、Top10 SKU 占需求量 88.5%、日变异系数 0.124（非常平稳）。评估时务必给出基线对比（移动平均/季节朴素），不要把"预测很准"当成结论。

## 4. 运行与调试

- 运行前先自检：`.venv\Scripts\python.exe check_env.py`
- 运行脚本：`.venv\Scripts\python.exe app\<脚本>.py`
- 启动界面：`.venv\Scripts\python.exe -m streamlit run app\<脚本>.py`
- 中文输出：运行时设置 `PYTHONUTF8=1`、`PYTHONIOENCODING=utf-8`
- 路径含空格（`D:\ai-race data`），命令行里必须加引号
