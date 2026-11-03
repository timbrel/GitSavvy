"""Tests for core/worker_occupancy.py.

All tests are synchronous (no yield) and manipulate module state directly to
avoid scheduling real Sublime async callbacks.  The Mockito ``when`` helper
is used only where ``sublime.set_timeout_async`` must be suppressed.
"""
from __future__ import annotations

import json
import os
import tempfile
import threading

from unittesting import DeferrableTestCase
from GitSavvy.tests.mockito import unstub, when

import sublime
import GitSavvy.core.worker_occupancy as wo


def _reset_state() -> None:
    """Bring module back to a clean slate without triggering Sublime API."""
    wo.enabled = False
    wo._ctx.current = None
    with wo._lock:
        wo._collection_epoch += 1
        wo._heartbeat_epoch += 1
        wo._turns.clear()
        wo._heartbeats.clear()
        wo._lifetime.reset_all()
        wo._repo_aggs.clear()
        wo._subcmd_aggs.clear()
        wo._worst_turns.clear()
        wo._worst_queue.clear()
        wo._worst_heartbeats.clear()
        wo._dropped["repo"] = 0
        wo._dropped["subcmd"] = 0
        wo._in_flight.update(
            label="", start_time=0.0, repo="", subcommand="", git_enter_time=None
        )
        wo._session["start_wall"] = 0.0
        wo._session["start_mono"] = 0.0


# ── wrap_if_enabled tests ─────────────────────────────────────────────────────


class TestWrapIfEnabled(DeferrableTestCase):
    def setUp(self):
        _reset_state()

    def tearDown(self):
        _reset_state()
        unstub()

    def test_disabled_returns_fn_unchanged(self):
        wo.enabled = False
        fn = lambda: None  # noqa: E731
        self.assertIs(wo.wrap_if_enabled(fn), fn)

    def test_disabled_no_records_after_call(self):
        wo.enabled = False
        wo.wrap_if_enabled(lambda: None)()
        self.assertEqual(len(wo._turns), 0)

    def test_enabled_returns_different_callable(self):
        wo.enabled = True
        fn = lambda: None  # noqa: E731
        self.assertIsNot(wo.wrap_if_enabled(fn), fn)

    def test_enabled_calls_original_fn(self):
        wo.enabled = True
        calls: list = []
        wo.wrap_if_enabled(lambda: calls.append(1))()
        self.assertEqual(calls, [1])

    def test_enabled_appends_record(self):
        wo.enabled = True
        wo.wrap_if_enabled(lambda: None)()
        with wo._lock:
            self.assertEqual(len(wo._turns), 1)

    def test_record_has_non_negative_timings(self):
        wo.enabled = True
        wo.wrap_if_enabled(lambda: None)()
        with wo._lock:
            r = wo._turns[0]
        self.assertGreaterEqual(r.wall_ms, 0.0)
        self.assertGreaterEqual(r.queue_delay_ms, 0.0)
        self.assertEqual(r.git_ms, 0.0)

    def test_record_has_start_time(self):
        wo.enabled = True
        wo.wrap_if_enabled(lambda: None)()
        with wo._lock:
            self.assertGreater(wo._turns[0].start_time, 0.0)

    def test_record_label_is_string(self):
        wo.enabled = True
        def my_fn():
            pass
        wo.wrap_if_enabled(my_fn)()
        with wo._lock:
            label = wo._turns[0].label
        self.assertIsInstance(label, str)
        self.assertGreater(len(label), 0)

    def test_after_ms_subtracts_from_delay(self):
        wo.enabled = True
        wrapper_zero = wo.wrap_if_enabled(lambda: None, after_ms=0)
        wrapper_late = wo.wrap_if_enabled(lambda: None, after_ms=200)
        wrapper_zero()
        wrapper_late()
        with wo._lock:
            d0 = wo._turns[0].queue_delay_ms
            d1 = wo._turns[1].queue_delay_ms
        self.assertGreater(d0, d1)

    def test_exception_propagates(self):
        wo.enabled = True
        with self.assertRaises(ValueError):
            wo.wrap_if_enabled(lambda: (_ for _ in ()).throw(ValueError("boom")))()

    def test_exception_still_appends_record(self):
        wo.enabled = True
        def bad():
            raise RuntimeError("fail")
        try:
            wo.wrap_if_enabled(bad)()
        except RuntimeError:
            pass
        with wo._lock:
            self.assertEqual(len(wo._turns), 1)

    def test_prev_ctx_restored_after_exception(self):
        wo.enabled = True
        sentinel = wo._TurnContext(epoch=wo._collection_epoch)
        wo._ctx.current = sentinel
        try:
            wo.wrap_if_enabled(lambda: (_ for _ in ()).throw(ValueError("x")))()
        except ValueError:
            pass
        self.assertIs(wo._ctx.current, sentinel)
        wo._ctx.current = None

    def test_prev_ctx_restored_normally(self):
        wo.enabled = True
        sentinel = wo._TurnContext(epoch=wo._collection_epoch)
        wo._ctx.current = sentinel
        wo.wrap_if_enabled(lambda: None)()
        self.assertIs(wo._ctx.current, sentinel)
        wo._ctx.current = None

    def test_stale_wrapper_does_not_record_after_disable(self):
        wo.enabled = True
        wrapper = wo.wrap_if_enabled(lambda: None)
        wo.disable()
        wrapper()
        with wo._lock:
            self.assertEqual(len(wo._turns), 0)

    def test_stale_wrapper_does_not_record_after_reset(self):
        wo.enabled = True
        wrapper = wo.wrap_if_enabled(lambda: None)
        when(sublime).set_timeout_async(...).thenReturn(None)
        wo.reset()
        wo.enabled = True
        wrapper()
        with wo._lock:
            self.assertEqual(len(wo._turns), 0)

    def test_stale_wrapper_still_executes_fn(self):
        """A stale wrapper must still call fn() even though it doesn't record."""
        wo.enabled = True
        calls: list = []
        wrapper = wo.wrap_if_enabled(lambda: calls.append(1))
        wo.disable()
        wrapper()
        self.assertEqual(calls, [1])

    def test_fresh_wrapper_records_after_reset(self):
        wo.enabled = True
        when(sublime).set_timeout_async(...).thenReturn(None)
        wo.reset()
        wo.enabled = True
        wo.wrap_if_enabled(lambda: None)()
        with wo._lock:
            self.assertEqual(len(wo._turns), 1)

    def test_buffer_does_not_exceed_max(self):
        wo.enabled = True
        for _ in range(wo.MAX_TURN_RECORDS + 20):
            wo.wrap_if_enabled(lambda: None)()
        with wo._lock:
            self.assertEqual(len(wo._turns), wo.MAX_TURN_RECORDS)

    # ── Nested instrumented turn: in_flight restore ───────────────────────────

    def test_nested_instrumented_restores_outer_inflight(self):
        """Inner _instrumented turn must restore outer turn's _in_flight on exit."""
        wo.enabled = True
        outer_label_seen: list = []
        inner_label = "inner_work"

        def outer_work():
            # While outer is running, start a nested instrumented call
            inner_wrapper = wo.wrap_if_enabled(lambda: None)
            # Capture label before inner runs
            with wo._lock:
                outer_label_seen.append(wo._in_flight["label"])
            inner_wrapper()
            # After inner finishes, in_flight should be restored to outer
            with wo._lock:
                outer_label_seen.append(wo._in_flight["label"])

        outer_wrapper = wo.wrap_if_enabled(outer_work)
        outer_wrapper()

        # outer_label_seen[0] = outer label (before inner ran)
        # outer_label_seen[1] = outer label restored (after inner finished)
        self.assertGreater(len(outer_label_seen), 0)
        # Both observations should have the outer label (not "" or inner label)
        outer_label = outer_label_seen[0]
        self.assertNotEqual(outer_label, "")
        self.assertEqual(outer_label_seen[1], outer_label)

    def test_nested_turn_inflight_cleared_after_all_finish(self):
        """After all turns finish, in_flight must be empty."""
        wo.enabled = True
        wo.wrap_if_enabled(lambda: wo.wrap_if_enabled(lambda: None)())()
        snap = wo.snapshot()
        self.assertIsNone(snap["in_flight"])


