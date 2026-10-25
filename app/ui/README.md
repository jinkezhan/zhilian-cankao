# 前端同学的专属工作区（app/ui/）

这个文件夹归你所有，里面的文件可以随便改——外面的核心代码不用碰，也不会有人覆盖你。

## 你要做什么

在 `app/ui/` 里写 Streamlit 页面（先画原型图，10/31 正式开工前跑通 hello world 就行）。

## 数据从哪来（只读，不要改）

页面显示的全部数据来自 `output/` 里这几张 csv（列名就是接口，只读既有列，不要自己重算指标）：

| 用途 | 文件 | 关键列 |
|---|---|---|
| 需求预测曲线 | `forecast_cumulative_predictions.csv` | sku_id / week / p50 / p90 / p95 / actual |
| 库存状态表 | `inventory_replenishment.csv` | sku_id / available_inventory / reorder_point / safety_stock / target_level / recommended_order_qty / urgency / drift_status / reason |
| 三策略对比 | `inventory_policy_simulation.csv` | policy / service_rate / avg_inventory / total_cost |
| 历史需求 | `demand_weekly.csv` | 周度需求序列 |

## 环境怎么建（一次性，约 10 分钟）

1. 装好 Python 3.13 或 3.14（勾 Add to PATH）
2. 在项目根目录执行：

```
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
.venv\Scripts\python.exe check_env.py
```

3. 看到"全部通过"就开工；不用装数据集、不用训练模型。

## 每天怎么交接

收工前把 `app/ui/` 整个文件夹发群里（只有这一个文件夹是你的），开发线收到后覆盖进主项目。其他任何文件夹都不要发、不要改。
