import time
_t0 = time.time()

import streamlit as st
st.set_option("client.showErrorDetails", False)
print(f"[TIMING] streamlit import: {time.time()-_t0:.1f}s")

import json
import re
from pathlib import Path

#from panel import state
#import streamlit as st 等下计时器删了之后，记得把这个注释去掉
from datetime import datetime
#import time 等下计时器删了之后，记得把这个注释去掉

from src.bid_eval.model import DeviationFlowState, ProjectMeta
from src.bid_eval.config import PROJECTS_DIR


# @st.cache_resource
# def _import_flow_modules():
#     """只在进程生命周期内执行一次，后续复用缓存结果"""
#     from src.bid_eval.main import DeviationFlow, save_flow_state, load_flow_state, run_flow_in_subprocess
#     return DeviationFlow, save_flow_state, load_flow_state, run_flow_in_subprocess

# ── 页面配置 ──────────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="招投标偏离表生成系统",
    #page_icon="📋",
    layout="wide",
)

# ── 工具函数 ──────────────────────────────────────────────────────────────────

def _save_uploads(files, project_id: str, subfolder: str) -> list[str]:
    """将上传的文件保存到本地，返回路径列表"""
    save_dir = PROJECTS_DIR / project_id / "uploads" / subfolder
    save_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for f in files:
        dest = save_dir / f.name
        dest.write_bytes(f.read())
        paths.append(str(dest))
    return paths

def _sync_flow_state_to_session():
    """确保 flow_state 与 session_state.deviations 保持同步"""
    if st.session_state.get("flow_state") is None:
        return
    
    state: DeviationFlowState = st.session_state.flow_state
    st.session_state.deviations = getattr(state, 'deviations', [])
    st.session_state.output_path = getattr(state, 'output_path', None)

def _deviation_badge(deviation_type: str) -> str:
    color_map = {
        "负偏离": "🔴",
        "正偏离": "🟢",
        "无偏离": "⚪",
    }
    return color_map.get(deviation_type, "⚪")


def _slugify(name: str) -> str:
    """将项目名转换为合法的目录名，保留中文"""
    # 去除非字母/数字/中文/横线/下划线/空格
    slug = re.sub(r'[^\w\s\u4e00-\u9fff-]', '', name)
    # 将空格和横线统一为下划线
    slug = re.sub(r'[-\s]+', '_', slug)
    # 去除首尾下划线
    slug = slug.strip('_')
    return slug.lower()


def _check_existing_project(project_id: str) -> dict | None:
    """检查项目目录是否存在，若存在则返回项目信息"""
    project_dir = PROJECTS_DIR / project_id
    uploads_dir = project_dir / "uploads"
    if not uploads_dir.exists():
        return None

    tender_dir = uploads_dir / "tender"
    bid_dir = uploads_dir / "bid"
    tender_files = list(tender_dir.glob("*.docx")) if tender_dir.exists() else []
    bid_files = list(bid_dir.glob("*.docx")) if bid_dir.exists() else []

    return {
        "project_id": project_id,
        "tender_files": [str(f) for f in tender_files],
        "bid_files": [str(f) for f in bid_files],
    }

