"""Project-level state helpers for Streamlit and Flow snapshots."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from src.bid_eval.config import PROJECTS_DIR
from src.bid_eval.model import DeviationFlowState


STATUS_ANALYZING = "analyzing"
STATUS_AWAITING_REVIEW = "awaiting_review"
STATUS_RESUMING_REVISE = "resuming_revise"
STATUS_RESUMING_APPROVED = "resuming_approved"
STATUS_GENERATING = "generating"
STATUS_DONE = "done"
STATUS_ERROR = "error"


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def project_dir(project_id: str) -> Path:
    return PROJECTS_DIR / project_id


def project_index_path(project_id: str) -> Path:
    return project_dir(project_id) / "project_index.json"


def flow_snapshot_path(project_id: str) -> Path:
    return project_dir(project_id) / "flow_state.json"


def load_project_index(project_id: str) -> dict[str, Any] | None:
    path = project_index_path(project_id)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def save_project_index(
    project_id: str,
    project_name: str,
    status: str,
    tender_files: list[str] | None = None,
    bid_files: list[str] | None = None,
    flow_id: str | None = None,
    pending_flow_id: str | None = None,
    output_path: str | None = None,
    error: str | None = None,
) -> None:
    existing = load_project_index(project_id) or {}
    data: dict[str, Any] = {
        **existing,
        "project_id": project_id,
        "project_name": project_name,
        "flow_id": flow_id or existing.get("flow_id") or project_id,
        "status": status,
        "updated_at": _now(),
    }

    if tender_files is not None:
        data["tender_files"] = tender_files
    elif "tender_files" not in data:
        data["tender_files"] = []

    if bid_files is not None:
        data["bid_files"] = bid_files
    elif "bid_files" not in data:
        data["bid_files"] = []

    if pending_flow_id is not None:
        data["pending_flow_id"] = pending_flow_id
    elif "pending_flow_id" not in data:
        data["pending_flow_id"] = None

    if output_path is not None:
        data["output_path"] = output_path
    elif "output_path" not in data:
        data["output_path"] = None

    if error is not None:
        data["error"] = error
    elif status != STATUS_ERROR:
        data.pop("error", None)

    path = project_index_path(project_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def update_project_index(project_id: str, **updates: Any) -> None:
    existing = load_project_index(project_id) or {"project_id": project_id}
    existing.update(updates)
    existing["updated_at"] = _now()
    path = project_index_path(project_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(existing, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def clear_project_index(project_id: str) -> None:
    path = project_index_path(project_id)
    if path.exists():
        path.unlink()


def clear_flow_persistence(project_id: str) -> None:
    """Remove CrewAI persisted state for a project id before a fresh restart."""
    try:
        from crewai.flow.persistence import SQLiteFlowPersistence

        persistence = SQLiteFlowPersistence()
        with sqlite3.connect(persistence.db_path, timeout=30) as conn:
            conn.execute("DELETE FROM flow_states WHERE flow_uuid = ?", (project_id,))
            conn.execute(
                "DELETE FROM pending_feedback WHERE flow_uuid = ?",
                (project_id,),
            )
    except Exception:
        # This is a best-effort cleanup; project_index controls the UI path.
        pass


def save_flow_snapshot(project_id: str, state: DeviationFlowState) -> None:
    path = flow_snapshot_path(project_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(state.model_dump_json(), encoding="utf-8")


def load_flow_snapshot(project_id: str) -> DeviationFlowState | None:
    path = flow_snapshot_path(project_id)
    if not path.exists():
        return None
    try:
        return DeviationFlowState.model_validate_json(
            path.read_text(encoding="utf-8")
        )
    except Exception:
        return None
