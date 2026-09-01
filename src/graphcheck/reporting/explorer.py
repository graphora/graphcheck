from __future__ import annotations

import argparse
import hmac
import http.cookies
import json
import secrets
import time
import urllib.parse
from collections.abc import Callable
from contextlib import suppress
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from graphcheck.reporting.history import (
    ReportHistoryError,
    ReportRun,
    delete_report_runs,
    discover_report_runs,
    find_report_run,
    format_report_comparison,
)
from graphcheck.reporting.html import (
    render_validated_html_report,
    render_validated_html_report_fragments,
)

_COOKIE_NAME = "graphcheck_report_explorer"
_MAX_REQUEST_BYTES = 64 * 1024
_IDLE_SECONDS = 300.0


class ReportExplorerServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, runs_dir: Path, port: int, token: str) -> None:
        self.runs_dir = runs_dir.resolve()
        self.token = token
        self.last_activity = time.monotonic()
        super().__init__(("127.0.0.1", port), ReportExplorerHandler)


class ReportExplorerHandler(BaseHTTPRequestHandler):
    server: ReportExplorerServer

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        self.server.last_activity = time.monotonic()
        parsed = urllib.parse.urlsplit(self.path)
        if not self._valid_host():
            self._json_error(403, "Invalid report explorer host.")
            return
        if parsed.path == "/health":
            if not self._valid_token(self.headers.get("X-GraphCheck-Token")):
                self._json_error(403, "Report explorer authorization failed.")
                return
            self._json(200, {"status": "ok"})
            return
        if not self._authenticated():
            query = urllib.parse.parse_qs(parsed.query)
            if self._valid_token(query.get("token", [""])[0]):
                query.pop("token", None)
                clean_query = urllib.parse.urlencode(query, doseq=True)
                self.send_response(303)
                location = f"{parsed.path}{'?' + clean_query if clean_query else ''}"
                self.send_header("Location", location)
                self.send_header(
                    "Set-Cookie",
                    f"{_COOKIE_NAME}={self.server.token}; HttpOnly; SameSite=Strict; Path=/",
                )
                self._security_headers()
                self.end_headers()
                return
            self._json_error(403, "Report explorer authorization failed.")
            return
        if parsed.path.startswith("/api/"):
            if not self._api_authorized():
                self._json_error(403, "Report explorer authorization failed.")
            elif parsed.path == "/api/reports":
                self._json(200, {"reports": _report_payload(self._records())})
            elif parsed.path == "/api/report":
                try:
                    self._report(parsed)
                except ReportHistoryError as exc:
                    self._json_error(404, str(exc))
                except ValueError as exc:
                    self._json_error(400, str(exc))
            elif parsed.path == "/api/ping":
                self._json(200, {"status": "ok"})
            else:
                self._json_error(404, "Unknown report explorer route.")
            return
        if parsed.path == "/":
            records = self._records()
            location = "/empty" if not records else _report_href(records[0].id)
            self.send_response(303)
            self.send_header("Location", location)
            self._security_headers()
            self.end_headers()
            return
        if parsed.path == "/empty":
            self._html(200, _empty_page())
            return
        if parsed.path != "/report":
            self._json_error(404, "Unknown report explorer route.")
            return
        try:
            records = self._records()
            query = urllib.parse.parse_qs(parsed.query)
            run_id = query.get("id", [records[0].id if records else ""])[0]
            record = find_report_run(records, run_id)
            self._html(
                200,
                render_validated_html_report(record.results, explorer_token=self.server.token),
            )
        except ReportHistoryError as exc:
            self._json_error(404, str(exc))

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        self.server.last_activity = time.monotonic()
        parsed = urllib.parse.urlsplit(self.path)
        if not self._valid_host() or not self._api_authorized() or not self._same_origin():
            self._discard_request_body()
            self._json_error(403, "Report explorer authorization failed.")
            return
        try:
            payload = self._request_json()
            if parsed.path == "/api/compare":
                self._compare(payload)
            elif parsed.path == "/api/delete":
                self._delete(payload)
            else:
                self._json_error(404, "Unknown report explorer route.")
        except (ReportHistoryError, TypeError, ValueError) as exc:
            self._json_error(400, str(exc))

    def log_message(self, format: str, *args: object) -> None:
        return

    def _records(self) -> list[ReportRun]:
        return discover_report_runs(self.server.runs_dir)

    def _compare(self, payload: dict[str, Any]) -> None:
        ids = _selected_ids(payload, exactly=2)
        records = self._records()
        first, second = (find_report_run(records, run_id) for run_id in ids)
        self._json(200, {"comparison": format_report_comparison(first, second)})

    def _report(self, parsed: urllib.parse.SplitResult) -> None:
        ids = urllib.parse.parse_qs(parsed.query).get("id", [])
        if len(ids) != 1 or not ids[0]:
            raise ValueError("Select one report ID.")
        record = find_report_run(self._records(), ids[0])
        self._json(200, {"report": _report_fragment_payload(record)})

    def _delete(self, payload: dict[str, Any]) -> None:
        ids = _selected_ids(payload)
        current = payload.get("current")
        if current is not None and not isinstance(current, str):
            raise ValueError("The current report ID must be a string.")
        removed = delete_report_runs(self.server.runs_dir, ids)
        records = self._records()
        current_removed = current in {record.id for record in removed}
        redirect = (
            ("/empty" if not records else _report_href(records[0].id)) if current_removed else None
        )
        self._json(
            200,
            {
                "deleted": [record.id for record in removed],
                "redirect": redirect,
                "replacement": (
                    _report_fragment_payload(records[0]) if current_removed and records else None
                ),
                "reports": _report_payload(records),
            },
        )

    def _request_json(self) -> dict[str, Any]:
        if self.headers.get_content_type() != "application/json":
            raise ValueError("Report explorer requests must use application/json.")
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise ValueError("Invalid report explorer request length.") from exc
        if length < 1 or length > _MAX_REQUEST_BYTES:
            raise ValueError("Invalid report explorer request length.")
        payload = json.loads(self.rfile.read(length))
        if not isinstance(payload, dict):
            raise ValueError("Report explorer request must be a JSON object.")
        return payload

    def _discard_request_body(self) -> None:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            return
        if 0 < length <= _MAX_REQUEST_BYTES:
            self.rfile.read(length)

    def _authenticated(self) -> bool:
        cookie = http.cookies.SimpleCookie(self.headers.get("Cookie", ""))
        morsel = cookie.get(_COOKIE_NAME)
        return morsel is not None and self._valid_token(morsel.value)

    def _api_authorized(self) -> bool:
        return self._authenticated() and self._valid_token(self.headers.get("X-GraphCheck-Token"))

    def _same_origin(self) -> bool:
        return self.headers.get("Origin") == self._origin

    def _valid_host(self) -> bool:
        return self.headers.get("Host") == f"127.0.0.1:{self.server.server_port}"

    def _valid_token(self, candidate: str | None) -> bool:
        return candidate is not None and hmac.compare_digest(candidate, self.server.token)

    @property
    def _origin(self) -> str:
        return f"http://127.0.0.1:{self.server.server_port}"

    def _json_error(self, status: int, message: str) -> None:
        self._json(status, {"error": message})

    def _json(self, status: int, payload: object) -> None:
        self._send(status, json.dumps(payload).encode("utf-8"), "application/json; charset=utf-8")

    def _html(self, status: int, document: str) -> None:
        self._send(status, document.encode("utf-8"), "text/html; charset=utf-8")

    def _send(self, status: int, content: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self._security_headers()
        self.end_headers()
        self.wfile.write(content)

    def _security_headers(self) -> None:
        self.send_header("Cache-Control", "no-store")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'none'; style-src 'unsafe-inline'; "
            "script-src 'unsafe-inline'; connect-src 'self'",
        )
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")


