#!/usr/bin/env python
import sys
import warnings
import uuid
from datetime import datetime
from pathlib import Path


from crewai.flow.flow import Flow, listen, start
from crewai.flow.persistence import persist
from crewai.flow.human_feedback import human_feedback
from crewai.flow.async_feedback import HumanFeedbackProvider, HumanFeedbackPending, PendingFeedbackContext
from src.bid_eval.config import memory_llm

from src.bid_eval.model import DeviationFlowState, ProjectMeta
from src.bid_eval.parsing.file_router import parse_files
from pydantic import BaseModel


class StreamlitHumanFeedbackProvider(HumanFeedbackProvider):
    """Non-blocking feedback provider for Streamlit web UI.

    Instead of reading from console (blocking), this raises HumanFeedbackPending
    to pause the flow. The Streamlit app handles feedback collection via its
    own UI, then calls flow.resume(feedback) to continue.
    """

    def request_feedback(self, context: PendingFeedbackContext, flow: Flow) -> str:
        raise HumanFeedbackPending(
            context=context,
            callback_info={"provider": "streamlit"},
        )

warnings.filterwarnings("ignore", category=SyntaxWarning, module="pysbd")

PROJECTS_DIR = Path("data/projects")

def save_flow_state(project_id: str, state: DeviationFlowState) -> None:
    path = PROJECTS_DIR / project_id / "flow_state.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(state.model_dump_json(), encoding="utf-8")

def load_flow_state(project_id: str) -> DeviationFlowState | None:
    path = PROJECTS_DIR / project_id / "flow_state.json"
    if not path.exists():
        return None
    try:
        return DeviationFlowState.model_validate_json(
            path.read_text(encoding="utf-8")
        )
    except Exception:
        return None

