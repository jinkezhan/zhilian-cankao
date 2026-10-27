# Python 虚拟环境搭建说明

本项目使用 Python 自带的 `venv` 模块创建独立虚拟环境，避免与系统或其他项目的依赖冲突。

## 环境要求

- Python 3.10 及以上
- Windows 下使用 PowerShell

查看本机 Python 版本：

```powershell
python --version
```

## 一、创建虚拟环境

在项目根目录（`zhilian-canmou/`）下执行：

```powershell
cd D:\mypro\zhilian-canmou
python -m venv .venv
```

执行后会在项目根目录生成 `.venv/` 文件夹，其中包含独立的 Python 解释器和 pip。

## 二、激活虚拟环境

**Windows PowerShell：**

```powershell
.\.venv\Scripts\Activate.ps1
```

**Windows CMD：**

```bat
.venv\Scripts\activate.bat
```

**macOS / Linux：**

```bash
source .venv/bin/activate
```

激活成功后，命令行提示符前会出现 `(.venv)` 标识。

> 若 PowerShell 提示“禁止运行脚本”，可先执行：
>
> ```powershell
> Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
> ```
>
> 或仅对当前窗口临时放开：
>
> ```powershell
> Set-ExecutionPolicy -Scope Process Bypass
> ```

## 三、安装依赖

```powershell
python -m pip install --upgrade pip
pip install -r requirements.txt
```

依赖清单见 `requirements.txt`：pandas、numpy、scikit-learn、lightgbm、streamlit、matplotlib、openpyxl（读取 Excel 数据需要）。

验证安装：

```powershell
pip list
```

## 四、使用与退出

- 每次开发前先激活虚拟环境；
- 运行脚本：`python app/xxx.py`
- 启动界面：`streamlit run app/xxx.py`
- 退出虚拟环境：`deactivate`

## 五、常见问题

| 问题 | 处理方式 |
| --- | --- |
| `python` 命令不存在 | 安装 Python 时勾选 “Add Python to PATH”，或用 `py -3 -m venv .venv` |
| 激活脚本被系统阻止 | 参见上文调整执行策略 |
| 依赖安装缓慢 | 使用国内镜像：`pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple` |
| LightGBM 安装失败 | 确认 Python 为 64 位，必要时先升级 pip 或安装 Visual C++ 运行库 |

## 六、注意事项

- `.venv/` 不应提交到版本库，建议加入 `.gitignore`；
- 不要直接修改 `.venv/` 内的文件；
- 环境损坏时可删除 `.venv/` 后重新创建。