def _render_resume_or_restart():  # 有中断流程
    p            = st.session_state.pending_interrupted
    project_id   = p["project_id"]
    project_name = p["project_name"]
    interrupted  = p["interrupted"]
    #tender_files = p["tender_files"]
    #bid_files    = p["bid_files"]

    st.warning(
        f"⚠️ 检测到项目「{project_name}」有未完成的分析流程 "
        f"中断阶段：{getattr(interrupted, 'current_step', 'unknown')}"
    )
    
    col_resume, col_fresh = st.columns(2)

    with col_resume:
        if st.button("▶️ 断点续传", use_container_width=True):
            from src.bid_eval.main import load_flow_state
            saved_state = load_flow_state(project_id)
            #flow = load_persisted_flow(project_id)
            #flow = load_flow_state(project_id)
            if saved_state:
                st.session_state.flow_state = saved_state
                st.session_state.deviations = getattr(saved_state, 'deviations', [])
                flow_step = getattr(saved_state, "current_step", "analyzing")
                if flow_step == "idle":
                    # 项目创建过但从未开始分析（有历史文件），直接开始分析
                    st.session_state.step = "analyzing"
                elif flow_step == "awaiting_review":
                    # Flow 内部状态映射到前端 review 步骤
                    st.session_state.step = "reviewing"
                else:
                    st.session_state.step = flow_step
                print(f"[DEBUG] 断点续传 - current_step: {flow_step}, 跳转到: {st.session_state.step}")
                
            else:
                # fallback：使用历史项目中已保存的文件路径（从 interrupted 或 _check_existing_project）
                existing_files = interrupted or {}
                st.session_state.flow_state = DeviationFlowState(
                    id=project_id,
                    project=ProjectMeta(
                        project_id=project_id,
                        project_name=project_name,
                        tender_files=existing_files.get("tender_files", []),
                        bid_files=existing_files.get("bid_files", []),
                    )
                )
                st.session_state.step = "analyzing"
                print(f"[DEBUG] 断点续传 - 没有找到 saved_state，跳转到 analyzing")
            st.session_state.pending_interrupted = None
            st.rerun()

    with col_fresh:
        if st.button("🆕 重新开始", use_container_width=True):
            tender_paths = _save_uploads(p["tender_files"], project_id, "tender")
            bid_paths    = _save_uploads(p["bid_files"],    project_id, "bid")
            st.session_state.flow_state = DeviationFlowState(
                id=project_id,
                project=ProjectMeta(
                    project_id=project_id,
                    project_name=project_name,
                    tender_files=tender_paths,
                    bid_files=bid_paths,
                )
            )
            st.session_state.step = "analyzing"
            st.session_state.pending_interrupted = None
            st.rerun()

def _render_continue_or_new(): # 有历史项目但无中断流程，说明之前只是新建了项目ID并上传过文件，但没有完成分析流程
    p            = st.session_state.pending_existing
    project_id   = p["project_id"]
    project_name = p["project_name"]
    existing     = p["existing"]
    #tender_files = p["tender_files"]  # 新上传的文件对象，供"新建"用
    #bid_files    = p["bid_files"]

    st.warning(f"⚠️ 项目「{project_name}」已存在。上传新文件将覆盖原有文件。")
    col_cont, col_new = st.columns(2)

    with col_cont:
        if st.button("📂 继续历史项目", use_container_width=True):
            st.session_state.flow_state = DeviationFlowState(
                id=project_id,
                project=ProjectMeta(
                    project_id=project_id,
                    project_name=project_name,
                    tender_files=existing.get("tender_files", []),
                    bid_files=existing.get("bid_files", []),
                )
            )
            st.session_state.step             = "analyzing"
            st.session_state.pending_existing = None
            st.rerun()

    with col_new:
        if st.button("🆕 新建项目（另起别名）", use_container_width=True):
            st.session_state.pending_existing = None
            st.info("请在项目名后添加区分标识，如「某市政务云项目_V2」")