@persist()
class DeviationFlow(Flow[DeviationFlowState]):
    @start()
    def parse_documents(self):
        """Step 1：解析文档 + 绑定持久化 ID"""
        inputs = getattr(self, "inputs", None) or {}
        if not isinstance(inputs, dict):
            inputs = {}

        # === 修复：绑定 project_id 到 state.id ===
        project_id = (
            inputs.get("id") or 
            inputs.get("project_id") or 
            getattr(self.state.project, "project_id", None) or
            getattr(self.state, "id", None)
        )

        if project_id and not getattr(self.state, 'id', None):
            self.state.id = project_id
        
        if project_id and not getattr(self.state.project, "project_id", None):
            self.state.project.project_id = project_id

        self.state.current_step = "parsing"



        print(f"[DEBUG] parse_documents - project_id: {project_id}")
        print(f"[DEBUG] tender_files: {getattr(self.state.project, 'tender_files', None)}")
        print(f"[DEBUG] bid_files: {getattr(self.state.project, 'bid_files', None)}")

        # === 防御性检查 ===
        tender_files = getattr(self.state.project, 'tender_files', []) or []
        bid_files = getattr(self.state.project, 'bid_files', []) or []

        if not tender_files:
            raise ValueError(f"招标文件列表为空！project_id={project_id}")
        if not bid_files:
            raise ValueError(f"投标文件列表为空！project_id={project_id}")




        # === 接收解析选项 ===
        # use_mineru = self.inputs.get("use_mineru", False)
        # parse_images = self.inputs.get("parse_images", False)

        inputs       = getattr(self, "inputs", None) or {}
        use_mineru   = inputs.get("use_mineru", False)
        parse_images = inputs.get("parse_images", False)

        try:
            # 解析招标文件
            tender_content = parse_files(
                tender_files,
                use_mineru=use_mineru,
                parse_images=parse_images,
            )

            # 解析投标文件
            bid_content = parse_files(
                bid_files,
                use_mineru=use_mineru,
                parse_images=parse_images,
            )

            # 存入 state 供后续步骤使用
            self.state.tender_content = tender_content
            self.state.bid_content    = bid_content

            print(f"[DEBUG] 解析成功 - tender_content length: {len(tender_content) if tender_content else 0}")
            print("[DEBUG] parse_documents 即将返回，Flow 框架应触发 run_analysis")

            return tender_content
        except Exception as e:
            print(f"[ERROR] 解析文档失败: {e}")
            raise

    @listen(parse_documents)
    def run_analysis(self, _):
        print("[DEBUG] run_analysis triggered!")
        # 如果是 revision 触发的，强制重新执行（即使 current_step 是 awaiting_review）
        inputs = getattr(self, "inputs", None) or {}
        if (inputs.get("force_reanalyze") or inputs.get("revision_note")):
            self.state.current_step = "analyzing"   # 强制重置

        if self.state.current_step in ["awaiting_review", "generating", "done"]:
            return self.state.deviations  # 已完成，跳过
        
        """Step 2：运行 AnalysisCrew，提取指标、匹配响应、分析偏离"""

        self.state.current_step = "analyzing"

        from src.bid_eval.crews.analysis_crew.analysis_crew import AnalysisCrew
        crew_instance = AnalysisCrew()
        crew_instance.project_id = self.state.project.project_id
        result = crew_instance.crew().kickoff(
            inputs={
                "project_id":       self.state.project.project_id,
                "tender_content":   self.state.tender_content,
                "bid_content":      self.state.bid_content,
                "requirements_json": "",   # 由 CrewAI context 机制自动传递
                "responses_json":   "",    # 由 CrewAI context 机制自动传递
            }
        )

        # 取 analyze_deviations_task 的 pydantic 输出存入 state
        # self.state.deviations = result.pydantic or []
        # self.state.deviations = result.pydantic.items if result.pydantic else []
        self.state.deviations = result.pydantic.items if getattr(result, 'pydantic', None) else []
        self.state.current_step = "awaiting_review"

        save_flow_state(self.state.project.project_id, self.state)

        return self.state.deviations

    @listen(run_analysis)
    @human_feedback(
        message=(
            "偏离分析已完成，请审核以下负偏离条目及处理方案。\n"
            "确认无误请输入 approved，需要修改请输入 revise。"
        ),
        emit=["approved", "revise"],
        llm=memory_llm,
        default_outcome="approved",
        provider=StreamlitHumanFeedbackProvider(),
    )
    def review_deviations(self, deviations):
        """Step 3：Human-in-the-loop，用户审核负偏离条目"""
        return deviations

    @listen("revise")
    def handle_revision(self, feedback_result):
        """Step 4a：用户要求修改，将反馈注入 state 后重新分析"""

        self.state.current_step = "revising"

        # 把用户反馈追加到 bid_content，让 AnalysisCrew 重新运行时能看到修改意见
        revision_note = (
            f"\n\n[用户修改意见]\n{feedback_result.feedback}"
        )

        from src.bid_eval.crews.analysis_crew.analysis_crew import AnalysisCrew
        crew_instance = AnalysisCrew()
        crew_instance.project_id = self.state.project.project_id
        result = crew_instance.crew().kickoff(
            inputs={
                "project_id":        self.state.project.project_id,
                "tender_content":    self.state.tender_content,
                "bid_content":       self.state.bid_content + revision_note,
                "requirements_json": "",
                "responses_json":    "",
            }
        )

        # self.state.deviations   = result.pydantic or []
        # self.state.deviations   = result.pydantic.items if result.pydantic else []
        self.state.deviations   = result.pydantic.items if getattr(result, 'pydantic', None) else []
        self.state.current_step = "awaiting_review"

        return self.state.deviations

    @listen("approved")
    def generate_report(self, feedback_result):
        """Step 4b：用户确认，运行 ReportCrew 生成偏离表"""
        if self.state.current_step == "done" and self.state.output_path:
                return self.state.output_path  # 已生成，跳过
        
        self.state.current_step = "generating"

        import json
        from datetime import datetime
        from bid_eval.tools.excel_writer import ExcelWriterTool

        # === 原 render_generating 中的三步全部移到这里 ===
        deviations_json = json.dumps(
            [d.model_dump() for d in self.state.deviations],
            ensure_ascii=False,
            indent=2,
        )

        inputs       = getattr(self, "inputs", None) or {}
        generated_at = inputs.get("generated_at") or datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        from src.bid_eval.crews.report_crew.report_crew import ReportCrew
        result = ReportCrew().crew().kickoff(
            inputs={
                "project_id":       self.state.project.project_id,
                "project_name":     self.state.project.project_name,
                "generated_at":     generated_at,
                "deviations_json":  deviations_json,
            }
        )

        # 手动剥离 markdown 代码块，再解析
        import re, json
        raw = result.raw if result.raw else ""
        raw = re.sub(r"^```(?:json)?\s*", "", raw.strip())
        raw = re.sub(r"\s*```$", "", raw)

        #report_json = ReportData.model_validate_json(raw) 
        #report_json = result.raw if result.raw else "" 初始版
        report_json = raw

        # 调用 ExcelWriterTool 写入文件
        writer = ExcelWriterTool()
        output_path = writer._run(
            report_json=report_json,
            project_id=self.state.project.project_id,
        )

        self.state.output_path  = output_path
        self.state.current_step = "done"

        return output_path


