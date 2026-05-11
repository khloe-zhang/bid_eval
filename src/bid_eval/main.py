#!/usr/bin/env python
import json
import re
import warnings
from datetime import datetime

from crewai.flow.async_feedback import (
    HumanFeedbackPending,
    HumanFeedbackProvider,
    PendingFeedbackContext,
)
from crewai.flow.flow import Flow, listen, or_, start
from crewai.flow.human_feedback import human_feedback
from crewai.flow.persistence import persist
from opentelemetry import baggage

from src.bid_eval.config import memory_llm
from src.bid_eval.model import DeviationFlowState
from src.bid_eval.parsing.file_router import parse_files
from src.bid_eval.project_store import (
    STATUS_ANALYZING,
    STATUS_AWAITING_REVIEW,
    STATUS_DONE,
    STATUS_GENERATING,
    save_flow_snapshot,
    save_project_index,
    update_project_index,
    load_flow_snapshot,
)


warnings.filterwarnings("ignore", category=SyntaxWarning, module="pysbd")


class StreamlitHumanFeedbackProvider(HumanFeedbackProvider):
    """Non-blocking feedback provider for Streamlit web UI."""

    def request_feedback(self, context: PendingFeedbackContext, flow: Flow) -> str:
        raise HumanFeedbackPending(
            context=context,
            callback_info={
                "provider": "streamlit",
                "project_id": getattr(flow.state.project, "project_id", ""),
            },
        )


def _flow_inputs(flow: Flow) -> dict:
    inputs = getattr(flow, "inputs", None) or baggage.get_baggage("flow_inputs") or {}
    return inputs if isinstance(inputs, dict) else {}


def _project_id(flow: Flow[DeviationFlowState]) -> str:
    return (
        getattr(flow.state, "id", "")
        or getattr(flow.state.project, "project_id", "")
    )


def _project_name(flow: Flow[DeviationFlowState]) -> str:
    return getattr(flow.state.project, "project_name", "") or _project_id(flow)


def save_flow_state(project_id: str, state: DeviationFlowState) -> None:
    """Compatibility alias: save a fallback Flow snapshot."""
    save_flow_snapshot(project_id, state)


def load_flow_state(project_id: str) -> DeviationFlowState | None:
    """Compatibility alias: load a fallback Flow snapshot."""
    return load_flow_snapshot(project_id)


