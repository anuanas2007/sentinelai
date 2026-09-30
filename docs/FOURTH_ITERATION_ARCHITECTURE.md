# SentinelAI — Iteration 4: Public Release Preparation

> Continues the decision log started in [FIRST_ITERATION_ARCHITECTURE.md](FIRST_ITERATION_ARCHITECTURE.md) and continued through [SECOND_ITERATION_ARCHITECTURE.md](SECOND_ITERATION_ARCHITECTURE.md) and [THIRD_ITERATION_ARCHITECTURE.md](THIRD_ITERATION_ARCHITECTURE.md).
> Those files document Iterations 1–3 as actually built. This file documents the decisions made in preparing the project for public release — what was broken, what was missing, and why each fix was made the way it was.

---

## The Goal Shift

The first three iterations were about building. This one is about making what was built defensible — publicly, to strangers who will judge the code without context, and to interviewers who will probe every decision.

Three things became clear from an honest audit before starting:

1. The README claimed a bug "was caught by a synthetic unit test" but there were no tests in the repository. Anyone cloning would see this immediately.
2. A correctness bug existed in the core detector that had never been caught — because there were no tests.
3. Several things visible on a public GitHub link would read as amateur before anyone read a line of code: no LICENSE, a Vite boilerplate README in `ui/`, a docker command shown to shoppers when the store couldn't load products.

The correct order was: fix what is broken, test what matters, clean what is embarrassing, then continue building.

---

## 1. The Detector Bug — Shared Sliding Window

### What was wrong

`ErrorDetector._handle_threshold()` used a single shared deque (`self.error_window`) across every threshold event type. That meant one `db_pool_exhausted` + one `payment_service_timeout` + one `external_api_timeout` accumulated to 3 entries in the window and incorrectly escalated the third event to `critical`, dispatching an AI call — even though none of those three event types had individually crossed the threshold.

This broke the core promise of the two-tier classifier. The detector was supposed to escalate only when one specific error had repeated enough to confirm a pattern. Instead, three completely unrelated errors could trigger an escalation between them — the exact alert fatigue the threshold tier was designed to prevent.

### Why it wasn't caught earlier

No tests. The system ran well enough during manual testing because the error types that actually fire frequently in practice (`external_api_timeout`, `analytics_failed`) tend to repeat on their own — the cross-contamination condition (three different threshold events close together) doesn't arise naturally in manual testing and only shows up under realistic mixed load.

### The fix

Replaced `self.error_window: deque` with `self.error_windows: defaultdict(deque)` keyed by event name. Each event type now has its own independent window. Three different timeout types that happen to co-occur still each read as their first occurrence. The same event type appearing three times still escalates.

```python
# Before — one shared window, cross-contamination possible
self.error_window: deque = deque()

# After — independent window per event type
self.error_windows: defaultdict = defaultdict(deque)
```

### _event_time() — making detection replayable

Also added `_event_time()` as a static method: extracts the log line's own ISO timestamp and returns it as a float, falling back to `time.time()` when missing or malformed. Before this, threshold detection used `time.time()` exclusively — which meant tests that passed log entries at fixed timestamps would still measure the gap between them using actual wall-clock time, making window-expiry tests either flaky or impossible to write without `time.sleep()`.

With `_event_time()`, two log entries with timestamps 90 seconds apart are detected as 90 seconds apart regardless of when the test runs. Detection is now deterministic.

---

## 2. Tests — Why Now, What's Covered

### The README problem

The README said: "A bug was caught by a synthetic unit test before it ever reached production." That was true for an earlier bug — but there were no tests in the repository at the time of writing. The claim was honest about the past but misleading about the present state of the codebase. Anyone reading the README and then looking at the repo would immediately see the contradiction.

### Why the tests live where they do

Tests go in `agent/tests/` rather than a top-level `tests/` directory for one reason: the agent's detector and parsing functions are pure Python with no external dependencies. They can be run with `pytest agent/tests` with no Docker, no Redis, no database, no OpenAI key. Placing them alongside the code they test makes that clear — no setup ceremony, just `pytest`.

### The log_parsing.py extraction

`log_collector.py` imports `ai_engine` at the top level. `ai_engine` imports CrewAI. This means `import log_collector` in a test would pull in the entire CrewAI dependency tree, which either fails in a lightweight test environment or makes the test suite slow and fragile for no reason.

`parse_log_line` and `is_error` are the two functions tests need from `log_collector`. Both are pure: no state, no I/O, no network. They belong in their own module. Extracted into `agent/log_parsing.py`, imported by both `log_collector` and the tests. No behavior changed.

### What the 57 tests cover

Every test uses explicit ISO timestamps rather than sleeping or relying on wall time — this is what `_event_time()` makes possible. The suite is fast (57 tests in under 0.1s) and deterministic.