# ── 入口函数 ──────────────────────────────────────────────────────────────────
def run():
    """本地运行入口：crewai run 或 uv run run 触发"""
    flow = DeviationFlow(tracing=True)
    flow.kickoff()


# def load_persisted_flow(project_id: str) -> "DeviationFlow | None":
#     """
#     尝试加载指定 project_id 的持久化 Flow 实例（供续传使用）。
#     如果存在未完成的流程（current_step != "done"），返回该实例；否则返回 None。
#     """
#     if not project_id:
#         return None
#     try:
#         flow = DeviationFlow()
#         # kickoff 会自动按 id 加载状态
#         # flow.kickoff(inputs={"id": project_id})
#         flow.kickoff(inputs={"id": project_id, "dry_run": True}) # 仅加载状态，不执行流程
#         if flow.state.id == project_id:
#             return flow
#     except Exception:
#         pass
#     return None


def check_interrupted_flow(project_id: str) -> dict | None:
    """
    检查指定 project_id 是否有被中断的流程。
    返回流程状态信息，否则返回 None。
    """
    if not project_id:
        return None
    
    try:
        # 创建临时 Flow 并强制传入 id 来触发加载
        flow = DeviationFlow()
        #flow.kickoff(inputs={"id": project_id})  # 这会加载状态，但如果已 done 会继续执行（需小心）
        #flow.kickoff(inputs={"id": project_id, "dry_run": True}) # 仅加载状态，不执行流程

        state = flow.state
        if state.id == project_id and state.current_step not in ["done", "idle"]:
            return {
                "project_id": state.project.project_id,
                "project_name": state.project.project_name,
                "current_step": state.current_step,
                "has_deviations": len(state.deviations) > 0,
                "has_output": bool(state.output_path),
                "tender_files":   getattr(state.project, "tender_files", []),
                "bid_files":      getattr(state.project, "bid_files", []),
            }
    except Exception:
        pass
    return None

# main.py 末尾添加

def generate_deviation_report(
    project_id: str,
    project_name: str,
    deviations: list,
    generated_at: str | None = None,
) -> str:
    """
    独立的报告生成函数，供断点续传场景下 app.py 直接调用。
    逻辑与 DeviationFlow.generate_report 完全一致。
    """
    import json, re
    from datetime import datetime
    from src.bid_eval.tools.excel_writer import ExcelWriterTool
    from src.bid_eval.crews.report_crew.report_crew import ReportCrew

    if generated_at is None:
        generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    deviations_json = json.dumps(
        [d.model_dump() for d in deviations],
        ensure_ascii=False,
        indent=2,
    )

    result = ReportCrew().crew().kickoff(
        inputs={
            "project_id":      project_id,
            "project_name":    project_name,
            "generated_at":    generated_at,
            "deviations_json": deviations_json,
        }
    )

    raw = result.raw if result.raw else ""
    raw = re.sub(r"^```(?:json)?\s*", "", raw.strip())
    raw = re.sub(r"\s*```$", "", raw)

    writer = ExcelWriterTool()
    output_path = writer._run(
        report_json=raw,
        project_id=project_id,
    )

    return output_path

if __name__ == "__main__":
    run()
