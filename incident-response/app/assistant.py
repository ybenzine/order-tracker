"""Start the coding assistant headlessly against a saved incident."""
import asyncio
import logging
import os
from pathlib import Path

from app import config

logger = logging.getLogger("incident-response")

REPORT_PROMPT = """You are the on-call engineer for the order-tracker service. A Grafana alert fired.

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

FIX_PROMPT = """You are the on-call engineer for the order-tracker service. A Grafana alert fired.

The incident evidence is saved in {incident_dir}:
- incident.md: summary and file guide
- alert.json, logs.json, traces.json, metrics.json

Your working directory is a git clone of the application on branch {branch}.

1. Investigate, then write your findings to {incident_dir}/analysis.md with:
   what is failing, the root cause (cite log lines, trace spans and source file/line),
   the fix you made, and how to verify it. Base every claim on the evidence and source.
2. Make the smallest change that fixes the root cause, and add a regression test.
3. Run `uv sync` then `uv run pytest`. Only commit if the tests pass.
4. Commit on {branch} with a clear message. Stay on this branch.

Do not push and do not open a pull request: the service does that after you finish.
If you cannot identify a fix you are confident in, make no commit and explain why in
analysis.md.
"""


async def _run(*args: str, cwd: Path, env: dict | None = None) -> tuple[int, str]:
    process = await asyncio.create_subprocess_exec(
        *args,
        cwd=cwd,
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    output, _ = await process.communicate()
    return process.returncode, output.decode(errors="replace").strip()


def _github_env() -> dict:
    return {
        **os.environ,
        "GH_TOKEN": config.GITHUB_TOKEN,
        "GIT_TERMINAL_PROMPT": "0",
    }


def _assistant_env(incident_dir: Path) -> dict:
    """The assistant gets no GitHub credentials; only the service pushes."""
    env = {**os.environ, "INCIDENT_DIR": str(incident_dir), "GIT_TERMINAL_PROMPT": "0"}
    for name in ("GITHUB_TOKEN", "GH_TOKEN"):
        env.pop(name, None)
    return env


async def prepare_clone(repo_dir: Path, branch: str) -> bool:
    steps = [
        ("git", "clone", "--quiet", "--branch", config.GITHUB_BASE_BRANCH, config.GITHUB_CLONE_URL, str(repo_dir)),
        ("git", "checkout", "-b", branch),
        ("git", "config", "user.name", "Incident Responder"),
        ("git", "config", "user.email", "incident-responder@users.noreply.github.com"),
    ]
    for step in steps:
        code, output = await _run(*step, cwd=repo_dir.parent if step[1] == "clone" else repo_dir, env=_github_env())
        if code != 0:
            logger.error("%s failed: %s", " ".join(step[:2]), output.replace(config.GITHUB_TOKEN, "***"))
            return False
    return True


def build_command(tools: list[str], incident_dir: Path) -> list[str]:
    command: list[str] = []
    for token in config.ASSISTANT_COMMAND:
        if token == "{tools}":
            command.extend(tools)
        else:
            command.append(token.replace("{incident_dir}", str(incident_dir)))
    return command


async def publish_branch(repo_dir: Path, branch: str, incident_dir: Path) -> str | None:
    """Push the branch and open a draft PR if the assistant committed changes."""
    base = f"origin/{config.GITHUB_BASE_BRANCH}"
    code, output = await _run("git", "rev-list", "--count", f"{base}..{branch}", cwd=repo_dir)
    if code != 0 or output == "0":
        logger.info("No commits on %s; nothing to publish", branch)
        return None
    # Only ever push the fix branch this service created.
    code, output = await _run("git", "push", "origin", f"refs/heads/{branch}:refs/heads/{branch}", cwd=repo_dir, env=_github_env())
    if code != 0:
        logger.error("Push failed: %s", output)
        return None
    return await create_pr(repo_dir, branch, incident_dir)


async def create_pr(repo_dir: Path, branch: str, incident_dir: Path) -> str | None:
    analysis = incident_dir / "analysis.md"
    body = analysis.read_text() if analysis.exists() else "The assistant did not write an analysis."
    body_path = incident_dir / "pr-body.md"
    body_path.write_text(
        f"{body}\n\n---\nDraft opened automatically for incident `{incident_dir.name}`. "
        "Review before merging.\n"
    )
    code, output = await _run(
        "gh", "pr", "create", "--draft",
        "--repo", config.GITHUB_REPO,
        "--base", config.GITHUB_BASE_BRANCH,
        "--head", branch,
        "--title", f"Automated fix proposal for incident {incident_dir.name}",
        "--body-file", str(body_path),
        cwd=repo_dir,
        env=_github_env(),
    )
    if code != 0:
        logger.error("Could not open PR: %s", output)
        return None
    url = output.splitlines()[-1]
    (incident_dir / "pr.txt").write_text(url + "\n")
    logger.info("Opened draft PR %s", url)
    return url


async def run_assistant(incident_dir: Path) -> None:
    branch = f"{config.BRANCH_PREFIX}{incident_dir.name}"
    repo_dir = incident_dir / "repo"
    log_path = incident_dir / "assistant.log"

    fix_mode = config.fix_mode_enabled() and await prepare_clone(repo_dir, branch)
    if fix_mode:
        tools, cwd = config.FIX_TOOLS, repo_dir
        prompt = FIX_PROMPT.format(incident_dir=incident_dir, branch=branch)
    else:
        tools = config.REPORT_TOOLS
        cwd = config.WORKSPACE_DIR if config.WORKSPACE_DIR.is_dir() else incident_dir
        prompt = REPORT_PROMPT.format(incident_dir=incident_dir, workspace_dir=config.WORKSPACE_DIR)

    command = build_command(tools, incident_dir)
    try:
        with log_path.open("wb") as log:
            process = await asyncio.create_subprocess_exec(
                *command,
                stdin=asyncio.subprocess.PIPE,
                stdout=log,
                stderr=asyncio.subprocess.STDOUT,
                cwd=cwd,
                env=_assistant_env(incident_dir),
            )
            logger.info("Started assistant for %s (pid %s, fix mode %s)", incident_dir.name, process.pid, fix_mode)
            try:
                await asyncio.wait_for(
                    process.communicate(prompt.encode()), config.ASSISTANT_TIMEOUT_SECONDS
                )
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
                logger.error("Assistant timed out for %s", incident_dir.name)
                return
    except OSError as error:
        logger.error("Could not start assistant %r: %s", command[0], error)
        log_path.write_text(f"Could not start assistant: {error}\n")
        return
    logger.info("Assistant exited %s for %s", process.returncode, incident_dir.name)
    if fix_mode and process.returncode == 0:
        await publish_branch(repo_dir, branch, incident_dir)