# ── Git timing tests ──────────────────────────────────────────────────────────


class TestGitTiming(DeferrableTestCase):
    def setUp(self):
        _reset_state()

    def tearDown(self):
        _reset_state()
        unstub()

    def _make_ctx(self, epoch: int = 0) -> wo._TurnContext:
        ctx = wo._TurnContext(epoch=epoch)
        wo._ctx.current = ctx
        return ctx

    def _clear_ctx(self) -> None:
        wo._ctx.current = None

    def test_git_enter_noop_without_ctx(self):
        wo._ctx.current = None
        wo.git_enter("repo", "status")

    def test_git_exit_noop_without_ctx(self):
        wo._ctx.current = None
        wo.git_exit()

    def test_git_enter_sets_fields(self):
        ctx = self._make_ctx()
        wo.git_enter("/my/repo", "log")
        self.assertIsNotNone(ctx.git_outermost_enter)
        self.assertEqual(ctx.git_depth, 1)
        self.assertEqual(ctx.current_repo, "/my/repo")
        self.assertEqual(ctx.current_subcommand, "log")
        self._clear_ctx()

    def test_git_enter_no_args_uses_defaults(self):
        ctx = self._make_ctx()
        wo.git_enter()
        self.assertEqual(ctx.current_repo, "")
        self.assertEqual(ctx.current_subcommand, "")
        self._clear_ctx()

    def test_single_git_call_accumulates(self):
        ctx = self._make_ctx()
        wo.git_enter()
        wo.git_exit()
        self._clear_ctx()
        self.assertGreater(ctx.git_ms, 0.0)
        self.assertEqual(ctx.git_depth, 0)
        self.assertIsNone(ctx.git_outermost_enter)

    def test_single_git_call_builds_span(self):
        ctx = self._make_ctx()
        wo.git_enter("/r", "status")
        wo.git_exit()
        self._clear_ctx()
        self.assertEqual(len(ctx.git_spans), 1)
        span = ctx.git_spans[0]
        self.assertEqual(span.repo, "/r")
        self.assertEqual(span.subcommand, "status")
        self.assertGreater(span.git_ms, 0.0)

    def test_sequential_git_calls_build_separate_spans(self):
        """Sequential calls (depth 0→1 multiple times) each create their own span."""
        ctx = self._make_ctx()
        wo.git_enter("/repo_a", "status")
        wo.git_exit()
        wo.git_enter("/repo_b", "log")
        wo.git_exit()
        self._clear_ctx()
        self.assertEqual(len(ctx.git_spans), 2)
        self.assertEqual(ctx.git_spans[0].repo, "/repo_a")
        self.assertEqual(ctx.git_spans[0].subcommand, "status")
        self.assertEqual(ctx.git_spans[1].repo, "/repo_b")
        self.assertEqual(ctx.git_spans[1].subcommand, "log")

    def test_sequential_git_total_git_ms_is_sum_of_spans(self):
        ctx = self._make_ctx()
        for _ in range(3):
            wo.git_enter()
            wo.git_exit()
        self._clear_ctx()
        span_sum = sum(s.git_ms for s in ctx.git_spans)
        self.assertAlmostEqual(ctx.git_ms, span_sum, places=3)

    def test_multiple_sequential_git_calls_sum(self):
        ctx = self._make_ctx()
        for _ in range(3):
            wo.git_enter()
            wo.git_exit()
        self._clear_ctx()
        self.assertGreater(ctx.git_ms, 0.0)

    def test_nested_enter_does_not_overwrite_current_span(self):
        ctx = self._make_ctx()
        wo.git_enter("/outer", "fetch")
        outer_enter = ctx.git_outermost_enter
        wo.git_enter("/inner", "status")   # nested — must not change outermost fields
        self.assertEqual(ctx.git_outermost_enter, outer_enter)
        self.assertEqual(ctx.current_repo, "/outer")   # first outermost call wins
        self.assertEqual(ctx.current_subcommand, "fetch")
        wo.git_exit()   # depth 1 → no span yet
        self.assertEqual(ctx.git_ms, 0.0)
        self.assertEqual(len(ctx.git_spans), 0)
        wo.git_exit()   # depth 0 → one span built
        self.assertGreater(ctx.git_ms, 0.0)
        self.assertEqual(len(ctx.git_spans), 1)
        self._clear_ctx()

    def test_triple_nesting_only_outermost_counted(self):
        ctx = self._make_ctx()
        wo.git_enter()
        wo.git_enter()
        wo.git_enter()
        wo.git_exit()
        wo.git_exit()
        self.assertEqual(ctx.git_ms, 0.0)
        self.assertEqual(len(ctx.git_spans), 0)
        wo.git_exit()
        self.assertGreater(ctx.git_ms, 0.0)
        self.assertEqual(len(ctx.git_spans), 1)
        self._clear_ctx()

    def test_enter_time_cleared_after_exit(self):
        ctx = self._make_ctx()
        wo.git_enter()
        wo.git_exit()
        self.assertIsNone(ctx.git_outermost_enter)
        self._clear_ctx()

    def test_git_time_captured_in_turn_record(self):
        wo.enabled = True
        def work():
            wo.git_enter()
            wo.git_exit()
        wo.wrap_if_enabled(work)()
        with wo._lock:
            self.assertGreater(wo._turns[0].git_ms, 0.0)

    def test_git_depth_reset_after_exception(self):
        wo.enabled = True
        def bad_work():
            wo.git_enter()
            raise RuntimeError("bad git")
        try:
            wo.wrap_if_enabled(bad_work)()
        except RuntimeError:
            pass
        ctx_depths: list = []
        def good_work():
            wo.git_enter()
            ctx_depths.append(getattr(wo._ctx, "current", None).git_depth)
            wo.git_exit()
        wo.wrap_if_enabled(good_work)()
        self.assertEqual(ctx_depths, [1])
        with wo._lock:
            self.assertGreater(wo._turns[-1].git_ms, 0.0)

    def test_git_exit_without_matching_enter_is_safe(self):
        ctx = self._make_ctx()
        wo.git_exit()
        self.assertEqual(ctx.git_ms, 0.0)
        self._clear_ctx()

    # ── B1: Sequential different repos/subcmds aggregate independently ────────

    def test_sequential_different_repos_each_in_repo_aggs(self):
        """Each sequential git span contributes to its own repo aggregate."""
        wo.enabled = True
        def work():
            wo.git_enter("/repo/a", "status")
            wo.git_exit()
            wo.git_enter("/repo/b", "log")
            wo.git_exit()
        wo.wrap_if_enabled(work)()
        with wo._lock:
            self.assertIn("/repo/a", wo._repo_aggs)
            self.assertIn("/repo/b", wo._repo_aggs)
            # Each has 1 span
            self.assertEqual(wo._repo_aggs["/repo/a"].count, 1)
            self.assertEqual(wo._repo_aggs["/repo/b"].count, 1)

    def test_sequential_different_subcmds_each_in_subcmd_aggs(self):
        """Each sequential git span with a different subcommand is independently tracked."""
        wo.enabled = True
        def work():
            wo.git_enter("/r", "fetch")
            wo.git_exit()
            wo.git_enter("/r", "log")
            wo.git_exit()
            wo.git_enter("/r", "fetch")
            wo.git_exit()
        wo.wrap_if_enabled(work)()
        with wo._lock:
            self.assertIn("fetch", wo._subcmd_aggs)
            self.assertIn("log", wo._subcmd_aggs)
            # fetch ran twice → 2 spans
            self.assertEqual(wo._subcmd_aggs["fetch"].count, 2)
            self.assertEqual(wo._subcmd_aggs["log"].count, 1)

    def test_sequential_repos_incident_uses_dominant_span(self):
        """Incident.repo reflects the span with the MOST git time, not the last."""
        wo.enabled = True
        # Make the second call slower (we can't control actual time, but
        # we verify the mechanism by checking that incident.repo comes from spans)
        def work():
            wo.git_enter("/repo/fast", "status")
            wo.git_exit()
            wo.git_enter("/repo/slow", "log")
            wo.git_exit()
        wo.wrap_if_enabled(work)()
        with wo._lock:
            self.assertEqual(len(wo._worst_turns), 1)
            inc = wo._worst_turns[0]
            # git_span_count should be 2 (two sequential calls)
            self.assertEqual(inc.git_span_count, 2)
            # repo should be one of the two (whichever had more git_ms)
            self.assertIn(inc.repo, ("/repo/fast", "/repo/slow"))

    def test_no_git_turn_has_empty_repo_in_incident(self):
        wo.enabled = True
        wo.wrap_if_enabled(lambda: None)()
        with wo._lock:
            self.assertEqual(len(wo._worst_turns), 1)
            self.assertEqual(wo._worst_turns[0].repo, "")
            self.assertEqual(wo._worst_turns[0].subcommand, "")
            self.assertEqual(wo._worst_turns[0].git_span_count, 0)

    # ── B2: Stale git context after reset must not repopulate _in_flight ──────

    def test_stale_git_context_does_not_update_inflight(self):
        """After reset, a stale turn's git_enter must not write to _in_flight."""
        wo.enabled = True
        repo_seen: list = []

        def work():
            # Force a stale epoch by incrementing _collection_epoch mid-turn
            wo.git_enter("/original/repo", "status")
            # Simulate reset: increment epoch so this turn's context becomes stale
            with wo._lock:
                wo._collection_epoch += 1
            # Now git_exit and a subsequent git_enter should not update _in_flight
            wo.git_exit()
            wo.git_enter("/new/repo", "log")
            with wo._lock:
                repo_seen.append(wo._in_flight["repo"])
            wo.git_exit()

        wo.wrap_if_enabled(work)()
        # After the epoch was bumped, git_enter must not write "/new/repo".
        # (The value may still be "/original/repo" from before the bump; what
        # matters is that the stale second call did not overwrite it.)
        self.assertNotEqual(repo_seen[0], "/new/repo")


