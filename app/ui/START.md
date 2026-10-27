# 智链参谋 · 生鲜前置仓看板

## 启动

在项目根目录 `zhilian-cankao/` 执行：

```powershell
C:\Users\15800\anaconda3\python.exe -m streamlit run app/ui/dashboard.py
```

启动后访问 `http://localhost:8501`。

当前 B 电脑使用 Anaconda Python（Streamlit 1.30）。`dashboard.py` 已做 1.30 兼容处理，
不再使用 `width="stretch"`、`vertical_alignment` 等 1.66 新 API。

## 页面

- 默认：**生鲜前置仓**，四区块为今日概览、库存健康与补货清单、未来 7 天日销预测、补货 Agent。
- 顶部可切换：**制造企业（保底）**，保留 DataCo 原有看板。
- 生鲜页面单位按展示口径显示为 `kg/份`；底层是 FreshRetailNet 归一化销量指数。

## 数据接口

生鲜看板只读以下文件，不重算模型指标：

| 用途 | 文件 |
|---|---|
| 库存 / 临期 / 建议订货 | `output/freshretail_inventory_replenishment.csv` |
| 未来 7 天日销预测 | `output/freshretail_forecast_predictions.csv` |
| 三策略 × 保质期对比 | `output/freshretail_inventory_policy_comparison.csv` |
| 效期倍数扫描 | `output/freshretail_inventory_cap_sweep.csv` |
| 序列历史汇总 | `output/freshretail_sku_summary_all.csv` |

DataCo 保底数据仍读取 `output/forecast_cumulative_predictions.csv`、
`inventory_replenishment.csv` 等原文件。

## 目录说明

- `dashboard.py`：唯一运行入口。
- `backup/main.py`、`backup/views/`：归档的旧制造业 UI，不参与运行。
- `data_loader.py`、`theme.py`、`components.py`、`agent_tools.py`：旧 UI 辅助文件，
  dashboard 不依赖它们。
- 运行依赖见项目根目录 `requirements.txt`；`app/agent.py` 需要 `requests`。

## 补货 Agent

- 需要 `.env` 或环境变量 `DEEPSEEK_API_KEY`；没有密钥时，图表与库存正常，对话区显示暂不可用。
- 生鲜系统提示词位于 `app/freshretail_agent/tools_freshretail.py` 的 `FRESH_SYSTEM_PROMPT`。
