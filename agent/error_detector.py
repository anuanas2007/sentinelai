import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Optional

# ============================================================
# ERROR CLASSIFICATION
#
# Two types of errors — this is the core design decision:
#
# IMMEDIATE: Deterministic failures. One occurrence = real problem.
# No threshold needed. User doesn't exist, item out of stock —
# these are definitive failures that need attention right away.
#
# THRESHOLD: Probabilistic failures. Infrastructure issues that
# happen sometimes. One timeout = noise. Five timeouts = incident.
# Only escalate when pattern confirms it's not a blip.
#
# Why this matters:
# Treating all errors the same causes alert fatigue — engineers
# start ignoring alerts because too many are false positives.
# Smart classification means every alert is worth looking at.
# ============================================================

IMMEDIATE_ERRORS = {
    "user_not_found",
    "order_failed_user_not_found",
    "order_failed_insufficient_stock",
    "order_failed_insufficient_balance",
    "order_failed_fk_violation",
    "negative_balance_detected",
    "unhandled_exception",
    "background_task_failed",
    "email_service_unreachable",
}

THRESHOLD_ERRORS = {
    "external_api_timeout",
    "external_api_error",
    "analytics_failed",
    "db_pool_exhausted",
    "db_deadlock",
    "db_connection_error",
    "payment_service_timeout",
}

# ============================================================
# AI HANDOFF — narrowed on purpose.
#
# Most errors here are self-explanatory: the log line already is the
# root cause ("user 99 not found" needs no LLM to explain). Calling
# an AI reasoning engine on those would be decorative, not useful.
#
# AI is only worth invoking when the log line names *what* broke but
# not *why* — that requires reading code or correlating signals, which
# is exactly what an LLM is for. Right now that's true for:
#   - negative_balance_detected: balance going negative is visible,
#     but *why* requires reading the non-atomic check-then-write gap
#     in db.py/main.py.
#   - analytics_failed: ZeroDivisionError is visible, but *which*
#     variable and *why* it's structurally always zero requires
#     reading main.py.
#   - external_api_timeout / external_api_error: the proximate cause
#     (timeout, bad response) can't be explained by reading OUR code --
#     that part lives entirely inside a third party we have no
#     visibility into. But our own code has a real, discoverable gap:
#     /external calls a 5-second-delay endpoint with a 3-second timeout
#     (guaranteed to fail by construction, not really "flaky
#     dependency"), and call_external() never checks response.status_code
#     or validates the response is JSON before parsing it. AI here isn't
#     "investigate httpbin's internals" (impossible) -- it's "investigate
#     whether our own usage/defensive coding is adequate" (discoverable
#     by reading main.py).
#   - unhandled_exception: by definition, nobody anticipated this one --
#     there's no pre-written business-rule check or known failure mode.
#     The log shows exception type/message (what), almost never why.
#     This is the most genuinely organic AI-worthy case of all: every
#     other entry here was deliberately engineered or at least expected
#     to occur; this is whatever actually breaks that nobody thought of.
#   - background_task_failed: the proximate cause (fake_email_service
#     returned 500) is visible, but whether OUR OWN handling is adequate
#     -- does send_order_confirmation retry at all before giving up? --
#     requires reading main.py. Same "investigate our own usage, not the
#     dependency" category as external_api_timeout/error.
#   - email_service_unreachable: distinct from background_task_failed --
#     this isn't about retry logic on one call, it's about coordination.
#     monitor_email_service_health already knows the service is in a
#     confirmed outage (3 consecutive failures), but nothing connects
#     that knowledge to create_order, which keeps firing background
#     tasks at the dependency regardless. The gap is the missing
#     circuit breaker between two pieces of code that both exist but
#     don't talk to each other -- discoverable by reading main.py, not
#     by investigating why fake_email_service itself is unreliable.
# ============================================================
AI_WORTHY_EVENTS = {
    "negative_balance_detected",
    "analytics_failed",
    "external_api_timeout",
    "external_api_error",
    "unhandled_exception",
    "background_task_failed",
    "email_service_unreachable",
}

# Thresholds for probabilistic errors. Lowered from 3/5 -- the original
# values left every single occurrence below 3 completely silent (no
# detector response at all until the 3rd), which made the live UI feel
# unresponsive during testing/demos. 1/3 means the very first
# occurrence is at least visible as a warning, and 3 still matches the
# "two could be coincidence, three is a pattern" reasoning already used
# for CASCADE_CONFIRMATION_THRESHOLD below -- not an arbitrarily chosen
# number, the same logic just applied consistently to AI dispatch too.
WARNING_THRESHOLD = 1    # errors in window = worth watching
INCIDENT_THRESHOLD = 3   # errors in window = invoke AI
WINDOW_SECONDS = 60      # sliding window size
CASCADE_CONFIRMATION_THRESHOLD = 3

# An event being AI-worthy doesn't mean every single occurrence should
# call AI. Without this, a single burst (one real cascade test fired
# db_pool_exhausted 32 times) would dispatch one AI call per occurrence
# past the threshold -- all diagnosing the identical root cause. This
# gates *dispatch*, not detection: every occurrence still gets flagged
# and logged exactly as before, only repeat AI calls within the
# cooldown for the same event/cascade get skipped.
AI_COOLDOWN_SECONDS = 120


