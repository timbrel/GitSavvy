"""
Worker occupancy instrumentation for GitSavvy (opt-in, disabled by default).

Measures Sublime shared-worker queue delay and per-turn wall duration for
callbacks routed through GitSavvy's ``_enqueue_on_worker`` seam.  Also
records the fraction of each turn spent waiting on git subprocesses.

GitSavvy's Start/Stop Logging commands also start/stop this profiler. Stop
exports a timestamped JSON report to Packages/User and prints its path. Hot
reloads retain the session and resume recording without exporting or resetting.
Use ``reset()`` explicitly when you want a fresh capture.

Console usage after plugin load::

    import GitSavvy.core.worker_occupancy as wo
    wo.enable()          # begin collecting + 1-s heartbeats
    wo.dump()            # full report; safe to call while worker is stuck
    wo.snapshot()        # returns dict of live in-flight state + totals
    wo.export("/tmp/gs_occupancy.json")  # JSON snapshot on demand
    wo.disable()         # stop (records survive)
    wo.reset()           # clear everything, restart heartbeat if enabled

    # Raw access for scripted analysis
    list(wo._turns)             # recent TurnRecords (ring buffer)
    list(wo._heartbeats)        # recent HeartbeatRecords
    list(wo._worst_turns)       # top-N slowest turns, all day
    list(wo._worst_queue)       # top-N worst queue-delay turns, all day
    list(wo._worst_heartbeats)  # top-N worst late heartbeats, all day
    dict(wo._repo_aggs)         # per-repo _AggStats (git spans)
    dict(wo._subcmd_aggs)       # per-subcommand _AggStats (git spans)
    wo._lifetime.wall           # _Stats: lifetime count/total/max + recent percentiles

Coverage limitations
--------------------
* Only GitSavvy worker callbacks enqueued through ``_enqueue_on_worker``
  (via ``enqueue_on_worker`` / ``run_when_worker_is_idle`` and
  ``cooperative_thread_hopper`` AWAIT_WORKER/ENSURE_WORKER hops) are tracked.
  Direct ``sublime.set_timeout_async()`` calls outside that seam are not.
* ``run_when_worker_is_idle`` retries go through ``_enqueue_on_worker``
  and appear as tiny turns.
* UI-thread callbacks (``set_timeout``, ``run_command``) are not covered.
* Heartbeat lateness reflects worker-thread pressure from *all* plugins.
* Git timing is subprocess wall-time per turn, not CPU time.
* Absolute repo working_dir paths are stored as-is; keep exports private.
"""
from __future__ import annotations

import datetime
import json
import os
import threading
import time
import types
from collections import deque
from functools import partial
from typing import Any, Callable, Deque, Dict, List, Optional, TypeVar

T = TypeVar("T")

# ── tunables ──────────────────────────────────────────────────────────────────

MAX_TURN_RECORDS = 500
MAX_HEARTBEAT_RECORDS = 200
MAX_REPO_CARDINALITY = 30
MAX_SUBCMD_CARDINALITY = 50
TOP_INCIDENTS = 50
TOP_QUEUE_INCIDENTS = 30
TOP_HEARTBEAT_INCIDENTS = 30
HEARTBEAT_INTERVAL_MS = 1000
SLOW_TURN_THRESHOLD_MS = 50.0
SLOW_QUEUE_DELAY_THRESHOLD_MS = 30.0
OVERFLOW_KEY = "<other>"
_STATS_RING = 1000   # exact-within-window ring size

# ── Stats accumulator ─────────────────────────────────────────────────────────


class _Stats:
    """Lifetime count/sum/max plus a fixed-size circular ring for recent-window percentiles.

    The ring holds the most-recent ``_STATS_RING`` values; percentiles are exact
    for that window.  count/total/max are exact lifetime values.
    Not thread-safe on its own; callers must hold ``_lock``.
    """

    def __init__(self) -> None:
        self.count: int = 0
        self.total: float = 0.0
        self.max_val: float = 0.0
        self._ring: List[float] = [0.0] * _STATS_RING
        self._pos: int = 0
        self._full: bool = False

    def add(self, v: float) -> None:
        self.count += 1
        self.total += v
        if v > self.max_val:
            self.max_val = v
        self._ring[self._pos] = v
        self._pos += 1
        if self._pos >= _STATS_RING:
            self._pos = 0
            self._full = True

    def reset(self) -> None:
        self.count = 0
        self.total = 0.0
        self.max_val = 0.0
        self._pos = 0
        self._full = False

    def mean(self) -> float:
        return self.total / self.count if self.count else 0.0

    def _n(self) -> int:
        return _STATS_RING if self._full else self._pos

    def percentile(self, pct: float) -> float:
        n = self._n()
        if n == 0:
            return 0.0
        s = sorted(self._ring[:n])
        return s[min(int(n * pct / 100), n - 1)]

    def to_dict(self) -> dict:
        n = self._n()
        return {
            "count_lifetime": self.count,
            "sum_lifetime_ms": round(self.total, 1),
            "max_lifetime_ms": round(self.max_val, 1),
            "mean_lifetime_ms": round(self.mean(), 1),
            # Percentiles are exact for the recent window, not approximations:
            "p50_recent_ms": round(self.percentile(50), 1),
            "p95_recent_ms": round(self.percentile(95), 1),
            "p99_recent_ms": round(self.percentile(99), 1),
            "recent_n": n,
        }


