"""LLM usage aggregation across a run's parent session and all sub-sessions.

Reads the events.jsonl files emitted by amplifier-core (the same files the
context-intelligence hook produces) and aggregates token counts, request
counts, and timings. Walks every events.jsonl file under the pulled
`session/` artifact directory, so it captures the parent session AND every
sub-session spawned via `delegate`, recipes, fork-skills, etc., without
needing to know the spawning mechanism — every sub-session writes to the
same `~/.amplifier/projects/<slug>/sessions/<id>/events.jsonl` layout, and
the harness's file-pull preserves it.

The schema is minimal-surface: only the `event == "llm:response"` lines
are consumed, and only the `data.usage.*` and `duration_ms` fields are
read. Unknown providers/models pass through transparently into the
per-model breakdown — no Anthropic-specific assumptions beyond field
names that loop-streaming normalizes for every provider.
"""

from __future__ import annotations

import dataclasses
import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ModelUsage:
    """Per-model breakdown so we can compare Anthropic vs OpenAI vs local easily."""

    requests: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    llm_time_ms: int = 0


@dataclass
class UsageMetrics:
    """LLM usage aggregated across the parent session and all sub-sessions of one run."""

    # Counts
    request_count: int = 0
    error_count: int = 0
    session_count: int = 0  # number of distinct sessions found (parent + every sub)
    parsed_event_lines: int = 0  # for diagnostics

    # Token totals (across the whole tree)
    input_tokens_total: int = 0
    output_tokens_total: int = 0
    cache_read_tokens_total: int = 0
    cache_write_tokens_total: int = 0

    # High-water marks: the largest single LLM call we saw in any session
    max_call_input_tokens: int = 0
    max_call_output_tokens: int = 0
    # input + output for one call (the "billable" tokens for that turn)
    max_call_combined_tokens: int = 0
    # input + output + cache_read + cache_write for one call (full payload size)
    max_call_total_tokens: int = 0

    # Time
    llm_time_ms_total: int = 0
    llm_time_ms_max: int = 0

    # Per-model breakdown — keyed by "provider/model" so cross-provider runs
    # don't collide if two providers ship the same model name.
    by_model: dict[str, ModelUsage] = field(default_factory=dict)

    # ------------------------------------------------------------------ derived

    @property
    def billable_tokens_total(self) -> int:
        """input + output across the whole tree (excludes cache to avoid double-counting)."""
        return self.input_tokens_total + self.output_tokens_total

    @property
    def total_tokens_with_cache(self) -> int:
        """All four columns summed — the actual transferred payload size."""
        return (
            self.input_tokens_total
            + self.output_tokens_total
            + self.cache_read_tokens_total
            + self.cache_write_tokens_total
        )

    @property
    def input_tokens_avg(self) -> float:
        return self.input_tokens_total / self.request_count if self.request_count else 0.0

    @property
    def output_tokens_avg(self) -> float:
        return self.output_tokens_total / self.request_count if self.request_count else 0.0

    @property
    def billable_tokens_avg(self) -> float:
        return self.billable_tokens_total / self.request_count if self.request_count else 0.0

    @property
    def llm_time_ms_avg(self) -> float:
        return self.llm_time_ms_total / self.request_count if self.request_count else 0.0

    # ------------------------------------------------------------------ to_dict

    def to_dict(self) -> dict:
        """JSON-serialisable form — used by results.write_run_artifacts."""
        return {
            "request_count": self.request_count,
            "error_count": self.error_count,
            "session_count": self.session_count,
            "parsed_event_lines": self.parsed_event_lines,
            "input_tokens_total": self.input_tokens_total,
            "output_tokens_total": self.output_tokens_total,
            "cache_read_tokens_total": self.cache_read_tokens_total,
            "cache_write_tokens_total": self.cache_write_tokens_total,
            "billable_tokens_total": self.billable_tokens_total,
            "total_tokens_with_cache": self.total_tokens_with_cache,
            "max_call_input_tokens": self.max_call_input_tokens,
            "max_call_output_tokens": self.max_call_output_tokens,
            "max_call_combined_tokens": self.max_call_combined_tokens,
            "max_call_total_tokens": self.max_call_total_tokens,
            "llm_time_ms_total": self.llm_time_ms_total,
            "llm_time_ms_max": self.llm_time_ms_max,
            "llm_time_ms_avg": round(self.llm_time_ms_avg, 1),
            "input_tokens_avg": round(self.input_tokens_avg, 1),
            "output_tokens_avg": round(self.output_tokens_avg, 1),
            "billable_tokens_avg": round(self.billable_tokens_avg, 1),
            "by_model": {k: dataclasses.asdict(v) for k, v in self.by_model.items()},
        }


