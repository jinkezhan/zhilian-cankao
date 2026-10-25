"""Agent 兜底回归测试：不存在的 SKU 不得回退到补货清单。

运行：
    C:\\Users\\15800\\anaconda3\\python.exe app/ui/tests/test_agent_fallback.py

测试不依赖 Streamlit，直接读取 output/*.csv。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

UI_DIR = Path(__file__).resolve().parents[1]
OUTPUT_DIR = Path(__file__).resolve().parents[3] / "output"
sys.path.insert(0, str(UI_DIR))

from agent_tools import (  # noqa: E402
    SYSTEM_PROMPT,
    answer_question,
    extract_sku_id,
    query_inventory,
)

EXACT_BUG_QUESTION = "帮我查一下 SKU 99999 的库存"
UNKNOWN_SKU = 99999
VALID_SKU = 365


def load_data():
    inventory = pd.read_csv(
        OUTPUT_DIR / "inventory_replenishment.csv", encoding="utf-8-sig"
    )
    predictions = pd.read_csv(
        OUTPUT_DIR / "forecast_cumulative_predictions.csv",
        parse_dates=["week"],
        encoding="utf-8-sig",
    )
    selection = pd.read_csv(
        OUTPUT_DIR / "forecast_selection_by_sku.csv", encoding="utf-8-sig"
    )
    simulation = pd.read_csv(
        OUTPUT_DIR / "inventory_policy_simulation.csv", encoding="utf-8-sig"
    )

    first = inventory.iloc[0]
    context = {
        "snapshot_week": pd.to_datetime(first["snapshot_week"]),
        "horizon_weeks": int(first["horizon_weeks"]),
        "lead_time_weeks": int(first["lead_time_weeks"]),
        "review_period_weeks": int(first["review_period_weeks"]),
        "service_level_target": float(first["service_level_target"]),
    }
    return inventory, predictions, selection, simulation, context


def main() -> int:
    inventory, predictions, selection, simulation, context = load_data()
    results: list[bool] = []

    def check(name: str, condition: bool, detail: str = "") -> None:
        results.append(condition)
        print(f"[{'PASS' if condition else 'FAIL'}] {name}")
        if detail:
            print(f"       {detail}")

    print("=" * 78)
    print("1. 提取 SKU 编号")
    print("=" * 78)
    extracted = extract_sku_id(EXACT_BUG_QUESTION)
    check("从「SKU 99999」中提取出 99999", extracted == UNKNOWN_SKU, f"extracted={extracted}")
    year_extracted = extract_sku_id("2017 年哪些 SKU 需要补货？")
    check("年份 2017 不会被误判为 SKU", year_extracted is None, f"extracted={year_extracted}")

    print()
    print("=" * 78)
    print("2. query_inventory(99999) 的结构化返回")
    print("=" * 78)
    lookup = query_inventory(UNKNOWN_SKU, inventory)
    print(lookup)
    check("ok 为 True（查询已正常执行）", lookup.get("ok") is True)
    check("found 为 False", lookup.get("found") is False)
    check("reason=unknown_sku", lookup.get("reason") == "unknown_sku")
    check(
        "message 明确提示未找到",
        "未找到 SKU 99999" in str(lookup.get("message", "")),
        str(lookup.get("message", "")),
    )
    check(
        "返回 available_skus 供引导",
        bool(lookup.get("available_skus")) and VALID_SKU in lookup["available_skus"],
        str(lookup.get("available_skus")),
    )
    check("未返回其他 SKU 的业务数据", "data" not in lookup)

    print()
    print("=" * 78)
    print("3. Agent 回答用户原始问题")
    print("   问题：" + EXACT_BUG_QUESTION)
    print("=" * 78)
    answer = answer_question(
        EXACT_BUG_QUESTION,
        inventory=inventory,
        predictions=predictions,
        selection=selection,
        simulation=simulation,
        context=context,
    )
    print(answer)
    print("-" * 78)
    plain_answer = answer.replace("**", "")
    check("回答明确包含「未找到 SKU 99999」", "未找到 SKU 99999" in plain_answer)
    check("回答包含可查询 SKU 引导", "可查询的 SKU" in plain_answer)
    check("没有输出补货建议表头", "建议订货量" not in plain_answer)
    check("没有输出其他 SKU 名称", "Perfect Fitness" not in plain_answer)
    check("没有输出其他 SKU 的订货量 608", "608" not in plain_answer)

    print()
    print("=" * 78)
    print("4. 未知 SKU 的预测问题也不能回退")
    print("=" * 78)
    forecast_answer = answer_question(
        "SKU 99999 未来 4 周需求如何？",
        inventory=inventory,
        predictions=predictions,
        selection=selection,
        simulation=simulation,
        context=context,
    )
    print(forecast_answer)
    plain_forecast = forecast_answer.replace("**", "")
    check("预测类问题同样提示未找到", "未找到 SKU 99999" in plain_forecast)
    check("预测类问题没有返回 P50/P90/P95", "P50" not in plain_forecast and "P90" not in plain_forecast)

    print()
    print("=" * 78)
    print("5. 正常路径回归")
    print("=" * 78)
    batch_answer = answer_question(
        "哪些 SKU 需要补货？",
        inventory=inventory,
        predictions=predictions,
        selection=selection,
        simulation=simulation,
        context=context,
    )
    valid_lookup = query_inventory(VALID_SKU, inventory)
    valid_answer = answer_question(
        "SKU 365 的库存和建议订货量是多少？",
        inventory=inventory,
        predictions=predictions,
        selection=selection,
        simulation=simulation,
        context=context,
    )
    check("批量补货问题仍返回 3 个 SKU", "3 个 SKU" in batch_answer, batch_answer.splitlines()[0])
    check("有效 SKU 365 查询 found=True", valid_lookup.get("found") is True)
    check("有效 SKU 365 回答包含建议订货量", "建议订货量" in valid_answer and "608" in valid_answer)

    print()
    print("=" * 78)
    print("6. System Prompt 兜底指令")
    print("=" * 78)
    check("提示词包含强制兜底段", "未找到数据时的强制兜底" in SYSTEM_PROMPT)
    check("提示词禁止返回无关补货数据", "绝对禁止返回其他无关的补货数据来凑数" in SYSTEM_PROMPT)
    check("提示词要求引导 available_skus", "available_skus" in SYSTEM_PROMPT)

    print()
    print("=" * 78)
    passed = sum(results)
    total = len(results)
    print(f"测试结果：{passed}/{total} 通过")
    print("=" * 78)
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
