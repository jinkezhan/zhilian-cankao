# 智链参谋 · 前端界面说明

## 启动

在项目根目录 `zhilian-cankao/` 执行：

```powershell
.venv\Scripts\python.exe -m streamlit run app/ui/main.py
```

当前传输目录里没有 `.venv`。若暂时不重建环境，可用本机可用的 Python（需已安装
streamlit、pandas、numpy、altair）：

```powershell
C:\Users\15800\anaconda3\python.exe -m streamlit run app/ui/main.py
```

启动后访问 `http://localhost:8501`。

## 文件结构

```
app/ui/
├── main.py            入口：侧边栏、导航、路由
├── data_loader.py     唯一的数据读取层（只读 output/*.csv）
├── theme.py           深蓝色主题、CSS、状态徽标配色
├── components.py      KPI、卡片、表格、Altair 图表组件
└── views/
    ├── demand.py          需求预测
    ├── inventory.py       库存健康
    ├── replenishment.py   补货建议
    ├── strategy.py        策略对比
    └── assistant.py       智能问答（规则演示）
```

## 数据接口

页面只读以下文件，不重算模型指标：

| 用途 | 文件 |
|---|---|
| 累计需求回测 | `output/forecast_cumulative_predictions.csv` |
| 整体回测指标 | `output/forecast_cumulative_metrics.csv` |
| 单 SKU 回测 | `output/forecast_selection_by_sku.csv` |
| 历史周需求 | `output/demand_weekly.csv` |
| 库存状态 / 补货 | `output/inventory_replenishment.csv` |
| 三策略仿真 | `output/inventory_policy_simulation.csv` |

重新运行模型脚本后，点击侧边栏「刷新数据」清除缓存即可看到新产物。

## Agent 兜底回归测试

```powershell
C:\Users\15800\anaconda3\python.exe app/ui/tests/test_agent_fallback.py
```

测试会验证不存在的 SKU（如 99999）返回明确报错，且不会回退到补货清单；
同时回归有效 SKU 和批量补货查询。

## 说明

- 图表使用 Altair；它随 Streamlit 一起安装，**不需要额外安装 plotly**。
- 所有主题样式在 `app/ui/theme.py` 中注入；项目根目录新增了
  `.streamlit/config.toml` 用于统一 Streamlit 控件配色。
- 智能问答目前是读取 CSV 的规则演示，未接入 `AGENT_DESIGN.md` 中的 LLM 对话层。
