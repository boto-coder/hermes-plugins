# Port Plan: Native Memory & Continuous Learning for Hermes

## Scope
Port Evey's memory/identity/learning plugins to run on this Hermes profile
(hermes 0.19.0, Nous Portal model, built-in memory provider) without requiring
Evey's Docker stack (Langfuse, Qdrant, Ollama, ntfy, searxng, crawl4ai, etc.).

## In-scope plugins
| Plugin | Action |
|--------|--------|
| evey-learner | Adapt to use Hermes built-in memory |
| evey-memory-adaptive | Keep as-is (pure local JSON) |
| evey-memory-consolidate | Rewrite fact extraction to use Hermes session history |
| evey-identity | Rewrite to use this profile's configured model |
| evey-habits | Keep as-is |
| evey-digest | Replace Langfuse + ntfy |
| evey-autonomy | Keep as-is (reads local files) |

## Out of scope (disable)
wallet, moltbook, mqtt, telegram-ux, news, watchdog, commands, validate,
sandbox, bridge, status, cost-guard, delegation-score, cache, verification.

## Changes needed

### 1. evey_utils.py - model abstraction
call_llm() hard-codes OPENAI_BASE_URL / OPENAI_API_KEY.
Replace with get_model_endpoint() reading hermes config.
Keep http_get / http_post_json as-is.

### 2. evey-memory-consolidate - replace Langfuse + Qdrant + Ollama
- Langfuse traces -> Hermes session history (~/.hermes/state.db SQLite messages)
- Qdrant embeddings -> drop (MEMORY.md is plain markdown)
- Ollama embedding model -> drop
- Fact extraction -> use this profile's configured model
- Output -> append to ~/.hermes/memories/MEMORY.md

### 3. evey-identity - replace qwen35-4b model
_extract_rule() calls call_llm("qwen35-4b", ...).
Use this profile's configured model via the new abstraction.
Keep SOUL.md writes local.

### 4. evey-learner - no infra dependency
Already pure local JSONL at ~/.hermes/workspace/orchestrator/learnings.jsonl.
No changes required.

### 5. evey-habits - no infra dependency
Already pure local JSON at ~/.hermes/workspace/manager/habits.json.
No changes required.

### 6. evey-digest - replace Langfuse + ntfy
- Langfuse costs -> hermes insights --days 1 CLI or ~/.hermes/executions.db
- ntfy alerts -> drop or make optional
- Keep goals.md and cron/jobs.json reads

### 7. Cron wiring - make the loop run nightly
hermes cron add that calls consolidate_daily_memory, update_identity,
memory_decay, habits_insights. Or register a pre_llm_call / session:end hook.

## Verification checklist
- [ ] hermes plugins validate . passes
- [ ] hermes plugins enable <name> succeeds
- [ ] hermes gateway restart loads without import errors
- [ ] consolidate_daily_memory appends to MEMORY.md
- [ ] update_identity adds a behavior rule to SOUL.md
- [ ] learn_from_interaction writes / apply_learnings reads learnings.jsonl
- [ ] memory_decay flags stale entries correctly
- [ ] No outbound network calls to Langfuse / Qdrant / Ollama / ntfy