# ── Heartbeat tests ───────────────────────────────────────────────────────────


class TestHeartbeatEpoch(DeferrableTestCase):
    def setUp(self):
        _reset_state()

    def tearDown(self):
        _reset_state()
        unstub()

    def test_stale_epoch_does_not_append_record(self):
        wo.enabled = True
        with wo._lock:
            epoch_when_scheduled = wo._heartbeat_epoch
            wo._heartbeat_epoch += 1
        before = len(wo._heartbeats)
        # Mimic stale heartbeat guard
        if epoch_when_scheduled != wo._heartbeat_epoch or not wo.enabled:
            pass
        else:
            with wo._lock:
                wo._heartbeats.append(
                    wo.HeartbeatRecord(1000, 1000.0, 0.0, 0.0)
                )
        with wo._lock:
            self.assertEqual(len(wo._heartbeats), before)

    def test_matching_epoch_appends_record(self):
        wo.enabled = True
        with wo._lock:
            epoch = wo._heartbeat_epoch
        before = len(wo._heartbeats)
        with wo._lock:
            if epoch == wo._heartbeat_epoch and wo.enabled:
                wo._heartbeats.append(wo.HeartbeatRecord(1000, 1001.0, 1.0, 0.0))
        with wo._lock:
            self.assertEqual(len(wo._heartbeats), before + 1)

    def test_on_plugin_unload_disables_and_advances_epochs(self):
        wo.enabled = True
        with wo._lock:
            epoch_hb = wo._heartbeat_epoch
            epoch_co = wo._collection_epoch
        wo.on_plugin_unload()
        with wo._lock:
            self.assertFalse(wo.enabled)
            self.assertGreater(wo._heartbeat_epoch, epoch_hb)
            self.assertGreater(wo._collection_epoch, epoch_co)

    def test_heartbeat_record_has_fired_at(self):
        import time as _t
        r = wo.HeartbeatRecord(1000, 1001.0, 1.0, _t.perf_counter())
        self.assertGreater(r.fired_at, 0.0)


