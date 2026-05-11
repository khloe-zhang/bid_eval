import re
from pathlib import Path

import streamlit as st

from crewai.flow.async_feedback import HumanFeedbackPending

from src.bid_eval.config import PROJECTS_DIR
from src.bid_eval.main import DeviationFlow
from src.bid_eval.main import generate_deviation_report
from src.bid_eval.model import DeviationFlowState
from src.bid_eval.project_store import (
    STATUS_ANALYZING,
    STATUS_AWAITING_REVIEW,
    STATUS_DONE,
    STATUS_ERROR,
    STATUS_GENERATING,
    STATUS_RESUMING_APPROVED,
    STATUS_RESUMING_REVISE,
    clear_flow_persistence,
    clear_project_index,
    load_flow_snapshot,
    load_project_index,
    save_project_index,
    update_project_index,
)


st.set_option("client.showErrorDetails", False)
st.set_page_config(
    page_title="招投标偏离表生成系统",
    layout="wide",
)


def _save_uploads(files, project_id: str, subfolder: str) -> list[str]:
    """Save uploaded files locally and return their paths."""
    save_dir = PROJECTS_DIR / project_id / "uploads" / subfolder
    save_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for uploaded_file in files or []:
        dest = save_dir / uploaded_file.name
        dest.write_bytes(uploaded_file.read())
        paths.append(str(dest))
    return paths


def _deviation_badge(deviation_type: str) -> str:
    color_map = {
        "负偏离": "🔴",
        "正偏离": "🟢",
        "无偏离": "⚪",
    }
    return color_map.get(deviation_type, "⚪")


def _slugify(name: str) -> str:
    """Convert a project name into a stable directory id, preserving Chinese."""
    slug = re.sub(r"[^\w\s\u4e00-\u9fff-]", "", name)
    slug = re.sub(r"[-\s]+", "_", slug)
    slug = slug.strip("_")
    return slug.lower()


def _glob_files(folder: Path) -> list[str]:
    patterns = ["*.docx", "*.pdf", "*.png", "*.jpg", "*.jpeg"]
    files: list[Path] = []
    for pattern in patterns:
        files.extend(folder.glob(pattern))
    return [str(path) for path in files]


def _check_existing_project(project_id: str) -> dict | None:
    """Return uploaded file information if the project directory exists."""
    uploads_dir = PROJECTS_DIR / project_id / "uploads"
    if not uploads_dir.exists():
        return None

    tender_files = _glob_files(uploads_dir / "tender")
    bid_files = _glob_files(uploads_dir / "bid")
    if not tender_files and not bid_files:
        return None

    return {
        "project_id": project_id,
        "tender_files": tender_files,
        "bid_files": bid_files,
    }


