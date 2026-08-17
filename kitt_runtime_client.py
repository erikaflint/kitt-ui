#!/usr/bin/env python3
"""KITT V1.2 runtime client.

Phase 1 adapter: scripts call this client instead of caring whether the runtime
is a local SQLite pilot database or the shared Cloudflare Worker API.
"""

from __future__ import annotations

import json
import os
import sqlite3
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / "db" / "kitt_v1_2_d1_schema.sql"
SEED_GATES = ROOT / "db" / "kitt_v1_2_seed_gates.sql"
DEFAULT_LOCAL_DB = ROOT / "outputs" / "runtime" / "kitt_v1_2.sqlite"

JOB_STATUSES = {
    "queued",
    "claimed",
    "running",
    "drafted",
    "audit_required",
    "audit_passed",
    "needs_erika",
    "approved",
    "needs_worker",
    "blocked",
    "returned",
    "done",
    "failed",
    "cancelled",
}

ACTION_TO_STATUS = {
    "claim": "claimed",
    "start": "running",
    "report": None,
    "drafted": "drafted",
    "audit-request": "audit_required",
    "audit-passed": "audit_passed",
    "needs-erika": "needs_erika",
    "needs-worker": "needs_worker",
    "return": "returned",
    "approve": "approved",
    "done": "done",
    "close": "done",
    "fail": "failed",
    "cancel": "cancelled",
    "cancelled": "cancelled",
}


class KittRuntimeError(RuntimeError):
    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_json_text(value: str | None, fallback: Any) -> Any:
    if not value:
        return fallback
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return fallback


def concrete_owner(owner: str | None) -> bool:
    return bool(owner) and not owner.startswith(("role:", "skill:", "helper:"))


def row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


