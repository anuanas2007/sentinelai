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
}

THRESHOLD_ERRORS = {
    "external_api_timeout",
    "analytics_failed",
    "db_pool_exhausted",
    "db_deadlock",
    "db_connection_error",
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
# A confirmed cascade is also AI-worthy regardless of which events
# it involves — "is this real causation or coincidence" is itself a
# genuine hypothesis question, not something the detector can answer.
# ============================================================
AI_WORTHY_EVENTS = {
    "negative_balance_detected",
    "analytics_failed",
}

# Thresholds for probabilistic errors
WARNING_THRESHOLD = 3    # errors in window = worth watching
INCIDENT_THRESHOLD = 5   # errors in window = invoke AI
WINDOW_SECONDS = 60      # sliding window size
CASCADE_CONFIRMATION_THRESHOLD = 3


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
    requires_ai: bool    # should AI reasoning engine be invoked?


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

        This is the same principle behind time-series anomaly detection —
        never trust a single data point.
        """
        if not self.last_error:
            return None

        time_diff = current.wall_time - self.last_error.wall_time

        if time_diff <= 30 and self.last_error.event != current.event:
            pattern = f"{self.last_error.event} → {current.event}"

            # Increment occurrence count for this pair
            self.cascade_counts[pattern] += 1

            # Only confirm after seen 3+ times
            if self.cascade_counts[pattern] >= CASCADE_CONFIRMATION_THRESHOLD:
                self.confirmed_cascades.add(pattern)
                return pattern

        return None

    @staticmethod
    def _requires_ai(event_name: str, cascade: Optional[str]) -> bool:
        """
        True only when the log line alone doesn't already contain the
        root cause. See AI_WORTHY_EVENTS above for why.
        """
        return event_name in AI_WORTHY_EVENTS or cascade is not None

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
        cascade = self._detect_cascade(error_event)

        return Incident(
            trigger_event=error_event,
            error_count=1,
            window_seconds=0,
            severity="immediate",
            pattern=cascade,
            context_window=context_window,
            requires_ai=self._requires_ai(error_event.event, cascade)
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
        cascade = self._detect_cascade(error_event)

        if error_count >= INCIDENT_THRESHOLD:
            self.threshold_count += 1
            self.incident_count += 1
            return Incident(
                trigger_event=error_event,
                error_count=error_count,
                window_seconds=WINDOW_SECONDS,
                severity="critical",
                pattern=cascade,
                context_window=context_window,
                requires_ai=self._requires_ai(error_event.event, cascade)
            )
        elif error_count >= WARNING_THRESHOLD:
            return Incident(
                trigger_event=error_event,
                error_count=error_count,
                window_seconds=WINDOW_SECONDS,
                severity="warning",
                pattern=cascade,
                context_window=context_window,
                requires_ai=False  # warning — watch but don't invoke AI yet
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
        self.last_error = error_event

        if error_class == "immediate":
            return self._handle_immediate(error_event, context_window)
        else:
            return self._handle_threshold(error_event, context_window)

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
            "thresholds": {
                "warning": WARNING_THRESHOLD,
                "incident": INCIDENT_THRESHOLD,
                "window_seconds": WINDOW_SECONDS,
            }
        }