@persist()
class DeviationFlow(Flow[DeviationFlowState]):
    @start()
    def parse_documents(self):
        """Step 1: parse documents and bind the Flow id to project_id."""
        inputs = _flow_inputs(self)
        project_id = (
            inputs.get("id")
            or inputs.get("project_id")
            or getattr(self.state.project, "project_id", None)
            or getattr(self.state, "id", None)
        )
        if not project_id:
            raise ValueError("缺少 project_id，无法启动或恢复 Flow。")

        self.state.id = project_id
        self.state.project.project_id = project_id
        self.state.project.project_name = (
            inputs.get("project_name")
            or self.state.project.project_name
            or project_id
        )

        if inputs.get("tender_files"):
            self.state.project.tender_files = list(inputs["tender_files"])
        if inputs.get("bid_files"):
            self.state.project.bid_files = list(inputs["bid_files"])

        if self.state.current_step == "done" and self.state.output_path:
            return self.state.tender_content

        self.state.current_step = "parsing"
        save_project_index(
            project_id=project_id,
            project_name=self.state.project.project_name,
            status="parsing",
            tender_files=self.state.project.tender_files,
            bid_files=self.state.project.bid_files,
            flow_id=project_id,
            output_path=self.state.output_path,
        )

        tender_files = self.state.project.tender_files or []
        bid_files = self.state.project.bid_files or []
        if not tender_files:
            raise ValueError(f"招标文件列表为空！project_id={project_id}")
        if not bid_files:
            raise ValueError(f"投标文件列表为空！project_id={project_id}")

        use_mineru = inputs.get("use_mineru", False)
        parse_images = inputs.get("parse_images", False)

        tender_content = parse_files(
            tender_files,
            use_mineru=use_mineru,
            parse_images=parse_images,
        )
        bid_content = parse_files(
            bid_files,
            use_mineru=use_mineru,
            parse_images=parse_images,
        )

        self.state.tender_content = tender_content
        self.state.bid_content = bid_content
        save_flow_snapshot(project_id, self.state)

        return tender_content

    @listen(parse_documents)
    def run_analysis(self, _):
        """Step 2: run AnalysisCrew and store structured deviations."""
        project_id = _project_id(self)
        if self.state.current_step == "done" and self.state.output_path:
            return self.state.deviations

        self.state.current_step = STATUS_ANALYZING
        update_project_index(project_id, status=STATUS_ANALYZING)

        from src.bid_eval.crews.analysis_crew.analysis_crew import AnalysisCrew

        crew_instance = AnalysisCrew()
        crew_instance.project_id = project_id
        result = crew_instance.crew().kickoff(
            inputs={
                "project_id": project_id,
                "tender_content": self.state.tender_content,
                "bid_content": self.state.bid_content,
                "requirements_json": "",
                "responses_json": "",
            }
        )

        self.state.deviations = (
            result.pydantic.items if getattr(result, "pydantic", None) else []
        )
        self.state.current_step = STATUS_AWAITING_REVIEW
        save_flow_snapshot(project_id, self.state)
        update_project_index(project_id, status=STATUS_AWAITING_REVIEW)

        return self.state.deviations

    @listen("revise")
    def handle_revision(self, feedback_result):
        """Step 3a: re-run analysis with user feedback, then review again."""
        project_id = _project_id(self)
        feedback = getattr(feedback_result, "feedback", str(feedback_result))
        self.state.revision_note = feedback
        self.state.revision_history.append(feedback)
        self.state.current_step = "revising"
        update_project_index(project_id, status="revising")

        revision_note = f"\n\n[用户修改意见]\n{feedback}"

        from src.bid_eval.crews.analysis_crew.analysis_crew import AnalysisCrew

        crew_instance = AnalysisCrew()
        crew_instance.project_id = project_id
        result = crew_instance.crew().kickoff(
            inputs={
                "project_id": project_id,
                "tender_content": self.state.tender_content,
                "bid_content": (self.state.bid_content or "") + revision_note,
                "requirements_json": "",
                "responses_json": "",
            }
        )

        self.state.deviations = (
            result.pydantic.items if getattr(result, "pydantic", None) else []
        )
        self.state.current_step = STATUS_AWAITING_REVIEW
        save_flow_snapshot(project_id, self.state)
        update_project_index(project_id, status=STATUS_AWAITING_REVIEW)

        return self.state.deviations

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
    @listen(or_(run_analysis, handle_revision))
    def review_deviations(self, deviations):
        """Step 3: pause for human review in Streamlit."""
        return deviations

    @listen("approved")
    def generate_report(self, feedback_result):
        """Step 4: generate the final Excel report."""
        project_id = _project_id(self)
        if self.state.current_step == STATUS_DONE and self.state.output_path:
            return self.state.output_path

        self.state.current_step = STATUS_GENERATING
        update_project_index(project_id, status=STATUS_GENERATING)

        deviations_json = json.dumps(
            [d.model_dump() for d in self.state.deviations],
            ensure_ascii=False,
            indent=2,
        )

        inputs = _flow_inputs(self)
        generated_at = inputs.get("generated_at") or datetime.now().strftime(
            "%Y-%m-%d %H:%M:%S"
        )

        from src.bid_eval.crews.report_crew.report_crew import ReportCrew
        from src.bid_eval.tools.excel_writer import ExcelWriterTool

        result = ReportCrew().crew().kickoff(
            inputs={
                "project_id": project_id,
                "project_name": _project_name(self),
                "generated_at": generated_at,
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

        self.state.output_path = output_path
        self.state.current_step = STATUS_DONE
        save_flow_snapshot(project_id, self.state)
        save_project_index(
            project_id=project_id,
            project_name=_project_name(self),
            status=STATUS_DONE,
            tender_files=self.state.project.tender_files,
            bid_files=self.state.project.bid_files,
            flow_id=project_id,
            output_path=output_path,
        )

        return output_path


def run():
    """Local entry point for crewai run."""
    flow = DeviationFlow(tracing=True)
    flow.kickoff()


def check_interrupted_flow(project_id: str) -> dict | None:
    """Compatibility helper for older UI code."""
    state = load_flow_snapshot(project_id)
    if not state or state.current_step in ["done", "idle"]:
        return None
    return {
        "project_id": state.project.project_id,
        "project_name": state.project.project_name,
        "current_step": state.current_step,
        "has_deviations": len(state.deviations) > 0,
        "has_output": bool(state.output_path),
        "tender_files": getattr(state.project, "tender_files", []),
        "bid_files": getattr(state.project, "bid_files", []),
    }


def generate_deviation_report(
    project_id: str,
    project_name: str,
    deviations: list,
    generated_at: str | None = None,
) -> str:
    """Legacy direct report generation path. Prefer Flow.resume('approved')."""
    from src.bid_eval.crews.report_crew.report_crew import ReportCrew
    from src.bid_eval.tools.excel_writer import ExcelWriterTool

    if generated_at is None:
        generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    deviations_json = json.dumps(
        [d.model_dump() for d in deviations],
        ensure_ascii=False,
        indent=2,
    )

    result = ReportCrew().crew().kickoff(
        inputs={
            "project_id": project_id,
            "project_name": project_name,
            "generated_at": generated_at,
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
