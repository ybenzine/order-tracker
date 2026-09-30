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

# The prompt is written to the assistant's stdin. {tools} expands to one argument
# per allowed tool and {incident_dir} to the incident folder.
ASSISTANT_COMMAND = shlex.split(
    os.getenv("ASSISTANT_COMMAND", "claude -p --allowedTools {tools} --add-dir {incident_dir}")
)
ASSISTANT_TIMEOUT_SECONDS = int(os.getenv("ASSISTANT_TIMEOUT_SECONDS", "900"))

# Fix mode: with a token and repo, the assistant works in a clone on its own branch
# and this service pushes that branch and opens a draft PR. The assistant never
# receives the token. Without them the assistant only writes analysis.md.
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")
GITHUB_REPO = os.getenv("GITHUB_REPO", "")  # owner/name
GITHUB_BASE_BRANCH = os.getenv("GITHUB_BASE_BRANCH", "main")
GITHUB_CLONE_URL = os.getenv("GITHUB_CLONE_URL", f"https://github.com/{GITHUB_REPO}.git")
BRANCH_PREFIX = "fix/"

REPORT_TOOLS = ["Read", "Grep", "Glob", "Write"]
FIX_TOOLS = REPORT_TOOLS + [
    "Edit",
    "Bash(git status:*)",
    "Bash(git diff:*)",
    "Bash(git add:*)",
    "Bash(git commit:*)",
    "Bash(uv sync:*)",
    "Bash(uv run pytest:*)",
]


def fix_mode_enabled() -> bool:
    return bool(GITHUB_TOKEN and GITHUB_REPO)