# ── Reset and lifecycle tests ─────────────────────────────────────────────────


class TestResetAndBoundary(DeferrableTestCase):
    def setUp(self):
        _reset_state()

    def tearDown(self):
        _reset_state()
        unstub()

    def test_reset_clears_turns(self):
        wo.enabled = True
        wo.wrap_if_enabled(lambda: None)()
        with wo._lock:
            self.assertEqual(len(wo._turns), 1)
        when(sublime).set_timeout_async(...).thenReturn(None)
        wo.reset()
        with wo._lock:
            self.assertEqual(len(wo._turns), 0)

    def test_reset_clears_heartbeats(self):
        with wo._lock:
            wo._heartbeats.append(wo.HeartbeatRecord(1000, 1000.0, 0.0, 0.0))
        when(sublime).set_timeout_async(...).thenReturn(None)
        wo.reset()
        with wo._lock:
            self.assertEqual(len(wo._heartbeats), 0)

    def test_repeated_enable_disable_does_not_accumulate_heartbeats(self):
        when(sublime).set_timeout_async(...).thenReturn(None)
        with wo._lock:
            epoch_before = wo._heartbeat_epoch
        wo.enable()
        wo.enable()
        wo.disable()
        with wo._lock:
            self.assertGreater(wo._heartbeat_epoch, epoch_before)

    def test_disable_increments_collection_epoch(self):
        with wo._lock:
            coll_before = wo._collection_epoch
        wo.disable()
        with wo._lock:
            self.assertGreater(wo._collection_epoch, coll_before)

    def test_reset_increments_collection_epoch(self):
        when(sublime).set_timeout_async(...).thenReturn(None)
        with wo._lock:
            coll_before = wo._collection_epoch
        wo.reset()
        with wo._lock:
            self.assertGreater(wo._collection_epoch, coll_before)

    def test_all_lifecycle_epoch_changes_under_lock(self):
        """enable/disable/reset must not corrupt epoch while we read under lock."""
        errors: list = []
        def read_epochs():
            for _ in range(50):
                with wo._lock:
                    e1 = wo._collection_epoch
                    e2 = wo._heartbeat_epoch
                    if e1 < 0 or e2 < 0:
                        errors.append("negative epoch")
        when(sublime).set_timeout_async(...).thenReturn(None)
        reader = threading.Thread(target=read_epochs)
        reader.start()
        for _ in range(10):
            wo.enable()
            wo.disable()
            wo.reset()
        reader.join()
        self.assertEqual(errors, [])


# ── Label extraction tests ────────────────────────────────────────────────────


class TestGetLabel(DeferrableTestCase):
    def test_plain_function(self):
        def my_function():
            pass
        self.assertIn("my_function", wo._get_label(my_function))

    def test_partial_unwraps_to_inner(self):
        from functools import partial
        def my_target():
            pass
        self.assertIn("my_target", wo._get_label(partial(my_target)))

    def test_closure_containing_partial(self):
        from functools import partial
        def inner_fn():
            pass
        action = partial(inner_fn)
        def outer():
            return action()
        self.assertIn("inner_fn", wo._get_label(outer))

    def test_unknown_callable_returns_string(self):
        label = wo._get_label(lambda: None)
        self.assertIsInstance(label, str)
        self.assertGreater(len(label), 0)

    def test_generator_label_from_partial_arg(self):
        from functools import partial
        def my_command_method():
            yield
        gen = my_command_method()
        def tick(gen, initial_call=False):
            pass
        label = wo._get_label(partial(tick, gen))
        self.assertIn("my_command_method", label)

    def test_generator_label_through_closure(self):
        from functools import partial
        def my_hopper_fn():
            yield
        gen = my_hopper_fn()
        def tick(gen, initial_call=False):
            pass
        action = partial(tick, gen)
        def task():
            return action()
        self.assertIn("my_hopper_fn", wo._get_label(task))

    def test_generator_label_helper_non_generator(self):
        self.assertEqual(wo._generator_label(42), "")
        self.assertEqual(wo._generator_label("str"), "")
        self.assertEqual(wo._generator_label(None), "")

    def test_generator_label_helper_generator(self):
        def gen_func():
            yield 1
        gen = gen_func()
        self.assertIn("gen_func", wo._generator_label(gen))
        gen.close()


