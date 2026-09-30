"""Fetch the telemetry needed to understand an alert and save it to disk."""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

from app import config


def parse_time(value: str | None) -> datetime | None:
    # Grafana sends RFC 3339 with nanoseconds and "0001-01-01T00:00:00Z" for "unset".
    if not value or value.startswith("0001-"):
        return None
    head, _, tail = value.rstrip("Z").partition(".")
    fraction = tail.split("+")[0][:6].ljust(6, "0") if tail else "000000"
    return datetime.fromisoformat(f"{head}.{fraction}").replace(tzinfo=timezone.utc)


def time_window(alert: dict) -> tuple[datetime, datetime]:
    started = parse_time(alert.get("startsAt")) or datetime.now(timezone.utc)
    end = datetime.now(timezone.utc)
    return started - timedelta(minutes=config.LOOKBACK_MINUTES), end


async def _get(client: httpx.AsyncClient, url: str, **params) -> dict:
    """GET JSON; a failing backend is recorded rather than aborting the incident."""
    try:
        response = await client.get(url, params=params)
        response.raise_for_status()
        return response.json()
    except (httpx.HTTPError, ValueError) as error:
        return {"error": f"{type(error).__name__}: {error}", "url": url}


async def fetch_logs(client, start: datetime, end: datetime) -> dict:
    return await _get(
        client,
        f"{config.LOKI_URL}/loki/api/v1/query_range",
        query=f'{{service_name="{config.APP_SERVICE_NAME}"}}',
        start=int(start.timestamp() * 1e9),
        end=int(end.timestamp() * 1e9),
        limit=1000,
        direction="forward",
    )


async def fetch_traces(client, route: str | None, start: datetime, end: datetime) -> dict:
    """Search for 5xx traces on the route, then load each trace in full."""
    conditions = [
        f'resource.service.name = "{config.APP_SERVICE_NAME}"',
        "span.http.status_code >= 500",
    ]
    if route:
        conditions.append(f'span.http.route = "{route}"')
    search = await _get(
        client,
        f"{config.TEMPO_URL}/api/search",
        q="{ " + " && ".join(conditions) + " }",
        start=int(start.timestamp()),
        end=int(end.timestamp()),
        limit=20,
    )
    traces = {}
    for found in search.get("traces", []):
        traces[found["traceID"]] = await _get(
            client, f"{config.TEMPO_URL}/api/traces/{found['traceID']}"
        )
    return {"search": search, "traces": traces}


async def fetch_metrics(client, route: str | None) -> dict:
    window = f"{config.LOOKBACK_MINUTES}m"
    selector = f'{{http_route="{route}"}}' if route else ""
    queries = {
        "requests_by_route_and_status": (
            f"sum by (http_route, http_response_status_code) "
            f"(increase(order_tracker_http_requests_total{selector}[{window}]))"
        ),
        "lookups_by_result": f"sum by (result) (increase(order_tracker_order_lookups_total[{window}]))",
    }
    return {
        name: await _get(client, f"{config.PROMETHEUS_URL}/api/v1/query", query=query)
        for name, query in queries.items()
    }


def _write_json(path: Path, data) -> None:
    path.write_text(json.dumps(data, indent=2, default=str))


def summarize(alert: dict, incident_id: str, start: datetime, end: datetime) -> str:
    labels, annotations = alert.get("labels", {}), alert.get("annotations", {})
    return f"""# Incident {incident_id}

- **Alert:** {labels.get("alertname", "unknown")}
- **Severity:** {labels.get("severity", "unknown")}
- **Affected endpoint:** {labels.get("http_route", "unknown")}
- **Alert started:** {alert.get("startsAt")}
- **Evidence window:** {start.isoformat()} to {end.isoformat()}
- **Summary:** {annotations.get("summary", "")}
- **Description:** {annotations.get("description", "")}
- **Dashboard:** {alert.get("dashboardURL") or alert.get("generatorURL", "")}

## Files

- `alert.json`: the alert exactly as Grafana sent it
- `logs.json`: Loki query_range result for `{{service_name="{config.APP_SERVICE_NAME}"}}` in the window
- `traces.json`: Tempo traces with a 5xx span on the affected route (search result plus full traces)
- `metrics.json`: Prometheus request counts by route and status, and lookups by result
"""


async def collect_incident(alert: dict, incident_dir: Path) -> None:
    route = alert.get("labels", {}).get("http_route")
    start, end = time_window(alert)
    _write_json(incident_dir / "alert.json", alert)
    async with httpx.AsyncClient(timeout=10) as client:
        _write_json(incident_dir / "logs.json", await fetch_logs(client, start, end))
        _write_json(incident_dir / "traces.json", await fetch_traces(client, route, start, end))
        _write_json(incident_dir / "metrics.json", await fetch_metrics(client, route))
    (incident_dir / "incident.md").write_text(summarize(alert, incident_dir.name, start, end))