@dataclass
class ErrorEvent:
    """
    Represents a single error event with full context.
    Typed dataclass — no loose dicts passed around the system.
    """
    event: str
    level: str
    timestamp: str
    wall_time: float
    error_class: str  # "immediate" or "threshold"
    context: dict = field(default_factory=dict)


@dataclass
class Incident:
    """
    A confirmed incident that requires AI investigation.

    Why a separate dataclass?
    Clean contract between error detector and AI reasoning engine.
    The AI receives an Incident object — fully typed, fully contexted.
    No ambiguity about what fields exist.
    """
    trigger_event: ErrorEvent
    error_count: int
    window_seconds: int
    severity: str        # "immediate", "warning", "critical"
    pattern: Optional[str]
    context_window: list
    requires_ai: bool    # should AI reasoning engine be invoked right now?
    ai_worthy: bool = False  # is this event/cascade AI-worthy at all,
                             # ignoring cooldown? lets callers distinguish
                             # "never AI-worthy" from "AI-worthy but
                             # suppressed by cooldown this time"
    cascade_peer_event: Optional["ErrorEvent"] = None  # upstream event when cascade confirmed


class ErrorDetector:
    """
    Two-mode stateful error detector.

    Mode 1 — Immediate: flags deterministic errors instantly.
    Mode 2 — Threshold: flags probabilistic errors after pattern.

    Why stateful?
    Threshold detection requires memory across multiple events.
    A function that only sees one log line at a time
    can never detect frequency or velocity.
    State is what separates detection from observation.
    """

    def __init__(self):
        # Sliding window for threshold errors
        self.error_window: deque = deque()

        # Per-event-type counts for pattern analysis
        self.error_counts: defaultdict = defaultdict(int)

        # Last error for cascade detection
        self.last_error: Optional[ErrorEvent] = None

        # Cascade pattern tracking
        self.cascade_counts: defaultdict = defaultdict(int)
        self.confirmed_cascades: set = set()

        # Stats
        self.incident_count: int = 0
        self.immediate_count: int = 0
        self.threshold_count: int = 0

        # AI dispatch cooldown -- keyed by cascade pattern when one's
        # confirmed, otherwise by event name. Only set when AI is
        # actually dispatched, not on every suppressed attempt, so the
        # next occurrence after the cooldown expires triggers AI again.
        self.last_ai_call: dict = {}
        self.ai_calls_suppressed: int = 0

    def _classify_error(self, event_name: str) -> str:
        """
        Classify error as immediate or threshold.

        If unknown — default to immediate.
        Why? Unknown errors are unpredictable.
        Better to over-alert on unknowns than miss a real incident.
        This is where AI will eventually replace hardcoded sets.
        """
        if event_name in IMMEDIATE_ERRORS:
            return "immediate"
        elif event_name in THRESHOLD_ERRORS:
            return "threshold"
        else:
            # Unknown error type — treat as immediate
            # AI reasoning engine will classify it properly
            return "immediate"

    def _clean_window(self):
        """Remove errors outside sliding window. O(1) with deque."""
        now = time.time()
        cutoff = now - WINDOW_SECONDS
        while self.error_window and self.error_window[0] < cutoff:
            self.error_window.popleft()

    def _current_error_rate(self) -> int:
        self._clean_window()
        return len(self.error_window)

    def _detect_cascade(self, current: ErrorEvent) -> Optional[str]:
        """
        Detects confirmed cascade patterns — one error reliably causing another.

        Why confirmation threshold?
        Two different errors close together could be coincidence.
        The same pair appearing 3+ times is a pattern — one is causing the other.
        Single occurrences are noise. Repeated patterns are signal.

        AI determines whether a confirmed cascade is real causation or coincidence —
        the detector's job is just to surface the pattern reliably.
        """
        if not self.last_error:
            return None

        time_diff = current.wall_time - self.last_error.wall_time

        if time_diff <= 30 and self.last_error.event != current.event:
            pattern = f"{self.last_error.event} → {current.event}"

            # Canonical key treats A→B and B→A as the same pair so both
            # directions count toward the same threshold and share a cooldown.
            # Without this, concurrent bursts that sometimes fire A first and
            # sometimes B first confirm two separate cascades and dispatch two
            # separate AI calls for the exact same event pair.
            canonical = " ↔ ".join(sorted([self.last_error.event, current.event]))

            self.cascade_counts[canonical] += 1

            if self.cascade_counts[canonical] >= CASCADE_CONFIRMATION_THRESHOLD:
                self.confirmed_cascades.add(canonical)
                return pattern

        return None

    @staticmethod
    def _requires_ai(event_name: str, cascade: Optional[str]) -> bool:
        """
        True only when the log line alone doesn't already contain the
        root cause. See AI_WORTHY_EVENTS above for why.
        """
        return event_name in AI_WORTHY_EVENTS or cascade is not None

    def _should_call_ai(
        self,
        event_name: str,
        cascade: Optional[str],
        wall_time: float
    ) -> bool:
        """
        Gates AI *dispatch*, separate from AI-worthiness. A cascade
        pattern is keyed by the pattern string, not the triggering
        event name -- two different cascades are genuinely different
        incidents and shouldn't share a cooldown.
        """
        if not self._requires_ai(event_name, cascade):
            return False

        if cascade:
            key = " ↔ ".join(sorted([p.strip() for p in cascade.split("→")]))
        else:
            key = event_name
        last_call = self.last_ai_call.get(key)

        if last_call is not None and wall_time - last_call < AI_COOLDOWN_SECONDS:
            self.ai_calls_suppressed += 1
            return False

        self.last_ai_call[key] = wall_time
        return True

    def _handle_immediate(
        self,
        error_event: ErrorEvent,
        context_window: list
    ) -> Incident:
        """
        Immediate errors always produce an incident.
        No threshold check needed.
        """
        self.immediate_count += 1
        self.incident_count += 1
        # Capture upstream event BEFORE _detect_cascade runs (it reads self.last_error)
        upstream = self.last_error
        cascade = self._detect_cascade(error_event)
        ai_worthy = self._requires_ai(error_event.event, cascade)

        return Incident(
            trigger_event=error_event,
            error_count=1,
            window_seconds=0,
            severity="immediate",
            pattern=cascade,
            context_window=context_window,
            requires_ai=ai_worthy and self._should_call_ai(error_event.event, cascade, error_event.wall_time),
            ai_worthy=ai_worthy,
            cascade_peer_event=upstream if cascade else None,
        )

    def _handle_threshold(
        self,
        error_event: ErrorEvent,
        context_window: list
    ) -> Optional[Incident]:
        """
        Threshold errors only produce incidents after pattern confirmed.
        Returns None if below threshold — just noise.
        """
        now = time.time()
        self.error_window.append(now)
        error_count = self._current_error_rate()
        # Capture upstream event BEFORE _detect_cascade runs (it reads self.last_error)
        upstream = self.last_error
        cascade = self._detect_cascade(error_event)

        if error_count >= INCIDENT_THRESHOLD:
            self.threshold_count += 1
            self.incident_count += 1
            ai_worthy = self._requires_ai(error_event.event, cascade)
            return Incident(
                trigger_event=error_event,
                error_count=error_count,
                window_seconds=WINDOW_SECONDS,
                severity="critical",
                pattern=cascade,
                context_window=context_window,
                requires_ai=ai_worthy and self._should_call_ai(error_event.event, cascade, error_event.wall_time),
                ai_worthy=ai_worthy,
                cascade_peer_event=upstream if cascade else None,
            )
        elif error_count >= WARNING_THRESHOLD:
            return Incident(
                trigger_event=error_event,
                error_count=error_count,
                window_seconds=WINDOW_SECONDS,
                severity="warning",
                pattern=cascade,
                context_window=context_window,
                requires_ai=False,  # warning — watch but don't invoke AI yet
                ai_worthy=self._requires_ai(error_event.event, cascade),
                cascade_peer_event=upstream if cascade else None,
            )
        return None

    def process_error(
        self,
        log_entry: dict,
        context_window: list
    ) -> Optional[Incident]:
        """
        Main entry point. Called by log collector for every error.
        Returns Incident if thresholds crossed, None if noise.
        """
        now = time.time()
        event_name = log_entry.get("event", "unknown")
        error_class = self._classify_error(event_name)

        error_event = ErrorEvent(
            event=event_name,
            level=log_entry.get("level", "error"),
            timestamp=log_entry.get("timestamp", ""),
            wall_time=now,
            error_class=error_class,
            context={
                k: v for k, v in log_entry.items()
                if k not in {"event", "level", "timestamp", "_raw"}
            }
        )

        self.error_counts[event_name] += 1

        if error_class == "immediate":
            result = self._handle_immediate(error_event, context_window)
        else:
            result = self._handle_threshold(error_event, context_window)

        # Updated only after cascade detection has compared error_event
        # against the *previous* last_error. Setting this earlier (as it
        # used to be) made every event its own "last_error" before the
        # comparison ran, so the != check could never be true and no
        # cascade was ever confirmed, in any iteration, until this fix.
        self.last_error = error_event
        return result

    def get_stats(self) -> dict:
        """Current detector stats — feeds into dashboard in Week 3."""
        return {
            "errors_in_window": self._current_error_rate(),
            "total_incidents": self.incident_count,
            "immediate_incidents": self.immediate_count,
            "threshold_incidents": self.threshold_count,
            "error_type_counts": dict(self.error_counts),
            "confirmed_cascades": list(self.confirmed_cascades),
            "cascade_candidates": {
                k: v for k, v in self.cascade_counts.items()
                if v < CASCADE_CONFIRMATION_THRESHOLD
            },
            "ai_calls_suppressed": self.ai_calls_suppressed,
            "thresholds": {
                "warning": WARNING_THRESHOLD,
                "incident": INCIDENT_THRESHOLD,
                "window_seconds": WINDOW_SECONDS,
                "ai_cooldown_seconds": AI_COOLDOWN_SECONDS,
            }
        }