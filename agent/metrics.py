"""
Prometheus metrics for SentinelAI's AI pipeline.

All metric objects live here so every other module imports from one
place rather than each defining its own. Prometheus_client raises if
you register the same metric name twice in the same process, so
centralising them is not just tidy, it's required.
"""
from prometheus_client import Counter, Histogram

errors_total = Counter(
    "sentinelai_errors_total",
    "Every error log line seen by the collector",
    ["event_type"],
)

incidents_total = Counter(
    "sentinelai_incidents_total",
    "Confirmed incidents produced by the detector",
    ["event_type", "severity"],
)

ai_dispatches_total = Counter(
    "sentinelai_ai_dispatches_total",
    "AI analysis calls actually queued (after cooldown gate)",
    ["event_type"],
)

ai_cooldowns_total = Counter(
    "sentinelai_ai_cooldowns_total",
    "AI dispatch attempts suppressed by the cooldown",
)

cascade_confirmations_total = Counter(
    "sentinelai_cascade_confirmations_total",
    "Cascade patterns confirmed by the detector",
    ["canonical_pair"],
)

fix_ratings_total = Counter(
    "sentinelai_fix_ratings_total",
    "Human ratings submitted for a proposed fix",
    ["event_type", "rating"],
)

vector_memory_hits_total = Counter(
    "sentinelai_vector_memory_hits_total",
    "get_similar_incidents calls, labelled by whether a match was found",
    ["found"],  # "true" or "false"
)

pipeline_duration_seconds = Histogram(
    "sentinelai_pipeline_duration_seconds",
    "Time taken per pipeline stage",
    ["stage"],  # "detection", "ai_dispatch_wait", "investigation", "fix_proposal", "end_to_end"
    buckets=[0.1, 0.5, 1, 2, 5, 10, 30, 60, 120, 300],
)
