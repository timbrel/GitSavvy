from unittesting import DeferrableTestCase

from GitSavvy.core import git_activity
from GitSavvy.tests.mockito import unstub, when


class TestGitActivity(DeferrableTestCase):
    def setUp(self):
        git_activity._reset()
        git_activity.initialize()
        self.now = 0.0
        self.callbacks = []
        self.view = View()
        self.window = Window(self.view)
        when(git_activity.time).monotonic().thenAnswer(lambda: self.now)
        when(git_activity).enqueue_on_ui(...).thenAnswer(lambda fn, *args: fn(*args))
        when(git_activity.sublime).active_window().thenAnswer(lambda: self.window)
        when(git_activity.sublime).set_timeout(...).thenAnswer(
            lambda callback, delay: self.callbacks.append((callback, delay))
        )

    def tearDown(self):
        git_activity.shutdown()
        unstub()

    def test_starts_after_five_seconds_and_uses_rolling_frames_with_text(self):
        git_activity.start(1)
        self.assertEqual(self.view.status, "")
        self.assertEqual(self.callbacks[0][1], 5000)

        self.fire_next()
        self.assertEqual(self.view.status, "◐ · Git is running (open Git Command Log for details)")
        self.assertEqual(self.callbacks[0][1], 200)
        self.fire_next()
        self.assertEqual(self.view.status, "◓ · Git is running (open Git Command Log for details)")

    def test_fast_command_never_shows_a_spinner(self):
        git_activity.start(1)
        git_activity.finish(1)
        self.fire_next()
        self.assertEqual(self.view.status, "")
        self.assertEqual(self.callbacks, [])

    def test_spinner_moves_between_views_and_windows_without_restarting(self):
        git_activity.start(1)
        self.fire_next()
        self.fire_next()
        frame = git_activity._frame
        previous = self.view
        self.view = View()
        self.window = Window(self.view)

        git_activity.clear_view(previous)
        git_activity.refresh()

        self.assertEqual(previous.status, "")
        self.assertEqual(git_activity._frame, frame)
        self.assertIn("Git is running", self.view.status)
        self.assertEqual(len(self.callbacks), 1)
        git_activity.finish(1)
        self.assertEqual(self.view.status, "")

    def test_concurrent_commands_share_one_animation(self):
        git_activity.start(1)
        git_activity.start(2)
        self.fire_next()
        self.fire_next()
        self.assertEqual(len(self.callbacks), 1)

        git_activity.finish(1)
        self.assertIn("Git is running", self.view.status)
        git_activity.finish(2)
        self.assertEqual(self.view.status, "")
        self.fire_next()
        self.assertEqual(self.callbacks, [])

    def test_new_slow_command_reuses_pending_tick(self):
        git_activity.start(1)
        git_activity.start(2)
        self.fire_next()
        git_activity.finish(1)
        # The second command becomes slow before the first one's pending tick.
        self.fire_next()
        self.assertEqual(len(self.callbacks), 1)
        self.fire_next()
        self.assertEqual(len(self.callbacks), 1)
        self.assertIn("Git is running", self.view.status)

    def test_closing_view_does_not_stop_global_activity(self):
        git_activity.start(1)
        self.fire_next()
        git_activity.clear_view(self.view)
        self.view.valid = False
        self.window = None
        self.fire_next()
        self.assertEqual(self.view.status, "")
        self.assertIn(1, git_activity._slow)

        self.view = View()
        self.window = Window(self.view)
        git_activity.refresh()
        self.assertIn("Git is running", self.view.status)

    def test_shutdown_clears_status_and_invalidates_delayed_callbacks(self):
        git_activity.start(1)
        git_activity.start(2)
        self.fire_next()
        git_activity.shutdown()
        for callback, _ in self.callbacks[:]:
            callback()
        self.assertEqual(self.view.status, "")
        self.assertEqual(git_activity._slow, set())
        self.assertEqual(git_activity._running, set())
        self.assertFalse(git_activity._tick_pending)

    def test_shutdown_rejects_late_starts(self):
        git_activity.shutdown()
        git_activity.start(1)

        self.assertEqual(git_activity._running, set())
        self.assertEqual(self.callbacks, [])

    def test_queued_start_cannot_cross_shutdown_and_reinitialization(self):
        queued = []
        when(git_activity).enqueue_on_ui(...).thenAnswer(
            lambda fn, *args: queued.append(lambda: fn(*args))
        )
        git_activity.start(1)
        git_activity.shutdown()
        git_activity.initialize()
        for callback in queued:
            callback()

        self.assertEqual(git_activity._running, set())
        self.assertEqual(self.callbacks, [])
        queued.clear()
        git_activity.start(2)
        queued.pop(0)()
        self.fire_next()
        self.assertEqual(git_activity._slow, {2})
        self.assertIn("Git is running", self.view.status)
        # Restore immediate cleanup for tearDown.
        when(git_activity).enqueue_on_ui(...).thenAnswer(lambda fn, *args: fn(*args))

    def test_old_reveal_and_tick_cannot_cross_reinitialization(self):
        git_activity.start(1)
        git_activity.start(2)
        self.fire_next()
        old_callbacks = self.callbacks[:]
        self.callbacks.clear()
        git_activity.shutdown()
        git_activity.initialize()
        git_activity.start(3)
        for callback, _ in old_callbacks:
            callback()

        self.assertEqual(git_activity._running, {3})
        self.assertEqual(git_activity._slow, set())
        self.assertEqual(len(self.callbacks), 1)
        self.fire_next()
        self.assertEqual(git_activity._slow, {3})

    def test_ui_queue_delay_counts_towards_five_seconds(self):
        queued = []
        when(git_activity).enqueue_on_ui(...).thenAnswer(
            lambda fn, *args: queued.append(lambda: fn(*args))
        )
        git_activity.start(1)
        self.now = 4.0
        queued.pop(0)()
        self.assertEqual(self.callbacks[0][1], 1000)

        git_activity.start(2)
        self.now = 10.0
        queued.pop(0)()
        self.assertEqual(self.callbacks[1][1], 0)
        when(git_activity).enqueue_on_ui(...).thenAnswer(lambda fn, *args: fn(*args))

    def fire_next(self):
        callback, _ = self.callbacks.pop(0)
        callback()


class View:
    def __init__(self):
        self.status = ""
        self.valid = True

    def is_valid(self):
        return self.valid

    def set_status(self, key, text):
        self.status = text

    def erase_status(self, key):
        self.status = ""


class Window:
    def __init__(self, view):
        self.view = view

    def active_view(self):
        return self.view