class _Lifetime:
    """Container for all per-metric lifetime stats."""

    def __init__(self) -> None:
        self.wall = _Stats()    # turn wall times
        self.queue = _Stats()   # queue delays (lateness beyond due)
        self.git = _Stats()     # git times per outermost span (only when > 0)
        self.hb = _Stats()      # heartbeat latenesses

    def reset_all(self) -> None:
        self.wall.reset()
        self.queue.reset()
        self.git.reset()
        self.hb.reset()


# ── Per-key aggregate (git-span-level, not turn-level) ────────────────────────


class _AggStats:
    """Per-repo or per-subcommand git-span aggregate.

    Each call to ``add_span`` records one outermost git call (depth 0→1 at
    git_exit).  Metrics are span-based, avoiding misleading attribution from
    multi-repo/multi-subcommand turns.
    """
    __slots__ = ("count", "total_git_ms", "max_git_ms")

    def __init__(self) -> None:
        self.count: int = 0          # number of git spans
        self.total_git_ms: float = 0.0
        self.max_git_ms: float = 0.0

    def add_span(self, git_ms: float) -> None:
        self.count += 1
        self.total_git_ms += git_ms
        if git_ms > self.max_git_ms:
            self.max_git_ms = git_ms

    def mean_git_ms(self) -> float:
        return self.total_git_ms / self.count if self.count else 0.0

    def to_dict(self) -> dict:
        return {
            "span_count": self.count,
            "total_git_ms": round(self.total_git_ms, 1),
            "max_git_ms": round(self.max_git_ms, 1),
            "mean_git_ms": round(self.mean_git_ms(), 1),
        }


# ── Record types ──────────────────────────────────────────────────────────────


class _GitSpan:
    """One completed outermost git call within a turn.  Collected in _TurnContext."""
    __slots__ = ("repo", "subcommand", "git_ms")

    def __init__(self, repo: str, subcommand: str, git_ms: float) -> None:
        self.repo = repo
        self.subcommand = subcommand
        self.git_ms = git_ms


class TurnRecord:
    """Timing data for a single instrumented worker turn (recent ring)."""
    __slots__ = ("label", "start_time", "queue_delay_ms", "wall_ms", "git_ms")

    def __init__(
        self,
        label: str,
        start_time: float,
        queue_delay_ms: float,
        wall_ms: float,
        git_ms: float,
    ) -> None:
        self.label = label
        self.start_time = start_time      # perf_counter; for heartbeat correlation
        self.queue_delay_ms = queue_delay_ms
        self.wall_ms = wall_ms
        self.git_ms = git_ms

    def __repr__(self) -> str:
        return (
            f"TurnRecord(label={self.label!r}, "
            f"queue_delay_ms={self.queue_delay_ms:.1f}, "
            f"wall_ms={self.wall_ms:.1f}, git_ms={self.git_ms:.1f})"
        )

    def to_dict(self) -> dict:
        return {
            "label": self.label,
            "start_time": self.start_time,
            "queue_delay_ms": round(self.queue_delay_ms, 2),
            "wall_ms": round(self.wall_ms, 2),
            "git_ms": round(self.git_ms, 2),
        }


class HeartbeatRecord:
    """Lateness of a single async heartbeat."""
    __slots__ = ("expected_ms", "actual_ms", "lateness_ms", "fired_at")

    def __init__(
        self, expected_ms: int, actual_ms: float, lateness_ms: float, fired_at: float
    ) -> None:
        self.expected_ms = expected_ms
        self.actual_ms = actual_ms
        self.lateness_ms = lateness_ms
        self.fired_at = fired_at    # perf_counter; for turn correlation

    def to_dict(self) -> dict:
        return {
            "expected_ms": self.expected_ms,
            "actual_ms": round(self.actual_ms, 1),
            "lateness_ms": round(self.lateness_ms, 1),
            "fired_at": self.fired_at,
        }