# ---------------------------------------------------------------------------
# Collection
# ---------------------------------------------------------------------------


def _coerce_int(v: object) -> int:
    """Defensive integer coerce — providers occasionally emit None for unset usage fields."""
    if v is None:
        return 0
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def collect_usage(session_dir: Path) -> UsageMetrics:
    """Walk every events.jsonl under ``session_dir`` and aggregate LLM usage.

    ``session_dir`` is the per-run pulled artifact directory (the harness's
    `<run_dir>/session/` after `_file_pull_session`). We rglob for
    `events.jsonl` to pick up the parent session AND every sub-session
    spawned via delegate / recipes / fork-skills — they all live under
    `projects/<slug>/sessions/<id>/events.jsonl`.

    Returns an empty UsageMetrics if no events.jsonl is found or no
    `llm:response` events appear.
    """
    metrics = UsageMetrics()
    if not session_dir.exists():
        return metrics

    sessions_seen: set[Path] = set()

    for events_file in session_dir.rglob("events.jsonl"):
        sessions_seen.add(events_file.parent)
        try:
            with events_file.open("r", encoding="utf-8", errors="replace") as f:
                for raw_line in f:
                    line = raw_line.strip()
                    if not line:
                        continue
                    metrics.parsed_event_lines += 1
                    try:
                        evt = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if evt.get("event") != "llm:response":
                        continue

                    status = evt.get("status", "ok")
                    data = evt.get("data") or {}
                    if status != "ok":
                        metrics.error_count += 1
                        continue

                    duration_ms = _coerce_int(evt.get("duration_ms"))
                    usage = data.get("usage") or {}
                    inp = _coerce_int(usage.get("input_tokens"))
                    out = _coerce_int(usage.get("output_tokens"))
                    cr = _coerce_int(usage.get("cache_read_tokens"))
                    cw = _coerce_int(usage.get("cache_write_tokens"))
                    combined = inp + out
                    total = combined + cr + cw

                    metrics.request_count += 1
                    metrics.input_tokens_total += inp
                    metrics.output_tokens_total += out
                    metrics.cache_read_tokens_total += cr
                    metrics.cache_write_tokens_total += cw
                    metrics.llm_time_ms_total += duration_ms

                    if inp > metrics.max_call_input_tokens:
                        metrics.max_call_input_tokens = inp
                    if out > metrics.max_call_output_tokens:
                        metrics.max_call_output_tokens = out
                    if combined > metrics.max_call_combined_tokens:
                        metrics.max_call_combined_tokens = combined
                    if total > metrics.max_call_total_tokens:
                        metrics.max_call_total_tokens = total
                    if duration_ms > metrics.llm_time_ms_max:
                        metrics.llm_time_ms_max = duration_ms

                    provider = data.get("provider") or "?"
                    model = data.get("model") or "?"
                    key = f"{provider}/{model}"
                    bm = metrics.by_model.setdefault(key, ModelUsage())
                    bm.requests += 1
                    bm.input_tokens += inp
                    bm.output_tokens += out
                    bm.cache_read_tokens += cr
                    bm.cache_write_tokens += cw
                    bm.llm_time_ms += duration_ms
        except OSError:
            # If we can't read one events.jsonl, skip it — partial data
            # is still better than nothing for the rest of the run.
            continue

    metrics.session_count = len(sessions_seen)
    return metrics
