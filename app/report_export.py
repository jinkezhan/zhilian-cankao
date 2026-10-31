"""补货日报导出：带格式 Excel 与 PDF（纯函数，不依赖 Streamlit）。

用法（看板内）：
    excel_bytes = build_excel_report(display, repl, snapshot, values, filter_note)
    pdf_bytes = build_pdf_report(display, repl, snapshot, values, filter_note)
"""

from __future__ import annotations

from datetime import datetime
from io import BytesIO

import pandas as pd

FRESH_UNIT = "kg/份"
NAVY = "0B2A4A"
RED_FILL = "FEE4E2"
AMBER_FILL = "FFF7E8"
GRAY = "6B7F94"


def _series_label(store_id: object, product_id: object) -> str:
    return f"SKU {int(store_id)}-{int(product_id)}"


def _fmt_number(value: object, digits: int = 2) -> str:
    try:
        return f"{float(value):,.{digits}f}"
    except (TypeError, ValueError):
        return str(value)


def _report_frames(repl: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    frame = repl.copy()
    frame["店 / SKU"] = [
        _series_label(store, product)
        for store, product in zip(frame["store_id"], frame["product_id"])
    ]
    columns = [
        "店 / SKU",
        "现有库存",
        "ROP_P90",
        "目标水位",
        "建议订货",
        "临期数量",
        "紧急度",
    ]
    urgent = frame[frame["urgency"].astype(str) == "紧急"].copy()
    urgent = urgent.rename(
        columns={
            "on_hand": "现有库存",
            "rop_p90": "ROP_P90",
            "target_level": "目标水位",
            "suggest_qty": "建议订货",
            "expiring_qty": "临期数量",
            "urgency": "紧急度",
        }
    )[columns]
    urgent = urgent.sort_values(["建议订货", "现有库存"], ascending=[False, True])

    expiring = frame[
        pd.to_numeric(frame["expiring_qty"], errors="coerce").fillna(0) > 0
    ].copy()
    expiring = expiring.rename(
        columns={
            "on_hand": "现有库存",
            "rop_p90": "ROP_P90",
            "target_level": "目标水位",
            "suggest_qty": "建议订货",
            "expiring_qty": "临期数量",
            "urgency": "紧急度",
        }
    )[columns]
    expiring = expiring.sort_values("临期数量", ascending=False)
    return urgent, expiring


# ---------------------------------------------------------------------------
# Excel
# ---------------------------------------------------------------------------
def _style_header(ws, row: int, columns: int) -> None:
    from openpyxl.styles import Alignment, Font, PatternFill

    for col in range(1, columns + 1):
        cell = ws.cell(row=row, column=col)
        cell.fill = PatternFill("solid", fgColor=NAVY)
        cell.font = Font(bold=True, color="FFFFFF", size=10)
        cell.alignment = Alignment(
            horizontal="center", vertical="center", wrap_text=True
        )


def _write_frame(ws, frame: pd.DataFrame, fill_rule=None) -> None:
    from openpyxl.styles import Alignment, Border, PatternFill, Side
    from openpyxl.utils import get_column_letter

    thin = Side(style="thin", color="DCE7F3")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    columns = list(frame.columns)
    for index, column in enumerate(columns, 1):
        ws.cell(row=1, column=index, value=str(column))
    _style_header(ws, 1, len(columns))

    for row_index, (_, row) in enumerate(frame.iterrows(), 2):
        fill_color = fill_rule(row) if fill_rule else None
        for col_index, column in enumerate(columns, 1):
            value = row[column]
            if pd.isna(value):
                value = ""
            if hasattr(value, "item") and not isinstance(value, (str, bytes)):
                try:
                    value = value.item()
                except Exception:
                    pass
            cell = ws.cell(row=row_index, column=col_index, value=value)
            cell.border = border
            cell.alignment = Alignment(vertical="top")
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                cell.number_format = (
                    "#,##0.00" if isinstance(value, float) else "#,##0"
                )
            if fill_color:
                cell.fill = PatternFill("solid", fgColor=fill_color)

    for index, column in enumerate(columns, 1):
        values = [str(column)] + [
            str(item) for item in frame[column].astype(str).head(200)
        ]
        width = min(max(10, max(len(item) for item in values) * 1.8), 30)
        ws.column_dimensions[get_column_letter(index)].width = width
    ws.freeze_panes = "A2"
    if len(frame):
        last = get_column_letter(len(columns))
        ws.auto_filter.ref = f"A1:{last}{len(frame) + 1}"


def build_excel_report(
    display: pd.DataFrame,
    repl: pd.DataFrame,
    snapshot: str,
    values: dict,
    filter_note: str = "全部门店 / 全部紧急度",
) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill

    wb = Workbook()
    overview = wb.active
    overview.title = "概览"
    overview.merge_cells("A1:D1")
    title = overview["A1"]
    title.value = "智链参谋 · 生鲜补货日报"
    title.font = Font(bold=True, size=16, color=NAVY)
    title.alignment = Alignment(vertical="center")
    overview.row_dimensions[1].height = 30

    overview.merge_cells("A2:D2")
    overview["A2"] = (
        f"补货快照 {snapshot} · 生成 "
        f"{datetime.now().strftime('%Y-%m-%d %H:%M')}"
    )
    overview["A2"].font = Font(size=10, color=GRAY)
    overview.merge_cells("A3:D3")
    overview["A3"] = f"当前筛选：{filter_note}"
    overview["A3"].font = Font(size=10, color=GRAY)

    rows = [
        ("指标", "数值", "说明", ""),
        (
            "总 SKU 数",
            int(values.get("series", len(repl))),
            "纳入分析的店 × SKU 序列",
            "",
        ),
        ("紧急缺货", int(values.get("urgent", 0)), "需立即补货的序列", ""),
        ("建议补货", int(values.get("advised", 0)), "建议补货档序列", ""),
        (
            "临期预警",
            int(values.get("expiring_series", 0)),
            f"涉及临期 {_fmt_number(values.get('expiring_qty', 0))} {FRESH_UNIT}",
            "",
        ),
        (
            "建议补货总量",
            int(pd.to_numeric(repl["suggest_qty"], errors="coerce").fillna(0).sum()),
            "按补货快照建议量合计",
            "",
        ),
    ]
    if values.get("short_shelf") is not None:
        rows.append(
            (
                "短保报损对比",
                f"{float(values.get('spoilage_c', 0)):.2%}",
                f"较传统补货 {float(values.get('spoilage_b', 0)):.2%}"
                f"（{int(values['short_shelf'])} 天档）",
                "",
            )
        )

    for row_index, row in enumerate(rows, 5):
        for col_index, value in enumerate(row[:3], 1):
            cell = overview.cell(row=row_index, column=col_index, value=value)
            cell.alignment = Alignment(vertical="center")
    _style_header(overview, 5, 3)
    for row_index in range(6, 5 + len(rows)):
        overview.row_dimensions[row_index].height = 22
    overview.column_dimensions["A"].width = 20
    overview.column_dimensions["B"].width = 18
    overview.column_dimensions["C"].width = 46
    note_row = 6 + len(rows)
    overview.merge_cells(f"A{note_row}:C{note_row}")
    overview[f"A{note_row}"] = (
        "数据来源：output/freshretail_*；本报告由系统自动生成，"
        "数字口径以页面与交付表为准。"
    )
    overview[f"A{note_row}"].font = Font(size=9, color=GRAY)

    replenishment = wb.create_sheet("补货清单")
    _write_frame(
        replenishment,
        display,
        fill_rule=lambda row: (
            RED_FILL
            if str(row.get("紧急度", "")) == "紧急"
            else AMBER_FILL
            if str(row.get("临期", "")) == "是"
            else None
        ),
    )

    urgent, expiring = _report_frames(repl)
    urgent_sheet = wb.create_sheet("紧急缺货")
    _write_frame(urgent_sheet, urgent, fill_rule=lambda row: RED_FILL)
    expiring_sheet = wb.create_sheet("临期")
    _write_frame(expiring_sheet, expiring, fill_rule=lambda row: AMBER_FILL)

    buffer = BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


# ---------------------------------------------------------------------------
# PDF
# ---------------------------------------------------------------------------
def _pdf_table(fig, rect, headers, rows, widths, font_size=8.2):
    ax = fig.add_axes(rect)
    ax.axis("off")
    table = ax.table(
        cellText=rows or [["—"] * len(headers)],
        colLabels=headers,
        loc="upper center",
        cellLoc="left",
    )
    table.auto_set_font_size(False)
    table.set_fontsize(font_size)
    table.scale(1, 1.35)
    for col, width in enumerate(widths):
        for row in range(len(rows) + 1):
            table[(row, col)].set_width(width)
    for (row, col), cell in table.get_celld().items():
        cell.set_edgecolor("#DCE7F3")
        if row == 0:
            cell.set_facecolor("#0B2A4A")
            cell.set_text_props(color="white", fontweight="bold")
        elif row % 2 == 0:
            cell.set_facecolor("#F7FAFD")


def build_pdf_report(
    display: pd.DataFrame,
    repl: pd.DataFrame,
    snapshot: str,
    values: dict,
    filter_note: str = "全部门店 / 全部紧急度",
) -> bytes | None:
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib.backends.backend_pdf import PdfPages

        plt.rcParams["font.sans-serif"] = [
            "Microsoft YaHei",
            "SimHei",
            "Arial Unicode MS",
            "DejaVu Sans",
        ]
        plt.rcParams["axes.unicode_minus"] = False

        urgent, expiring = _report_frames(repl)
        generated = datetime.now().strftime("%Y-%m-%d %H:%M")

        buffer = BytesIO()
        with PdfPages(buffer) as pdf:
            fig = plt.figure(figsize=(8.27, 11.69))
            fig.text(0.06, 0.955, "智链参谋 · 生鲜补货日报", fontsize=19,
                     fontweight="bold", color="#0B2A4A")
            fig.text(0.06, 0.928,
                     f"补货快照 {snapshot} · 生成 {generated}",
                     fontsize=9, color="#6B7F94")
            fig.text(0.06, 0.908, f"当前筛选：{filter_note}",
                     fontsize=9, color="#6B7F94")
            kpi = (
                f"总 SKU {int(values.get('series', len(repl))):,} · "
                f"紧急缺货 {int(values.get('urgent', 0)):,} · "
                f"临期 {int(values.get('expiring_series', 0)):,} 种 / "
                f"{_fmt_number(values.get('expiring_qty', 0))} {FRESH_UNIT}"
            )
            fig.text(0.06, 0.882, kpi, fontsize=10.5, color="#1E293B")

            fig.text(0.06, 0.845, f"紧急缺货（{len(urgent)} 条）",
                     fontsize=11, fontweight="bold", color="#B42318")
            urgent_rows = [
                [
                    row["店 / SKU"],
                    _fmt_number(row["现有库存"]),
                    _fmt_number(row["ROP_P90"]),
                    str(int(row["建议订货"])),
                ]
                for _, row in urgent.head(6).iterrows()
            ]
            _pdf_table(
                fig,
                [0.06, 0.69, 0.88, 0.14],
                ["店 / SKU", "现有库存", "ROP_P90", "建议订货"],
                urgent_rows,
                [0.42, 0.2, 0.2, 0.18],
            )

            fig.text(0.06, 0.645, f"临期（{len(expiring)} 条）",
                     fontsize=11, fontweight="bold", color="#B45309")
            expiring_rows = [
                [
                    row["店 / SKU"],
                    _fmt_number(row["临期数量"]),
                    _fmt_number(row["现有库存"]),
                    str(int(row["建议订货"])),
                ]
                for _, row in expiring.head(6).iterrows()
            ]
            _pdf_table(
                fig,
                [0.06, 0.49, 0.88, 0.14],
                ["店 / SKU", "临期数量", "现有库存", "建议订货"],
                expiring_rows,
                [0.42, 0.2, 0.2, 0.18],
            )

            fig.text(
                0.06,
                0.08,
                "数据来源：output/freshretail_*；本报告由系统自动生成，"
                "数字口径以页面与交付表为准。",
                fontsize=8.5,
                color="#6B7F94",
            )
            pdf.savefig(fig)
            plt.close(fig)

            fig2 = plt.figure(figsize=(8.27, 11.69))
            fig2.text(0.06, 0.955, "补货清单（前 20 条）", fontsize=16,
                      fontweight="bold", color="#0B2A4A")
            fig2.text(
                0.06,
                0.93,
                f"快照 {snapshot} · 筛选：{filter_note} · 共 {len(display)} 条",
                fontsize=9,
                color="#6B7F94",
            )
            display_rows = []
            for _, row in display.head(20).iterrows():
                display_rows.append(
                    [
                        str(row.get("店 / SKU", "")),
                        _fmt_number(row.get("现有库存（kg/份）", 0)),
                        _fmt_number(row.get("ROP_P90（kg/份）", 0)),
                        str(int(row.get("建议订货（kg/份）", 0))),
                        str(row.get("紧急度", "")),
                        str(row.get("临期数量（kg/份）", "")),
                    ]
                )
            _pdf_table(
                fig2,
                [0.04, 0.08, 0.92, 0.8],
                ["店 / SKU", "现有库存", "ROP_P90", "建议订货", "紧急度", "临期数量"],
                display_rows,
                [0.3, 0.14, 0.14, 0.14, 0.14, 0.14],
                font_size=7.6,
            )
            pdf.savefig(fig2)
            plt.close(fig2)

        return buffer.getvalue()
    except Exception:
        return None
