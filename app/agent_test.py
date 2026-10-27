"""智链参谋 · Agent 10 条测试。

用法：
    .venv\\Scripts\\python.exe app\\agent_test.py

说明：
- 第 8 条（缺 key）不依赖 API，始终执行。
- 其余 9 条需要 .env 或进程环境变量中的 DEEPSEEK_API_KEY；缺 key 时标记
  “未执行（缺 key）”，不会伪造回答或通过状态。
- 输出：output/agent_test_cases.csv
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pandas as pd

APP_DIR = Path(__file__).resolve().parent
BASE_DIR = APP_DIR.parent
OUTPUT_DIR = BASE_DIR / "output"
AGENT_PATH = APP_DIR / "agent.py"
TEST_CSV = OUTPUT_DIR / "agent_test_cases.csv"

sys.path.insert(0, str(APP_DIR))
import agent  # noqa: E402


FORECAST_COLS = ["week", "sku_id", "sku_name", "p50", "p90", "p95"]
REPL_COLS = [
    "sku_id", "sku_name", "snapshot_week", "horizon_weeks", "lead_time_weeks",
    "review_period_weeks", "service_level_target", "effective_service_quantile",
    "on_hand", "in_transit", "available_inventory", "p50_cum", "p90_cum", "p95_cum",
    "safety_stock", "reorder_point", "target_level", "gap", "recommended_order_qty",
    "urgency", "drift_status", "drift_detail", "reason",
]
SKU_ALL_COLS = ["rank", "Product Card Id", "Product Name", "demand_qty", "order_count", "line_count"]

CASES = [
    (1, "正常问缺货", "SKU 365 下个月会不会缺货？现在该补多少？",
     "调用 get_forecast + get_inventory_status + recommend_replenishment；建议补 608 件；含 P90=2270.46、可用 2231.00"),
    (2, "正常问补量", "SKU 502 现在该补多少？", "建议订货 523 件"),
    (3, "批量问哪些要补", "哪些 SKU 需要马上补货？",
     "调用 list_risk_skus；默认 3 条：365(608)、502(523)、1073(190)"),
    (4, "不存在的 SKU", "SKU 9999 呢？", "明确说“数据里没有这个 SKU”，不编造数字"),
    (5, "模糊问题", "哪些货要补？", "调用 list_risk_skus，不凭印象回答"),
    (6, "非 Top-10 SKU", "SKU 977 现在该补多少？",
     "说明 977 存在但不在 Top-10，无法给出补货建议"),
    (7, "超出预测周数", "SKU 365 未来 8 周累计需求是多少？",
     "明确只覆盖未来 4 周，拒绝按比例外推"),
    (8, "缺 key", "SKU 365 现在该补多少？（--env-file 指向不存在的文件）",
     "中文提示、退出码 2、无 traceback"),
    (9, "工具返回空表", "哪些 SKU 需要马上补货？（空数据夹具）",
     "回答当前没有需要补货的 SKU，不编造"),
    (10, "连续追问", "先问“哪些 SKU 需要马上补货？”，再问“那第二个呢？”",
     "第二轮解析为 SKU 502，建议 523 件"),
]


def _flat(text: str) -> str:
    return text.replace(",", "").replace("，", "")


def _tools_text(logs: list[dict]) -> str:
    if not logs:
        return "（无）"
    return "; ".join(
        f"{log['name']}({json.dumps(log['args'], ensure_ascii=False)})"
        for log in logs
    )


def _run_single(question: str, api_key: str, data_dir: Path | None = None) -> tuple[str, list[dict]]:
    store = agent.DataStore(data_dir)
    messages = [{"role": "system", "content": agent.SYSTEM_PROMPT}]
    messages.append({"role": "user", "content": question})
    return agent.run_turn(messages, store, api_key, echo=False)


def _run_missing_key() -> dict:
    env = os.environ.copy()
    env.pop("DEEPSEEK_API_KEY", None)
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    with tempfile.TemporaryDirectory(prefix="agent_no_key_") as tmp:
        missing_env = Path(tmp) / "no_such.env"
        cmd = [
            sys.executable, str(AGENT_PATH),
            "--env-file", str(missing_env),
            "--once", "SKU 365 现在该补多少？",
        ]
        proc = subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8",
            errors="replace", env=env, cwd=str(BASE_DIR), timeout=60,
        )
    output = ((proc.stdout or "") + (proc.stderr or "")).strip()
    passed = (
        proc.returncode == 2
        and "未找到 DEEPSEEK_API_KEY" in output
        and "Traceback" not in output
    )
    return {
        "actual": output,
        "tools": "（启动前检查，不调用模型）",
        "passed": passed,
        "note": f"returncode={proc.returncode}",
    }


def _make_empty_fixture(directory: Path) -> None:
    pd.DataFrame(columns=FORECAST_COLS).to_csv(
        directory / "forecast_cumulative_predictions.csv", index=False, encoding="utf-8-sig"
    )
    pd.DataFrame(columns=REPL_COLS).to_csv(
        directory / "inventory_replenishment.csv", index=False, encoding="utf-8-sig"
    )
    pd.DataFrame(columns=SKU_ALL_COLS).to_csv(
        directory / "sku_summary_all.csv", index=False, encoding="utf-8-sig"
    )


def _run_case(case_id: int, question: str, api_key: str) -> dict:
    if case_id == 8:
        return _run_missing_key()

    if case_id == 9:
        with tempfile.TemporaryDirectory(prefix="agent_empty_") as tmp:
            fixture = Path(tmp)
            _make_empty_fixture(fixture)
            answer, logs = _run_single(question, api_key, data_dir=fixture)
        passed = (
            "没有" in answer
            and any(
                log["result"].get("counts", {}).get("total") == 0
                for log in logs
            )
        )
        return {"actual": answer, "tools": _tools_text(logs), "passed": passed, "note": ""}

    if case_id == 10:
        store = agent.DataStore()
        messages = [{"role": "system", "content": agent.SYSTEM_PROMPT}]
        messages.append({"role": "user", "content": "哪些 SKU 需要马上补货？"})
        answer1, logs1 = agent.run_turn(messages, store, api_key, echo=False)
        messages.append({"role": "user", "content": "那第二个呢？"})
        answer2, logs2 = agent.run_turn(messages, store, api_key, echo=False)
        actual = f"第一轮：{answer1}\n第二轮：{answer2}"
        tools = f"第一轮：{_tools_text(logs1)}\n第二轮：{_tools_text(logs2)}"
        passed = (
            "523" in _flat(answer2)
            and any(log["args"].get("sku_id") == 502 for log in logs2)
        )
        return {"actual": actual, "tools": tools, "passed": passed, "note": ""}

    answer, logs = _run_single(question, api_key)
    names = {log["name"] for log in logs}
    flat = _flat(answer)
    passed = False
    note = ""

    if case_id == 1:
        passed = (
            {"get_forecast", "get_inventory_status", "recommend_replenishment"} <= names
            and "608" in flat
            and ("2270.46" in flat or "2270.5" in flat or "2270" in flat)
            and ("2231" in flat)
        )
    elif case_id == 2:
        passed = "523" in flat
    elif case_id == 3:
        passed = (
            "list_risk_skus" in names
            and all(x in flat for x in ["365", "502", "1073", "608", "523", "190"])
        )
    elif case_id == 4:
        passed = "数据里没有" in answer and bool(logs)
    elif case_id == 5:
        passed = "list_risk_skus" in names
    elif case_id == 6:
        passed = (
            "977" in flat
            and any(x in answer for x in ["Top-10", "Top10", "不在", "只覆盖"])
            and any(log["result"].get("reason") == "not_in_scope" for log in logs)
        )
    elif case_id == 7:
        passed = (
            ("4 周" in answer or "4周" in answer)
            and any(x in answer for x in ["无法", "只提供", "只覆盖"])
            and any(log["result"].get("horizon_matched") is False for log in logs)
        )
    else:
        note = "未知用例编号"

    return {"actual": answer, "tools": _tools_text(logs), "passed": passed, "note": note}


def main() -> int:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    api_key = agent.load_api_key(agent.ENV_PATH)
    has_key = bool(api_key)
    if not has_key:
        print("未找到 DEEPSEEK_API_KEY：只执行第 8 条（缺 key），其余标记为未执行。")

    rows: list[dict] = []
    executed = 0
    passed_count = 0

    for case_id, category, question, expected in CASES:
        if case_id != 8 and not has_key:
            rows.append(
                {
                    "序号": case_id,
                    "类别": category,
                    "问题": question,
                    "期望": expected,
                    "实际回答": "（未执行：缺少 DEEPSEEK_API_KEY）",
                    "工具调用": "（未执行）",
                    "是否通过": "未执行（缺 key）",
                    "备注": "配置 .env 后重新运行本脚本",
                }
            )
            continue

        try:
            outcome = _run_case(case_id, question, api_key)
        except agent.AgentError as exc:
            outcome = {
                "actual": f"模型调用失败：{exc}",
                "tools": "（调用失败）",
                "passed": False,
                "note": f"exit_code={exc.exit_code}",
            }
        except Exception as exc:  # 测试脚本兜底，保留失败原因
            outcome = {
                "actual": f"测试执行异常：{type(exc).__name__}: {exc}",
                "tools": "（异常）",
                "passed": False,
                "note": "见实际回答",
            }

        executed += 1
        passed_count += int(bool(outcome["passed"]))
        rows.append(
            {
                "序号": case_id,
                "类别": category,
                "问题": question,
                "期望": expected,
                "实际回答": outcome["actual"],
                "工具调用": outcome["tools"],
                "是否通过": "通过" if outcome["passed"] else "未通过",
                "备注": outcome.get("note", ""),
            }
        )
        print(f"\n[{case_id}] {category} -> {'通过' if outcome['passed'] else '未通过'}")
        print(f"  工具：{outcome['tools']}")
        print(f"  回答：{outcome['actual']}")

    df = pd.DataFrame(rows)
    df.to_csv(TEST_CSV, index=False, encoding="utf-8-sig")

    print("\n" + "=" * 60)
    print(f"已执行 {executed} 条，通过 {passed_count} 条；未执行 {len(CASES) - executed} 条。")
    print(f"结果已写入：{TEST_CSV}")

    if not has_key:
        return 0 if passed_count == 1 and executed == 1 else 1
    return 0 if passed_count >= 9 else 1


if __name__ == "__main__":
    sys.exit(main())