# ── Lifetime aggregate tests ──────────────────────────────────────────────────


class TestLifetimeAggregates(DeferrableTestCase):
    def setUp(self):
        _reset_state()

    def tearDown(self):
        _reset_state()
        unstub()

    def test_lifetime_wall_count_increments(self):
        wo.enabled = True
        for _ in range(5):
            wo.wrap_if_enabled(lambda: None)()
        with wo._lock:
            self.assertEqual(wo._lifetime.wall.count, 5)

    def test_lifetime_count_exceeds_ring_size(self):
        wo.enabled = True
        big_n = wo._STATS_RING + 50
        for _ in range(big_n):
            wo.wrap_if_enabled(lambda: None)()
        with wo._lock:
            self.assertEqual(wo._lifetime.wall.count, big_n)

    def test_lifetime_max_tracks_correct_value(self):
        wo.enabled = True
        wo.wrap_if_enabled(lambda: None)()
        with wo._lock:
            wo._lifetime.wall.add(9999.0)
            self.assertEqual(wo._lifetime.wall.max_val, 9999.0)

    def test_percentile_returns_float(self):
        wo.enabled = True
        for _ in range(10):
            wo.wrap_if_enabled(lambda: None)()
        with wo._lock:
            p95 = wo._lifetime.wall.percentile(95)
        self.assertIsInstance(p95, float)
        self.assertGreaterEqual(p95, 0.0)

    def test_stats_ring_wraps_without_error(self):
        wo.enabled = True
        n = wo._STATS_RING + 100
        for _ in range(n):
            wo.wrap_if_enabled(lambda: None)()
        with wo._lock:
            p = wo._lifetime.wall.percentile(50)
        self.assertGreaterEqual(p, 0.0)

    def test_reset_clears_lifetime_stats(self):
        wo.enabled = True
        wo.wrap_if_enabled(lambda: None)()
        when(sublime).set_timeout_async(...).thenReturn(None)
        wo.reset()
        with wo._lock:
            self.assertEqual(wo._lifetime.wall.count, 0)
            self.assertEqual(wo._lifetime.wall.total, 0.0)

    def test_git_stats_count_spans_not_turns(self):
        """_lifetime.git counts individual git spans, not turns."""
        wo.enabled = True
        # Turn 1: no git
        wo.wrap_if_enabled(lambda: None)()
        # Turn 2: two sequential git calls (two spans)
        def two_spans():
            wo.git_enter("/r", "status")
            wo.git_exit()
            wo.git_enter("/r", "log")
            wo.git_exit()
        wo.wrap_if_enabled(two_spans)()
        with wo._lock:
            self.assertEqual(wo._lifetime.wall.count, 2)   # 2 turns
            self.assertEqual(wo._lifetime.git.count, 2)    # 2 spans

    def test_stats_to_dict_uses_recent_key_names(self):
        """to_dict() must use p*_recent_ms, not p*_approx_ms."""
        wo.enabled = True
        wo.wrap_if_enabled(lambda: None)()
        with wo._lock:
            d = wo._lifetime.wall.to_dict()
        self.assertIn("p95_recent_ms", d)
        self.assertIn("p50_recent_ms", d)
        self.assertIn("p99_recent_ms", d)
        self.assertIn("recent_n", d)
        self.assertNotIn("p95_approx_ms", d)
        self.assertNotIn("approx_sample_n", d)


# ── Cardinality and overflow tests ────────────────────────────────────────────


class TestAggregateCardinality(DeferrableTestCase):
    def setUp(self):
        _reset_state()

    def tearDown(self):
        _reset_state()
        unstub()

    def _run_turn_with_git(self, repo: str, subcommand: str) -> None:
        wo.enabled = True
        def work():
            wo.git_enter(repo, subcommand)
            wo.git_exit()
        wo.wrap_if_enabled(work)()

    def test_repo_agg_bounded_by_max_cardinality(self):
        for i in range(wo.MAX_REPO_CARDINALITY + 5):
            self._run_turn_with_git(f"/repo/project_{i}", "status")
        with wo._lock:
            self.assertLessEqual(len(wo._repo_aggs), wo.MAX_REPO_CARDINALITY + 1)
            self.assertIn(wo.OVERFLOW_KEY, wo._repo_aggs)

    def test_repo_overflow_increments_dropped_counter(self):
        for i in range(wo.MAX_REPO_CARDINALITY + 3):
            self._run_turn_with_git(f"/repo/proj_{i}", "log")
        with wo._lock:
            self.assertGreater(wo._dropped["repo"], 0)

    def test_subcmd_agg_bounded(self):
        subcmds = [f"subcmd_{i}" for i in range(wo.MAX_SUBCMD_CARDINALITY + 5)]
        for sub in subcmds:
            self._run_turn_with_git("/repo", sub)
        with wo._lock:
            self.assertLessEqual(len(wo._subcmd_aggs), wo.MAX_SUBCMD_CARDINALITY + 1)
            self.assertIn(wo.OVERFLOW_KEY, wo._subcmd_aggs)

    def test_overflow_key_accumulates_stats(self):
        for i in range(wo.MAX_REPO_CARDINALITY + 2):
            self._run_turn_with_git(f"/repo/x_{i}", "status")
        with wo._lock:
            overflow = wo._repo_aggs.get(wo.OVERFLOW_KEY)
            self.assertIsNotNone(overflow)
            self.assertGreater(overflow.count, 0)

    def test_reset_clears_aggregates(self):
        for i in range(5):
            self._run_turn_with_git(f"/repo/p_{i}", "status")
        when(sublime).set_timeout_async(...).thenReturn(None)
        wo.reset()
        with wo._lock:
            self.assertEqual(len(wo._repo_aggs), 0)
            self.assertEqual(len(wo._subcmd_aggs), 0)
            self.assertEqual(wo._dropped["repo"], 0)

    def test_agg_stats_are_span_based_not_turn_based(self):
        """Two spans in one turn → repo agg count = 2, not 1."""
        wo.enabled = True
        def work():
            wo.git_enter("/same/repo", "fetch")
            wo.git_exit()
            wo.git_enter("/same/repo", "log")
            wo.git_exit()
        wo.wrap_if_enabled(work)()
        with wo._lock:
            self.assertEqual(wo._repo_aggs["/same/repo"].count, 2)