# ── 步骤一：文件上传 ──────────────────────────────────────────────────────────
# Session State 初始化，新增一个中间态
def _init_session():
    defaults = {
        "step":             "upload",
        "flow_state":       None,
        "deviations":       [],
        "output_path":      None,
        "error":            None,
        "pending_existing":  None,   # 历史项目，无中断流程但已存在（已上传过文件）
        "pending_interrupted": None,  # 历史项目，有中断流程
        "use_mineru": False,  
        "parse_images": False,
        "revision_note": None,
    }
    for key, val in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = val

    #_init_session()



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

        # 侧边栏加解析选项
        with st.sidebar:
            st.markdown("### 解析选项")
            use_mineru    = st.toggle("启用 MinerU 解析复杂表格", value=False)
            parse_images  = st.toggle("解析图片内容（消耗额外 token）", value=False)

        submitted = st.form_submit_button(
            "开始分析",
            type="primary",
            use_container_width=True,
        )

    if st.session_state.get("pending_interrupted"):
        _render_resume_or_restart()
    elif st.session_state.get("pending_existing"):
        _render_continue_or_new()

    if submitted:
        from src.bid_eval.main import load_flow_state
        # 输入校验
        if not project_name.strip():
            st.error("请输入项目名称")
            return
        if not tender_files:
            st.error("请上传至少一份招标文件")
            return
        if not bid_files:
            st.error("请上传至少一份投标文件")
            return

        # 保存解析选项到 session_state
        st.session_state.use_mineru = use_mineru
        st.session_state.parse_images = parse_images

        # 用项目名 slug 作为 project_id
        project_id = _slugify(project_name.strip())

        # 检查是否有被中断的流程
        #interrupted = check_interrupted_flow(project_id)
        interrupted = load_flow_state(project_id)
        existing = _check_existing_project(project_id)

        if interrupted:
            st.session_state.pending_interrupted = {
                "project_id":    project_id,
                "project_name":  project_name.strip(),
                "interrupted":   interrupted,   # 保留完整字典，含 current_step
                "tender_files":  tender_files,  # 新上传的文件对象，供"重新开始"用
                "bid_files":     bid_files,
            }
            st.rerun()

        elif existing:
            # 不再嵌套 form，改为把信息存进 session_state，触发 rerun
            st.session_state.pending_existing = {
                "project_id":   project_id,
                "project_name": project_name.strip(),
                "existing":     existing,
                "tender_files": tender_files,   # 上传的新文件对象
                "bid_files":    bid_files,
            }
            st.rerun()   # ← rerun 后在 form 外部渲染确认按钮
        

        else:
            # 新项目，直接保存文件并开始分析
            tender_paths = _save_uploads(tender_files, project_id, "tender")
            bid_paths = _save_uploads(bid_files, project_id, "bid")

            flow_state = DeviationFlowState(
                id=project_id,
                project=ProjectMeta(
                    project_id=project_id,
                    project_name=project_name.strip(),
                    tender_files=tender_paths,
                    bid_files=bid_paths,
                )
            )
            st.session_state.flow_state = flow_state
            st.session_state.step = "analyzing"
            st.rerun()


# ── 步骤二：分析中 ────────────────────────────────────────────────────────────
def render_analyzing():
    _sync_flow_state_to_session()
    state: DeviationFlowState = st.session_state.flow_state
    project_id = state.project.project_id

    st.title("📋 招投标偏离表生成系统")
    st.markdown(f"**项目：{state.project.project_name}**")
    st.divider()

    with st.spinner("分析进行中，请稍候..."):
        try:
            from src.bid_eval.main import DeviationFlow, save_flow_state
            from crewai.flow.async_feedback import HumanFeedbackPending

            flow = DeviationFlow()
            saved_dict = state.model_dump()
            for field, value in saved_dict.items():
                try:
                    setattr(flow.state, field, value)
                except (AttributeError, TypeError):
                    pass

            use_mineru   = st.session_state.get("use_mineru", False)
            parse_images = st.session_state.get("parse_images", False)

            result = flow.kickoff(inputs={
                "id":           project_id,
                "project_id":   project_id,
                "use_mineru":   use_mineru,
                "parse_images": parse_images,
            })

            if isinstance(result, HumanFeedbackPending):
                # Flow paused for human feedback — 存储 flow 到 session_state，跳转到 reviewing 页面
                st.session_state.flow_state = flow.state
                st.session_state.deviations = flow.state.deviations
                st.session_state.flow_for_review = flow
                st.session_state.step = "reviewing"
                st.rerun()  # 触发新请求，渲染 render_reviewing
            else:
                # 正常完成
                save_flow_state(project_id, flow.state)
                st.session_state.flow_state = flow.state
                st.session_state.deviations = flow.state.deviations
                st.session_state.step = "reviewing"
                st.rerun()

        except Exception as e:
            st.session_state.error = f"分析流程执行失败：{str(e)}"
            st.session_state.step  = "upload"
            st.rerun()






# ── 步骤三：人工审核 ──────────────────────────────────────────────────────────