def launch_report_explorer(
    runs_dir: Path,
    run_id: str | None = None,
    *,
    opener: Callable[[str], bool] | None = None,
    on_open: Callable[[str], None] | None = None,
) -> str:
    records = discover_report_runs(runs_dir)
    record = find_report_run(records, run_id) if run_id is not None else _latest(records)
    token = secrets.token_urlsafe(32)
    server = ReportExplorerServer(runs_dir, 0, token)
    clean_url = f"http://127.0.0.1:{server.server_port}{_report_href(record.id)}"
    initial_url = f"{clean_url}&token={urllib.parse.quote(token, safe='')}"
    try:
        if opener is None:
            import webbrowser

            opener = webbrowser.open
        if not opener(initial_url):
            raise ReportHistoryError("Could not open the report explorer in the default browser.")
        if on_open is not None:
            on_open(clean_url)
        with suppress(KeyboardInterrupt):
            _serve_server(server)
    finally:
        server.server_close()
    return clean_url


def serve_report_explorer(runs_dir: Path, port: int, token: str) -> None:
    server = ReportExplorerServer(runs_dir, port, token)
    try:
        _serve_server(server)
    finally:
        server.server_close()


def _serve_server(server: ReportExplorerServer) -> None:
    server.timeout = 1.0
    while time.monotonic() - server.last_activity < _IDLE_SECONDS:
        server.handle_request()