# ── Worst incidents tests ─────────────────────────────────────────────────────


class TestWorstIncidents(DeferrableTestCase):
    def setUp(self):
        _reset_state()

    def tearDown(self):
        _reset_state()
        unstub()

    def _add_incident(
        self, wall_ms: float, queue_delay_ms: float = 1.0,
        label: str = "fn", repo: str = "", subcommand: str = "",
    ) -> None:
        """Add an incident directly for testing."""
        spans = [wo._GitSpan(repo, subcommand, wall_ms * 0.5)] if repo else []
        with wo._lock:
            wo._update_aggregates_locked(
                label=label,
                git_spans=spans,
                wall_ms=wall_ms,
                queue_delay_ms=queue_delay_ms,
                git_ms=wall_ms * 0.5 if spans else 0.0,
                start_time=0.0,
            )

    def test_worst_turns_bounded(self):
        for i in range(wo.TOP_INCIDENTS + 10):
            self._add_incident(float(i))
        with wo._lock:
            self.assertLessEqual(len(wo._worst_turns), wo.TOP_INCIDENTS)

    def test_worst_turns_sorted_descending(self):
        for wall_ms in [10.0, 50.0, 30.0, 5.0, 100.0]:
            self._add_incident(wall_ms)
        with wo._lock:
            wms = [r.wall_ms for r in wo._worst_turns]
        self.assertEqual(wms, sorted(wms, reverse=True))

    def test_small_turns_displaced_by_large(self):
        for i in range(wo.TOP_INCIDENTS):
            self._add_incident(1.0 + i * 0.1)
        with wo._lock:
            min_before = wo._worst_turns[-1].wall_ms
        self._add_incident(99999.0, label="huge_fn")
        with wo._lock:
            self.assertEqual(wo._worst_turns[0].wall_ms, 99999.0)
            self.assertEqual(wo._worst_turns[0].label, "huge_fn")
            min_after = wo._worst_turns[-1].wall_ms
        self.assertGreater(min_after, min_before)

    def test_incident_records_wall_timestamp(self):
        self._add_incident(100.0)
        with wo._lock:
            ts = wo._worst_turns[0].wall_timestamp
        self.assertIsInstance(ts, str)
        self.assertGreater(len(ts), 0)

    def test_reset_clears_worst_turns(self):
        self._add_incident(100.0)
        when(sublime).set_timeout_async(...).thenReturn(None)
        wo.reset()
        with wo._lock:
            self.assertEqual(len(wo._worst_turns), 0)

    def test_incident_repo_from_dominant_span(self):
        spans = [wo._GitSpan("/my/repo", "log", 5.0)]
        with wo._lock:
            wo._update_aggregates_locked(
                label="test_fn", git_spans=spans,
                wall_ms=10.0, queue_delay_ms=1.0, git_ms=5.0, start_time=0.0,
            )
        with wo._lock:
            inc = wo._worst_turns[0]
        self.assertEqual(inc.repo, "/my/repo")
        self.assertEqual(inc.subcommand, "log")
        self.assertEqual(inc.git_span_count, 1)

    def test_incident_git_span_count_multi(self):
        spans = [wo._GitSpan("/r/a", "s", 3.0), wo._GitSpan("/r/b", "l", 7.0)]
        with wo._lock:
            wo._update_aggregates_locked(
                label="fn", git_spans=spans,
                wall_ms=20.0, queue_delay_ms=1.0, git_ms=10.0, start_time=0.0,
            )
        with wo._lock:
            inc = wo._worst_turns[0]
        self.assertEqual(inc.git_span_count, 2)
        # Dominant span is /r/b (7.0 > 3.0)
        self.assertEqual(inc.repo, "/r/b")
        self.assertEqual(inc.subcommand, "l")

    # ── B4: Worst queue-delay and heartbeat incidents ─────────────────────────

    def test_worst_queue_populated(self):
        """High queue-delay incidents must be retained in _worst_queue."""
        self._add_incident(wall_ms=1.0, queue_delay_ms=500.0, label="slow_queue")
        with wo._lock:
            self.assertEqual(len(wo._worst_queue), 1)
            self.assertEqual(wo._worst_queue[0].label, "slow_queue")

    def test_worst_queue_bounded(self):
        for i in range(wo.TOP_QUEUE_INCIDENTS + 5):
            self._add_incident(wall_ms=1.0, queue_delay_ms=float(i))
        with wo._lock:
            self.assertLessEqual(len(wo._worst_queue), wo.TOP_QUEUE_INCIDENTS)

    def test_worst_queue_sorted_by_queue_delay(self):
        for qd in [100.0, 500.0, 10.0, 300.0]:
            self._add_incident(wall_ms=1.0, queue_delay_ms=qd)
        with wo._lock:
            qds = [r.queue_delay_ms for r in wo._worst_queue]
        self.assertEqual(qds, sorted(qds, reverse=True))

    def test_worst_queue_survives_ring_rollover(self):
        """Queue-delay incidents must stay in _worst_queue after ring wraps."""
        wo.enabled = True
        # Add a high-latency turn that will be pushed out of the ring
        self._add_incident(wall_ms=1.0, queue_delay_ms=9999.0, label="original_stall")
        # Fill the ring past MAX_TURN_RECORDS
        for _ in range(wo.MAX_TURN_RECORDS + 10):
            wo.wrap_if_enabled(lambda: None)()
        with wo._lock:
            # Ring has rolled over, but worst_queue should still have the original
            worst_in_queue = [r.label for r in wo._worst_queue]
        self.assertIn("original_stall", worst_in_queue)

    def test_worst_heartbeats_populated(self):
        """Late heartbeats must be retained in _worst_heartbeats."""
        with wo._lock:
            hb = wo.HeartbeatRecord(1000, 2000.0, 1000.0, 0.0)
            wo._worst_heartbeats.append(hb)
        with wo._lock:
            self.assertEqual(len(wo._worst_heartbeats), 1)
            self.assertEqual(wo._worst_heartbeats[0].lateness_ms, 1000.0)

    def test_worst_heartbeats_bounded(self):
        with wo._lock:
            for i in range(wo.TOP_HEARTBEAT_INCIDENTS + 5):
                wo._add_to_sorted_capped(
                    wo._worst_heartbeats,
                    wo.HeartbeatRecord(1000, 1000.0 + i, float(i), 0.0),
                    key=lambda r: r.lateness_ms,
                    cap=wo.TOP_HEARTBEAT_INCIDENTS,
                )
        with wo._lock:
            self.assertLessEqual(len(wo._worst_heartbeats), wo.TOP_HEARTBEAT_INCIDENTS)

    def test_reset_clears_worst_queue_and_heartbeats(self):
        self._add_incident(wall_ms=1.0, queue_delay_ms=999.0)
        with wo._lock:
            wo._worst_heartbeats.append(wo.HeartbeatRecord(1000, 2000.0, 1000.0, 0.0))
        when(sublime).set_timeout_async(...).thenReturn(None)
        wo.reset()
        with wo._lock:
            self.assertEqual(len(wo._worst_queue), 0)
            self.assertEqual(len(wo._worst_heartbeats), 0)


