from __future__ import annotations

import json
import threading
import uuid
from pathlib import Path
from typing import Any

from cerebro_mcp.research_models import (
    EvidenceRef,
    PeerReviewResult,
    ResearchFinding,
    ResearchMemoryEntry,
    ResearchProjectState,
)
from cerebro_mcp.research_workflow import empty_phase_records


def _write_json_atomic(path: Path, payload: Any) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    tmp.replace(path)


class ResearchStore:
    def __init__(self, base_dir: str):
        self.base_dir = Path(base_dir)
        self._lock = threading.RLock()

    def _project_dir(self, project_id: str) -> Path:
        return self.base_dir / project_id

    def _project_file(self, project_id: str) -> Path:
        return self._project_dir(project_id) / "project.json"

    def _memory_file(self, project_id: str) -> Path:
        return self._project_dir(project_id) / "memory.json"

    def _findings_file(self, project_id: str) -> Path:
        return self._project_dir(project_id) / "findings.json"

    def _evidence_file(self, project_id: str) -> Path:
        return self._project_dir(project_id) / "evidence.json"

    def _peer_review_file(self, project_id: str) -> Path:
        return self._project_dir(project_id) / "peer_review.json"

    def _phase_dir(self, project_id: str) -> Path:
        return self._project_dir(project_id) / "phases"

    def _artifact_dir(self, project_id: str, kind: str) -> Path:
        return self._project_dir(project_id) / "artifacts" / kind

    def ensure_base_dir(self) -> None:
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def create_project(
        self,
        hypothesis: str,
        scope: str,
        target_models: list[str] | None = None,
    ) -> ResearchProjectState:
        with self._lock:
            self.ensure_base_dir()
            project_id = f"rp_{uuid.uuid4().hex[:12]}"
            project = ResearchProjectState(
                project_id=project_id,
                hypothesis=hypothesis.strip(),
                scope=scope.strip(),
                target_models=target_models or [],
                phases=empty_phase_records(),
            )
            project_dir = self._project_dir(project_id)
            project_dir.mkdir(parents=True, exist_ok=True)
            self._phase_dir(project_id).mkdir(parents=True, exist_ok=True)
            self._artifact_dir(project_id, "query_results").mkdir(parents=True, exist_ok=True)
            self._artifact_dir(project_id, "schema_snapshots").mkdir(parents=True, exist_ok=True)
            self.save_project(project)
            _write_json_atomic(self._memory_file(project_id), [])
            _write_json_atomic(self._findings_file(project_id), [])
            _write_json_atomic(self._evidence_file(project_id), [])
            return project

    def load_project(self, project_id: str) -> ResearchProjectState:
        with self._lock:
            path = self._project_file(project_id)
            if not path.exists():
                raise ValueError(f"Research project '{project_id}' not found.")
            payload = json.loads(path.read_text(encoding="utf-8"))
            return ResearchProjectState.model_validate(payload)

    def save_project(self, project: ResearchProjectState) -> None:
        with self._lock:
            self.ensure_base_dir()
            project_dir = self._project_dir(project.project_id)
            project_dir.mkdir(parents=True, exist_ok=True)
            self._phase_dir(project.project_id).mkdir(parents=True, exist_ok=True)
            for phase, record in project.phases.items():
                _write_json_atomic(
                    self._phase_dir(project.project_id) / f"{phase}.json",
                    record.model_dump(),
                )
            _write_json_atomic(
                self._project_file(project.project_id),
                project.model_dump(),
            )

    def _load_model_list(self, path: Path, model_cls):
        if not path.exists():
            return []
        payload = json.loads(path.read_text(encoding="utf-8"))
        return [model_cls.model_validate(item) for item in payload]

    def _save_model_list(self, path: Path, values: list[Any]) -> None:
        _write_json_atomic(path, [value.model_dump() for value in values])

    def list_memory(self, project_id: str) -> list[ResearchMemoryEntry]:
        with self._lock:
            self.load_project(project_id)
            return self._load_model_list(
                self._memory_file(project_id),
                ResearchMemoryEntry,
            )

    def append_memory(self, project_id: str, entry: ResearchMemoryEntry) -> None:
        with self._lock:
            current = self.list_memory(project_id)
            current.append(entry)
            self._save_model_list(self._memory_file(project_id), current)

    def list_findings(self, project_id: str) -> list[ResearchFinding]:
        with self._lock:
            self.load_project(project_id)
            return self._load_model_list(
                self._findings_file(project_id),
                ResearchFinding,
            )

    def append_finding(self, project_id: str, finding: ResearchFinding) -> None:
        with self._lock:
            current = self.list_findings(project_id)
            current.append(finding)
            self._save_model_list(self._findings_file(project_id), current)

    def list_evidence(self, project_id: str) -> list[EvidenceRef]:
        with self._lock:
            self.load_project(project_id)
            return self._load_model_list(self._evidence_file(project_id), EvidenceRef)

    def append_evidence(self, project_id: str, evidence: EvidenceRef) -> None:
        with self._lock:
            current = self.list_evidence(project_id)
            current.append(evidence)
            self._save_model_list(self._evidence_file(project_id), current)

    def load_peer_review(self, project_id: str) -> PeerReviewResult | None:
        with self._lock:
            self.load_project(project_id)
            path = self._peer_review_file(project_id)
            if not path.exists():
                return None
            payload = json.loads(path.read_text(encoding="utf-8"))
            return PeerReviewResult.model_validate(payload)

    def save_peer_review(self, project_id: str, result: PeerReviewResult) -> None:
        with self._lock:
            self.load_project(project_id)
            _write_json_atomic(
                self._peer_review_file(project_id),
                result.model_dump(),
            )

    def save_query_result_artifact(
        self,
        *,
        project_id: str,
        title: str,
        sql: str,
        database: str,
        columns: list[str],
        rows: list[list],
        row_count: int,
    ) -> str:
        with self._lock:
            self.load_project(project_id)
            ref_id = f"qry_{uuid.uuid4().hex[:12]}"
            artifact = {
                "ref_id": ref_id,
                "kind": "query_result",
                "project_id": project_id,
                "title": title,
                "sql": sql,
                "database": database,
                "columns": columns,
                "rows": rows,
                "row_count": row_count,
            }
            path = self._artifact_dir(project_id, "query_results") / f"{ref_id}.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            _write_json_atomic(path, artifact)
            return ref_id

    def load_query_result_artifact(self, project_id: str, ref_id: str) -> dict[str, Any]:
        with self._lock:
            path = self._artifact_dir(project_id, "query_results") / f"{ref_id}.json"
            if not path.exists():
                raise ValueError(
                    f"Query result artifact '{ref_id}' not found for project '{project_id}'."
                )
            return json.loads(path.read_text(encoding="utf-8"))

    def save_semantic_query_result_artifact(
        self,
        *,
        project_id: str,
        title: str,
        sql: str,
        database: str,
        columns: list[str],
        rows: list[list],
        row_count: int,
        semantic_plan: dict[str, Any],
    ) -> str:
        with self._lock:
            self.load_project(project_id)
            ref_id = f"semqry_{uuid.uuid4().hex[:12]}"
            artifact = {
                "ref_id": ref_id,
                "kind": "semantic_query_result",
                "project_id": project_id,
                "title": title,
                "sql": sql,
                "database": database,
                "columns": columns,
                "rows": rows,
                "row_count": row_count,
                "semantic_plan": semantic_plan,
            }
            path = self._artifact_dir(project_id, "query_results") / f"{ref_id}.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            _write_json_atomic(path, artifact)
            return ref_id

    def save_schema_snapshot_artifact(
        self,
        *,
        project_id: str,
        database: str,
        table: str,
        payload: dict[str, Any],
        title: str = "",
    ) -> str:
        with self._lock:
            self.load_project(project_id)
            ref_id = f"schema_{uuid.uuid4().hex[:12]}"
            artifact = {
                "ref_id": ref_id,
                "kind": "schema_snapshot",
                "project_id": project_id,
                "database": database,
                "table": table,
                "title": title,
                "payload": payload,
            }
            path = self._artifact_dir(project_id, "schema_snapshots") / f"{ref_id}.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            _write_json_atomic(path, artifact)
            return ref_id

    def artifact_exists(self, project_id: str, kind: str, ref_id: str) -> bool:
        with self._lock:
            if kind in {"query_result", "semantic_query_result"}:
                path = self._artifact_dir(project_id, "query_results") / f"{ref_id}.json"
                return path.exists()
            if kind == "schema_snapshot":
                path = self._artifact_dir(project_id, "schema_snapshots") / f"{ref_id}.json"
                return path.exists()
            return False

    def artifact_count(self, project_id: str) -> int:
        with self._lock:
            self.load_project(project_id)
            total = 0
            for subdir in ("query_results", "schema_snapshots"):
                directory = self._artifact_dir(project_id, subdir)
                if directory.exists():
                    total += len(list(directory.glob("*.json")))
            return total
