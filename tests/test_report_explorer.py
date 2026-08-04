import http.client
import json
import shutil
import threading
from contextlib import contextmanager
from pathlib import Path

from graphcheck.reporting import explorer as explorer_module
from graphcheck.reporting.explorer import ReportExplorerServer

FIXTURES = Path(__file__).parent / "contracts" / "fixtures"


def _write_run(
    runs_dir: Path,
    run_id: str,
    finished_at: str,
    *,
    fixture: str = "complete",
    directory: str | None = None,
) -> Path:
    payload = json.loads((FIXTURES / f"results.{fixture}.json").read_text(encoding="utf-8"))
    payload["run"]["id"] = run_id
    payload["run"]["started_at"] = finished_at
    payload["run"]["finished_at"] = finished_at
    run_dir = runs_dir / (directory or run_id)
    run_dir.mkdir(parents=True)
    (run_dir / "results.json").write_text(json.dumps(payload), encoding="utf-8")
    (run_dir / "report.html").write_text(f"report for {run_id}", encoding="utf-8")
    return run_dir


@contextmanager
def _server(runs_dir: Path, token: str = "test-secret"):
    server = ReportExplorerServer(runs_dir, 0, token)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server, token
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _request(
    server: ReportExplorerServer,
    method: str,
    path: str,
    *,
    body: object | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, dict[str, str], bytes]:
    connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
    encoded = None if body is None else json.dumps(body).encode("utf-8")
    connection.request(method, path, body=encoded, headers=headers or {})
    response = connection.getresponse()
    content = response.read()
    result = response.status, dict(response.getheaders()), content
    connection.close()
    return result


def _authorized_headers(server: ReportExplorerServer, token: str, cookie: str) -> dict[str, str]:
    return {
        "Content-Type": "application/json",
        "Cookie": cookie,
        "Origin": f"http://127.0.0.1:{server.server_port}",
        "X-GraphCheck-Token": token,
    }


def test_report_explorer_lists_switches_compares_and_deletes_only_reports(tmp_path):
    runs_dir = tmp_path / "reports" / "runs"
    old = _write_run(runs_dir, "run-old", "2026-07-01T10:00:00Z", fixture="partial")
    new = _write_run(runs_dir, "run-new", "2026-07-02T10:00:00Z")
    shutil.copytree(new, runs_dir / "latest")
    notes = runs_dir / "manual-notes"
    notes.mkdir()
    (notes / "keep.txt").write_text("keep", encoding="utf-8")
    private = tmp_path / "private.txt"
    private.write_text("not a report", encoding="utf-8")

    with _server(runs_dir) as (server, token):
        status, headers, _ = _request(
            server,
            "GET",
            f"/report?id=run-new&token={token}",
        )
        cookie = headers["Set-Cookie"].split(";", 1)[0]
        assert status == 303
        assert headers["Location"] == "/report?id=run-new"

        status, headers, document = _request(
            server,
            "GET",
            "/report?id=run-new",
            headers={"Cookie": cookie},
        )
        assert status == 200
        assert b'content="test-secret"' in document
        assert document.index(b'id="report-explorer"') < document.index(b"Graph Health Overview")
        assert headers["X-Frame-Options"] == "DENY"

        status, _, payload = _request(
            server,
            "GET",
            "/api/reports",
            headers={
                "Cookie": cookie,
                "X-GraphCheck-Token": token,
            },
        )
        reports = json.loads(payload)["reports"]
        assert status == 200
        assert [report["id"] for report in reports] == ["run-new", "run-old"]
        assert reports[0]["latest"] is True
        assert reports[1]["latest"] is False

        status, headers, payload = _request(
            server,
            "GET",
            "/api/report?id=run-old",
            headers={
                "Cookie": cookie,
                "X-GraphCheck-Token": token,
            },
        )
        fragment_report = json.loads(payload)["report"]
        fragments = fragment_report["fragments"]
        assert status == 200
        assert headers["Content-Type"] == "application/json; charset=utf-8"
        assert fragment_report["id"] == "run-old"
        assert fragment_report["href"] == "/report?id=run-old"
        assert fragment_report["title"] == "GraphCheck Dashboard - run-old"
        assert set(fragments) == {"run_title", "overview", "checks"}
        assert "run-old" not in fragments["run_title"]
        assert "<strong>Partial Run.</strong>" in fragments["run_title"]
        assert '<section id="report-overview"' in fragments["overview"]
        assert '<section id="checks-panel"' in fragments["checks"]
        assert 'id="report-explorer"' not in "".join(fragments.values())

        status, _, payload = _request(
            server,
            "POST",
            "/api/compare",
            body={"ids": ["run-old", "run-new"]},
            headers=_authorized_headers(server, token, cookie),
        )
        assert status == 200
        assert "Comparing run-old -> run-new" in json.loads(payload)["comparison"]

        status, _, _ = _request(
            server,
            "GET",
            "/../../private.txt",
            headers={"Cookie": cookie},
        )
        assert status == 404
        assert private.read_text(encoding="utf-8") == "not a report"

        status, _, payload = _request(
            server,
            "POST",
            "/api/delete",
            body={"ids": ["run-new"], "current": "run-new"},
            headers=_authorized_headers(server, token, cookie),
        )
        deletion = json.loads(payload)
        assert status == 200
        assert deletion["deleted"] == ["run-new"]
        assert deletion["redirect"] == "/report?id=run-old"
        assert deletion["replacement"]["id"] == "run-old"
        assert deletion["replacement"]["href"] == "/report?id=run-old"
        assert "<strong>Partial Run.</strong>" in deletion["replacement"]["fragments"]["run_title"]

    assert not new.exists()
    assert old.exists()
    assert json.loads((runs_dir / "latest" / "results.json").read_text())["run"]["id"] == "run-old"
    assert (notes / "keep.txt").read_text(encoding="utf-8") == "keep"
    assert private.read_text(encoding="utf-8") == "not a report"