def render_reviewing():
    _sync_flow_state_to_session()   # 确保 deviations 最新

    state: DeviationFlowState = st.session_state.flow_state
    deviations = st.session_state.deviations

    st.title("📋 招投标偏离表生成系统")
    st.markdown(f"**项目：{state.project.project_name}**")
    st.divider()

    # 统计摘要
    total    = len(deviations)
    neg      = sum(1 for d in deviations if d.deviation_type == "负偏离")
    pos      = sum(1 for d in deviations if d.deviation_type == "正偏离")
    no_dev   = sum(1 for d in deviations if d.deviation_type == "无偏离")

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("总条数", total)
    c2.metric("无偏离", no_dev)
    c3.metric("正偏离", pos)
    c4.metric("负偏离", neg)

    st.divider()
    st.markdown("#### 偏离详情（请审核后确认或修改）")

    # 负偏离优先展示
    sorted_deviations = sorted(
        deviations,
        key=lambda d: {"负偏离": 0, "正偏离": 1, "无偏离": 2}.get(
            d.deviation_type, 3
        ),
    )

    for d in sorted_deviations:
        badge = _deviation_badge(d.deviation_type)
        with st.expander(
            f"{badge} {d.item_id}  {d.requirement[:40]}...",
            expanded=(d.deviation_type == "负偏离"),
        ):
            col1, col2 = st.columns(2)
            with col1:
                st.markdown("**招标要求**")
                st.info(d.requirement)
                st.markdown("**投标响应**")
                st.info(d.response)
            with col2:
                st.markdown("**偏离情况**")
                st.warning(d.deviation_detail)
                if d.deviation_type == "负偏离":
                    st.markdown("**处理方案**")
                    st.success(d.solution or "（未生成处理方案）")


    st.divider()

    # 修改意见输入框（可选）
    revision_note = st.text_area(
        "修改意见（可选）",
        placeholder="如需重新分析，请在此描述修改意见，例如：第3.2.1条处理方案需要更具体...",
        height=80,
        value=st.session_state.get("revision_note", ""), # 保留已输入内容
    )

    # 如果有问题就把这个引入去掉
    from src.bid_eval.main import DeviationFlow
    # 检查是否有 pending flow（从 HumanFeedbackPending 保存的）
    pending_flow: "DeviationFlow" | None = st.session_state.get("flow_for_review")

    col_approve, col_revise = st.columns([3, 1])
    with col_approve:
        if st.button(
            "✅ 确认无误，生成偏离表",
            type="primary",
            use_container_width=True,
        ):
            if pending_flow:
                # 有 pending flow，通过 resume 继续流程
                from src.bid_eval.main import save_flow_state
                pending_flow.resume("approved")
                with st.spinner("正在生成偏离表..."):
                    pending_flow.resume("approved")
                save_flow_state(state.project.project_id, pending_flow.state)
                st.session_state.flow_state = pending_flow.state
                st.session_state.deviations = pending_flow.state.deviations
                st.session_state.output_path = getattr(pending_flow.state, 'output_path', None)
                del st.session_state["flow_for_review"]
                st.session_state.step = "done"   # ← 直接跳 done，跳过 generating
            else:
                st.session_state.revision_note = revision_note
                st.session_state.step = "generating"
            st.rerun()

    with col_revise:
        if st.button(
            "🔄 重新分析",
            use_container_width=True,
            disabled=not revision_note,
        ):
            if pending_flow:
                # 有 pending flow，通过 resume 继续流程
                from src.bid_eval.main import save_flow_state
                pending_flow.resume(f"需要修改：{revision_note}")
                save_flow_state(state.project.project_id, pending_flow.state)
                st.session_state.flow_state = pending_flow.state
                st.session_state.deviations = pending_flow.state.deviations
                del st.session_state["flow_for_review"]
            st.session_state.step = "generating"
            st.rerun()


# ── 步骤三附：重新分析 ────────────────────────────────────────────────────────

