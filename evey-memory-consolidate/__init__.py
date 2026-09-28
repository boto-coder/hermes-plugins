"""Evey Memory Consolidation — extracts facts from daily conversations.

Runs daily via cron. Queries this Hermes profile's session history
(~/.hermes/state.db SQLite messages table) for the last N hours,
uses this profile's configured model to extract key facts, and
updates MEMORY.md. No Qdrant, no Ollama, no Langfuse.
"""

import json
import os
import time
import sqlite3
import urllib.request
import urllib.error
from pathlib import Path

LITELLM_URL = os.environ.get("OPENAI_BASE_URL", "")
LITELLM_KEY = os.environ.get("OPENAI_API_KEY", "")
MEMORY_PATH = Path(os.environ.get("HERMES_HOME", os.path.expanduser("~/.hermes"))) / "memories" / "MEMORY.md"
CHAR_LIMIT = 4200

SCORE_PROMPT = """Rate the importance of this fact for an AI agent's long-term memory (1-10).

10 = Critical (security rule, user preference, architecture decision)
7-9 = Important (learned behavior, tool discovery, cost insight)
4-6 = Useful (research finding, model comparison, minor observation)
1-3 = Trivial (greeting, routine check, temporary state)

Fact: {fact}

Reply with ONLY a number 1-10:"""

EXTRACT_PROMPT = """Extract 3-5 key facts from these AI agent conversation traces.

Rules:
- Only novel, useful facts (not greetings, errors, tool calls)
- Format: "- [category] fact" where category is one of: learned, decided, discovered, created, fixed
- Use COMPACT language — no filler words, abbreviate where clear
- Be specific, terse, max 15 words per fact
- Skip anything trivial or repetitive

TRACES:
{traces}

KEY FACTS:"""

SCHEMA = {
    "name": "consolidate_daily_memory",
    "description": (
        "Extract key facts from recent conversations and store them. "
        "Updates MEMORY.md with new learnings. "
        "Run this daily or when you want to consolidate recent knowledge."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "hours_back": {
                "type": "number",
                "description": "How many hours back to look (default: 24)",
            },
        },
    },
}


def _session_traces(hours_back=24):
    """Query this Hermes profile's session history for recent messages."""
    db_path = Path(os.environ.get("HERMES_HOME", os.path.expanduser("~/.hermes"))) / "state.db"
    if not db_path.exists():
        return []
    try:
        conn = sqlite3.connect(str(db_path), timeout=5)
        conn.row_factory = sqlite3.Row
        cutoff = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - hours_back * 3600))
        rows = conn.execute(
            "SELECT session_id, message, role, created_at FROM messages "
            "WHERE created_at >= ? ORDER BY created_at DESC LIMIT 100",
            (cutoff,),
        ).fetchall()
        conn.close()
        summaries = []
        for r in rows:
            role = r["role"] or ""
            msg = r["message"] or ""
            if isinstance(msg, dict):
                msg = msg.get("content", "") if isinstance(msg.get("content"), str) else str(msg)[:200]
            summaries.append(f"{role}: {str(msg)[:150]}")
        return summaries
    except Exception:
        return []


def _load_call_llm():
    import importlib.util as _iu, os as _os
    _spec = _iu.spec_from_file_location("evey_utils", _os.path.join(_os.path.dirname(_os.path.dirname(__file__)), "evey_utils.py"))
    _eu = _iu.module_from_spec(_spec)
    _spec.loader.exec_module(_eu)
    return _eu.call_llm


def _extract_facts(trace_summaries):
    call_llm = _load_call_llm()
    text = "\n".join(trace_summaries[:15])
    result = call_llm(None, EXTRACT_PROMPT.format(traces=text[:3000]), max_tokens=300, temperature=0.3)
    return result or "Extraction failed"


def _score_fact(fact):
    """Score a fact's importance (1-10) using this profile's model."""
    call_llm = _load_call_llm()
    text = call_llm(None, SCORE_PROMPT.format(fact=fact), max_tokens=5, temperature=0)
    if text:
        try:
            return int("".join(c for c in text if c.isdigit())[:2])
        except ValueError:
            pass
    return 5


def _update_memory(new_facts):
    MEMORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    current = MEMORY_PATH.read_text() if MEMORY_PATH.exists() else ""

    scored_facts = []
    for fact in new_facts.split("\n"):
        if fact.strip() and fact.strip().startswith("-"):
            score = _score_fact(fact)
            if score >= 5:
                scored_facts.append(f"{fact.strip()} [importance:{score}]")

    if not scored_facts:
        return len(current)

    date_header = f"\n## Learned {time.strftime('%Y-%m-%d')}\n"
    updated = current + date_header + "\n".join(scored_facts) + "\n"

    if len(updated) > CHAR_LIMIT:
        lines = updated.split("\n")
        removable = [(i, l) for i, l in enumerate(lines) if "[importance:" in l]
        removable.sort(key=lambda x: int(x[1].split("importance:")[1].split("]")[0]) if "importance:" in x[1] else 10)
        while len("\n".join(lines)) > CHAR_LIMIT and removable:
            idx = removable.pop(0)[0]
            if idx < len(lines):
                lines.pop(idx)
                removable = [(i if i < idx else i-1, l) for i, l in removable]
        updated = "\n".join(lines)

    MEMORY_PATH.write_text(updated)
    return len(updated)


def handler(args, **kwargs):
    try:
        hours = args.get("hours_back", 24)
        traces = _session_traces(hours)
        if not traces:
            return json.dumps({"status": "empty", "message": "No session traces found"})
        facts = _extract_facts(traces)
        mem_size = _update_memory(facts)
        return json.dumps({
            "status": "consolidated",
            "traces_analyzed": len(traces),
            "facts_extracted": facts,
            "memory_size": mem_size,
        })
    except Exception as e:
        return json.dumps({"error": str(e)})


def register(ctx):
    ctx.register_tool(name="consolidate_daily_memory", toolset="evey_memory",
        schema=SCHEMA, handler=handler)