def test_report_explorer_rejects_unauthenticated_and_cross_origin_actions(tmp_path):
    runs_dir = tmp_path / "runs"
    _write_run(runs_dir, "run-one", "2026-07-01T10:00:00Z")

    with _server(runs_dir) as (server, token):
        status, _, _ = _request(server, "GET", "/api/reports")
        assert status == 403
        status, _, _ = _request(server, "GET", "/api/report?id=run-one")
        assert status == 403

        _, headers, _ = _request(server, "GET", f"/report?id=run-one&token={token}")
        cookie = headers["Set-Cookie"].split(";", 1)[0]
        authorized_get_headers = {
            "Cookie": cookie,
            "X-GraphCheck-Token": token,
        }
        status, _, payload = _request(
            server,
            "GET",
            "/api/report?id=missing",
            headers=authorized_get_headers,
        )
        assert status == 404
        assert "missing" in json.loads(payload)["error"]
        status, _, payload = _request(
            server,
            "GET",
            "/api/report",
            headers=authorized_get_headers,
        )
        assert status == 400
        assert json.loads(payload)["error"] == "Select one report ID."

        request_headers = _authorized_headers(server, token, cookie)
        request_headers["Origin"] = "https://example.com"
        status, _, _ = _request(
            server,
            "POST",
            "/api/delete",
            body={"ids": ["run-one"], "current": "run-one"},
            headers=request_headers,
        )
        assert status == 403

    assert (runs_dir / "run-one").exists()


def test_report_explorer_launch_serves_in_the_invoking_process(tmp_path, monkeypatch):
    runs_dir = tmp_path / "runs"
    _write_run(runs_dir, "run-one", "2026-07-01T10:00:00Z")
    invoking_thread = threading.get_ident()
    served_threads = []
    opened_urls = []
    announced_urls = []
    monkeypatch.setattr(
        explorer_module,
        "_serve_server",
        lambda server: served_threads.append(threading.get_ident()),
    )

    clean_url = explorer_module.launch_report_explorer(
        runs_dir,
        "run-one",
        opener=lambda url: opened_urls.append(url) or True,
        on_open=announced_urls.append,
    )

    assert served_threads == [invoking_thread]
    assert len(opened_urls) == 1
    assert "&token=" in opened_urls[0]
    assert announced_urls == [clean_url]
    assert "&token=" not in clean_url