def _init_session():
    defaults = {
        "step": "upload",
        "project_id": None,
        "project_name": None,
        "pending_flow_id": None,
        "error": None,
        "pending_existing": None,
        "pending_interrupted": None,
        "use_mineru": False,
        "parse_images": False,
        "revision_note": "",
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def _set_active_project(project_id: str, project_name: str, pending_flow_id=None):
    st.session_state.project_id = project_id
    st.session_state.project_name = project_name
    st.session_state.pending_flow_id = pending_flow_id


def _index_status(index: dict | None) -> str:
    return (index or {}).get("status", "")


def _files_from_index_or_existing(project_id: str) -> tuple[list[str], list[str]]:
    index = load_project_index(project_id) or {}
    existing = _check_existing_project(project_id) or {}
    tender_files = index.get("tender_files") or existing.get("tender_files") or []
    bid_files = index.get("bid_files") or existing.get("bid_files") or []
    return tender_files, bid_files


def _fallback_state(project_id: str) -> DeviationFlowState | None:
    return load_flow_snapshot(project_id)


def _load_pending_flow(project_id: str, pending_flow_id: str | None):
    errors = []
    for flow_id in [pending_flow_id, project_id]:
        if not flow_id:
            continue
        try:
            return DeviationFlow.from_pending(flow_id)
        except Exception as exc:
            errors.append(str(exc))
    raise ValueError("无法恢复等待审核的 Flow：" + " | ".join(errors))


def _render_resume_or_restart():
    pending = st.session_state.pending_interrupted
    project_id = pending["project_id"]
    project_name = pending["project_name"]
    index = pending.get("index") or {}
    status = index.get("status", "unknown")

    st.warning(
        f"⚠️ 检测到项目「{project_name}」有未完成的分析流程，中断阶段：{status}"
    )

    col_resume, col_fresh = st.columns(2)
    with col_resume:
        if st.button("▶️ 断点续传", use_container_width=True):
            pending_flow_id = index.get("pending_flow_id") or index.get("flow_id") or project_id
            _set_active_project(project_id, project_name, pending_flow_id)
            if status == STATUS_AWAITING_REVIEW:
                st.session_state.step = "reviewing"
            elif status == STATUS_DONE:
                st.session_state.step = "done"
            elif status in {
                STATUS_RESUMING_APPROVED,
                STATUS_GENERATING,
            }:
                st.session_state.step = "generating"
            else:
                st.session_state.step = "analyzing"
            st.session_state.pending_interrupted = None
            st.rerun()

    with col_fresh:
        if st.button("🆕 重新开始并覆盖", use_container_width=True):
            tender_files = pending.get("tender_files") or []
            bid_files = pending.get("bid_files") or []
            if not tender_files or not bid_files:
                st.error("重新开始需要重新上传招标文件和投标文件。")
                return
            tender_paths = _save_uploads(tender_files, project_id, "tender")
            bid_paths = _save_uploads(bid_files, project_id, "bid")
            clear_project_index(project_id)
            clear_flow_persistence(project_id)
            save_project_index(
                project_id=project_id,
                project_name=project_name,
                status=STATUS_ANALYZING,
                tender_files=tender_paths,
                bid_files=bid_paths,
                flow_id=project_id,
                pending_flow_id=None,
                output_path=None,
            )
            _set_active_project(project_id, project_name, project_id)
            st.session_state.step = "analyzing"
            st.session_state.pending_interrupted = None
            st.rerun()


def _render_continue_or_new():
    pending = st.session_state.pending_existing
    project_id = pending["project_id"]
    project_name = pending["project_name"]
    existing = pending["existing"]

    st.warning(f"⚠️ 项目「{project_name}」已存在。上传新文件将覆盖原有文件。")
    col_cont, col_new = st.columns(2)

    with col_cont:
        if st.button("📂 继续历史项目", use_container_width=True):
            save_project_index(
                project_id=project_id,
                project_name=project_name,
                status=STATUS_ANALYZING,
                tender_files=existing.get("tender_files", []),
                bid_files=existing.get("bid_files", []),
                flow_id=project_id,
            )
            _set_active_project(project_id, project_name, project_id)
            st.session_state.step = "analyzing"
            st.session_state.pending_existing = None
            st.rerun()

    with col_new:
        if st.button("🆕 新建项目（另起别名）", use_container_width=True):
            st.session_state.pending_existing = None
            st.info("请在项目名后添加区分标识，如「某市政务云项目_V2」")


def render_upload():
    st.title("📋 招投标偏离表生成系统")
    st.markdown("上传招标文件与投标文件，系统将自动完成偏离分析并生成偏离表。")
    st.divider()

    with st.form("upload_form"):
        project_name = st.text_input(
            "项目名称",
            placeholder="例：某市政务云采购项目",
        )

        col1, col2 = st.columns(2)
        with col1:
            st.markdown("#### 招标文件")
            tender_files = st.file_uploader(
                "上传招标文件（支持多个 .docx 和 .pdf）",
                type=["docx", "pdf"],
                accept_multiple_files=True,
                key="tender_uploader",
            )
        with col2:
            st.markdown("#### 投标文件")
            bid_files = st.file_uploader(
                "上传投标文件（支持多个 .docx 和 .pdf）",
                type=["docx", "pdf"],
                accept_multiple_files=True,
                key="bid_uploader",
            )

        with st.sidebar:
            st.markdown("### 解析选项")
            use_mineru = st.toggle("启用 MinerU 解析复杂表格", value=False)
            parse_images = st.toggle("解析图片内容（消耗额外 token）", value=False)

        submitted = st.form_submit_button(
            "开始分析 / 检查项目",
            type="primary",
            use_container_width=True,
        )

    if st.session_state.get("pending_interrupted"):
        _render_resume_or_restart()
    elif st.session_state.get("pending_existing"):
        _render_continue_or_new()

    if not submitted:
        return

    if not project_name.strip():
        st.error("请输入项目名称")
        return

    st.session_state.use_mineru = use_mineru
    st.session_state.parse_images = parse_images

    project_name_clean = project_name.strip()
    project_id = _slugify(project_name_clean)
    index = load_project_index(project_id)
    existing = _check_existing_project(project_id)

    if index and _index_status(index) != STATUS_DONE:
        st.session_state.pending_interrupted = {
            "project_id": project_id,
            "project_name": project_name_clean,
            "index": index,
            "tender_files": tender_files,
            "bid_files": bid_files,
        }
        st.rerun()

    if existing and (not tender_files or not bid_files):
        st.session_state.pending_existing = {
            "project_id": project_id,
            "project_name": project_name_clean,
            "existing": existing,
            "tender_files": tender_files,
            "bid_files": bid_files,
        }
        st.rerun()

    if not tender_files:
        st.error("请上传至少一份招标文件")
        return
    if not bid_files:
        st.error("请上传至少一份投标文件")
        return

    tender_paths = _save_uploads(tender_files, project_id, "tender")
    bid_paths = _save_uploads(bid_files, project_id, "bid")
    if index:
        clear_project_index(project_id)
        clear_flow_persistence(project_id)
    save_project_index(
        project_id=project_id,
        project_name=project_name_clean,
        status=STATUS_ANALYZING,
        tender_files=tender_paths,
        bid_files=bid_paths,
        flow_id=project_id,
        pending_flow_id=None,
        output_path=None,
    )
    _set_active_project(project_id, project_name_clean, project_id)
    st.session_state.step = "analyzing"
    st.rerun()


def render_analyzing():
    project_id = st.session_state.project_id
    project_name = st.session_state.project_name or project_id
    if not project_id:
        st.session_state.error = "缺少项目 ID，请重新输入项目名称。"
        st.session_state.step = "upload"
        st.rerun()

    tender_files, bid_files = _files_from_index_or_existing(project_id)
    st.title("📋 招投标偏离表生成系统")
    st.markdown(f"**项目：{project_name}**")
    st.divider()

    with st.spinner("分析进行中，请稍候..."):
        try:
            update_project_index(project_id, status=STATUS_ANALYZING)
            flow = DeviationFlow(tracing=True)
            result = flow.kickoff(
                inputs={
                    "id": project_id,
                    "project_id": project_id,
                    "project_name": project_name,
                    "tender_files": tender_files,
                    "bid_files": bid_files,
                    "use_mineru": st.session_state.get("use_mineru", False),
                    "parse_images": st.session_state.get("parse_images", False),
                }
            )

            if isinstance(result, HumanFeedbackPending):
                pending_flow_id = result.context.flow_id
                save_project_index(
                    project_id=project_id,
                    project_name=project_name,
                    status=STATUS_AWAITING_REVIEW,
                    tender_files=tender_files,
                    bid_files=bid_files,
                    flow_id=project_id,
                    pending_flow_id=pending_flow_id,
                    output_path=getattr(flow.state, "output_path", None),
                )
                st.session_state.pending_flow_id = pending_flow_id
                st.session_state.step = "reviewing"
                st.rerun()

            current_step = getattr(flow.state, "current_step", "")
            if current_step == STATUS_DONE:
                update_project_index(
                    project_id,
                    status=STATUS_DONE,
                    output_path=getattr(flow.state, "output_path", None),
                )
                st.session_state.step = "done"
            elif current_step == STATUS_AWAITING_REVIEW:
                update_project_index(project_id, status=STATUS_AWAITING_REVIEW)
                st.session_state.pending_flow_id = project_id
                st.session_state.step = "reviewing"
            else:
                st.session_state.step = "reviewing"
            st.rerun()

        except Exception as exc:
            update_project_index(project_id, status=STATUS_ERROR, error=str(exc))
            st.session_state.error = f"分析流程执行失败：{exc}"
            st.session_state.step = "upload"
            st.rerun()


def _render_deviation_list(state: DeviationFlowState):
    deviations = state.deviations
    total = len(deviations)
    neg = sum(1 for d in deviations if d.deviation_type == "负偏离")
    pos = sum(1 for d in deviations if d.deviation_type == "正偏离")
    no_dev = sum(1 for d in deviations if d.deviation_type == "无偏离")

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("总条数", total)
    c2.metric("无偏离", no_dev)
    c3.metric("正偏离", pos)
    c4.metric("负偏离", neg)

    st.divider()
    st.markdown("#### 偏离详情（请审核后确认或修改）")

    sorted_deviations = sorted(
        deviations,
        key=lambda d: {"负偏离": 0, "正偏离": 1, "无偏离": 2}.get(
            d.deviation_type, 3
        ),
    )

    for item in sorted_deviations:
        badge = _deviation_badge(item.deviation_type)
        with st.expander(
            f"{badge} {item.item_id}  {item.requirement[:40]}...",
            expanded=(item.deviation_type == "负偏离"),
        ):
            col1, col2 = st.columns(2)
            with col1:
                st.markdown("**招标要求**")
                st.info(item.requirement)
                st.markdown("**投标响应**")
                st.info(item.response)
            with col2:
                st.markdown("**偏离情况**")
                st.warning(item.deviation_detail)
                if item.deviation_type == "负偏离":
                    st.markdown("**处理方案**")
                    st.success(item.solution or "（未生成处理方案）")


def render_reviewing():
    project_id = st.session_state.project_id
    project_name = st.session_state.project_name or project_id
    index = load_project_index(project_id) or {}
    pending_flow_id = (
        st.session_state.pending_flow_id
        or index.get("pending_flow_id")
        or index.get("flow_id")
        or project_id
    )

    st.title("📋 招投标偏离表生成系统")
    st.markdown(f"**项目：{project_name}**")
    st.divider()

    try:
        flow = _load_pending_flow(project_id, pending_flow_id)
        state = flow.state
        can_resume = True
    except Exception as exc:
        state = _fallback_state(project_id)
        can_resume = False
        st.warning(f"暂时无法恢复 CrewAI 等待审核状态：{exc}")
        if state is None:
            st.error("未找到可展示的偏离分析快照，请重新开始分析。")
            return
        if state.deviations:
            st.info("已找到上次审核后的偏离分析快照，可以跳过等待审核状态并继续生成表格。")
            if st.button("继续生成偏离表", type="primary", use_container_width=True):
                st.session_state.error = None
                update_project_index(project_id, status=STATUS_GENERATING, error=None)
                st.session_state.step = "generating"
                st.rerun()

    _render_deviation_list(state)

    st.divider()
    revision_note = st.text_area(
        "修改意见（可选）",
        placeholder="如需重新分析，请在此描述修改意见，例如：第3.2.1条处理方案需要更具体...",
        height=80,
        value=st.session_state.get("revision_note", ""),
    )
    st.session_state.revision_note = revision_note

    col_approve, col_revise = st.columns([3, 1])
    with col_approve:
        if st.button(
            "✅ 确认无误，生成偏离表",
            type="primary",
            use_container_width=True,
            disabled=not can_resume,
        ):
            update_project_index(project_id, status=STATUS_RESUMING_APPROVED)
            with st.spinner("正在生成偏离表..."):
                try:
                    flow.resume("approved")
                    update_project_index(
                        project_id,
                        status=STATUS_DONE,
                        output_path=getattr(flow.state, "output_path", None),
                    )
                    st.session_state.step = "done"
                    st.rerun()
                except Exception as exc:
                    update_project_index(
                        project_id,
                        status=STATUS_GENERATING,
                        error=str(exc),
                    )
                    st.session_state.error = f"生成偏离表失败：{exc}"
                    st.session_state.step = "generating"
                    st.rerun()

    with col_revise:
        if st.button(
            "🔄 重新分析",
            use_container_width=True,
            disabled=(not revision_note.strip() or not can_resume),
        ):
            update_project_index(project_id, status=STATUS_RESUMING_REVISE)
            with st.spinner("正在根据修改意见重新分析..."):
                try:
                    result = flow.resume(f"revise: {revision_note.strip()}")
                    if isinstance(result, HumanFeedbackPending):
                        pending_flow_id = result.context.flow_id
                        update_project_index(
                            project_id,
                            status=STATUS_AWAITING_REVIEW,
                            pending_flow_id=pending_flow_id,
                        )
                        st.session_state.pending_flow_id = pending_flow_id
                    else:
                        update_project_index(
                            project_id,
                            status=STATUS_AWAITING_REVIEW,
                            pending_flow_id=project_id,
                        )
                    st.session_state.step = "reviewing"
                    st.rerun()
                except Exception as exc:
                    update_project_index(
                        project_id,
                        status=STATUS_AWAITING_REVIEW,
                        error=str(exc),
                    )
                    st.session_state.error = f"重新分析失败：{exc}"
                    st.rerun()


def render_generating():
    project_id = st.session_state.project_id
    project_name = st.session_state.project_name or project_id
    index = load_project_index(project_id) or {}
    state = _fallback_state(project_id)

    st.title("📋 招投标偏离表生成系统")
    st.markdown(f"**项目：{project_name}**")
    st.divider()

    if state is None or not state.deviations:
        st.error("未找到可用于生成表格的偏离分析快照，请返回重新分析。")
        if st.button("返回重新分析", use_container_width=True):
            update_project_index(project_id, status=STATUS_ANALYZING)
            st.session_state.step = "analyzing"
            st.rerun()
        return

    last_error = index.get("error")
    if last_error:
        st.warning(f"上次生成中断：{last_error}")

    st.info("审核已通过，可以继续生成偏离表。")
    if st.button("继续生成偏离表", type="primary", use_container_width=True):
        st.session_state.error = None
        update_project_index(project_id, status=STATUS_GENERATING, error=None)
        with st.spinner("正在生成偏离表..."):
            try:
                output_path = generate_deviation_report(
                    project_id=project_id,
                    project_name=state.project.project_name or project_name,
                    deviations=state.deviations,
                )
                state.output_path = output_path
                state.current_step = STATUS_DONE
                from src.bid_eval.project_store import save_flow_snapshot

                save_flow_snapshot(project_id, state)
                update_project_index(
                    project_id,
                    status=STATUS_DONE,
                    output_path=output_path,
                    error=None,
                )
                st.session_state.error = None
                st.session_state.step = "done"
                st.rerun()
            except Exception as exc:
                update_project_index(project_id, status=STATUS_GENERATING, error=str(exc))
                st.session_state.error = f"生成偏离表失败：{exc}"
                st.rerun()


def render_done():
    project_id = st.session_state.project_id
    project_name = st.session_state.project_name or project_id
    index = load_project_index(project_id) or {}
    state = _fallback_state(project_id)
    output_path = index.get("output_path") or getattr(state, "output_path", None)

    st.title("📋 招投标偏离表生成系统")
    st.success(f"✅ 偏离表已生成：{project_name}")
    st.divider()

    if state:
        _render_deviation_list(state)
        st.divider()

    if output_path and Path(output_path).exists():
        with open(output_path, "rb") as file:
            st.download_button(
                label="⬇️ 下载偏离表 Excel",
                data=file,
                file_name=Path(output_path).name,
                mime=(
                    "application/vnd.openxmlformats-"
                    "officedocument.spreadsheetml.sheet"
                ),
                type="primary",
                use_container_width=True,
            )
    else:
        st.error("输出文件未找到，请重新生成。")

    st.divider()
    if st.button("🆕 开始新项目", use_container_width=True):
        for key in list(st.session_state.keys()):
            del st.session_state[key]
        st.rerun()


def render_error():
    error = st.session_state.get("error")
    if not error:
        return
    st.error(f"发生错误：{error}")
    if st.button("返回重试"):
        st.session_state.error = None
        st.rerun()


def main():
    _init_session()
    render_error()

    step = st.session_state.step
    if step == "upload":
        render_upload()
    elif step in {"idle", "analyzing", "revising"}:
        render_analyzing()
    elif step == "generating":
        render_generating()
    elif step == "reviewing":
        render_reviewing()
    elif step == "done":
        render_done()


if __name__ == "__main__":
    main()