def _selected_ids(payload: dict[str, Any], *, exactly: int | None = None) -> list[str]:
    ids = payload.get("ids")
    if (
        not isinstance(ids, list)
        or not ids
        or len(ids) > 100
        or any(not isinstance(run_id, str) or not run_id for run_id in ids)
        or len(set(ids)) != len(ids)
    ):
        raise ValueError("Select valid, distinct report IDs.")
    if exactly is not None and len(ids) != exactly:
        raise ValueError(f"Select exactly {exactly} reports to compare.")
    return ids


def _report_payload(records: list[ReportRun]) -> list[dict[str, object]]:
    return [
        {
            "id": record.id,
            "finished_at": record.summary.finished_at,
            "coverage_status": record.summary.coverage_status.value,
            "suite_scores": [
                {"id": suite_id, "score": score} for suite_id, score in record.summary.suite_scores
            ],
            "href": _report_href(record.id),
            "latest": index == 0,
        }
        for index, record in enumerate(records)
    ]


def _report_fragment_payload(record: ReportRun) -> dict[str, object]:
    return {
        "id": record.id,
        "href": _report_href(record.id),
        "title": f"GraphCheck Dashboard - {record.id}",
        "fragments": render_validated_html_report_fragments(record.results),
    }


def _report_href(run_id: str) -> str:
    return f"/report?id={urllib.parse.quote(run_id, safe='')}"


def _latest(records: list[ReportRun]) -> ReportRun:
    if not records:
        raise ReportHistoryError(
            "No results.json found in report history. Run `graphcheck run` first."
        )
    return records[0]


def _empty_page() -> str:
    return """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>GraphCheck Report Explorer</title><style>
body{margin:0;display:grid;min-height:100vh;place-items:center;background:#f8fafc;
color:#0f172a;font:15px system-ui,sans-serif}
main{max-width:520px;padding:36px;border:1px solid #e2e8f0;border-radius:10px;
background:#fff;box-shadow:0 8px 30px rgba(15,23,42,.08)}
h1{margin:0 0 8px;font-size:22px}p{margin:0;color:#64748b}
code{padding:2px 5px;border-radius:4px;background:#f1f5f9}
</style></head><body><main><h1>No reports remain</h1>
<p>Run <code>graphcheck run</code> to create a new report.</p></main></body></html>"""


def _main() -> None:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--runs-dir", required=True, type=Path)
    parser.add_argument("--port", required=True, type=int)
    parser.add_argument("--token", required=True)
    args = parser.parse_args()
    with suppress(KeyboardInterrupt):
        serve_report_explorer(args.runs_dir, args.port, args.token)


if __name__ == "__main__":
    _main()
