import subprocess

import pytest
from fastapi.testclient import TestClient

from app import assistant, collect, config, main

ALERT = {
    "status": "firing",
    "fingerprint": "abc123",
    "startsAt": "2026-09-30T15:04:50.123456789Z",
    "labels": {"alertname": "Order Tracker 5xx responses", "http_route": "/api/orders/{order_id}"},
    "annotations": {"summary": "5xx responses on /api/orders/{order_id}"},
}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "INCIDENTS_DIR", tmp_path)
    monkeypatch.setattr(config, "WORKSPACE_DIR", tmp_path / "missing")
    # No real backends: every fetch is recorded as an error, which must not stop the run.
    monkeypatch.setattr(config, "LOKI_URL", "http://127.0.0.1:1")
    monkeypatch.setattr(config, "TEMPO_URL", "http://127.0.0.1:1")
    monkeypatch.setattr(config, "PROMETHEUS_URL", "http://127.0.0.1:1")
    monkeypatch.setattr(config, "ASSISTANT_COMMAND", ["cat"])
    monkeypatch.setattr(config, "GITHUB_TOKEN", "")
    return TestClient(main.app)


def test_firing_alert_saves_evidence_and_starts_assistant(client, tmp_path):
    response = client.post("/alerts", json={"alerts": [ALERT]})
    assert response.status_code == 202
    (incident,) = response.json()["started"]
    incident_dir = tmp_path / incident
    for name in ("alert.json", "logs.json", "traces.json", "metrics.json", "incident.md"):
        assert (incident_dir / name).exists(), name
    assert "/api/orders/{order_id}" in (incident_dir / "incident.md").read_text()
    # `cat` echoes the prompt from stdin, proving the assistant was started with it.
    assert str(incident_dir) in (incident_dir / "assistant.log").read_text()


def test_resolved_and_duplicate_alerts_are_skipped(client):
    resolved = {**ALERT, "status": "resolved"}
    assert client.post("/alerts", json={"alerts": [resolved]}).json()["started"] == []
    assert len(client.post("/alerts", json={"alerts": [ALERT]}).json()["started"]) == 1
    again = client.post("/alerts", json={"alerts": [ALERT]}).json()
    assert again["started"] == [] and again["skipped"][0]["reason"] == "duplicate"


def test_missing_assistant_is_recorded(client, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "ASSISTANT_COMMAND", ["definitely-not-installed"])
    (incident,) = client.post("/alerts", json={"alerts": [ALERT]}).json()["started"]
    assert "Could not start assistant" in (tmp_path / incident / "assistant.log").read_text()


def test_parse_time_handles_nanoseconds_and_unset():
    assert collect.parse_time("2026-09-30T15:04:50.123456789Z").second == 50
    assert collect.parse_time("0001-01-01T00:00:00Z") is None


def git(*args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def fix_mode(client, tmp_path, monkeypatch):
    """Fix mode against a local bare repo standing in for GitHub."""
    seed = tmp_path / "seed"
    seed.mkdir()
    git("init", "-q", "-b", "main", cwd=seed)
    git("config", "user.name", "t", cwd=seed)
    git("config", "user.email", "t@example.com", cwd=seed)
    (seed / "app.py").write_text("print('hi')\n")
    git("add", ".", cwd=seed)
    git("commit", "-qm", "init", cwd=seed)
    remote = tmp_path / "remote.git"
    git("clone", "-q", "--bare", str(seed), str(remote), cwd=tmp_path)

    prs = []

    async def fake_create_pr(repo_dir, branch, incident_dir):
        prs.append(branch)
        return "https://example.test/pr/1"

    monkeypatch.setattr(config, "GITHUB_TOKEN", "x")
    monkeypatch.setattr(config, "GITHUB_REPO", "owner/repo")
    monkeypatch.setattr(config, "GITHUB_CLONE_URL", str(remote))
    monkeypatch.setattr(assistant, "create_pr", fake_create_pr)
    return remote, prs


def branches(remote):
    out = subprocess.run(["git", "branch", "--format=%(refname:short)"], cwd=remote, capture_output=True, text=True)
    return out.stdout.split()


def test_fix_mode_pushes_fix_branch_and_opens_pr(client, tmp_path, monkeypatch, fix_mode):
    remote, prs = fix_mode
    commit = "echo fixed > fix.txt && git add fix.txt && git commit -qm fix"
    monkeypatch.setattr(config, "ASSISTANT_COMMAND", ["sh", "-c", commit])
    (incident,) = client.post("/alerts", json={"alerts": [ALERT]}).json()["started"]
    assert branches(remote) == ["fix/" + incident, "main"]
    assert prs == ["fix/" + incident]


def test_fix_mode_without_commits_publishes_nothing(client, monkeypatch, fix_mode):
    remote, prs = fix_mode
    client.post("/alerts", json={"alerts": [ALERT]})
    assert branches(remote) == ["main"]
    assert prs == []


def test_assistant_does_not_receive_github_token(client, tmp_path, monkeypatch, fix_mode):
    monkeypatch.setenv("GITHUB_TOKEN", "secret-token")
    monkeypatch.setenv("GH_TOKEN", "secret-token")
    monkeypatch.setattr(config, "ASSISTANT_COMMAND", ["sh", "-c", "env"])
    (incident,) = client.post("/alerts", json={"alerts": [ALERT]}).json()["started"]
    assert "secret-token" not in (tmp_path / incident / "assistant.log").read_text()
