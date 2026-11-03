from __future__ import annotations

import importlib
import json
import os
import tempfile

import sublime
from unittesting import DeferrableTestCase

from GitSavvy.core import debug, worker_occupancy as wo
from GitSavvy.tests.mockito import unstub, verify, when


class TestDebugWorkerOccupancy(DeferrableTestCase):
    def setUp(self) -> None:
        self.was_logging = debug.enabled
        self.log = debug._log
        when(sublime).set_timeout_async(...).thenReturn(None)
        wo.disable()
        wo.reset()
        wo._resume_after_reload = False
        debug.set_logging_enabled(False)
        self.directory = tempfile.TemporaryDirectory()
        when(sublime).packages_path().thenReturn(self.directory.name)

    def tearDown(self) -> None:
        wo.disable()
        wo.reset()
        wo._resume_after_reload = False
        debug.set_logging_enabled(self.was_logging)
        debug._log = self.log
        unstub()
        self.directory.cleanup()

    def test_logger_starts_occupancy_and_stop_exports_to_user(self) -> None:
        debug.start_logging()
        self.assertTrue(debug.enabled)
        self.assertTrue(wo.enabled)
        wo.wrap_if_enabled(lambda: None)()

        debug.stop_logging()

        self.assertFalse(debug.enabled)
        self.assertFalse(wo.enabled)
        user = os.path.join(self.directory.name, "User")
        names = os.listdir(user)
        self.assertEqual(len(names), 1)
        self.assertTrue(names[0].startswith("GitSavvy-worker-occupancy-"))
        with open(os.path.join(user, names[0]), encoding="utf-8") as handle:
            report = json.load(handle)
        self.assertEqual(report["turns_lifetime"], 1)

    def test_repeated_stop_does_not_export_again(self) -> None:
        when(wo).export_to_user().thenReturn(None)
        debug.start_logging()
        debug.stop_logging()
        debug.stop_logging()
        verify(wo, times=1).export_to_user()

    def test_export_failure_still_stops_logging_and_retains_records(self) -> None:
        when(wo).export_to_user().thenRaise(OSError("test: read-only directory"))
        debug.start_logging()
        wo.wrap_if_enabled(lambda: None)()
        debug.stop_logging()
        self.assertFalse(debug.enabled)
        self.assertFalse(wo.enabled)
        self.assertEqual(wo.snapshot()["turns_lifetime"], 1)

    def test_hot_reload_preserves_session_and_rearms_heartbeat(self) -> None:
        callbacks = []
        when(sublime).set_timeout_async(...).thenAnswer(
            lambda callback, delay=0: callbacks.append(callback)
        )
        debug.start_logging()
        wo.wrap_if_enabled(lambda: None)()
        before = wo.snapshot()
        old_heartbeat = callbacks[-1]

        wo.on_plugin_unload()
        importlib.reload(debug)
        importlib.reload(wo)
        wo.on_plugin_load()

        after = wo.snapshot()
        self.assertTrue(debug.enabled)
        self.assertTrue(wo.enabled)
        self.assertEqual(after["session_start"], before["session_start"])
        self.assertEqual(after["turns_lifetime"], 1)
        self.assertEqual(len(callbacks), 2)
        old_heartbeat()
        self.assertEqual(len(callbacks), 2)  # old chain cannot revive
        callbacks[-1]()
        self.assertEqual(len(callbacks), 3)  # resumed chain continues
        self.assertFalse(os.path.exists(os.path.join(self.directory.name, "User")))

    def test_disabled_logger_stays_disabled_after_reload_lifecycle(self) -> None:
        wo.on_plugin_unload()
        wo.on_plugin_load()
        self.assertFalse(wo.enabled)

    def test_existing_logging_switch_enables_profiler_on_load(self) -> None:
        debug.set_logging_enabled(True)
        wo.on_plugin_load()
        self.assertTrue(wo.enabled)

    def test_manual_recording_also_resumes_after_reload_lifecycle(self) -> None:
        wo.enable()
        wo.on_plugin_unload()
        wo.on_plugin_load()
        self.assertTrue(wo.enabled)
