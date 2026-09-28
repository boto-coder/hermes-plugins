"""Evey Digest Plugin — aggregates overnight activity for V's morning briefing.

Reads Hermes-native sources: cron/jobs.json, goals.md, session history,
and this profile's model for cost summarization. No Langfuse, no ntfy.
"""

import base64
import json
import os
import time
import sqlite3
import urllib.request
import urllib.error
import subprocess
from pathlib import Path

HERMES_HOME = Path(os.environ.get("HERMES_HOME", os.path.expanduser("~/.hermes")))
NTFY_URL = os.environ.get("NTFY_URL", "")  # optional; dropped if empty
HTTP_TIMEOUT = 10

SCHEMA = {
    "name": "daily_digest",
    "description": (
        "Generate a morning digest. Aggregates cron job health, goal counts, "
        "session activity from the last 24h, and model costs from this profile's "
        "configured model. No parameters needed — just call it."
    ),
    "parameters": {
        "type": "object",
        "properties": {},
        "required": [],
    },
}


def _http_get(url, headers=None):
    """GET request with timeout. Returns parsed JSON or None."""
    if headers:
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
                return json.loads(resp.read().decode())
        except Exception:
            return None
    return None


def _get_costs():
    """Query Hermes execution DB for last 24h model usage and cost."""
    try:
        db_path = HERMES_HOME / "executions.db"
        if not db_path.exists():
            return {"total": 0.0, "top_model": "unknown", "trace_count": 0}
        conn = sqlite3.connect(str(db_path), timeout=5)
        conn.row_factory = sqlite3.Row
        cutoff = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(time.time() - 86400))
        rows = conn.execute(
            "SELECT model, total_tokens, started_at FROM executions "
            "WHERE started_at >= ? ORDER BY started_at DESC LIMIT 100",
            (cutoff,),
        ).fetchall()
        conn.close()
        total_tokens = sum((r["total_tokens"] or 0) for r in rows)
        models = {}
        for r in rows:
            m = r["model"] or "unknown"
            models[m] = models.get(m, 0) + (r["total_tokens"] or 0)
        top_model = max(models, key=models.get) if models else "unknown"
        # Rough cost estimate: $0.001 per 1K tokens average
        total = round(total_tokens / 1000 * 0.001, 4)
        return {"total": total, "top_model": top_model, "trace_count": len(rows)}
    except Exception:
        return {"total": 0.0, "top_model": "unknown", "trace_count": 0}


def _get_cron():
    """Read cron job status from jobs.json."""
    try:
        jobs_path = HERMES_HOME / "cron" / "jobs.json"
        if not jobs_path.exists():
            return {"total": 0, "healthy": 0, "errors": 0}
        jobs = json.loads(jobs_path.read_text())
        total = len(jobs)
        errors = sum(
            1 for j in jobs if j.get("last_status") == "error"
            or j.get("enabled") is False
        )
        healthy = total - errors
        return {"total": total, "healthy": healthy, "errors": errors}
    except Exception:
        return {"total": 0, "healthy": 0, "errors": 0}


def _get_bridge():
    """Check bridge inbox/outbox for pending items."""
    try:
        bridge_dir = HERMES_HOME / "claude-bridge"
        inbox = bridge_dir / "inbox"
        outbox = bridge_dir / "outbox"
        inbox_count = len(list(inbox.iterdir())) if inbox.is_dir() else 0
        outbox_count = len(list(outbox.iterdir())) if outbox.is_dir() else 0
        return {"inbox": inbox_count, "outbox": outbox_count}
    except Exception:
        return {"inbox": 0, "outbox": 0}


def _get_goals():
    """Count active/completed goals from goals.md."""
    try:
        goals_path = HERMES_HOME / "goals.md"
        if not goals_path.exists():
            return {"active": 0, "completed": 0}
        content = goals_path.read_text()
        current_section = None
        active = 0
        completed = 0
        for line in content.split("\n"):
            if line.startswith("## "):
                current_section = line[3:].strip()
            elif line.strip().startswith("- "):
                if current_section == "Active":
                    active += 1
                elif current_section == "Completed":
                    completed += 1
        return {"active": active, "completed": completed}
    except Exception:
        return {"active": 0, "completed": 0}


def _get_session_count():
    """Count sessions from last 24h via Hermes CLI."""
    try:
        out = subprocess.run(
            ["hermes", "sessions", "list", "--since", "24h"],
            capture_output=True, text=True, timeout=10,
        )
        if out.returncode == 0 and out.stdout.strip():
            lines = out.stdout.strip().split("\n")
            return len(lines) - 1 if lines else 0
        return 0
    except Exception:
        return 0


def _get_alerts():
    """Check ntfy for alerts in last 24h (optional; only if NTFY_URL is set)."""
    if not NTFY_URL:
        return {"count": 0, "latest": []}
    try:
        url = f"{NTFY_URL}/evey-alerts/json?poll=1&since=24h"
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            raw = resp.read().decode()
        alerts = []
        for line in raw.strip().split("\n"):
            if line.strip():
                msg = json.loads(line)
                if msg.get("event") == "message":
                    alerts.append(msg.get("message", ""))
        return {"count": len(alerts), "latest": alerts[:3]}
    except Exception:
        return {"count": 0, "latest": []}


def handler(args, **kwargs):
    try:
        costs = _get_costs()
        cron = _get_cron()
        bridge = _get_bridge()
        goals = _get_goals()
        alerts = _get_alerts()
        session_count = _get_session_count()

        pending = bridge["inbox"] + bridge["outbox"]

        digest = {
            "overnight": (
                f"{session_count} sessions, "
                f"{costs['trace_count']} API calls, "
                f"{cron['total']} cron jobs ran, "
                f"{alerts['count']} alerts"
            ),
            "costs": (
                f"${costs['total']:.2f} estimated, "
                f"top model: {costs['top_model']}"
            ),
            "cron": (
                f"{cron['healthy']}/{cron['total']} jobs healthy, "
                f"{cron['errors']} errors"
            ),
            "bridge": f"{pending} pending tasks",
            "goals": (
                f"{goals['active']} active, "
                f"{goals['completed']} completed"
            ),
            "alerts": (
                f"{alerts['count']} alerts in 24h"
                + (f" — latest: {alerts['latest'][0]}" if alerts["latest"] else "")
            ),
        }

        return json.dumps(digest)

    except Exception as e:
        return json.dumps({"error": str(e)})


def register(ctx):
    ctx.register_tool(
        name="daily_digest",
        toolset="evey_digest",
        schema=SCHEMA,
        handler=handler,
    )
