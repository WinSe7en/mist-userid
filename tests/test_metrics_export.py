"""Metrics export: worker endpoint and multi-process API aggregation (issue #1)."""

import os
import socket
import subprocess
import sys
import textwrap
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _env(**extra) -> dict:
    env = {k: v for k, v in os.environ.items() if k != "PROMETHEUS_MULTIPROC_DIR"}
    env.update({"PA_TARGETS": "https://pa-test.example.com", "PA_API_KEY": "k",
                "MIST_WEBHOOK_SECRET": "s"}, **extra)
    return env


def test_worker_metrics_server_disabled_when_port_zero():
    from app.worker import start_metrics_server
    assert start_metrics_server(0) is None


def test_worker_metrics_server_serves_worker_metrics_only():
    from app.worker import start_metrics_server
    from app.worker_metrics import PA_REQUESTS

    PA_REQUESTS.labels(target="https://pa-test.example.com", status="success").inc()
    port = _free_port()
    server = start_metrics_server(port)
    try:
        body = urllib.request.urlopen(f"http://127.0.0.1:{port}/metrics", timeout=5).read().decode()
    finally:
        server.shutdown()
    assert 'mist_userid_pa_requests_total{status="success",target="https://pa-test.example.com"}' in body
    assert "mist_userid_dlq_events_total" in body
    # API-side metrics belong to the API endpoint, not the worker's
    assert "mist_userid_events_received_total" not in body


def test_worker_metrics_server_binds_localhost_only():
    from app.worker import start_metrics_server
    port = _free_port()
    server = start_metrics_server(port)
    try:
        assert server.server_address[0] == "127.0.0.1"
    finally:
        server.shutdown()


def test_api_does_not_import_worker_metrics():
    """Importing the API app must not create worker metrics: in multiprocess mode
    they would be exported by the API as permanent zeros."""
    code = "import sys, app.main; print('app.worker_metrics' in sys.modules)"
    out = subprocess.run(
        [sys.executable, "-c", code], cwd=REPO, capture_output=True, text=True,
        env=_env(),
    )
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "False"


def test_api_metrics_aggregate_across_processes(tmp_path):
    """With PROMETHEUS_MULTIPROC_DIR set, /metrics sums counters from every
    uvicorn process instead of reporting whichever process answered."""
    env = _env(PROMETHEUS_MULTIPROC_DIR=str(tmp_path))
    bump = "from app.metrics import EVENTS_QUEUED; EVENTS_QUEUED.inc(5)"
    for _ in range(3):  # three separate processes, like uvicorn workers
        r = subprocess.run([sys.executable, "-c", bump], cwd=REPO, env=env,
                           capture_output=True, text=True)
        assert r.returncode == 0, r.stderr

    render = textwrap.dedent("""
        from app.main import render_metrics
        print(render_metrics().decode())
    """)
    r = subprocess.run([sys.executable, "-c", render], cwd=REPO, env=env,
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert "mist_userid_events_queued_total 15.0" in r.stdout
