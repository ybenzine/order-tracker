import os
import shlex
from pathlib import Path

LOKI_URL = os.getenv("LOKI_URL", "http://loki:3100")
TEMPO_URL = os.getenv("TEMPO_URL", "http://tempo:3200")
PROMETHEUS_URL = os.getenv("PROMETHEUS_URL", "http://prometheus:9090")
APP_SERVICE_NAME = os.getenv("APP_SERVICE_NAME", "order-tracker")

INCIDENTS_DIR = Path(os.getenv("INCIDENTS_DIR", "/incidents"))
WORKSPACE_DIR = Path(os.getenv("WORKSPACE_DIR", "/workspace"))

# How far before the alert started to look for logs, traces and metrics.
LOOKBACK_MINUTES = int(os.getenv("LOOKBACK_MINUTES", "15"))

# The prompt is written to the assistant's stdin. The default runs Claude Code
# headless with read-only tools plus Write; the workspace is mounted read-only in
# Compose, so Write can only succeed inside the incident directory.
ASSISTANT_COMMAND = shlex.split(
    os.getenv("ASSISTANT_COMMAND", "claude -p --allowedTools Read,Grep,Glob,Write")
)
ASSISTANT_TIMEOUT_SECONDS = int(os.getenv("ASSISTANT_TIMEOUT_SECONDS", "900"))