class IncidentRecord:
    """A worst-of-session incident retained beyond the ring buffer.

    ``repo`` and ``subcommand`` reflect the dominant git span (largest git_ms)
    for the turn.  ``git_span_count`` is the total number of outermost git
    calls in the turn (> 1 = multi-span).
    """
    __slots__ = (
        "label", "repo", "subcommand", "git_span_count",
        "wall_ms", "git_ms", "queue_delay_ms", "wall_timestamp", "start_time",
    )

    def __init__(
        self,
        label: str,
        repo: str,
        subcommand: str,
        git_span_count: int,
        wall_ms: float,
        git_ms: float,
        queue_delay_ms: float,
        wall_timestamp: str,
        start_time: float,
    ) -> None:
        self.label = label
        self.repo = repo
        self.subcommand = subcommand
        self.git_span_count = git_span_count
        self.wall_ms = wall_ms
        self.git_ms = git_ms
        self.queue_delay_ms = queue_delay_ms
        self.wall_timestamp = wall_timestamp   # ISO-8601
        self.start_time = start_time           # perf_counter

    def to_dict(self) -> dict:
        return {
            "label": self.label,
            "repo": self.repo,
            "subcommand": self.subcommand,
            "git_span_count": self.git_span_count,
            "wall_ms": round(self.wall_ms, 1),
            "git_ms": round(self.git_ms, 1),
            "queue_delay_ms": round(self.queue_delay_ms, 1),
            "wall_timestamp": self.wall_timestamp,
            "start_time": self.start_time,
        }


# ── Per-thread turn context ───────────────────────────────────────────────────


class _TurnContext:
    """Mutable accumulator for the currently executing instrumented turn.

    ``git_spans`` accumulates completed outermost git calls during the turn.
    ``current_repo``/``current_subcommand`` hold the in-progress span info
    (overwritten on each sequential git call at depth 0→1; used by git_exit
    to build the finished _GitSpan).  ``epoch`` is the collection epoch at
    turn creation; used by git_enter/exit to guard _in_flight updates.
    """
    __slots__ = (
        "git_ms", "git_depth", "git_outermost_enter",
        "current_repo", "current_subcommand", "git_spans", "epoch",
    )

    def __init__(self, epoch: int = 0) -> None:
        self.git_ms: float = 0.0
        self.git_depth: int = 0
        self.git_outermost_enter: Optional[float] = None
        self.current_repo: str = ""
        self.current_subcommand: str = ""
        self.git_spans: List[_GitSpan] = []
        self.epoch: int = epoch


_ctx = threading.local()

# ── Persistent state (survives hot-reload via globals().get) ──────────────────

enabled: bool = globals().get("enabled", False)
_resume_after_reload: bool = globals().get("_resume_after_reload", False)
_collection_epoch: int = globals().get("_collection_epoch", 0)
_heartbeat_epoch: int = globals().get("_heartbeat_epoch", 0)

_lock: threading.Lock = globals().get("_lock", threading.Lock())

_session: Dict[str, float] = globals().get("_session", {"start_wall": 0.0, "start_mono": 0.0})

_turns: Deque[TurnRecord] = globals().get("_turns", deque(maxlen=MAX_TURN_RECORDS))
_heartbeats: Deque[HeartbeatRecord] = globals().get(
    "_heartbeats", deque(maxlen=MAX_HEARTBEAT_RECORDS)
)
_lifetime: _Lifetime = globals().get("_lifetime", _Lifetime())
_repo_aggs: Dict[str, _AggStats] = globals().get("_repo_aggs", {})
_subcmd_aggs: Dict[str, _AggStats] = globals().get("_subcmd_aggs", {})
_worst_turns: List[IncidentRecord] = globals().get("_worst_turns", [])
_worst_queue: List[IncidentRecord] = globals().get("_worst_queue", [])
_worst_heartbeats: List[HeartbeatRecord] = globals().get("_worst_heartbeats", [])
_dropped: Dict[str, int] = globals().get("_dropped", {"repo": 0, "subcmd": 0})

# In-flight info for the currently executing worker turn, readable from any
# thread.  Protected by _lock for writes; snapshot() reads under _lock.
# Keys: label, start_time, repo, subcommand, git_enter_time (Optional[float])
_in_flight: Dict[str, Any] = globals().get(
    "_in_flight",
    {"label": "", "start_time": 0.0, "repo": "", "subcommand": "", "git_enter_time": None},
)

# ── Public API ────────────────────────────────────────────────────────────────


def enable() -> None:
    """Enable collection and arm the 1-second heartbeat chain.

    Repeated calls do not reset records or restart the session clock.
    """
    new_epoch: int
    with _lock:
        global enabled, _heartbeat_epoch
        enabled = True
        _heartbeat_epoch += 1
        new_epoch = _heartbeat_epoch
        if _session["start_wall"] == 0.0:
            _session["start_wall"] = time.time()
            _session["start_mono"] = time.perf_counter()
    _schedule_heartbeat(new_epoch)
    print("GitSavvy worker occupancy: ENABLED  (wo.dump() / wo.snapshot() / wo.export(path))")


def disable(*, quiet: bool = False) -> None:
    """Stop collecting; all records survive for a later wo.dump() or wo.export()."""
    with _lock:
        global enabled, _collection_epoch, _heartbeat_epoch
        was_enabled = enabled
        enabled = False
        _collection_epoch += 1
        _heartbeat_epoch += 1
    if not quiet and was_enabled:
        print("GitSavvy worker occupancy: DISABLED  (records preserved)")