def render_revising():
    from src.bid_eval.main import DeviationFlow
    _sync_flow_state_to_session()
    state: DeviationFlowState = st.session_state.flow_state
    revision_note = st.session_state.get("revision_note", "").strip()
    project_id = state.project.project_id

    st.title("📋 招投标偏离表生成系统")
    st.markdown(f"**项目：{state.project.project_name}**")
    st.divider()

    with st.spinner("正在根据修改意见重新分析..."):
        try:
            flow = DeviationFlow()

            bid_content_with_note = (
                (state.bid_content or "") )
            if revision_note:
                bid_content_with_note += f"\n\n[用户修改意见]\n{revision_note}"
                

            # 传入 revision_note
            result = flow.kickoff(
                inputs={
                    "id": project_id,                    # ← 必须
                    "project_id": project_id,
                    "use_mineru": st.session_state.get("use_mineru", False),
                    "parse_images": st.session_state.get("parse_images", False),
                    "revision_note": revision_note,      # 让 Flow 的 handle_revision 使用
                    "force_reanalyze": True,
                }
            )

            #state.deviations            = result.pydantic.items if result.pydantic else []
            st.session_state.flow_state = flow.state

            new_deviations = []
            pydantic_output = getattr(result, 'pydantic', None) or getattr(flow.state, 'deviations', None)
            if pydantic_output:
                if hasattr(pydantic_output, 'items'):
                    new_deviations = pydantic_output.items
                else:
                    new_deviations = pydantic_output
            st.session_state.deviations = new_deviations

            st.session_state.step       = "reviewing"
            
            st.success("重新分析完成，返回审核页面")
            st.rerun()

        except Exception as e:
            st.session_state.error = f"重新分析失败：{str(e)}"
            st.session_state.step  = "reviewing"
            st.rerun()


# ── 步骤四：生成报告 ──────────────────────────────────────────────────────────

def render_generating():
    from src.bid_eval.main import generate_deviation_report 
    _sync_flow_state_to_session()
    state: DeviationFlowState = st.session_state.flow_state
    project_id = state.project.project_id

    st.title("📋 招投标偏离表生成系统")
    st.markdown(f"**项目：{state.project.project_name}**")
    st.divider()

    with st.spinner("正在生成偏离表 Excel..."):
        try:
            deviations = st.session_state.deviations
            output_path = generate_deviation_report(
                project_id=project_id,
                project_name=state.project.project_name,
                deviations=deviations,
                generated_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            )
            st.session_state.output_path = output_path
            state.output_path = output_path          # 同步回 flow_state
            st.session_state.step = "done"
            st.success("✅ 偏离表生成完成")
            st.rerun()

        except Exception as e:
            st.session_state.error = f"生成表格失败：{str(e)}"
            st.session_state.step  = "reviewing"
            st.rerun()


# ── 步骤五：完成 ──────────────────────────────────────────────────────────────

def render_done():
    _sync_flow_state_to_session()
    state: DeviationFlowState = st.session_state.flow_state
    output_path = st.session_state.output_path

    st.title("📋 招投标偏离表生成系统")
    st.success(f"✅ 偏离表已生成：{state.project.project_name}")
    st.divider()

    # 统计摘要
    deviations = st.session_state.deviations
    total  = len(deviations)
    neg    = sum(1 for d in deviations if d.deviation_type == "负偏离")
    pos    = sum(1 for d in deviations if d.deviation_type == "正偏离")
    no_dev = sum(1 for d in deviations if d.deviation_type == "无偏离")

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("总条数", total)
    c2.metric("无偏离", no_dev)
    c3.metric("正偏离", pos)
    c4.metric("负偏离", neg)

    st.divider()

    # 下载按钮
    if output_path and Path(output_path).exists():
        with open(output_path, "rb") as f:
            st.download_button(
                label="⬇️ 下载偏离表 Excel",
                data=f,
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


# ── 错误提示 ──────────────────────────────────────────────────────────────────

def render_error():
    error = st.session_state.get("error")
    if error:
        st.error(f"发生错误：{error}")
        if st.button("返回重试"):
            st.session_state.error = None
            st.rerun()


# ── 主路由 ────────────────────────────────────────────────────────────────────


def main():
    _init_session()
    render_error()

    step = st.session_state.step
    if   step == "upload":     render_upload()
    elif step == "idle":       render_analyzing()
    elif step == "analyzing":  render_analyzing()
    elif step == "reviewing":  render_reviewing()
    elif step == "revising":   render_revising()
    elif step == "generating": render_generating()
    elif step == "done":       render_done()



if __name__ == "__main__":
    main()