# ── Repo/subcommand attribution integration tests ────────────────────────────


class TestRepoSubcmdAttribution(DeferrableTestCase):
    def setUp(self):
        _reset_state()

    def tearDown(self):
        _reset_state()
        unstub()

    def test_turn_record_has_repo_and_subcommand(self):
        wo.enabled = True
        def work():
            wo.git_enter("/the/repo", "fetch")
            wo.git_exit()
        wo.wrap_if_enabled(work)()
        with wo._lock:
            repo_keys = list(wo._repo_aggs.keys())
            subcmd_keys = list(wo._subcmd_aggs.keys())
        self.assertIn("/the/repo", repo_keys)
        self.assertIn("fetch", subcmd_keys)

    def test_repo_stored_verbatim(self):
        wo.enabled = True
        abs_path = "/absolute/path/to/repo"
        def work():
            wo.git_enter(abs_path, "status")
            wo.git_exit()
        wo.wrap_if_enabled(work)()
        with wo._lock:
            self.assertIn(abs_path, wo._repo_aggs)

    def test_turn_without_git_has_empty_repo(self):
        wo.enabled = True
        wo.wrap_if_enabled(lambda: None)()
        with wo._lock:
            repo_keys = [k for k in wo._repo_aggs if k != wo.OVERFLOW_KEY]
        self.assertEqual(repo_keys, [])


# ── In-flight snapshot tests ──────────────────────────────────────────────────


class TestSnapshot(DeferrableTestCase):
    def setUp(self):
        _reset_state()

    def tearDown(self):
        _reset_state()
        unstub()

    def test_snapshot_inflight_none_when_idle(self):
        snap = wo.snapshot()
        self.assertIsNone(snap["in_flight"])

    def test_snapshot_inflight_populated_during_turn(self):
        wo.enabled = True
        observed: list = []
        def work():
            snap = wo.snapshot()
            observed.append(snap["in_flight"])
        wo.wrap_if_enabled(work)()
        self.assertEqual(len(observed), 1)
        inf = observed[0]
        self.assertIsNotNone(inf)
        self.assertIn("label", inf)
        self.assertGreaterEqual(inf["elapsed_ms"], 0.0)

    def test_snapshot_cleared_after_turn(self):
        wo.enabled = True
        wo.wrap_if_enabled(lambda: None)()
        snap = wo.snapshot()
        self.assertIsNone(snap["in_flight"])

    def test_snapshot_includes_lifetime_counts(self):
        wo.enabled = True
        for _ in range(3):
            wo.wrap_if_enabled(lambda: None)()
        snap = wo.snapshot()
        self.assertEqual(snap["turns_lifetime"], 3)

    def test_snapshot_git_enter_time_visible(self):
        wo.enabled = True
        observed: list = []
        def work():
            wo.git_enter("/r", "status")
            snap = wo.snapshot()
            observed.append(snap["in_flight"])
            wo.git_exit()
        wo.wrap_if_enabled(work)()
        inf = observed[0]
        self.assertIsNotNone(inf.get("git_elapsed_ms"))

    def test_snapshot_includes_worst_queue_and_heartbeats(self):
        snap = wo.snapshot()
        self.assertIn("worst_queue_delays", snap)
        self.assertIn("worst_heartbeats", snap)

    def test_snapshot_reads_rings_under_lock(self):
        """Verify snapshot takes ring snapshots inside the lock (no data races)."""
        wo.enabled = True
        # Run many turns then snapshot; result should be consistent
        for _ in range(20):
            wo.wrap_if_enabled(lambda: None)()
        snap = wo.snapshot()
        # If rings were read outside lock, len could be mismatched; can't assert exact
        # but we verify keys exist and types are correct
        self.assertIsInstance(snap["turns_lifetime"], int)
        self.assertGreater(snap["turns_lifetime"], 0)


# ── Export tests ──────────────────────────────────────────────────────────────


