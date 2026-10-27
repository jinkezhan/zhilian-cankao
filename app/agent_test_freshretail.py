"""生鲜线 Agent 测试（第二轮验收用）。

内容：
1. 单元测试：直接调用四个工具，不经过 LLM，核对数字与 csv 一致；
   另用构造序列验证“正常档订货量必为 0”，并用临时快照验证 Agent 层断言。
2. Agent 用例：≥10 条，覆盖预测、库存、补货、批量风险、临期、跨店跨 SKU、
   不存在序列、空数据、正常档提问、连续追问、P90 说明、品类脱敏。
3. 输出：output/freshretail_agent_test_cases.csv（问题/工具调用链/回答全文/人工判定）。

运行：
    .venv\\Scripts\\python.exe app\\agent_test_freshretail.py
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

import pandas as pd

APP_DIR = Path(__file__).resolve().parent
BASE_DIR = APP_DIR.parent
OUTPUT_DIR = BASE_DIR / "output"
TEST_CSV = OUTPUT_DIR / "freshretail_agent_test_cases.csv"
REPL_PATH = OUTPUT_DIR / "freshretail_inventory_replenishment.csv"
FORECAST_PATH = OUTPUT_DIR / "freshretail_forecast_predictions.csv"

sys.path.insert(0, str(APP_DIR))
import agent  # noqa: E402
from freshretail_agent.tools_freshretail import (  # noqa: E402
    FreshDataStore,
    evaluate_state,
)

agent.use_dataset("freshretail")

FORBIDDEN_NAMES = ["苹果", "猪肉", "蔬菜", "水果", "牛肉", "鸡肉"]
FORECAST_COLS = [
    "store_id", "product_id", "dt", "horizon", "y_actual",
    "y_pred_p50", "y_pred_p90", "stockout_rate",
]
REPL_COLS = [
    "store_id", "product_id", "shelf_life_days", "on_hand", "rop_p90",
    "target_level", "suggest_qty", "urgency", "expiring_qty",
]
SUMMARY_COLS = ["store_id", "product_id", "selected"]


def _flat(text: str) -> str:
    return text.replace(",", "").replace("，", "").replace(" ", "")


def _tools_text(logs: list[dict]) -> str:
    if not logs:
        return "（无）"
    return "; ".join(
        f"{log['name']}({json.dumps(log['args'], ensure_ascii=False)})" for log in logs
    )


def _write_empty_fixture(directory: Path) -> None:
    pd.DataFrame(columns=FORECAST_COLS).to_csv(
        directory / "freshretail_forecast_predictions.csv", index=False, encoding="utf-8-sig"
    )
    pd.DataFrame(columns=REPL_COLS).to_csv(
        directory / "freshretail_inventory_replenishment.csv", index=False, encoding="utf-8-sig"
    )
    pd.DataFrame(columns=SUMMARY_COLS).to_csv(
        directory / "freshretail_sku_summary_all.csv", index=False, encoding="utf-8-sig"
    )


def _write_normal_fixture(directory: Path, suggest: int) -> None:
    row = {
        "store_id": 1,
        "product_id": 1,
        "shelf_life_days": 5,
        "on_hand": 20.0,
        "rop_p90": 5.0,
        "target_level": 10.0,
        "suggest_qty": suggest,
        "urgency": "正常",
        "expiring_qty": 0.0,
    }
    pd.DataFrame([row], columns=REPL_COLS).to_csv(
        directory / "freshretail_inventory_replenishment.csv", index=False, encoding="utf-8-sig"
    )
    pd.DataFrame(columns=FORECAST_COLS).to_csv(
        directory / "freshretail_forecast_predictions.csv", index=False, encoding="utf-8-sig"
    )
    pd.DataFrame(columns=SUMMARY_COLS).to_csv(
        directory / "freshretail_sku_summary_all.csv", index=False, encoding="utf-8-sig"
    )


def unit_tests() -> tuple[int, int, list[tuple[str, bool, str]]]:
    """直接工具测试；返回 (通过数, 总数, 明细)。"""
    results: list[tuple[str, bool, str]] = []
    store = FreshDataStore()
    rep = pd.read_csv(REPL_PATH, encoding="utf-8-sig")
    fc = pd.read_csv(FORECAST_PATH, encoding="utf-8-sig")

    # 1) 全部 200 条建议订货量与 csv 一致
    bad = []
    for row in rep.itertuples():
        got = store.recommend_replenishment(int(row.store_id), int(row.product_id))
        if got.get("suggest_qty") != int(row.suggest_qty):
            bad.append(f"{row.store_id}-{row.product_id}")
    results.append(("200 条建议订货量与补货快照一致", not bad, f"不一致 {len(bad)} 条"))

    # 2) 预测逐日 P50/P90 与 csv 一致
    f = fc[(fc.store_id == 3) & (fc.product_id == 834)].sort_values("dt")
    got = store.get_forecast(3, 834)
    p50_ok = [d["p50"] for d in got["days"]] == [round(float(x), 2) for x in f["y_pred_p50"]]
    p90_ok = [d["p90"] for d in got["days"]] == [round(float(x), 2) for x in f["y_pred_p90"]]
    results.append(("SKU 3-834 逐日 P50/P90 与预测表一致", p50_ok and p90_ok, "7 天逐日比对"))

    # 3) 库存字段与 csv 一致
    r = rep[(rep.store_id == 18) & (rep.product_id == 300)].iloc[0]
    inv = store.get_inventory_status(18, 300)
    inv_ok = (
        inv["on_hand"] == round(float(r.on_hand), 2)
        and inv["rop_p90"] == round(float(r.rop_p90), 2)
        and inv["target_level"] == round(float(r.target_level), 2)
        and inv["expiring_qty"] == round(float(r.expiring_qty), 2)
        and inv["expiring_qty"] > 0
        and "临期" in inv["expiring_alert"]
    )
    results.append(("SKU 18-300 库存/临期字段与快照一致", inv_ok, inv["expiring_alert"]))

    # 4) 推荐理由含效期上限数字
    rec = store.recommend_replenishment(145, 802)
    rec_ok = (
        rec["suggest_qty"] == 6
        and rec["urgency"] == "紧急"
        and "订货上限" in rec["cap_reason"]
        and f"{rec['cap_qty']:.2f}" in rec["cap_reason"]
    )
    results.append(("SKU 145-802 推荐量/效期上限理由", rec_ok, rec["cap_reason"]))

    # 5) 构造正常档：on_hand >= target，订货必为 0
    normal_state = evaluate_state(10.0, 4.0, 10.0, 10.0, 0.5, 1, 1)
    results.append(
        ("构造正常档：on_hand=target 判定正常且订货 0", normal_state == ("正常", 10.0, 0), str(normal_state))
    )

    # 6) Agent 层断言：正常档 suggest_qty > 0 必须报错
    with tempfile.TemporaryDirectory(prefix="normal_bad_") as tmp:
        _write_normal_fixture(Path(tmp), suggest=1)
        try:
            FreshDataStore(Path(tmp))
            raised = False
        except ValueError:
            raised = True
    with tempfile.TemporaryDirectory(prefix="normal_ok_") as tmp:
        _write_normal_fixture(Path(tmp), suggest=0)
        ok = FreshDataStore(Path(tmp)).list_risk_skus()["counts"]["total"] == 1
    results.append(("正常档订货量断言（构造快照）", raised and ok, "suggest=1 报错；suggest=0 通过"))

    # 7) 空数据兜底
    with tempfile.TemporaryDirectory(prefix="fresh_empty_") as tmp:
        _write_empty_fixture(Path(tmp))
        empty = FreshDataStore(Path(tmp)).list_risk_skus()
    results.append(
        ("空数据：计数为 0 且给出中文兜底", empty["counts"]["total"] == 0 and "没有" in empty["message"], empty["message"])
    )

    # 8) 不存在序列
    unknown = store.get_inventory_status(999, 999)
    results.append(("不存在序列返回 unknown_series", (not unknown["found"]) and unknown["reason"] == "unknown_series", unknown["message"]))

    # 9) 工具返回数据无编造品类名；标签格式统一
    risk = store.list_risk_skus()
    payloads = [
        json.dumps(store.get_forecast(3, 834), ensure_ascii=False),
        json.dumps(store.get_inventory_status(18, 300), ensure_ascii=False),
        json.dumps(store.recommend_replenishment(145, 802), ensure_ascii=False),
        json.dumps(risk, ensure_ascii=False),
    ]
    no_names = all(not any(word in p for word in FORBIDDEN_NAMES) for p in payloads)
    labels_ok = all(str(x["sku_label"]).startswith("SKU ") for x in risk["skus"])
    results.append(("工具返回无编造品类名；SKU 标签格式统一", no_names and labels_ok, "grep 禁用词 + 标签前缀"))

    passed = sum(1 for _, ok, _ in results if ok)
    return passed, len(results), results


CASES = [
    {
        "id": 1, "category": "预测",
        "question": "SKU 3-834 未来 7 天的 P50/P90 预测和缺货率是多少？",
        "expect": "调用 get_forecast；含 P50/P90/缺货率；说明 P90 已含缺货风险冗余",
    },
    {
        "id": 2, "category": "库存+临期",
        "question": "SKU 18-300 现在库存和临期情况怎么样？",
        "expect": "调用 get_inventory_status；26.36 件临期；提示优先促销",
    },
    {
        "id": 3, "category": "紧急补货",
        "question": "SKU 145-802 现在该补多少？",
        "expect": "调用 recommend_replenishment；6 件；紧急；含效期上限 7.06",
    },
    {
        "id": 4, "category": "批量风险",
        "question": "哪些序列需要补货？给我一个风险清单。",
        "expect": "调用 list_risk_skus；紧急 2、建议补货 153、计划补货 45、正常 0",
    },
    {
        "id": 5, "category": "正常档提问",
        "question": "有没有库存正常的序列？",
        "expect": "如实回答这批序列没有正常档（0 条），不编造",
    },
    {
        "id": 6, "category": "跨店跨 SKU",
        "question": "SKU 3-834 和 SKU 18-19 的库存分别怎么样？",
        "expect": "分别调用 get_inventory_status；两条序列数字不混",
    },
    {
        "id": 7, "category": "不存在序列",
        "question": "SKU 999-999 未来 7 天预测是多少？",
        "expect": "明确数据里没有这个序列，不编造数字",
    },
    {
        "id": 8, "category": "空数据",
        "question": "哪些序列需要补货？（空数据夹具）",
        "expect": "回答没有/0 条，不编造",
    },
    {
        "id": 9, "category": "批量临期",
        "question": "哪些序列有临期库存？",
        "expect": "调用 list_risk_skus；提到临期 29 条，建议优先促销",
    },
    {
        "id": 10, "category": "连续追问",
        "question": "先问“哪些序列紧急？”，再问“第一个呢？”",
        "expect": "第二轮定位到第一条紧急序列，建议订货 6 件",
    },
    {
        "id": 11, "category": "P90 解释",
        "question": "SKU 18-19 的 P90 为什么比 P50 高？",
        "expect": "调用 get_forecast；说明 P90 已含缺货风险冗余",
    },
    {
        "id": 12, "category": "计划补货",
        "question": "SKU 18-300 应该订多少？",
        "expect": "调用 recommend_replenishment；56 件；计划补货；上限约 81.44",
    },
    {
        "id": 13, "category": "品类脱敏",
        "question": "SKU 18-300 是什么商品？",
        "expect": "说明品类已脱敏，只能用 SKU 18-300 标识；不编造品类名",
    },
    {
        "id": 14, "category": "建议补货+临期",
        "question": "SKU 555-300 该补多少？临期怎么办？",
        "expect": "调用 recommend_replenishment；47 件；建议补货；临期 6.08 件优先促销",
    },
]


def _run_single(question: str, api_key: str, data_dir: Path | None = None) -> tuple[str, list[dict]]:
    store = agent.DataStore(data_dir)
    messages = [{"role": "system", "content": agent.SYSTEM_PROMPT}]
    messages.append({"role": "user", "content": question})
    return agent.run_turn(messages, store, api_key, echo=False)


def _judge(case_id: int, answer: str, logs: list[dict]) -> tuple[bool, str]:
    names = [log["name"] for log in logs]
    flat = _flat(answer)
    if case_id == 1:
        ok = (
            "get_forecast" in names
            and "3-834" in answer and "P50" in answer and "P90" in answer
            and ("缺货" in answer or "stockout" in answer.lower())
            and ("冗余" in answer or "缺货风险" in answer)
        )
        return ok, "工具/字段/P90 说明"
    if case_id == 2:
        ok = (
            "get_inventory_status" in names
            and "18-300" in answer and "26.36" in flat
            and ("临期" in answer or "促销" in answer)
        )
        return ok, "库存+临期提示"
    if case_id == 3:
        ok = (
            "recommend_replenishment" in names
            and "145-802" in answer and "6" in flat
            and "紧急" in answer and "订货上限" in answer
            and ("7.06" in flat or "7.1" in flat or "7" in flat)
        )
        return ok, "建议量/紧急度/效期上限"
    if case_id == 4:
        ok = (
            "list_risk_skus" in names
            and "紧急" in answer and "2" in flat
            and "153" in flat and "45" in flat
            and "正常" in answer and ("0" in flat or "没有" in answer)
            and "大部分" not in answer
        )
        return ok, "四档计数完整"
    if case_id == 5:
        ok = ("没有" in answer or "0 条" in answer or "0条" in answer) and "正常" in answer and "大部分" not in answer
        return ok, "不编造正常档"
    if case_id == 6:
        ok = (
            names.count("get_inventory_status") >= 2
            and "3-834" in answer and "18-19" in answer
            and "4" in flat and ("5.58" in flat or "29.99" in flat or "18.74" in flat)
        )
        return ok, "两条序列分别查询"
    if case_id == 7:
        ok = (
            "999-999" in answer and ("没有" in answer or "不存在" in answer)
            and any(log["result"].get("reason") == "unknown_series" for log in logs)
        )
        return ok, "unknown_series 兜底"
    if case_id == 8:
        ok = "没有" in answer or "0 条" in answer or "0条" in answer
        return ok, "空数据兜底"
    if case_id == 9:
        ok = "list_risk_skus" in names and "临期" in answer and ("29" in flat or "优先促销" in answer)
        return ok, "临期风险单列"
    if case_id == 10:
        ok = any(n in ("get_inventory_status", "recommend_replenishment") for n in names) and (
            "145-802" in answer or "781-802" in answer
        ) and "6" in flat
        return ok, "追问定位到首条紧急序列"
    if case_id == 11:
        ok = "get_forecast" in names and "P90" in answer and ("冗余" in answer or "缺货风险" in answer)
        return ok, "P90 缺货风险冗余"
    if case_id == 12:
        ok = (
            "recommend_replenishment" in names
            and "18-300" in answer and "56" in flat and "计划补货" in answer
            and "订货上限" in answer and ("81.44" in flat or "81.4" in flat or "81" in flat)
        )
        return ok, "计划补货/效期上限"
    if case_id == 13:
        ok = (
            "18-300" in answer
            and ("脱敏" in answer or "只有 SKU" in answer or "无法" in answer)
            and not any(word in answer for word in FORBIDDEN_NAMES)
        )
        return ok, "品类脱敏且无编造"
    if case_id == 14:
        ok = (
            "recommend_replenishment" in names
            and "555-300" in answer and "47" in flat and "建议补货" in answer
            and ("6.08" in flat or "6.1" in flat or "6" in flat)
            and ("临期" in answer or "促销" in answer)
        )
        return ok, "建议补货+临期"
    return False, "未知用例"


def _run_case(case: dict, api_key: str) -> dict:
    cid = case["id"]
    if cid == 8:
        with tempfile.TemporaryDirectory(prefix="fresh_agent_empty_") as tmp:
            _write_empty_fixture(Path(tmp))
            answer, logs = _run_single(case["question"], api_key, data_dir=Path(tmp))
    elif cid == 10:
        store = agent.DataStore()
        messages = [{"role": "system", "content": agent.SYSTEM_PROMPT}]
        messages.append({"role": "user", "content": "哪些序列紧急？"})
        answer1, logs1 = agent.run_turn(messages, store, api_key, echo=False)
        messages.append({"role": "user", "content": "第一个呢？"})
        answer2, logs2 = agent.run_turn(messages, store, api_key, echo=False)
        answer = f"第一轮：{answer1}\n第二轮：{answer2}"
        logs = logs1 + logs2
    else:
        answer, logs = _run_single(case["question"], api_key)

    ok, note = _judge(cid, answer, logs)
    return {
        "case": case,
        "answer": answer,
        "tools": _tools_text(logs),
        "ok": ok,
        "note": note,
        "n_tools": len(logs),
    }


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:
            pass

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    print("=" * 60)
    print("单元测试（不经过 LLM）")
    up, ut, details = unit_tests()
    for name, ok, note in details:
        print(f"  [{'OK' if ok else 'FAIL'}] {name} — {note}")
    print(f"单元测试：{up}/{ut} 通过")

    api_key = agent.load_api_key(agent.ENV_PATH)
    rows: list[dict] = []
    passed = 0
    if not api_key:
        print("\n未找到 DEEPSEEK_API_KEY，Agent 用例未执行。")
    else:
        print("\n" + "=" * 60)
        print(f"Agent 用例（{len(CASES)} 条）")
        for case in CASES:
            try:
                outcome = _run_case(case, api_key)
            except agent.AgentError as exc:
                outcome = {
                    "case": case,
                    "answer": f"模型调用失败：{exc}",
                    "tools": "（调用失败）",
                    "ok": False,
                    "note": f"exit_code={exc.exit_code}",
                    "n_tools": 0,
                }
            except Exception as exc:  # 测试兜底，保留失败原因
                outcome = {
                    "case": case,
                    "answer": f"测试执行异常：{type(exc).__name__}: {exc}",
                    "tools": "（异常）",
                    "ok": False,
                    "note": "见回答",
                    "n_tools": 0,
                }
            passed += int(bool(outcome["ok"]))
            rows.append(
                {
                    "序号": case["id"],
                    "类别": case["category"],
                    "问题": case["question"],
                    "期望": case["expect"],
                    "工具调用链": outcome["tools"],
                    "回答全文": outcome["answer"],
                    "人工判定": "正确" if outcome["ok"] else "错误",
                    "备注": outcome["note"],
                }
            )
            print(f"\n[{case['id']}] {case['category']} -> {'正确' if outcome['ok'] else '错误'}")
            print(f"  工具：{outcome['tools']}")
            print(f"  回答：{outcome['answer']}")

    df = pd.DataFrame(rows)
    df.to_csv(TEST_CSV, index=False, encoding="utf-8-sig")
    print("\n" + "=" * 60)
    print(f"Agent 用例：{passed}/{len(CASES)} 通过；单元测试：{up}/{ut}")
    print(f"结果已写入：{TEST_CSV}")
    return 0 if (up == ut and passed == len(CASES)) else 1


if __name__ == "__main__":
    sys.exit(main())
