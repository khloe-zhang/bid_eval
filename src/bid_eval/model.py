# Pydantic 模型定义，用于校验流程中的数据结构
from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime


class RequirementItem(BaseModel):
    """单条招标指标"""
    item_id: str                        # 条目编号，如 "3.2.1"
    section: str                        # 所属章节，如 "3.2 技术要求"
    requirement: str                    # 指标原文
    category: str                       # 分类：硬件规格 / 软件功能 / 合规资质 / 商务条款
    mandatory: bool = True              # 是否为强制要求
    confidence: float = 1.0            # 解析置信度，低于阈值时前端标黄


class ResponseItem(BaseModel):
    """单条投标响应"""
    item_id: str                        # 与 RequirementItem.item_id 对应
    response: str                       # 投标文件中的响应原文
    source_page: Optional[str] = None  # 来源页码或章节，用于证明材料索引


class DeviationItem(BaseModel):
    """单条偏离分析结果"""
    item_id: str
    requirement: str
    response: str
    deviation_type: str                 # "正偏离" | "负偏离" | "无偏离"
    deviation_detail: str               # 偏离说明，如 "主频差 0.2GHz"
    solution: Optional[str] = None     # 负偏离处理方案，其他类型为 None
    confidence: float = 1.0


class ProjectMeta(BaseModel):
    """项目基本信息"""
    project_id: str
    project_name: str
    created_at: str = Field(
        default_factory=lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    )
    tender_files: list[str] = []        # 招标文件路径列表
    bid_files: list[str] = []           # 投标文件路径列表


class DeviationFlowState(BaseModel):
    """Flow 全局状态，贯穿整个执行周期"""

    # === 新增：Flow 持久化必须的 id 字段 ===
    id: str = Field(default="", description="Flow 持久化唯一标识，与 project_id 绑定")

    # 项目信息
    project: ProjectMeta = Field(default_factory=lambda: ProjectMeta(
        project_id="", project_name=""
    ))

    # 各阶段产物
    requirements: list[RequirementItem] = []
    responses: list[ResponseItem] = []
    deviations: list[DeviationItem] = []

    # 文档解析结果              
    tender_content: Optional[str] = None   
    bid_content:    Optional[str] = None   

    # 流程控制
    current_step: str = "idle"          # 当前步骤，用于前端进度展示
    error: Optional[str] = None        # 错误信息，非空时流程中止

    # 用户确认标记
    requirements_confirmed: bool = False
    deviations_confirmed: bool = False

    # 输出
    output_path: Optional[str] = None  # 生成的 Excel 文件路径


class ReportRow(BaseModel):
    """偏离表单行数据"""
    index: int
    requirement: str
    response: str
    deviation_type: str                 # "正偏离" | "负偏离" | "无偏离"
    deviation_detail: str
    solution: str = "/"
    evidence_ref: str = "/"


class ReportSummary(BaseModel):
    """偏离表统计摘要"""
    total: int
    no_deviation: int
    positive_deviation: int
    negative_deviation: int


class ReportData(BaseModel):
    """generate_report_task 的完整输出结构"""
    project_name: str
    generated_at: str
    summary: ReportSummary
    rows: list[ReportRow]


# ── 列表包装模型 ──────────────────────────────────────────────────────────────
# 用于 CrewAI output_pydantic，因为 LLM 返回的是数组而非单个对象

class RequirementItemList(BaseModel):
    """招标指标列表（用于 output_pydantic）"""
    items: list[RequirementItem]


class ResponseItemList(BaseModel):
    """投标响应列表（用于 output_pydantic）"""
    items: list[ResponseItem]


class DeviationItemList(BaseModel):
    """偏离分析列表（用于 output_pydantic）"""
    items: list[DeviationItem]