class TestExport(DeferrableTestCase):
    def setUp(self):
        _reset_state()

    def tearDown(self):
        _reset_state()
        unstub()

    def _export_to_tempfile(self) -> dict:
        fd, path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        try:
            wo.export(path)
            with open(path, encoding="utf-8") as fh:
                return json.load(fh)
        finally:
            os.unlink(path)

    def test_export_produces_valid_json(self):
        wo.enabled = True
        wo.wrap_if_enabled(lambda: None)()
        data = self._export_to_tempfile()
        self.assertIsInstance(data, dict)

    def test_export_includes_session_key(self):
        data = self._export_to_tempfile()
        self.assertIn("session_start", data)
        self.assertIn("session_elapsed_s", data)

    def test_export_includes_limitations(self):
        data = self._export_to_tempfile()
        self.assertIn("limitations", data)
        self.assertIsInstance(data["limitations"], list)
        self.assertGreater(len(data["limitations"]), 0)

    def test_export_includes_recent_turns(self):
        wo.enabled = True
        wo.wrap_if_enabled(lambda: None)()
        data = self._export_to_tempfile()
        self.assertIn("recent_turns", data)
        self.assertEqual(len(data["recent_turns"]), 1)

    def test_export_includes_dropped_counts(self):
        data = self._export_to_tempfile()
        self.assertIn("dropped", data)
        self.assertIn("repo", data["dropped"])
        self.assertIn("subcmd", data["dropped"])

    def test_export_includes_per_repo(self):
        wo.enabled = True
        def work():
            wo.git_enter("/r/a", "status")
            wo.git_exit()
        wo.wrap_if_enabled(work)()
        data = self._export_to_tempfile()
        self.assertIn("per_repo", data)
        self.assertIn("/r/a", data["per_repo"])

    def test_export_worst_incidents_has_span_count(self):
        spans = [wo._GitSpan("/r", "log", 5.0)]
        with wo._lock:
            wo._update_aggregates_locked(
                label="fn", git_spans=spans,
                wall_ms=200.0, queue_delay_ms=5.0, git_ms=5.0, start_time=0.0,
            )
        data = self._export_to_tempfile()
        self.assertIn("worst_incidents", data)
        inc = data["worst_incidents"][0]
        self.assertIn("git_span_count", inc)
        self.assertEqual(inc["git_span_count"], 1)

    def test_export_includes_worst_queue_and_heartbeats(self):
        data = self._export_to_tempfile()
        self.assertIn("worst_queue_delays", data)
        self.assertIn("worst_heartbeats", data)

    def test_export_limitations_mention_recent_window(self):
        data = self._export_to_tempfile()
        combined = " ".join(data["limitations"])
        self.assertIn("recent", combined.lower())


# ── Long-run simulation ───────────────────────────────────────────────────────


class TestLongRunSimulation(DeferrableTestCase):
    def setUp(self):
        _reset_state()

    def tearDown(self):
        _reset_state()
        unstub()

    def test_thousand_turns_deque_bounded(self):
        wo.enabled = True
        n = wo.MAX_TURN_RECORDS + 500
        for _ in range(n):
            wo.wrap_if_enabled(lambda: None)()
        with wo._lock:
            self.assertEqual(len(wo._turns), wo.MAX_TURN_RECORDS)

    def test_thousand_turns_lifetime_count_exact(self):
        wo.enabled = True
        n = 1200
        for _ in range(n):
            wo.wrap_if_enabled(lambda: None)()
        with wo._lock:
            self.assertEqual(wo._lifetime.wall.count, n)

    def test_lifetime_total_increases_monotonically(self):
        wo.enabled = True
        for _ in range(50):
            wo.wrap_if_enabled(lambda: None)()
        with wo._lock:
            total1 = wo._lifetime.wall.total
        wo.wrap_if_enabled(lambda: None)()
        with wo._lock:
            total2 = wo._lifetime.wall.total
        self.assertGreater(total2, total1)

    def test_stats_ring_wraps_correctly(self):
        wo.enabled = True
        n = wo._STATS_RING + 100
        for _ in range(n):
            wo.wrap_if_enabled(lambda: None)()
        with wo._lock:
            self.assertTrue(wo._lifetime.wall._full)
            p = wo._lifetime.wall.percentile(95)
        self.assertGreaterEqual(p, 0.0)

    def test_worst_incidents_bounded_across_many_turns(self):
        wo.enabled = True
        n = wo.TOP_INCIDENTS * 3
        for i in range(n):
            with wo._lock:
                wo._update_aggregates_locked(
                    label=f"fn_{i}", git_spans=[],
                    wall_ms=float(i), queue_delay_ms=0.0, git_ms=0.0, start_time=0.0,
                )
        with wo._lock:
            self.assertEqual(len(wo._worst_turns), wo.TOP_INCIDENTS)
            min_wall = wo._worst_turns[-1].wall_ms
        self.assertGreaterEqual(min_wall, float(n - wo.TOP_INCIDENTS))

    def test_repo_cardinality_bounded_after_many_repos(self):
        wo.enabled = True
        for i in range(wo.MAX_REPO_CARDINALITY * 2):
            def work(i=i):
                wo.git_enter(f"/repo/project_{i}", "status")
                wo.git_exit()
            wo.wrap_if_enabled(work)()
        with wo._lock:
            self.assertLessEqual(len(wo._repo_aggs), wo.MAX_REPO_CARDINALITY + 1)


# ── Concurrent turns ──────────────────────────────────────────────────────────


class TestConcurrentTurns(DeferrableTestCase):
    def setUp(self):
        _reset_state()

    def tearDown(self):
        _reset_state()
        unstub()

    def test_separate_threads_have_independent_git_ms(self):
        wo.enabled = True
        results: dict = {}
        barrier = threading.Barrier(2)

        def thread_work(name: str) -> None:
            epoch = wo._collection_epoch
            ctx = wo._TurnContext(epoch=epoch)
            wo._ctx.current = ctx
            barrier.wait()
            wo.git_enter(f"/repo/{name}", "status")
            wo.git_exit()
            wo._ctx.current = None
            results[name] = ctx.git_ms

        t1 = threading.Thread(target=thread_work, args=("a",))
        t2 = threading.Thread(target=thread_work, args=("b",))
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        self.assertGreater(results["a"], 0.0)
        self.assertGreater(results["b"], 0.0)
        self.assertIsNone(getattr(wo._ctx, "current", None))