def reset() -> None:
    """Clear all records and restart the session clock + heartbeat when enabled."""
    run_heartbeat: bool = False
    new_epoch: int
    with _lock:
        global _collection_epoch, _heartbeat_epoch
        _collection_epoch += 1
        _heartbeat_epoch += 1
        new_epoch = _heartbeat_epoch
        _lifetime.reset_all()
        _repo_aggs.clear()
        _subcmd_aggs.clear()
        _worst_turns.clear()
        _worst_queue.clear()
        _worst_heartbeats.clear()
        _dropped["repo"] = 0
        _dropped["subcmd"] = 0
        _in_flight.update(label="", start_time=0.0, repo="", subcommand="", git_enter_time=None)
        _turns.clear()
        _heartbeats.clear()
        if enabled:
            _session["start_wall"] = time.time()
            _session["start_mono"] = time.perf_counter()
            run_heartbeat = True
        else:
            _session["start_wall"] = 0.0
            _session["start_mono"] = 0.0
    if run_heartbeat:
        _schedule_heartbeat(new_epoch)
    print("GitSavvy worker occupancy: RESET")


def on_plugin_load() -> None:
    """Resume recording after reload without clearing the session's data."""
    from . import debug

    global _resume_after_reload
    if debug.enabled or _resume_after_reload:
        enable()
    _resume_after_reload = False


def on_plugin_unload() -> None:
    """Suspend recording across reload; cancel callbacks but retain data."""
    global _resume_after_reload
    _resume_after_reload = enabled
    disable(quiet=True)


def dump() -> None:
    """Print a full occupancy report to the Sublime console.

    Safe to call from the console while the worker thread appears stuck:
    all profiler state is snapshotted under a brief lock before printing.
    """
    snap, recent_turns, recent_hbs = _take_snapshot_with_rings()
    _print_report(snap, recent_turns, recent_hbs)


def snapshot() -> dict:
    """Return a dict of live in-flight state and session totals.

    Acquired under lock for consistency; safe to call from any thread.
    """
    snap, _, _ = _take_snapshot_with_rings()
    return snap


def export(path: str) -> None:
    """Write a JSON snapshot to *path* on demand (no per-event I/O).

    Note: ``per_repo`` keys are absolute working_dir paths; keep the file
    private if repo paths are sensitive.
    """
    data = _build_export_data()
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
    print(f"GitSavvy worker occupancy: exported to {path}")