| Test class | What it pins down |
|---|---|
| `TestImmediateErrors` | Every known immediate event escalates on the first occurrence with severity "immediate" |
| `TestUnknownEvents` | Unknown event names default to immediate — over-alerting is safer than missing a real incident |
| `TestThresholdErrors` | First and second occurrences produce warnings; the third produces critical with AI dispatch for AI-worthy events |
| `TestSharedWindowRegression` | Three different threshold events must never reach critical — the exact bug fixed above |
| `TestWindowExpiry` | Events older than 60s don't count; events just inside the window do |
| `TestCascadeDetection` | Confirmation requires 3 co-occurrences within 30s; A→B and B→A count toward the same canonical pair |
| `TestAiCooldown` | Second dispatch for the same event within 120s is suppressed; dispatch works again after the cooldown expires |
| `TestCascadeCooldown` | Cooldown is keyed by the canonical pair, not the individual event name; different pairs have independent cooldowns |
| `TestEventTime` | Valid ISO, Z-suffix, naive (treated as UTC), missing, malformed, and None timestamps all handled without crashing |
| `TestParseLogLine` | Valid JSON becomes a dict with `_raw` attached; plain text, empty, partial, and non-object JSON return None |
| `TestIsError` | error and critical return True; info, warning, debug, empty, and missing return False; case-insensitive |

The regression test for the shared-window bug (`TestSharedWindowRegression::test_three_different_threshold_events_do_not_reach_critical`) is the one that would have caught the bug if it had existed from the start. It now runs on every commit.

---

## 3. Repo Cleanup — What Was Embarrassing

### No LICENSE

A public repository without a LICENSE is legally "all rights reserved." This is the kind of thing that signals a project wasn't meant to be used or shared — the opposite of the intent here. Added MIT.

### ui/README.md

Was Vite's template boilerplate: "This template provides a minimal setup to get React working in Vite with HMR and some Oxlint rules." Not a single word about this project. Deleted.

### ShopPage load error

When the store couldn't reach the backend, the error shown to the user was:
> "Run `docker compose down -v && docker compose up --build` to reset the database schema."

A shopper would never see a docker command. The TechNest store is meant to read as a real consumer application — internal tooling instructions appearing in the UI break that entirely. Replaced with a friendly retry message.

### .env.example

Was four lines with `OPENAI_API_KEY=sk-changeme`. Problems: `sk-changeme` looks like a placeholder API key format that might confuse people into thinking it's real, `SENTINEL_LLM_MODEL` was documented in `.env.example` comments but not actually in the file, and there was no explanation of the zero-cost mode. Updated to:

```env
# Required
POSTGRES_USER=sentinel
POSTGRES_PASSWORD=changeme
POSTGRES_DB=sentinelai

# Optional — leave empty to run with full detection but zero AI cost
OPENAI_API_KEY=

# Optional — override the LLM model
# SENTINEL_LLM_MODEL=gpt-4o-mini
```

### .gitignore

Had `node_modules/` listed twice, only `__pycache__/` (not `**/__pycache__/` which would catch subdirectories), and `ui/dist/` was missing. Fixed.

---

## 4. Code Cleanup

Three targeted changes — nothing cosmetic:

**`LLM_MODEL` was hardcoded in `ai_engine.py`**

```python
# Before
LLM_MODEL = "gpt-4o-mini"

# After
LLM_MODEL = os.environ.get("SENTINEL_LLM_MODEL", "gpt-4o-mini")
```

`SENTINEL_LLM_MODEL` was already documented in `.env.example` as the override mechanism. It just wasn't actually wired up.

**Stale comment in `log_collector.py`**

`watch_log_file()` had a comment saying "In Week 2 this function is replaced by Docker log stream reader." Docker was implemented in Week 2. The comment was left from the original Week 1 plan and never removed.

**Missing `payment_method` in traffic worker**

`_traffic_worker()` in `web.py` sent order requests without a `payment_method` field. The field has a default (`"credits"`) so it never crashed, but it was the only place in the codebase that omitted it — inconsistent with every other order in the codebase.

---

## 5. What Comes Next

The release plan defines a further four parts of work before the project is ready to demo publicly:

**Part 2 — Backend enrichment:** each incident currently emits only its event name, severity, and two booleans. The new dashboard needs the full story per incident: the raw triggering log line, the last 10 lines of context before it fired, a plain-English routing reason ("Threshold reached and the log line doesn't explain why — sent to the investigator"), stage-started events when each AI agent begins, confidence and files-read from the AI output, and duration per stage. This is the data layer that makes Part 3 possible.

**Part 3 — Dashboard redesign:** replace the current four-column layout with a single-incident journey view. One error, read top to bottom: what happened in the app, how it was detected, why it was routed the way it was, what the investigator read, what the root cause was, what fix was proposed. No modals for the core flow.

**Part 4 / 4a — Store as the demo surface:** errors should surface through normal shopping (flash sale concurrency, track order, analytics page) rather than admin scenario buttons. And the store itself needs to read as a real shop — light theme, real product imagery, pages a real store would have — so it is instantly distinguishable from SentinelAI in a split-screen recording.

**Part 6 — Evaluation:** run each AI-worthy scenario N times, score the diagnosis against known ground truth, report honestly. This is the credibility piece that separates a working demo from a measurably reliable tool.

---

## 6. What Stayed Unchanged

- The core detection pipeline (log tailing, two-tier classification, cascade detection, AI dispatch, cooldown)
- The AI reasoning engine (CrewAI, investigator → fixer, propose-only)
- Redis, ChromaDB, Prometheus, Grafana
- All deliberately preserved bugs in `target_app` — the race condition, division by zero in analytics, external timeout, missing retry on email, missing circuit breaker
- The TechNest store structure, data model, and all existing trigger scenarios
