import asyncio

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
