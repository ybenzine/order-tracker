import logging
import re

from fastapi import BackgroundTasks, FastAPI, Request

from app import config
from app.assistant import run_assistant
from app.collect import collect_incident, parse_time

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("incident-response")

app = FastAPI(title="Incident Response")


def incident_id(alert: dict) -> str:
    started = parse_time(alert.get("startsAt"))
    stamp = started.strftime("%Y%m%dT%H%M%SZ") if started else "unknown"
    fingerprint = re.sub(r"[^A-Za-z0-9_-]", "", alert.get("fingerprint", "")) or "nofingerprint"
    return f"{stamp}-{fingerprint}"


async def handle_incident(alert: dict, incident_dir) -> None:
    try:
        await collect_incident(alert, incident_dir)
    except Exception:
        logger.exception("Failed to collect evidence for %s", incident_dir.name)
    # Start the assistant even if some evidence is missing; it can see what was saved.
    await run_assistant(incident_dir)


@app.get("/healthz")
def health():
    return {"status": "ok"}


@app.post("/alerts", status_code=202)
async def receive_alerts(request: Request, background: BackgroundTasks):
    """Grafana webhook receiver. One incident per firing alert instance."""
    payload = await request.json()
    started, skipped = [], []
    for alert in payload.get("alerts", []):
        if alert.get("status") != "firing":
            skipped.append({"reason": "not firing", "status": alert.get("status")})
            continue
        incident = incident_id(alert)
        incident_dir = config.INCIDENTS_DIR / incident
        try:
            incident_dir.mkdir(parents=True)
        except FileExistsError:
            # Grafana re-sends still-firing alerts; one incident per alert instance.
            skipped.append({"reason": "duplicate", "incident": incident})
            continue
        logger.info("Alert firing: %s -> %s", alert.get("labels", {}).get("alertname"), incident)
        background.add_task(handle_incident, alert, incident_dir)
        started.append(incident)
    return {"started": started, "skipped": skipped}