def expand_job(row: sqlite3.Row | dict[str, Any] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    data = dict(row)
    data["ref"] = data.get("ref") or f"job.{data['id']}"
    data["required_gates"] = parse_json_text(data.pop("required_gates_json", "[]"), [])
    data["payload"] = parse_json_text(data.pop("payload_json", "{}"), {})
    data["audit_required"] = bool(data.get("audit_required"))
    data["approval_required"] = bool(data.get("approval_required"))
    return data


def expand_audit(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    data = dict(row)
    data["gates_applied"] = parse_json_text(data.pop("gates_applied_json", "[]"), [])
    data["evidence_data"] = parse_json_text(data.pop("evidence_json", "{}"), {})
    data["payload"] = parse_json_text(data.pop("payload_json", "{}"), {})
    data["ready_for_erika_review"] = bool(data.get("ready_for_erika_review"))
    return data


class LocalRuntimeClient:
    """Local SQLite implementation of the KITT V1.2 runtime API contract."""

    def __init__(self, db_path: str | Path = DEFAULT_LOCAL_DB, initialize: bool = True):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        if initialize:
            self.initialize()

    def initialize(self) -> None:
        self.conn.executescript(SCHEMA.read_text(encoding="utf-8"))
        self.conn.executescript(SEED_GATES.read_text(encoding="utf-8"))
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def health(self) -> dict[str, Any]:
        jobs = self.conn.execute("SELECT COUNT(*) AS count FROM service_jobs").fetchone()["count"]
        return {
            "status": "ok",
            "schema_version": "kitt-v1.2",
            "checked_at": now_iso(),
            "jobs": jobs,
            "backend": "local-sqlite",
            "db_path": str(self.db_path),
        }

    def create_job(
        self,
        *,
        service: str,
        title: str,
        owner: str | None = None,
        priority: str = "normal",
        source: str | None = None,
        due_at: str | None = None,
        risk_level: str = "green",
        audit_required: bool | None = None,
        required_gates: list[str] | None = None,
        campaign_id: str | None = None,
        spec_id: str | None = None,
        approval_required: bool | None = None,
        payload: dict[str, Any] | None = None,
        next_action: str | None = None,
        actor: str = "runtime-client",
    ) -> dict[str, Any]:
        if not service or not title:
            raise KittRuntimeError("missing_required_fields", "service and title are required.")
        if risk_level not in {"green", "yellow", "red"}:
            raise KittRuntimeError("invalid_risk_level", "risk_level must be green, yellow, or red.")
        audit = (risk_level != "green") if audit_required is None else bool(audit_required)
        approval = (risk_level == "red") if approval_required is None else bool(approval_required)
        created = now_iso()
        cursor = self.conn.execute(
            """
            INSERT INTO service_jobs
              (created_at, updated_at, service, title, status, owner, priority, source, due_at, risk_level,
               audit_required, required_gates_json, campaign_id, spec_id, approval_required, payload_json, next_action)
            VALUES (?, ?, ?, ?, 'queued', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                created,
                created,
                service,
                title,
                owner,
                priority,
                source,
                due_at,
                risk_level,
                1 if audit else 0,
                json.dumps(required_gates or []),
                campaign_id,
                spec_id,
                1 if approval else 0,
                json.dumps(payload or {}),
                next_action,
            ),
        )
        job_id = cursor.lastrowid
        self.conn.execute("UPDATE service_jobs SET ref = ? WHERE id = ?", (f"job.{job_id}", job_id))
        self._insert_event(
            service=service,
            event_type="created",
            title=f"Created job.{job_id}: {title}",
            details=next_action or "",
            related_job_id=job_id,
            actor=actor,
            payload={"risk_level": risk_level, "audit_required": audit, "approval_required": approval},
        )
        self.conn.commit()
        return self.get_job(job_id)

    def list_jobs(self, **filters: Any) -> dict[str, Any]:
        limit = min(int(filters.pop("limit", 100) or 100), 250)
        where: list[str] = []
        values: list[Any] = []
        for field in ["status", "owner", "campaign_id", "priority", "risk_level"]:
            value = filters.get(field)
            if value:
                where.append(f"{field} = ?")
                values.append(value)
        sql_where = f"WHERE {' AND '.join(where)}" if where else ""
        rows = self.conn.execute(
            f"""
            SELECT * FROM service_jobs
            {sql_where}
            ORDER BY CASE priority WHEN 'urgent' THEN 0 WHEN 'high' THEN 1 WHEN 'normal' THEN 2 WHEN 'low' THEN 3 ELSE 4 END,
                     id DESC
            LIMIT ?
            """,
            (*values, limit),
        ).fetchall()
        return {"jobs": [expand_job(row) for row in rows]}

    def get_job(self, job_id: int) -> dict[str, Any]:
        row = self.conn.execute("SELECT * FROM service_jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None:
            raise KittRuntimeError("job_not_found", f"No job exists with id {job_id}.", 404)
        events = self.conn.execute(
            "SELECT * FROM service_events WHERE related_job_id = ? ORDER BY id DESC LIMIT 50",
            (job_id,),
        ).fetchall()
        audits = self.conn.execute(
            "SELECT * FROM audit_packets WHERE job_id = ? ORDER BY id DESC LIMIT 25",
            (job_id,),
        ).fetchall()
        approvals = self.conn.execute(
            "SELECT * FROM approvals WHERE job_id = ? ORDER BY id DESC LIMIT 25",
            (job_id,),
        ).fetchall()
        return {
            "job": expand_job(row),
            "events": [row_to_dict(event) for event in events],
            "audits": [expand_audit(audit) for audit in audits],
            "approvals": [self._expand_payload_row(approval) for approval in approvals],
        }

    def action_job(
        self,
        job_id: int,
        *,
        action: str,
        actor: str,
        summary: str = "",
        evidence: str = "",
        next_action: str | None = None,
        next_owner: str | None = None,
        status: str | None = None,
        payload: dict[str, Any] | None = None,
        audit_exempted_by_erika: bool = False,
        approval_exempted_by_erika: bool = False,
    ) -> dict[str, Any]:
        if not action or not actor:
            raise KittRuntimeError("missing_required_fields", "action and actor are required.")
        if action not in ACTION_TO_STATUS:
            raise KittRuntimeError("invalid_action", f"Unsupported action: {action}.")
        job = self.conn.execute("SELECT * FROM service_jobs WHERE id = ?", (job_id,)).fetchone()
        if job is None:
            raise KittRuntimeError("job_not_found", f"No job exists with id {job_id}.", 404)
        if action == "claim" and concrete_owner(job["owner"]) and job["owner"] != actor:
            raise KittRuntimeError("job_owned_by_another_worker", f"job.{job_id} is owned by {job['owner']}.", 409)
        target_status = status or ACTION_TO_STATUS[action]
        if target_status and target_status not in JOB_STATUSES:
            raise KittRuntimeError("invalid_status", f"Unsupported status: {target_status}.")
        if action == "approve":
            self._insert_approval(job_id, requested_by=job["owner"] or "", approved_by=actor, details=summary or evidence)
        if target_status == "done":
            if self._needs_audit(job, job_id) and not audit_exempted_by_erika:
                raise KittRuntimeError(
                    "audit_required",
                    f"job.{job_id} cannot close until an audit packet passes or Erika explicitly exempts it.",
                    409,
                )
            if self._needs_approval(job, job_id) and not approval_exempted_by_erika:
                raise KittRuntimeError(
                    "approval_required",
                    f"job.{job_id} cannot close until Erika approval is recorded or explicitly exempted.",
                    409,
                )
        owner = actor if action == "claim" else next_owner or job["owner"]
        now = now_iso()
        if target_status:
            self.conn.execute(
                "UPDATE service_jobs SET status = ?, owner = ?, next_action = COALESCE(?, next_action), updated_at = ? WHERE id = ?",
                (target_status, owner, next_action, now, job_id),
            )
        elif next_action or next_owner:
            self.conn.execute(
                "UPDATE service_jobs SET owner = ?, next_action = COALESCE(?, next_action), updated_at = ? WHERE id = ?",
                (owner, next_action, now, job_id),
            )
        self._insert_event(
            service=job["service"],
            event_type=action,
            title=f"{actor} {action} job.{job_id}",
            details=summary or evidence,
            related_job_id=job_id,
            actor=actor,
            payload=payload or {},
        )
        self.conn.commit()
        return self.get_job(job_id)

    def worker_next(self, worker_id: str) -> dict[str, Any]:
        row = self.conn.execute(
            """
            SELECT * FROM service_jobs
            WHERE status = 'queued'
              AND (
                owner = ?
                OR owner LIKE 'role:%'
                OR owner LIKE 'skill:%'
                OR owner LIKE 'helper:%'
                OR owner IS NULL
              )
            ORDER BY CASE priority WHEN 'urgent' THEN 0 WHEN 'high' THEN 1 WHEN 'normal' THEN 2 WHEN 'low' THEN 3 ELSE 4 END,
                     id ASC
            LIMIT 1
            """,
            (worker_id,),
        ).fetchone()
        return {"worker": worker_id, "job": expand_job(row)}

    def worker_check_in(
        self,
        worker_id: str,
        *,
        status: str = "ready_idle",
        current_job_id: int | None = None,
        helper: str | None = None,
        machine: str | None = None,
        lane: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.conn.execute(
            """
            INSERT INTO worker_states (id, helper, machine, lane, status, current_job_id, last_seen_at, report_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
              helper = excluded.helper,
              machine = excluded.machine,
              lane = excluded.lane,
              status = excluded.status,
              current_job_id = excluded.current_job_id,
              last_seen_at = excluded.last_seen_at,
              report_json = excluded.report_json
            """,
            (worker_id, helper, machine, lane, status, current_job_id, now_iso(), json.dumps(payload or {})),
        )
        self.conn.commit()
        return {"worker": worker_id, "status": status}

    def create_audit(
        self,
        *,
        job_id: int,
        auditor: str,
        result: str,
        gates_applied: list[str] | None = None,
        evidence: str = "",
        evidence_json: dict[str, Any] | None = None,
        required_fix: str = "",
        ready_for_erika_review: bool = False,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if result not in {"pass", "needs_revision", "fail"}:
            raise KittRuntimeError("invalid_result", "result must be pass, needs_revision, or fail.")
        job = self.conn.execute("SELECT * FROM service_jobs WHERE id = ?", (job_id,)).fetchone()
        if job is None:
            raise KittRuntimeError("job_not_found", f"No job exists with id {job_id}.", 404)
        created = now_iso()
        cursor = self.conn.execute(
            """
            INSERT INTO audit_packets
              (created_at, updated_at, job_id, auditor, result, gates_applied_json, evidence, evidence_json,
               required_fix, ready_for_erika_review, payload_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                created,
                created,
                job_id,
                auditor,
                result,
                json.dumps(gates_applied or []),
                evidence,
                json.dumps(evidence_json or {}),
                required_fix,
                1 if ready_for_erika_review else 0,
                json.dumps(payload or {}),
            ),
        )
        status = "audit_passed" if result == "pass" else "needs_worker"
        self.conn.execute("UPDATE service_jobs SET status = ?, updated_at = ? WHERE id = ?", (status, now_iso(), job_id))
        self._insert_event(
            service=job["service"],
            event_type="audited",
            title=f"{auditor} audited job.{job_id}: {result}",
            details=required_fix or evidence,
            related_job_id=job_id,
            actor=auditor,
            payload={"audit_id": cursor.lastrowid, "gates_applied": gates_applied or []},
        )
        self.conn.commit()
        return {"audit_id": cursor.lastrowid, "job_id": job_id, "result": result}

    def audits_for_job(self, job_id: int) -> dict[str, Any]:
        rows = self.conn.execute("SELECT * FROM audit_packets WHERE job_id = ? ORDER BY id DESC", (job_id,)).fetchall()
        return {"audits": [expand_audit(row) for row in rows]}

    def _needs_audit(self, job: sqlite3.Row, job_id: int) -> bool:
        if not (bool(job["audit_required"]) or job["risk_level"] in {"yellow", "red"}):
            return False
        row = self.conn.execute(
            "SELECT id FROM audit_packets WHERE job_id = ? AND result = 'pass' ORDER BY id DESC LIMIT 1",
            (job_id,),
        ).fetchone()
        return row is None

    def _needs_approval(self, job: sqlite3.Row, job_id: int) -> bool:
        if not (bool(job["approval_required"]) or job["risk_level"] == "red"):
            return False
        row = self.conn.execute(
            "SELECT id FROM approvals WHERE job_id = ? AND status = 'approved' ORDER BY id DESC LIMIT 1",
            (job_id,),
        ).fetchone()
        return row is None

    def _insert_approval(self, job_id: int, *, requested_by: str, approved_by: str, details: str = "") -> None:
        created = now_iso()
        self.conn.execute(
            """
            INSERT INTO approvals
              (created_at, updated_at, job_id, approval_type, requested_by, approved_by, status, details, payload_json)
            VALUES (?, ?, ?, 'live_mutation', ?, ?, 'approved', ?, '{}')
            """,
            (created, created, job_id, requested_by, approved_by, details),
        )

    def _insert_event(
        self,
        *,
        service: str,
        event_type: str,
        title: str,
        details: str = "",
        related_job_id: int | None = None,
        actor: str = "",
        payload: dict[str, Any] | None = None,
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO service_events
              (created_at, service, event_type, title, details, related_job_id, actor, payload_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (now_iso(), service, event_type, title, details, related_job_id, actor, json.dumps(payload or {})),
        )

    @staticmethod
    def _expand_payload_row(row: sqlite3.Row) -> dict[str, Any]:
        data = dict(row)
        data["payload"] = parse_json_text(data.pop("payload_json", "{}"), {})
        return data


@dataclass
class HttpRuntimeClient:
    """HTTP implementation for the Cloudflare Worker runtime API."""

    base_url: str
    read_token: str | None = None
    worker_token: str | None = None
    timeout: int = 20

    def _request(self, method: str, path: str, payload: dict[str, Any] | None = None, write: bool = False) -> dict[str, Any]:
        url = self.base_url.rstrip("/") + path
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        headers = {
            "accept": "application/json",
            "user-agent": "KITT-Runtime-Client/1.0",
        }
        if data is not None:
            headers["content-type"] = "application/json"
        token = self.worker_token if write else self.read_token or self.worker_token
        if token:
            headers["authorization"] = f"Bearer {token}"
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            try:
                body = json.loads(error.read().decode("utf-8"))
                detail = body.get("error") or {}
                raise KittRuntimeError(detail.get("code", "http_error"), detail.get("message", str(error)), error.code) from error
            except json.JSONDecodeError as decode_error:
                raise KittRuntimeError("http_error", str(error), error.code) from decode_error
        if not body.get("ok"):
            detail = body.get("error") or {}
            raise KittRuntimeError(detail.get("code", "runtime_error"), detail.get("message", "Runtime request failed."))
        return body["data"]

    def health(self) -> dict[str, Any]:
        return self._request("GET", "/health")

    def create_job(self, **payload: Any) -> dict[str, Any]:
        return self._request("POST", "/jobs", payload, write=True)

    def list_jobs(self, **filters: Any) -> dict[str, Any]:
        query = urllib.parse.urlencode({key: value for key, value in filters.items() if value is not None})
        return self._request("GET", f"/jobs?{query}" if query else "/jobs")

    def get_job(self, job_id: int) -> dict[str, Any]:
        return self._request("GET", f"/jobs/{job_id}")

    def action_job(self, job_id: int, **payload: Any) -> dict[str, Any]:
        return self._request("POST", f"/jobs/{job_id}/action", payload, write=True)

    def worker_next(self, worker_id: str) -> dict[str, Any]:
        return self._request("GET", f"/workers/{urllib.parse.quote(worker_id)}/next")

    def worker_check_in(self, worker_id: str, **payload: Any) -> dict[str, Any]:
        return self._request("POST", f"/workers/{urllib.parse.quote(worker_id)}/check-in", payload, write=True)

    def create_audit(self, **payload: Any) -> dict[str, Any]:
        return self._request("POST", "/audits", payload, write=True)

    def audits_for_job(self, job_id: int) -> dict[str, Any]:
        return self._request("GET", f"/audits/job/{job_id}")


def client_from_env() -> LocalRuntimeClient | HttpRuntimeClient:
    runtime_url = os.environ.get("KITT_RUNTIME_URL", "").strip()
    if runtime_url:
        return HttpRuntimeClient(
            base_url=runtime_url,
            read_token=os.environ.get("KITT_READ_TOKEN"),
            worker_token=os.environ.get("KITT_WORKER_TOKEN"),
        )
    if os.environ.get("KITT_ALLOW_LOCAL_RUNTIME_FALLBACK", "").strip().lower() not in {"1", "true", "yes"}:
        raise KittRuntimeError(
            "local_runtime_disabled",
            "Local KITT runtime fallback is disabled. Set KITT_RUNTIME_URL for the shared Cloudflare runtime.",
            500,
        )
    return LocalRuntimeClient(os.environ.get("KITT_RUNTIME_LOCAL_DB", str(DEFAULT_LOCAL_DB)))


__all__ = ["HttpRuntimeClient", "KittRuntimeError", "LocalRuntimeClient", "client_from_env"]