def export_to_user() -> None:
    """Save a development capture under Packages/User and print its path."""
    import sublime

    directory = os.path.join(sublime.packages_path(), "User")
    os.makedirs(directory, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    export(os.path.join(directory, "GitSavvy-worker-occupancy-{}.json".format(stamp)))


# ── Internal hooks (called by runtime.py and git_command.py) ─────────────────


def wrap_if_enabled(fn: Callable[[], T], after_ms: int = 0) -> Callable[[], T]:
    """Wrap *fn* with occupancy timing when enabled; return *fn* unchanged if not.

    ``after_ms`` is the requested scheduling delay; ``queue_delay_ms`` measures
    lateness beyond that due time only.  Called by ``_enqueue_on_worker`` before
    the ``user_friendly_traceback`` wrapper.

    Stale wrappers (epoch changed between wrap-time and run-time) execute *fn*
    normally but publish nothing to shared state.
    """
    if not enabled:
        return fn
    enqueue_time = time.perf_counter()
    due_at = enqueue_time + after_ms / 1000.0
    label = _get_label(fn)
    epoch_at_wrap = _collection_epoch

    def _instrumented() -> T:
        start_time = time.perf_counter()

        # ── Epoch check + in_flight setup, both under one lock acquisition ───
        prev_saved: Optional[dict]
        with _lock:
            is_live = (_collection_epoch == epoch_at_wrap)
            if is_live:
                prev_saved = dict(_in_flight)
                _in_flight["label"] = label
                _in_flight["start_time"] = start_time
                _in_flight["repo"] = ""
                _in_flight["subcommand"] = ""
                _in_flight["git_enter_time"] = None
            else:
                prev_saved = None

        if not is_live:
            # Stale: execute fn unchanged, no instrumentation
            return fn()

        turn_ctx = _TurnContext(epoch=epoch_at_wrap)
        prev_ctx = getattr(_ctx, "current", None)
        _ctx.current = turn_ctx
        try:
            return fn()
        finally:
            end_time = time.perf_counter()
            _ctx.current = prev_ctx
            wall_ms = (end_time - start_time) * 1000.0
            queue_delay_ms = (start_time - due_at) * 1000.0
            with _lock:
                if _collection_epoch == epoch_at_wrap:
                    # Still live: restore outer in_flight state, record data
                    assert prev_saved is not None
                    _in_flight.update(**prev_saved)
                    _update_aggregates_locked(
                        label=label,
                        git_spans=turn_ctx.git_spans,
                        wall_ms=wall_ms,
                        queue_delay_ms=queue_delay_ms,
                        git_ms=turn_ctx.git_ms,
                        start_time=start_time,
                    )
                    _turns.append(TurnRecord(
                        label=label,
                        start_time=start_time,
                        queue_delay_ms=queue_delay_ms,
                        wall_ms=wall_ms,
                        git_ms=turn_ctx.git_ms,
                    ))
                # else: stale — leave _in_flight as reset() left it

    return _instrumented


def git_enter(repo: str = "", subcommand: str = "") -> None:
    """Record entry into a blocking git call on the current instrumented turn.

    Called unconditionally from ``_GitCommand.git`` (non-just_the_proc path).
    At depth 0→1 (outermost entry), records ``repo`` and ``subcommand`` for
    the current span.  Sequential calls (depth 0→1 multiple times per turn)
    each set their own repo/subcommand; git_exit builds a _GitSpan for each.
    The in-flight dict is updated only when the turn's epoch is still current.
    """
    ctx: Optional[_TurnContext] = getattr(_ctx, "current", None)
    if ctx is None:
        return
    if ctx.git_depth == 0:
        now = time.perf_counter()
        ctx.git_outermost_enter = now
        ctx.current_repo = repo
        ctx.current_subcommand = subcommand
        with _lock:
            if _collection_epoch == ctx.epoch:
                _in_flight["repo"] = repo
                _in_flight["subcommand"] = subcommand
                _in_flight["git_enter_time"] = now
    ctx.git_depth += 1


def git_exit() -> None:
    """Accumulate git wall-time for the current turn; build a _GitSpan.

    At depth 1→0 (outermost exit), computes the span duration, appends a
    _GitSpan to ctx.git_spans, and clears git_enter_time in _in_flight.
    Per-repo/subcmd aggregation uses these spans (one per outermost call),
    so sequential git calls in one turn each contribute independently.
    """
    ctx: Optional[_TurnContext] = getattr(_ctx, "current", None)
    if ctx is None:
        return
    if ctx.git_depth > 0:
        ctx.git_depth -= 1
    if ctx.git_depth == 0 and ctx.git_outermost_enter is not None:
        span_ms = (time.perf_counter() - ctx.git_outermost_enter) * 1000.0
        ctx.git_ms += span_ms
        ctx.git_spans.append(_GitSpan(ctx.current_repo, ctx.current_subcommand, span_ms))
        ctx.git_outermost_enter = None
        with _lock:
            if _collection_epoch == ctx.epoch:
                _in_flight["git_enter_time"] = None


# ── Heartbeat ─────────────────────────────────────────────────────────────────


def _schedule_heartbeat(epoch: int) -> None:
    import sublime
    scheduled_at = time.perf_counter()

    def _heartbeat() -> None:
        if epoch != _heartbeat_epoch or not enabled:
            return
        now = time.perf_counter()
        actual_ms = (now - scheduled_at) * 1000.0
        lateness_ms = actual_ms - HEARTBEAT_INTERVAL_MS
        hb_rec = HeartbeatRecord(
            expected_ms=HEARTBEAT_INTERVAL_MS,
            actual_ms=actual_ms,
            lateness_ms=lateness_ms,
            fired_at=now,
        )
        with _lock:
            # Double-check epoch under lock to avoid race with reset/disable
            if epoch != _heartbeat_epoch:
                return
            _lifetime.hb.add(lateness_ms)
            _heartbeats.append(hb_rec)
            _add_to_sorted_capped(
                _worst_heartbeats, hb_rec,
                key=lambda r: r.lateness_ms,
                cap=TOP_HEARTBEAT_INCIDENTS,
            )
        _schedule_heartbeat(epoch)

    sublime.set_timeout_async(_heartbeat, HEARTBEAT_INTERVAL_MS)


# ── Aggregate update (called under _lock) ────────────────────────────────────


def _update_aggregates_locked(
    label: str,
    git_spans: List[_GitSpan],
    wall_ms: float,
    queue_delay_ms: float,
    git_ms: float,
    start_time: float,
) -> None:
    """Update lifetime stats, per-span aggregates, and worst-incident lists.

    Per-repo and per-subcommand aggregates receive one ``add_span`` call per
    completed outermost git span (not per turn), so sequential calls to
    different repos/subcommands within one turn each contribute independently.

    Must be called with ``_lock`` held.
    """
    _lifetime.wall.add(wall_ms)
    _lifetime.queue.add(queue_delay_ms)

    # Per-span repo/subcmd aggregation: independent per span, no turn totals
    for span in git_spans:
        _lifetime.git.add(span.git_ms)
        if span.repo:
            agg_repo = span.repo
            if agg_repo not in _repo_aggs:
                if len(_repo_aggs) >= MAX_REPO_CARDINALITY:
                    agg_repo = OVERFLOW_KEY
                    _dropped["repo"] += 1
                else:
                    _repo_aggs[agg_repo] = _AggStats()
            _repo_aggs.setdefault(agg_repo, _AggStats()).add_span(span.git_ms)
        if span.subcommand:
            agg_sub = span.subcommand
            if agg_sub not in _subcmd_aggs:
                if len(_subcmd_aggs) >= MAX_SUBCMD_CARDINALITY:
                    agg_sub = OVERFLOW_KEY
                    _dropped["subcmd"] += 1
                else:
                    _subcmd_aggs[agg_sub] = _AggStats()
            _subcmd_aggs.setdefault(agg_sub, _AggStats()).add_span(span.git_ms)

    # Dominant span (for incident attribution): span with largest git_ms
    dominant = max(git_spans, key=lambda s: s.git_ms) if git_spans else None
    incident = IncidentRecord(
        label=label,
        repo=dominant.repo if dominant else "",
        subcommand=dominant.subcommand if dominant else "",
        git_span_count=len(git_spans),
        wall_ms=wall_ms,
        git_ms=git_ms,
        queue_delay_ms=queue_delay_ms,
        wall_timestamp=datetime.datetime.now().isoformat(timespec="seconds"),
        start_time=start_time,
    )
    _add_to_sorted_capped(_worst_turns, incident, key=lambda r: r.wall_ms, cap=TOP_INCIDENTS)
    _add_to_sorted_capped(
        _worst_queue, incident, key=lambda r: r.queue_delay_ms, cap=TOP_QUEUE_INCIDENTS
    )


def _add_to_sorted_capped(lst: list, item: Any, key: Callable, cap: int) -> None:
    """Keep *lst* sorted descending by *key*, bounded to *cap* entries.
    Replaces the current worst only if the new item beats it.
    Must be called with ``_lock`` held.
    """
    if len(lst) < cap:
        lst.append(item)
        lst.sort(key=key, reverse=True)
    elif key(item) > key(lst[-1]):
        lst[-1] = item
        lst.sort(key=key, reverse=True)


# ── Label extraction ──────────────────────────────────────────────────────────


def _generator_label(obj: Any) -> str:
    """Return the qualname of a generator's code object, or '' if not a generator."""
    if not isinstance(obj, types.GeneratorType):
        return ""
    code = getattr(obj, "gi_code", None)
    if code is None:
        return ""
    return getattr(code, "co_qualname", None) or getattr(code, "co_name", None) or ""


def _get_label(fn: Callable) -> str:
    """Return a readable name for *fn*, unwrapping partials and closure layers."""
    seen: set = set()
    while True:
        fn_id = id(fn)
        if fn_id in seen:
            break
        seen.add(fn_id)
        if isinstance(fn, partial):
            for arg in fn.args:
                gen_label = _generator_label(arg)
                if gen_label:
                    return gen_label
            fn = fn.func
            continue
        wrapped = getattr(fn, "__wrapped__", None)
        if wrapped is not None and callable(wrapped):
            fn = wrapped
            continue
        closure = getattr(fn, "__closure__", None) or ()
        for cell in closure:
            try:
                v = cell.cell_contents
            except ValueError:
                continue
            if isinstance(v, partial):
                fn = v
                break
        else:
            break
    return getattr(fn, "__qualname__", None) or getattr(fn, "__name__", None) or repr(fn)


# ── Snapshot helpers ──────────────────────────────────────────────────────────


def _take_snapshot_with_rings() -> tuple:
    """Build a consistent dict plus ring snapshots, all under one lock."""
    now_mono = time.perf_counter()
    with _lock:
        inflight = dict(_in_flight)
        session_start_wall = _session["start_wall"]
        session_start_mono = _session["start_mono"]
        lifetime_snap = {
            "wall": _lifetime.wall.to_dict(),
            "queue": _lifetime.queue.to_dict(),
            "git": _lifetime.git.to_dict(),
            "heartbeat_lateness": _lifetime.hb.to_dict(),
        }
        repo_snap = {k: v.to_dict() for k, v in _repo_aggs.items()}
        subcmd_snap = {k: v.to_dict() for k, v in _subcmd_aggs.items()}
        worst_snap = list(_worst_turns)
        worst_q_snap = list(_worst_queue)
        worst_hb_snap = list(_worst_heartbeats)
        dropped_snap = dict(_dropped)
        recent_turns = list(_turns)
        recent_hbs = list(_heartbeats)

    session_elapsed = now_mono - session_start_mono if session_start_mono else 0.0
    in_flight_out: Optional[dict] = None
    if inflight.get("label"):
        elapsed_ms = (now_mono - inflight["start_time"]) * 1000.0
        git_enter_t = inflight.get("git_enter_time")
        in_flight_out = {
            "label": inflight["label"],
            "elapsed_ms": round(elapsed_ms, 1),
            "repo": inflight["repo"],
            "subcommand": inflight["subcommand"],
            "git_elapsed_ms": (
                round((now_mono - git_enter_t) * 1000.0, 1) if git_enter_t else None
            ),
        }

    snap = {
        "enabled": enabled,
        "session_start": (
            datetime.datetime.fromtimestamp(session_start_wall).isoformat(timespec="seconds")
            if session_start_wall else None
        ),
        "session_elapsed_s": round(session_elapsed, 1),
        "turns_lifetime": lifetime_snap["wall"]["count_lifetime"],
        "in_flight": in_flight_out,
        "lifetime": lifetime_snap,
        "per_repo": repo_snap,
        "per_subcommand": subcmd_snap,
        "worst_incidents": [r.to_dict() for r in worst_snap],
        "worst_queue_delays": [r.to_dict() for r in worst_q_snap],
        "worst_heartbeats": [r.to_dict() for r in worst_hb_snap],
        "dropped": dropped_snap,
    }
    return snap, recent_turns, recent_hbs


# ── Export ────────────────────────────────────────────────────────────────────


def _build_export_data() -> dict:
    snap, recent_turns, recent_hbs = _take_snapshot_with_rings()
    snap["recent_turns"] = [r.to_dict() for r in recent_turns]
    snap["recent_heartbeats"] = [r.to_dict() for r in recent_hbs]
    snap["limitations"] = [
        "Only _enqueue_on_worker-routed callbacks are covered.",
        "run_when_worker_is_idle retries are instrumented as tiny turns.",
        "UI-thread callbacks (set_timeout, run_command) are not covered.",
        "Heartbeat lateness reflects all-plugin worker pressure.",
        "Git timing is subprocess wall-time, not CPU time.",
        "per_repo and incident.repo are absolute working_dir paths; keep file private if sensitive.",
        "Percentiles (p*_recent_ms) are exact within the recent-window ring, not lifetime estimates.",
        "Per-repo/subcmd aggregates count git spans, not turns.",
    ]
    return snap


# ── Reporting ─────────────────────────────────────────────────────────────────


def _print_report(snap: dict, recent_turns: list, recent_hbs: list) -> None:
    hr = "=" * 65
    print(hr)
    print("GitSavvy Worker Occupancy Report")
    print(hr)

    # ── In-flight ─────────────────────────────────────────────────────────────
    inf = snap.get("in_flight")
    if inf:
        print(f"\n*** IN FLIGHT (currently executing) ***")
        print(f"  label    : {inf['label']}")
        print(f"  elapsed  : {inf['elapsed_ms']:.0f} ms")
        if inf["repo"]:
            print(f"  repo     : {inf['repo']}")
        if inf["subcommand"]:
            print(f"  git cmd  : {inf['subcommand']}")
        if inf.get("git_elapsed_ms") is not None:
            print(f"  git span : {inf['git_elapsed_ms']:.0f} ms (in progress)")
    else:
        print("\n  (no turn currently executing)")

    # ── Session ───────────────────────────────────────────────────────────────
    start_s = snap.get("session_start") or "not started"
    elapsed_h = snap["session_elapsed_s"] / 3600.0
    total = snap["turns_lifetime"]
    print(f"\n--- Session ---")
    print(f"  start={start_s}  elapsed={snap['session_elapsed_s']:.0f}s ({elapsed_h:.2f}h)"
          f"  turns_lifetime={total}")
    if not enabled:
        print("  (collection DISABLED)")

    # ── Lifetime stats ────────────────────────────────────────────────────────
    lf = snap["lifetime"]

    def _fmt(d: dict, name: str) -> str:
        n = d["count_lifetime"]
        if n == 0:
            return f"{name}: no data"
        return (
            f"{name}: n={n}"
            f"  mean={d['mean_lifetime_ms']:.1f}"
            f"  p95(rec)={d['p95_recent_ms']:.1f}"
            f"  max={d['max_lifetime_ms']:.1f} ms"
            f"  (p50(rec)={d['p50_recent_ms']:.1f})"
        )

    print(f"\n--- Lifetime stats (percentiles exact within recent {_STATS_RING}-sample window) ---")
    print(f"  {_fmt(lf['wall'], 'wall')}")
    print(f"  {_fmt(lf['queue'], 'qdelay')}")
    if lf["git"]["count_lifetime"]:
        print(f"  {_fmt(lf['git'], 'git_span')}")
    if lf["heartbeat_lateness"]["count_lifetime"]:
        print(f"  {_fmt(lf['heartbeat_lateness'], 'hb_late')}")

    # ── Per-subcommand breakdown ───────────────────────────────────────────────
    subcmds = sorted(
        snap["per_subcommand"].items(), key=lambda kv: kv[1]["total_git_ms"], reverse=True
    )
    if subcmds:
        print(f"\n--- Per git-subcommand (top 10 by total git time, per-span) ---")
        for key, st in subcmds[:10]:
            print(
                f"  {key:20s}  spans={st['span_count']:5d}"
                f"  total_git={st['total_git_ms']:.0f}ms"
                f"  max_git={st['max_git_ms']:.0f}ms"
            )
        drp = snap["dropped"]["subcmd"]
        if drp:
            print(f"  [{drp} spans mapped to '{OVERFLOW_KEY}' – cardinality overflow]")

    # ── Per-repo breakdown ────────────────────────────────────────────────────
    repos = sorted(
        snap["per_repo"].items(), key=lambda kv: kv[1]["total_git_ms"], reverse=True
    )
    if repos:
        print(f"\n--- Per repo (top 5 by total git time, per-span) ---")
        for key, st in repos[:5]:
            print(
                f"  {key[-40:]:40s}  spans={st['span_count']:5d}"
                f"  total_git={st['total_git_ms']:.0f}ms"
                f"  max_git={st['max_git_ms']:.0f}ms"
            )
        drp = snap["dropped"]["repo"]
        if drp:
            print(f"  [{drp} spans mapped to '{OVERFLOW_KEY}' – cardinality overflow]")

    # ── Top slow incidents ────────────────────────────────────────────────────
    worst = snap["worst_incidents"]
    if worst:
        print(f"\n--- Top {min(len(worst), 10)} slow turns (of {len(worst)} retained) ---")
        for inc in worst[:10]:
            ts = inc.get("wall_timestamp", "?")
            sub = inc.get("subcommand", "")
            spans = inc.get("git_span_count", 0)
            span_s = f" [{sub}{'×'+str(spans) if spans > 1 else ''}]" if (sub or spans > 1) else ""
            print(
                f"  {ts}  {inc['label'][:40]:40s}{span_s}"
                f"  wall={inc['wall_ms']:.0f}ms  git={inc['git_ms']:.0f}ms"
                f"  qdelay={inc['queue_delay_ms']:.0f}ms"
            )

    # ── Top queue-delay incidents ─────────────────────────────────────────────
    worst_q = snap["worst_queue_delays"]
    if worst_q:
        print(f"\n--- Top {min(len(worst_q), 5)} worst queue delays (of {len(worst_q)} retained) ---")
        for inc in worst_q[:5]:
            print(
                f"  {inc.get('wall_timestamp','?')}  {inc['label'][:44]:44s}"
                f"  qdelay={inc['queue_delay_ms']:.0f}ms  wall={inc['wall_ms']:.0f}ms"
            )

    # ── Top late heartbeats ───────────────────────────────────────────────────
    worst_hb = snap["worst_heartbeats"]
    if worst_hb:
        print(f"\n--- Top {min(len(worst_hb), 5)} worst heartbeat latenesses (of {len(worst_hb)} retained) ---")
        for hb in worst_hb[:5]:
            print(f"  +{hb.get('lateness_ms', hb.lateness_ms if hasattr(hb, 'lateness_ms') else '?'):.0f}ms")

    # ── Recent heartbeat ring ─────────────────────────────────────────────────
    if recent_hbs:
        latenesses = [h.lateness_ms for h in recent_hbs]
        n_hb = len(recent_hbs)
        late_hbs = [h for h in recent_hbs if h.lateness_ms > SLOW_QUEUE_DELAY_THRESHOLD_MS]
        print(
            f"\n--- Heartbeats (recent {n_hb}) ---\n"
            f"  mean={sum(latenesses)/n_hb:.1f} ms lateness"
            f"  max={max(latenesses):.1f} ms"
        )
        if late_hbs and recent_turns:
            print(f"  Late heartbeats with blocking turns:")
            shown = 0
            for hb in late_hbs[-5:]:
                due = hb.fired_at - hb.lateness_ms / 1000.0
                blocking = [
                    r for r in recent_turns
                    if r.start_time <= due
                    and r.start_time + r.wall_ms / 1000.0 >= due
                ]
                if blocking:
                    for r in blocking:
                        print(
                            f"    hb+{hb.lateness_ms:.0f}ms  {r.label[:40]:40s}"
                            f"  wall={r.wall_ms:.0f}ms  git={r.git_ms:.0f}ms"
                        )
                    shown += 1
            if not shown:
                print("    (no matching turns in recent buffer; buffer may have wrapped)")

    # ── Recent ring summary ───────────────────────────────────────────────────
    if recent_turns:
        n_rec = len(recent_turns)
        slow = [r for r in recent_turns if r.wall_ms > SLOW_TURN_THRESHOLD_MS]
        print(f"\n--- Recent ring ({n_rec}/{MAX_TURN_RECORDS}) ---")
        if slow:
            print(f"  {len(slow)} slow turns (>{SLOW_TURN_THRESHOLD_MS:.0f}ms), last 5:")
            for r in slow[-5:]:
                print(
                    f"    {r.label[:46]:46s}"
                    f"  wall={r.wall_ms:.0f}ms  git={r.git_ms:.0f}ms"
                    f"  qdelay={r.queue_delay_ms:.0f}ms"
                )

    print(f"\n--- Limitations ---")
    print("  * Only _enqueue_on_worker callbacks are covered (not raw set_timeout_async).")
    print("  * UI-thread callbacks, savvy_executor, run_on_new_thread are not covered.")
    print("  * Heartbeat lateness reflects all-plugin worker pressure.")
    print("  * Git timing = outermost subprocess wall-time; nested calls not double-counted.")
    print("  * Per-repo/subcmd metrics count git spans independently per sequential call.")
    print("  * p*_recent_ms: exact within the recent 1000-sample window, not lifetime.")
    print("  * wo.export('/tmp/gs.json') for full JSON snapshot.")
    print(hr)
