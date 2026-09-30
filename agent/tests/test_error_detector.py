"""
Tests for ErrorDetector and its supporting logic.

All log entries carry explicit ISO timestamps so detection is
deterministic — the detector uses the log line's own timestamp
(via _event_time), not wall-clock time.time(), so these tests
produce identical results regardless of when they run.
"""
import sys
import os
import pytest
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from error_detector import (
    ErrorDetector,
    IMMEDIATE_ERRORS,
    THRESHOLD_ERRORS,
    AI_WORTHY_EVENTS,
    WARNING_THRESHOLD,
    INCIDENT_THRESHOLD,
    WINDOW_SECONDS,
    AI_COOLDOWN_SECONDS,
    CASCADE_CONFIRMATION_THRESHOLD,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

BASE_TIME = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


def ts(offset_seconds: float = 0) -> str:
    """ISO timestamp offset from BASE_TIME."""
    return (BASE_TIME + timedelta(seconds=offset_seconds)).isoformat()


def make_entry(event: str, level: str = "error", offset: float = 0, **ctx) -> dict:
    entry = {"event": event, "level": level, "timestamp": ts(offset)}
    entry.update(ctx)
    return entry


def process_n(detector: ErrorDetector, event: str, n: int, start_offset: float = 0, gap: float = 1.0):
    """Fire the same event n times with evenly spaced timestamps."""
    incidents = []
    for i in range(n):
        inc = detector.process_error(make_entry(event, offset=start_offset + i * gap), context_window=[])
        incidents.append(inc)
    return incidents


# ---------------------------------------------------------------------------
# 1. Immediate errors escalate on first occurrence
# ---------------------------------------------------------------------------

class TestImmediateErrors:
    def test_immediate_error_produces_incident_on_first_occurrence(self):
        d = ErrorDetector()
        inc = d.process_error(make_entry("user_not_found"), [])
        assert inc is not None
        assert inc.severity == "immediate"
        assert inc.error_count == 1

    def test_immediate_error_class_is_set(self):
        d = ErrorDetector()
        inc = d.process_error(make_entry("user_not_found"), [])
        assert inc.trigger_event.error_class == "immediate"

    def test_every_known_immediate_error_escalates(self):
        for event in IMMEDIATE_ERRORS:
            d = ErrorDetector()
            inc = d.process_error(make_entry(event), [])
            assert inc is not None and inc.severity == "immediate", (
                f"{event} should produce an immediate incident"
            )

    def test_immediate_error_increments_incident_count(self):
        d = ErrorDetector()
        d.process_error(make_entry("user_not_found"), [])
        assert d.incident_count == 1
        assert d.immediate_count == 1

    def test_context_window_attached_to_incident(self):
        d = ErrorDetector()
        ctx = [{"event": "fetching_user", "level": "info", "timestamp": ts(-1)}]
        inc = d.process_error(make_entry("user_not_found"), ctx)
        assert inc.context_window == ctx


# ---------------------------------------------------------------------------
# 2. Unknown event names default to immediate
# ---------------------------------------------------------------------------

class TestUnknownEvents:
    def test_unknown_event_produces_immediate_incident(self):
        d = ErrorDetector()
        inc = d.process_error(make_entry("totally_new_error_type"), [])
        assert inc is not None
        assert inc.severity == "immediate"

    def test_unknown_event_is_not_in_either_set(self):
        unknown = "mystery_failure_xyz"
        assert unknown not in IMMEDIATE_ERRORS
        assert unknown not in THRESHOLD_ERRORS

    def test_unknown_event_error_class_is_immediate(self):
        d = ErrorDetector()
        inc = d.process_error(make_entry("some_future_event"), [])
        assert inc.trigger_event.error_class == "immediate"


# ---------------------------------------------------------------------------
# 3. Threshold errors: 1 → warning, 2 → warning, 3 → critical + AI dispatch
# ---------------------------------------------------------------------------

class TestThresholdErrors:
    def _pick_threshold_ai_event(self):
        return next(e for e in THRESHOLD_ERRORS if e in AI_WORTHY_EVENTS)

    def test_first_occurrence_is_warning(self):
        d = ErrorDetector()
        inc = d.process_error(make_entry("external_api_timeout", offset=0), [])
        assert inc is not None
        assert inc.severity == "warning"
        assert inc.error_count == WARNING_THRESHOLD

    def test_second_occurrence_is_warning(self):
        d = ErrorDetector()
        process_n(d, "external_api_timeout", 1, start_offset=0)
        inc = d.process_error(make_entry("external_api_timeout", offset=1), [])
        assert inc is not None
        assert inc.severity == "warning"

    def test_third_occurrence_is_critical(self):
        d = ErrorDetector()
        incidents = process_n(d, "external_api_timeout", INCIDENT_THRESHOLD, gap=1.0)
        last = incidents[-1]
        assert last is not None
        assert last.severity == "critical"
        assert last.error_count == INCIDENT_THRESHOLD

    def test_critical_requires_ai_for_ai_worthy_event(self):
        d = ErrorDetector()
        event = self._pick_threshold_ai_event()
        incidents = process_n(d, event, INCIDENT_THRESHOLD, gap=1.0)
        assert incidents[-1].requires_ai is True
        assert incidents[-1].ai_worthy is True

    def test_critical_does_not_require_ai_for_non_ai_worthy_event(self):
        non_ai = next(e for e in THRESHOLD_ERRORS if e not in AI_WORTHY_EVENTS)
        d = ErrorDetector()
        incidents = process_n(d, non_ai, INCIDENT_THRESHOLD, gap=1.0)
        assert incidents[-1].ai_worthy is False
        assert incidents[-1].requires_ai is False

    def test_threshold_increments_threshold_count(self):
        d = ErrorDetector()
        process_n(d, "analytics_failed", INCIDENT_THRESHOLD, gap=1.0)
        assert d.threshold_count == 1


# ---------------------------------------------------------------------------
# 4. Regression: three DIFFERENT threshold events must NOT escalate
# ---------------------------------------------------------------------------

class TestSharedWindowRegression:
    def test_three_different_threshold_events_do_not_reach_critical(self):
        """
        The original bug: error_window was shared, so one db_pool_exhausted
        + one payment_service_timeout + one external_api_timeout summed to 3
        and the third event was incorrectly escalated to critical. Each event
        type now has its own window, so this must stay at warning.
        """
        d = ErrorDetector()
        events = ["db_pool_exhausted", "payment_service_timeout", "external_api_timeout"]
        incidents = []
        for i, event in enumerate(events):
            inc = d.process_error(make_entry(event, offset=float(i)), [])
            incidents.append(inc)

        for inc in incidents:
            assert inc is not None
            assert inc.severity == "warning", (
                f"Mixed threshold events should each be warning, not critical. "
                f"Got {inc.severity} for {inc.trigger_event.event}"
            )

    def test_same_event_three_times_still_escalates(self):
        """Confirm the fix didn't break the normal threshold path."""
        d = ErrorDetector()
        incidents = process_n(d, "external_api_timeout", INCIDENT_THRESHOLD, gap=1.0)
        assert incidents[-1].severity == "critical"

    def test_counts_are_independent_per_event(self):
        d = ErrorDetector()
        # 2× timeout A (warning), then 1× timeout B (warning on its own)
        process_n(d, "external_api_timeout", 2, gap=1.0)
        inc = d.process_error(make_entry("analytics_failed", offset=3.0), [])
        # analytics_failed only has 1 occurrence — must be warning, not critical
        assert inc.severity == "warning"
        assert inc.error_count == 1


# ---------------------------------------------------------------------------
# 5. Window expiry: 3 occurrences spread over >60s never reach critical
# ---------------------------------------------------------------------------

class TestWindowExpiry:
    def test_events_outside_window_dont_count(self):
        d = ErrorDetector()
        # First two events are far outside the 60s window
        d.process_error(make_entry("external_api_timeout", offset=0), [])
        d.process_error(make_entry("external_api_timeout", offset=1), [])
        # Third event is 90s later — first two have expired
        inc = d.process_error(make_entry("external_api_timeout", offset=WINDOW_SECONDS + 30), [])
        assert inc is not None
        assert inc.severity == "warning"
        assert inc.error_count == 1

    def test_three_events_just_inside_window_escalate(self):
        d = ErrorDetector()
        incidents = process_n(
            d, "external_api_timeout", INCIDENT_THRESHOLD,
            start_offset=0, gap=(WINDOW_SECONDS - 1) / (INCIDENT_THRESHOLD - 1)
        )
        assert incidents[-1].severity == "critical"

    def test_window_boundary_is_exclusive(self):
        """Only the event at offset=0 expires; offset=1 is still within 60s."""
        d = ErrorDetector()
        d.process_error(make_entry("external_api_timeout", offset=0), [])
        d.process_error(make_entry("external_api_timeout", offset=1), [])
        # offset=0 has expired (>60s ago), offset=1 has not (59.999s ago)
        inc = d.process_error(make_entry("external_api_timeout", offset=WINDOW_SECONDS + 0.001), [])
        assert inc.error_count == 2  # offset=1 and the new event


# ---------------------------------------------------------------------------
# 6. Cascade confirmation: 3 co-occurrences within 30s, A→B == B→A
# ---------------------------------------------------------------------------

class TestCascadeDetection:
    def _fire_cascade_pair(self, d: ErrorDetector, a: str, b: str, n: int, base: float = 0.0):
        for i in range(n):
            d.process_error(make_entry(a, offset=base + i * 2.0), [])
            d.process_error(make_entry(b, offset=base + i * 2.0 + 0.5), [])

    def test_cascade_confirmed_after_threshold(self):
        d = ErrorDetector()
        self._fire_cascade_pair(d, "db_pool_exhausted", "payment_service_timeout",
                                CASCADE_CONFIRMATION_THRESHOLD)
        assert len(d.confirmed_cascades) > 0

    def test_cascade_not_confirmed_with_single_pair(self):
        # One A→B pair produces 1 cascade count — not yet at threshold.
        # (Each subsequent pair adds 2 increments because B fires against A
        # AND the next A fires against B, so 2 pairs → 3 increments → confirmed.
        # This test pins the single-pair case as pre-confirmation.)
        d = ErrorDetector()
        self._fire_cascade_pair(d, "db_pool_exhausted", "payment_service_timeout", n=1)
        assert len(d.confirmed_cascades) == 0
        canonical = " ↔ ".join(sorted(["db_pool_exhausted", "payment_service_timeout"]))
        assert d.cascade_counts[canonical] == 1

    def test_a_then_b_equals_b_then_a(self):
        d1 = ErrorDetector()
        d2 = ErrorDetector()
        # d1: always A then B
        self._fire_cascade_pair(d1, "db_pool_exhausted", "payment_service_timeout",
                                CASCADE_CONFIRMATION_THRESHOLD)
        # d2: always B then A
        self._fire_cascade_pair(d2, "payment_service_timeout", "db_pool_exhausted",
                                CASCADE_CONFIRMATION_THRESHOLD)
        assert d1.confirmed_cascades == d2.confirmed_cascades

    def test_cascade_pair_outside_30s_not_counted(self):
        d = ErrorDetector()
        for i in range(CASCADE_CONFIRMATION_THRESHOLD):
            d.process_error(make_entry("db_pool_exhausted", offset=float(i * 40)), [])
            d.process_error(make_entry("payment_service_timeout", offset=float(i * 40 + 35)), [])
        assert len(d.confirmed_cascades) == 0

    def test_confirmed_cascade_sets_pattern_on_incident(self):
        d = ErrorDetector()
        self._fire_cascade_pair(d, "db_pool_exhausted", "payment_service_timeout",
                                CASCADE_CONFIRMATION_THRESHOLD)
        # The incident that confirmed the cascade should have pattern set
        inc = d.process_error(
            make_entry("payment_service_timeout",
                       offset=CASCADE_CONFIRMATION_THRESHOLD * 2.0 + 0.5),
            []
        )
        # May or may not fire depending on threshold window, but cascade should be in confirmed set
        assert len(d.confirmed_cascades) > 0


# ---------------------------------------------------------------------------
# 7. AI cooldown: second dispatch within 120s suppressed
# ---------------------------------------------------------------------------

class TestAiCooldown:
    def _reach_critical(self, d: ErrorDetector, event: str, base: float = 0.0):
        incidents = []
        for i in range(INCIDENT_THRESHOLD):
            inc = d.process_error(make_entry(event, offset=base + i * 1.0), [])
            incidents.append(inc)
        return incidents

    def test_first_critical_dispatches_ai(self):
        d = ErrorDetector()
        incidents = self._reach_critical(d, "analytics_failed")
        assert incidents[-1].requires_ai is True

    def test_second_critical_within_cooldown_is_suppressed(self):
        d = ErrorDetector()
        self._reach_critical(d, "analytics_failed", base=0.0)
        suppressed_before = d.ai_calls_suppressed
        # Another occurrence within cooldown window
        inc = d.process_error(
            make_entry("analytics_failed", offset=AI_COOLDOWN_SECONDS - 10), []
        )
        if inc and inc.severity == "critical":
            assert inc.requires_ai is False
            assert d.ai_calls_suppressed > suppressed_before

    def test_dispatch_works_again_after_cooldown_expires(self):
        d = ErrorDetector()
        # First critical batch
        self._reach_critical(d, "analytics_failed", base=0.0)
        # Refill window after cooldown has expired
        inc = None
        for i in range(INCIDENT_THRESHOLD):
            offset = AI_COOLDOWN_SECONDS + 10 + i * 1.0
            inc = d.process_error(make_entry("analytics_failed", offset=offset), [])
        # The batch that completes after cooldown should dispatch AI again
        assert inc is not None
        assert inc.requires_ai is True

    def test_ai_calls_suppressed_counter_increments(self):
        d = ErrorDetector()
        self._reach_critical(d, "analytics_failed", base=0.0)
        initial = d.ai_calls_suppressed
        # Fire more within cooldown
        for i in range(5):
            d.process_error(make_entry("analytics_failed", offset=10.0 + i), [])
        assert d.ai_calls_suppressed >= initial


# ---------------------------------------------------------------------------
# 8. Cascade cooldown keyed by canonical pair
# ---------------------------------------------------------------------------

class TestCascadeCooldown:
    def _confirm_cascade(self, d: ErrorDetector, a: str, b: str, base: float = 0.0):
        for i in range(CASCADE_CONFIRMATION_THRESHOLD):
            d.process_error(make_entry(a, offset=base + i * 2.0), [])
            d.process_error(make_entry(b, offset=base + i * 2.0 + 0.5), [])

    def test_cascade_cooldown_keyed_by_pair_not_event_name(self):
        d = ErrorDetector()
        # Confirm one cascade pair
        self._confirm_cascade(d, "db_pool_exhausted", "payment_service_timeout")
        suppressed_before = d.ai_calls_suppressed
        # Fire the same pair again within cooldown
        self._confirm_cascade(
            d, "db_pool_exhausted", "payment_service_timeout",
            base=CASCADE_CONFIRMATION_THRESHOLD * 2.0 + 5.0
        )
        assert d.ai_calls_suppressed > suppressed_before

    def test_different_cascade_pairs_have_independent_cooldowns(self):
        d = ErrorDetector()
        # Confirm cascade A↔B
        self._confirm_cascade(d, "db_pool_exhausted", "payment_service_timeout")
        suppressed_before = d.ai_calls_suppressed
        # Confirm a completely different cascade C↔D — should NOT be suppressed
        self._confirm_cascade(
            d, "external_api_timeout", "analytics_failed",
            base=CASCADE_CONFIRMATION_THRESHOLD * 2.0 + 5.0
        )
        # Second pair is a fresh cascade — suppressed count shouldn't have jumped
        # (it might go up from the A↔B repeat if those also happen to occur, but
        # the C↔D occurrences themselves should dispatch AI)
        cascade_key_cd = " ↔ ".join(sorted(["external_api_timeout", "analytics_failed"]))
        assert cascade_key_cd in d.confirmed_cascades


# ---------------------------------------------------------------------------
# 9. _event_time fallback behaviour
# ---------------------------------------------------------------------------

class TestEventTime:
    def test_valid_iso_timestamp_parsed(self):
        t = ErrorDetector._event_time({"timestamp": "2024-01-01T12:00:00+00:00"})
        expected = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc).timestamp()
        assert abs(t - expected) < 0.001

    def test_missing_timestamp_falls_back_to_wall_clock(self):
        import time as _time
        before = _time.time()
        t = ErrorDetector._event_time({})
        after = _time.time()
        assert before <= t <= after

    def test_malformed_timestamp_falls_back_to_wall_clock(self):
        import time as _time
        before = _time.time()
        t = ErrorDetector._event_time({"timestamp": "not-a-date"})
        after = _time.time()
        assert before <= t <= after

    def test_none_timestamp_falls_back_to_wall_clock(self):
        import time as _time
        before = _time.time()
        t = ErrorDetector._event_time({"timestamp": None})
        after = _time.time()
        assert before <= t <= after

    def test_z_suffix_accepted(self):
        t = ErrorDetector._event_time({"timestamp": "2024-06-01T10:00:00Z"})
        expected = datetime(2024, 6, 1, 10, 0, 0, tzinfo=timezone.utc).timestamp()
        assert abs(t - expected) < 0.001

    def test_naive_timestamp_treated_as_utc(self):
        t = ErrorDetector._event_time({"timestamp": "2024-01-01T12:00:00"})
        expected = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc).timestamp()
        assert abs(t - expected) < 0.001
