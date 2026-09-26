"""SPEC.md acceptance scenario 1 against the REAL running API + DB.

1. Register two agents. One sends a task; the other claims and completes it;
   the sender reads the result.

Requires: relay running at RELAY_BASE_URL (local `uv run uvicorn main:app`
or container `docker run -p 8000:8000 agent-relay:local`).
Run: `uv run pytest test_scenario1_live.py -q`
This hits http://127.0.0.1:8000 over HTTP and verifies sqlite,
not TestClient. Against Docker the DB lives inside the container
(/app/agent-relay.db), not ./agent-relay.db on the host.
"""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import urllib.request
import urllib.error

import pytest

BASE = os.getenv("RELAY_BASE_URL", "http://127.0.0.1:8000")
DB_PATH = os.path.join(os.path.dirname(__file__), "agent-relay.db")


def api(method: str, path: str, body: dict | None = None, token: str | None = None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            raw = r.read().decode()
            return r.status, json.loads(raw) if raw else None
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw)
        except ValueError:
            return e.code, {"raw": raw}


@pytest.fixture
def server_up():
    try:
        with urllib.request.urlopen(BASE + "/health", timeout=5) as r:
            assert json.loads(r.read().decode())["status"] == "ok"
    except Exception as e:
        pytest.skip(f"live relay not running at {BASE}: {e}")


def test_acceptance_scenario1_exchange_task_and_result(server_up):
    # Register two agents
    s, alice = api("POST", "/api/v1/agents", {"name": "alice-sender"})
    assert s == 201, alice
    s, bob = api("POST", "/api/v1/agents", {"name": "bob-worker"})
    assert s == 201, bob

    # One sends a task
    s, task = api("POST", "/api/v1/tasks",
                  {"to": bob["agent_id"], "input": "hello relay"},
                  token=alice["token"])
    assert s == 201, task
    task_id = task["task_id"]
    assert task["status"] == "queued"

    # The other claims and completes it
    s, claim = api("POST", "/api/v1/tasks/claim",
                   {"worker_id": "bob-laptop-1", "wait_seconds": 5},
                   token=bob["token"])
    assert s == 200, claim
    assert claim["task_id"] == task_id
    assert claim["attempt"] == 1
    assert claim["claim_token"]

    s, done = api("POST", f"/api/v1/tasks/{task_id}/complete",
                  {"claim_token": claim["claim_token"], "output": "HELLO RELAY"},
                  token=bob["token"])
    assert s == 200, done
    assert done["status"] == "completed"

    # Sender reads the result
    s, result = api("GET", f"/api/v1/tasks/{task_id}", token=alice["token"])
    assert s == 200, result
    assert result["status"] == "completed"
    assert result["output"] == "HELLO RELAY"
    assert result["from"] == alice["agent_id"]
    assert result["to"] == bob["agent_id"]

    # Same data the dashboard shows, verified in the real DB.
    # Host path works for local uvicorn; container keeps its own
    # /app/agent-relay.db filesystem, so fall back to `docker exec`.
    row = None
    if os.path.exists(DB_PATH):
        con = sqlite3.connect(DB_PATH)
        try:
            row = con.execute(
                "SELECT status, output FROM tasks WHERE id=?", (task_id,)).fetchone()
        finally:
            con.close()
    if row is None:
        # Ask the single-image container for its own DB file.
        for cname in ("agent-relay", "agent-relay-api-1"):
            try:
                out = subprocess.run(
                    ["docker", "exec", cname,
                     "python", "-c",
                     f"import sqlite3;con=sqlite3.connect('/app/agent-relay.db');"
                     f"print(con.execute(\"SELECT status, output FROM tasks WHERE id='{task_id}'\").fetchone())"],
                    capture_output=True, text=True, timeout=15)
                if "completed" in out.stdout and "HELLO RELAY" in out.stdout:
                    row = ("completed", "HELLO RELAY")
                    break
            except Exception:
                pass
    if row is None:
        # Compose stack: data lives in postgres service, not a sqlite file.
        try:
            out = subprocess.run(
                ["docker", "compose", "exec", "postgres",
                 "psql", "-U", "relay", "-d", "relay", "-t", "-A",
                 "-c", f"SELECT status || '|' || output FROM tasks WHERE id='{task_id}'"],
                capture_output=True, text=True, timeout=15,
                cwd=os.path.dirname(__file__))
            if "completed|HELLO RELAY" in out.stdout:
                row = ("completed", "HELLO RELAY")
        except Exception:
            pass
    assert row == ("completed", "HELLO RELAY"), (
        f"task {task_id} not in host sqlite, container sqlite, nor postgres (API already returned completed)")
