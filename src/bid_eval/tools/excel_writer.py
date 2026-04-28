import json
from datetime import datetime
from pathlib import Path

from crewai.tools import BaseTool
from pydantic import BaseModel, Field
import openpyxl
from openpyxl.styles import (
    Alignment, Border, Font, PatternFill, Side
)

from src.bid_eval.config import PROJECTS_DIR
from src.bid_eval.model import ReportData



# ── 样式常量 ────────────────────────────────────────────────────────────────

HEADER_FILL = PatternFill("solid", fgColor="1F4E79")   # 深蓝
POS_FILL    = PatternFill("solid", fgColor="E2EFDA")   # 浅绿：正偏离
NEG_FILL    = PatternFill("solid", fgColor="FCE4D6")   # 浅红：负偏离
NO_FILL     = PatternFill("solid", fgColor="FFFFFF")   # 白：无偏离

HEADER_FONT = Font(name="微软雅黑", bold=True, color="FFFFFF", size=11)
BODY_FONT   = Font(name="微软雅黑", size=10)
TITLE_FONT  = Font(name="微软雅黑", bold=True, size=13)

THIN_SIDE   = Side(style="thin", color="BFBFBF")
THIN_BORDER = Border(
    left=THIN_SIDE, right=THIN_SIDE,
    top=THIN_SIDE,  bottom=THIN_SIDE
)

COLUMNS = [
    ("序号",     6),
    ("招标要求", 36),
    ("投标响应", 36),
    ("偏离情况", 16),
    ("偏离说明", 30),
    ("处理方案", 40),
]

DEVIATION_FILL_MAP = {
    "正偏离": POS_FILL,
    "负偏离": NEG_FILL,
    "无偏离": NO_FILL,
}


# ── Tool 输入的 Pydantic模型 ────────────────────────────────────────────────────────────

class ExcelWriterInput(BaseModel):
    report_json: str = Field(
        description=(
            "generate_report_task 输出的 JSON 字符串，"
            "包含 project_name、summary、rows 字段"
        )
    )
    project_id: str = Field(
        description="项目 ID，用于确定输出文件路径"
    )


# ── Tool 实现 ────────────────────────────────────────────────────────────────

class ExcelWriterTool(BaseTool):
    name: str = "Excel 偏离表生成工具"
    description: str = (
        "接收偏离分析 JSON 数据，生成格式规范的 Excel 偏离表文件，"
        "返回输出文件的绝对路径。"
    )
    args_schema: type[BaseModel] = ExcelWriterInput

    def _run(self, report_json: str, project_id: str) -> str:
        # 1. 解析 JSON
        try:
            data = ReportData.model_validate_json(report_json)
        except json.JSONDecodeError as e:
            return f"ERROR: 报告数据结构校验失败 — {e}"

        # 2. 确定输出路径
        output_dir = PROJECTS_DIR / project_id / "output"
        output_dir.mkdir(parents=True, exist_ok=True)
        timestamp   = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = output_dir / f"偏离表_{timestamp}.xlsx"

        # 3. 创建工作簿
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "偏离表"

        # 4. 标题行
        project_name = data.project_name
        generated_at = data.generated_at
        ws.merge_cells("A1:G1")
        title_cell = ws["A1"]
        title_cell.value    = f"{project_name} — 招投标偏离表"
        title_cell.font     = TITLE_FONT
        title_cell.alignment = Alignment(horizontal="center", vertical="center")
        ws.row_dimensions[1].height = 30

        # 5. 生成时间行
        ws.merge_cells("A2:G2")
        time_cell = ws["A2"]
        time_cell.value     = f"生成时间：{generated_at}"
        time_cell.font      = Font(name="微软雅黑", size=9, color="808080")
        time_cell.alignment = Alignment(horizontal="right", vertical="center")
        ws.row_dimensions[2].height = 18

        # 6. 表头行
        ws.row_dimensions[3].height = 24
        for col_idx, (header, width) in enumerate(COLUMNS, start=1):
            cell = ws.cell(row=3, column=col_idx, value=header)
            cell.fill      = HEADER_FILL
            cell.font      = HEADER_FONT
            cell.border    = THIN_BORDER
            cell.alignment = Alignment(
                horizontal="center", vertical="center", wrap_text=True
            )
            ws.column_dimensions[
                openpyxl.utils.get_column_letter(col_idx)
            ].width = width

        # 7. 数据行
        rows = data.rows
        for row_idx, row in enumerate(rows, start=4):
            deviation_type = row.deviation_type
            fill = DEVIATION_FILL_MAP.get(deviation_type, NO_FILL)

            values = [
                row.index,
                row.requirement,
                row.response,
                row.deviation_type,
                row.deviation_detail,
                row.solution,
                row.evidence_ref,
            ]

            for col_idx, value in enumerate(values, start=1):
                cell = ws.cell(row=row_idx, column=col_idx, value=value)
                cell.fill      = fill
                cell.font      = BODY_FONT
                cell.border    = THIN_BORDER
                cell.alignment = Alignment(
                    horizontal="left" if col_idx > 1 else "center",
                    vertical="top",
                    wrap_text=True,
                )

            ws.row_dimensions[row_idx].height = 60

        # 8. 统计摘要行
        summary     = data.summary
        summary_row = len(rows) + 4
        ws.merge_cells(f"A{summary_row}:G{summary_row}")
        summary_text = (
            f"统计摘要  ·  "
            f"共 {summary.total} 条  |  "
            f"无偏离 {summary.no_deviation} 条  |  "
            f"正偏离 {summary.positive_deviation} 条  |  "
            f"负偏离 {summary.negative_deviation} 条"
        )
        summary_cell = ws.cell(row=summary_row, column=1, value=summary_text)
        summary_cell.font      = Font(name="微软雅黑", size=10, bold=True)
        summary_cell.fill      = PatternFill("solid", fgColor="F2F2F2")
        summary_cell.border    = THIN_BORDER
        summary_cell.alignment = Alignment(horizontal="center", vertical="center")
        ws.row_dimensions[summary_row].height = 24

        # 9. 冻结表头
        ws.freeze_panes = "A4"

        # 10. 保存
        wb.save(output_path)
        return str(output_path.resolve())