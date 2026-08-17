#!/usr/bin/env python3
"""KITT UI V1 local operator app.

Small localhost-only server that renders a calm job-board UI and proxies
runtime API calls so browser JavaScript never receives runtime tokens.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse


ROOT = Path(__file__).resolve().parent
APP_ROOT = ROOT
STATIC_ROOT = APP_ROOT / "static"
PACKETS_ROOT = APP_ROOT / "packets"
LOG_DIR = ROOT / "outputs" / "kitt-ui"
LOG_FILE = LOG_DIR / "kitt-ui.log"
DEFAULT_ENV_FILE = Path(
    os.environ.get(
        "KITT_UI_ENV_FILE",
        str(ROOT.parent / "chc-ai-operating-system" / "cloudflare" / "kitt-runtime-worker" / ".dev.vars"),
    )
)
DEFAULT_RUNTIME_URL = "https://kitt-runtime.erika-6a7.workers.dev"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8776

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from kitt_runtime_client import KittRuntimeError, client_from_env  # noqa: E402


def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def configure_runtime() -> None:
    load_env_file(DEFAULT_ENV_FILE)
    if not os.environ.get("KITT_USE_LOCAL_RUNTIME"):
        os.environ.setdefault("KITT_RUNTIME_URL", DEFAULT_RUNTIME_URL)


def configure_logging() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=LOG_FILE,
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )


def sanitize_text(value: Any, *, limit: int = 2000) -> str:
    text = "" if value is None else str(value)
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text)
    return text.strip()[:limit]


def normalize_priority(value: str) -> str:
    return value if value in {"low", "normal", "high", "urgent"} else "normal"


def normalize_risk(value: str) -> str:
    return value if value in {"green", "yellow", "red"} else "yellow"


def job_defaults(risk_level: str) -> dict[str, Any]:
    risk = normalize_risk(risk_level)
    return {
        "risk_level": risk,
        "audit_required": risk != "green",
        "approval_required": risk == "red",
        "payload": {
            "scope_status": "unscoped",
            "implementation_allowed": False,
            "source": "kitt-ui-v1",
        },
    }


def build_job_payload(form: dict[str, Any]) -> dict[str, Any]:
    title = sanitize_text(form.get("title"), limit=180)
    service = sanitize_text(form.get("service") or "general", limit=80)
    if not title:
        raise ValueError("Title is required.")
    if not service:
        raise ValueError("Service or lane is required.")

    risk_level = normalize_risk(sanitize_text(form.get("risk_level"), limit=20))
    defaults = job_defaults(risk_level)
    instructions = sanitize_text(form.get("instructions"), limit=4000)
    definition_of_done = sanitize_text(form.get("definition_of_done"), limit=2000)
    if instructions:
        defaults["payload"]["instructions"] = instructions
    if definition_of_done:
        defaults["payload"]["definition_of_done"] = definition_of_done

    return {
        "service": service,
        "title": title,
        "owner": sanitize_text(form.get("owner"), limit=120),
        "priority": normalize_priority(sanitize_text(form.get("priority"), limit=20)),
        "risk_level": defaults["risk_level"],
        "audit_required": defaults["audit_required"],
        "approval_required": defaults["approval_required"],
        "campaign_id": sanitize_text(form.get("campaign_id"), limit=120),
        "next_action": sanitize_text(form.get("next_action"), limit=500),
        "source": "kitt-ui-v1",
        "payload": defaults["payload"],
        "actor": "kitt-ui-v1",
    }


def public_job(job: dict[str, Any]) -> dict[str, Any]:
    safe = dict(job)
    if isinstance(safe.get("payload_json"), str):
        safe.pop("payload_json", None)
    return safe


def load_packets() -> list[dict[str, Any]]:
    packets: list[dict[str, Any]] = []
    for mode in ("active", "samples"):
        folder = PACKETS_ROOT / mode
        if not folder.exists():
            continue
        for path in sorted(folder.glob("*.json")):
            try:
                packet = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as error:
                logging.warning("skipping malformed packet %s: %s", path, error)
                continue
            if not isinstance(packet, dict):
                logging.warning("skipping non-object packet %s", path)
                continue
            packet.setdefault("packet_id", path.stem)
            packet["_source_file"] = str(path.relative_to(APP_ROOT))
            packet["_mode"] = "sample" if mode == "samples" else "active"
            packets.append(packet)
    return packets


class KittUIHandler(BaseHTTPRequestHandler):
    server_version = "KITTUI/1.0"

    def log_message(self, fmt: str, *args: Any) -> None:
        logging.info("%s - %s", self.address_string(), fmt % args)

    @property
    def runtime(self):
        return self.server.runtime_client  # type: ignore[attr-defined]

    def _send_json(self, payload: dict[str, Any], status: int = 200) -> None:
        body = json.dumps(payload, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("content-type", "application/json; charset=utf-8")
        self.send_header("cache-control", "no-store")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path: Path, content_type: str) -> None:
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("content-type", content_type)
        self.send_header("cache-control", "no-store")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _api_error(self, code: str, message: str, status: int = 500) -> None:
        self._send_json({"ok": False, "data": None, "error": {"code": code, "message": message}}, status)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        try:
            if path in {"/", "/index.html"}:
                return self._send_file(STATIC_ROOT / "index.html", "text/html; charset=utf-8")
            if path == "/static/app.css":
                return self._send_file(STATIC_ROOT / "app.css", "text/css; charset=utf-8")
            if path == "/static/app.js":
                return self._send_file(STATIC_ROOT / "app.js", "application/javascript; charset=utf-8")
            if path == "/api/health":
                data = self.runtime.health()
                return self._send_json({"ok": True, "data": data, "error": None})
            if path == "/api/packets":
                return self._send_json({"ok": True, "data": {"packets": load_packets()}, "error": None})
            if path == "/api/jobs":
                query = parse_qs(parsed.query)
                data = self.runtime.list_jobs(
                    status=(query.get("status") or [""])[0],
                    owner=(query.get("owner") or [""])[0],
                    limit=int((query.get("limit") or ["50"])[0]),
                )
                jobs = [public_job(job) for job in data.get("jobs", [])]
                return self._send_json({"ok": True, "data": {"jobs": jobs}, "error": None})
            match = re.fullmatch(r"/api/jobs/(\d+)", path)
            if match:
                data = self.runtime.get_job(int(match.group(1)))
                return self._send_json({"ok": True, "data": data, "error": None})
            self._api_error("not_found", "That KITT UI route does not exist.", HTTPStatus.NOT_FOUND)
        except KittRuntimeError as error:
            logging.warning("runtime error: %s %s", error.code, error.message)
            self._api_error(error.code, error.message, error.status or 500)
        except Exception as error:  # noqa: BLE001
            logging.exception("unexpected GET failure")
            self._api_error("server_error", str(error), 500)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path != "/api/jobs":
            return self._api_error("not_found", "That KITT UI route does not exist.", HTTPStatus.NOT_FOUND)
        try:
            length = int(self.headers.get("content-length", "0"))
            raw = self.rfile.read(length).decode("utf-8") if length else "{}"
            form = json.loads(raw)
            if not isinstance(form, dict):
                raise ValueError("Request body must be a JSON object.")
            payload = build_job_payload(form)
            data = self.runtime.create_job(**payload)
            logging.info("created job via kitt-ui-v1: %s", data.get("ref") or data.get("job", {}).get("ref"))
            self._send_json({"ok": True, "data": data, "error": None}, HTTPStatus.CREATED)
        except ValueError as error:
            self._api_error("invalid_input", str(error), HTTPStatus.BAD_REQUEST)
        except KittRuntimeError as error:
            logging.warning("runtime create job failed: %s %s", error.code, error.message)
            self._api_error(error.code, error.message, error.status or 500)
        except Exception as error:  # noqa: BLE001
            logging.exception("unexpected POST failure")
            self._api_error("server_error", str(error), 500)


def run(host: str, port: int) -> None:
    if host != DEFAULT_HOST:
        raise SystemExit("KITT UI V1 is localhost-only. Use host 127.0.0.1.")
    configure_runtime()
    configure_logging()
    server = ThreadingHTTPServer((host, port), KittUIHandler)
    server.runtime_client = client_from_env()  # type: ignore[attr-defined]
    logging.info("KITT UI V1 starting on http://%s:%s", host, port)
    print(f"KITT UI V1: http://{host}:{port}")
    print(f"Logs: {LOG_FILE}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nKITT UI V1 stopped.")
        logging.info("KITT UI V1 stopped")
    finally:
        server.server_close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the KITT UI V1 local app.")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=int(os.environ.get("KITT_UI_PORT", DEFAULT_PORT)))
    args = parser.parse_args()
    run(args.host, args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
