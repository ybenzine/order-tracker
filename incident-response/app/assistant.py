"""Start the coding assistant headlessly against a saved incident."""
import asyncio
import logging
import os
from pathlib import Path

from app import config

logger = logging.getLogger("incident-response")

PROMPT = """You are the on-call engineer for the order-tracker service. A Grafana alert fired.

The incident evidence is saved in {incident_dir}:
- incident.md: summary and file guide
- alert.json, logs.json, traces.json, metrics.json

The application source is in {workspace_dir} (read-only).

Investigate, then write your findings to {incident_dir}/analysis.md with:
1. What is failing (endpoint, status code, how many requests, since when)
2. The root cause, citing the specific log lines, trace spans and source file/line
3. A proposed fix as a code diff (do not apply it)
4. How to verify the fix

Base every claim on the saved evidence and the source code.
"""


async def run_assistant(incident_dir: Path) -> None:
    prompt = PROMPT.format(incident_dir=incident_dir, workspace_dir=config.WORKSPACE_DIR)
    log_path = incident_dir / "assistant.log"
    cwd = config.WORKSPACE_DIR if config.WORKSPACE_DIR.is_dir() else incident_dir
    try:
        with log_path.open("wb") as log:
            process = await asyncio.create_subprocess_exec(
                *config.ASSISTANT_COMMAND,
                stdin=asyncio.subprocess.PIPE,
                stdout=log,
                stderr=asyncio.subprocess.STDOUT,
                cwd=cwd,
                env={**os.environ, "INCIDENT_DIR": str(incident_dir)},
            )
            logger.info("Started assistant for %s (pid %s)", incident_dir.name, process.pid)
            try:
                await asyncio.wait_for(
                    process.communicate(prompt.encode()), config.ASSISTANT_TIMEOUT_SECONDS
                )
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
                logger.error("Assistant timed out for %s", incident_dir.name)
                return
        logger.info("Assistant exited %s for %s", process.returncode, incident_dir.name)
    except OSError as error:
        logger.error("Could not start assistant %r: %s", config.ASSISTANT_COMMAND[0], error)
        log_path.write_text(f"Could not start assistant: {error}\n")
