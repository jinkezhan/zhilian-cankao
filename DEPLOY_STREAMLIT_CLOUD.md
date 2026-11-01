# 智链参谋 · Streamlit Community Cloud 部署指南

> 目标：把 `app/ui/dashboard.py` 部署到 `https://share.streamlit.io`，使用 GitHub 仓库 `jinkezhan/zhilian-cankao`。

## 0. 当前状态

- 本地项目：`D:\new_project\zhilian-cankao`
- 当前分支：`ui`
- 已有远程：Gitee `origin`
- GitHub 远程：尚未添加
- 已准备：
  - 云端兼容的 `requirements.txt`（Python 3.11 实测版本）
  - `packages.txt`（安装中文字体，保证 PDF 中文正常）
  - `DEEPSEEK_API_KEY` 同时支持 `.env`、环境变量、Streamlit `st.secrets`
  - 运行所需 `output/*.csv`、`config/users.yaml`、`app/*.py`

---

## 1. 推送到 GitHub

### 1.1 添加 GitHub 远程

```powershell
& 'C:\Program Files\Git\cmd\git.exe' -C 'D:\new_project\zhilian-cankao' remote add github https://github.com/jinkezhan/zhilian-cankao.git
```

如果提示 remote 已存在：

```powershell
& 'C:\Program Files\Git\cmd\git.exe' -C 'D:\new_project\zhilian-cankao' remote set-url github https://github.com/jinkezhan/zhilian-cankao.git
```

### 1.2 推送 `ui` 分支

```powershell
& 'C:\Program Files\Git\cmd\git.exe' -C 'D:\new_project\zhilian-cankao' push -u github ui
```

- 用户名：GitHub 账号 `jinkezhan`
- 密码：**Personal Access Token（PAT）**，不是 GitHub 登录密码
  - GitHub → Settings → Developer settings → Personal access tokens → Tokens (classic) → Generate new token
  - 勾选 `repo` 权限，复制 `ghp_...`
- 如果仓库还没有任何分支，这一步会创建 `ui` 分支。

### 1.3 如果推送时报 `Connection was reset`

这是当前命令行网络无法直连 GitHub（本次检查时确实如此），不是仓库问题。三选一：

**A. 开启代理/VPN 的全局模式**，然后给 Git 设置代理（端口以你的客户端为准，常见 7890）：

```powershell
& 'C:\Program Files\Git\cmd\git.exe' config --global http.proxy http://127.0.0.1:7890
& 'C:\Program Files\Git\cmd\git.exe' config --global https.proxy http://127.0.0.1:7890
```

推送成功后可以取消：

```powershell
& 'C:\Program Files\Git\cmd\git.exe' config --global --unset http.proxy
& 'C:\Program Files\Git\cmd\git.exe' config --global --unset https.proxy
```

**B. 用 GitHub Desktop**：Add existing repository → 选择 `D:\new_project\zhilian-cankao` → Publish/Push `ui` 分支。浏览器能打开 GitHub 时，桌面端通常也能走系统代理。

**C. 在 GitHub 网页手动上传**：不推荐，仓库有 60+ 个文件和 7.7MB 数据，容易漏。

---

## 2. 在 Streamlit Community Cloud 新建 App

1. 打开 <https://share.streamlit.io>，用 GitHub 账号登录并授权；
2. 点击 **Create app** / **New app**；
3. 选择 **Deploy a public app from GitHub** / **Yup, I have an app**；
4. 填写：
   - **Repository**：`jinkezhan/zhilian-cankao`
   - **Branch**：`ui`
   - **Main file path**：`app/ui/dashboard.py`
   - **App URL**：自定义，例如 `zhilian-cankao`（需全局唯一）
5. 点 **Advanced settings...**：
   - **Python version**：选 **3.11**
   - **Secrets**：粘贴（TOML 格式）：

```toml
DEEPSEEK_API_KEY = "sk-你的真实密钥"
APP_SESSION_SECRET = "一串足够长的随机字符串"
```

生成 `APP_SESSION_SECRET`：

```powershell
C:\Users\15800\anaconda3\python.exe -c "import secrets; print(secrets.token_hex(32))"
```

6. 点击 **Deploy**，等待依赖安装（首次 2–5 分钟）。
7. 部署日志出现 `You can now view your Streamlit app` 即成功。

> 账号使用仓库里的 `config/users.yaml`（只存 PBKDF2 哈希，不存明文）。
> 演示口令不写入仓库，请向管理员索取；如需改口令，用
> `python app/hash_password.py "新口令"` 生成哈希后替换 `users.yaml`。

---

## 3. 代码里的密钥读取逻辑（已改好）

优先级统一为：

```text
进程环境变量 > Streamlit st.secrets > 本地 .env
```

涉及位置：

- `app/agent.py` → `load_api_key()`：Agent / 全局 AI / 行内 AI；
- `app/daily_brief.py` → `_load_api_key()`：可选 LLM 润色；
- `app/ui/dashboard.py` → `_session_secret()`、`_load_login_credentials()`：登录 token 与会话。

云端没有 `.env` 也能正常拿到 `DEEPSEEK_API_KEY`；本地开发仍然读 `.env`，两边互不影响。

---

## 4. 数据与持久化说明

- 页面读取的 `output/*.csv` 已随仓库上传，云端可以直接加载；
- 以下运行时文件会写入应用容器：
  - `output/decision_items.csv`
  - `output/decision_batches.csv`
  - `output/saved_views.json`
- **Streamlit Community Cloud 的文件系统是临时的**：应用重启/重新部署后这些写入可能丢失；页面上的导出（CSV/Excel/PDF）不受影响。
- 如果要做长期正式使用，建议把补货单和视图改存到数据库 / Google Sheets / S3，或部署到带持久盘的云服务器。

---

## 5. 常见问题

| 现象 | 处理 |
|---|---|
| 依赖安装失败 | 确认 Python 选 3.11；`requirements.txt` 已按本地实测版本锁定 |
| 页面 404 / 主文件找不到 | Main file path 必须是 `app/ui/dashboard.py`，注意大小写 |
| AI 不可用 | 检查 Secrets 里 `DEEPSEEK_API_KEY` 名称和引号；改完保存后重启 App |
| PDF 中文变方块 | `packages.txt` 已声明 `fonts-noto-cjk`；若仍异常，先用 Excel 日报 |
| 登录刷新后掉线 | 检查 Secrets 里是否配置了 `APP_SESSION_SECRET`；同一密钥才能校验旧 token |
| 补货单第二天消失 | 云端文件系统临时，属预期；长期使用需接持久化存储 |
| 页面公开可见 | Community Cloud 默认公开；当前是“可浏览、写操作需登录”，如需全站登录需再加一道门槛 |

---

## 6. 部署后自检

- [ ] 首页能加载，顶栏日期为当天，Hero 显示 2 紧急 / 29 临期 / 3,056 件
- [ ] 库存表能筛选、分页、导出 CSV
- [ ] 选 `SKU 145-802` 三个溯源分页正常
- [ ] 点「问 AI 解释」能返回工具调用
- [ ] 登录店长后能确认补货单、审批/下单/到货
- [ ] Excel/PDF 日报能下载，PDF 中文正常
- [ ] 退出登录后写操作被禁用
