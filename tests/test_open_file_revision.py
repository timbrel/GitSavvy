from concurrent.futures import Future
import os
from pathlib import Path
from threading import current_thread
from time import monotonic

import sublime
from unittesting import DeferrableTestCase

from GitSavvy.core.interfaces import show_file_at_commit as module
from GitSavvy.tests.mockito import ANY, unstub, when
from GitSavvy.tests.test_git.common import GitRepoTestCase


class TestOpenFileInRepository(GitRepoTestCase):
    def test_opens_latest_revision_without_a_waiting_view(self):
        self.window.run_command("gs_show_file_at_commit", {"filepath": os.path.join(self._temp_dir, "README.md")})
        view = self.window.active_view()
        self.assertEqual(view.substr(sublime.Region(0, view.size())), "README")

    def test_closing_dinosaur_aborts_running_git(self):
        self.addCleanup(unstub)
        self.addCleanup(setattr, module, "OPEN_BUDGET", module.OPEN_BUDGET)
        module.OPEN_BUDGET = 0
        marker = Path(self._temp_dir, "git-started")
        stopped = Future()

        def slow_load(cmd, *args):
            try:
                cmd.git("-c", "alias.slow=!echo ready > git-started; exec sleep 5", "slow")
            except Exception as e:
                stopped.set_exception(e)
                raise
            else:
                stopped.set_result(None)

        when(module).prepare_revision(...).thenAnswer(slow_load)
        self.window.run_command("gs_show_file_at_commit", {"filepath": os.path.join(self._temp_dir, "README.md")})
        waiting = self.window.active_view()
        self.assertTrue(waiting.settings().get("git_savvy.loading_revision_view"))
        yield marker.exists
        waiting.close()
        yield stopped.done
        with self.assertRaises(module.AbortedGitCall):
            stopped.result()


class TestWaitingView(DeferrableTestCase):
    def setUp(self):
        self.window = sublime.active_window()
        source = self.window.new_file()
        source.set_scratch(True)
        source.settings().set("git_savvy.repo_path", sublime.packages_path())
        self.addCleanup(source.close)
        self.addCleanup(unstub)
        self.addCleanup(setattr, module, "OPEN_BUDGET", module.OPEN_BUDGET)
        module.OPEN_BUDGET = 0.05
        self.future = module.AbortableFuture()
        self.ticks = []
        self.ui_turns = []

        def start(*args):
            self.started = monotonic()
            sublime.set_timeout(lambda: self.ui_turns.append("yield"))
            return self.future

        new_buffer = module.new_revision_buffer

        def create(*args):
            self.time_before_creation = monotonic() - self.started
            return new_buffer(*args)

        when(module).run_as_abortable_future(...).thenAnswer(start)
        when(module).new_revision_buffer(...).thenAnswer(create)
        when(module).enqueue_on_worker(...)
        schedule = sublime.set_timeout
        when(sublime).set_timeout(...).thenAnswer(schedule)
        when(sublime).set_timeout(ANY, 250).thenAnswer(lambda fn, ms: self.ticks.append(fn))

        self.window.run_command("gs_show_file_at_commit", {"filepath": "/repo/test", "commit_hash": "abc"})
        self.view = self.window.active_view()
        self.addCleanup(self.view.close)

    def test_blocks_main_before_creating_any_view(self):
        self.assertGreaterEqual(self.time_before_creation, module.OPEN_BUDGET)
        self.assertEqual(self.ui_turns, [])
        self.assertTrue(self.view.settings().get("git_savvy.loading_revision_view"))

    def test_slow_load_finishes_in_place_without_stealing_focus(self):
        self.assertIn("unusually long", self.view.substr(sublime.Region(0, self.view.size())))
        self.assertTrue(self.view.get_regions(module.WAIT_LINK))
        self.assertFalse(self.view.settings().get("git_savvy.show_file_at_commit_view"))
        self.ticks.pop()()
        other = self.window.new_file()
        self.addCleanup(other.close)

        self.future.set_result(module.Revision("/repo/test", "abc", None, "done"))
        self.ticks.pop()()

        self.assertEqual(self.window.active_view(), other)
        self.assertEqual(self.view.substr(sublime.Region(0, self.view.size())), "done")
        self.assertTrue(self.view.settings().get("git_savvy.show_file_at_commit_view"))
        self.assertFalse(self.view.get_regions(module.WAIT_LINK))
        self.assertFalse(self.ticks)

    def test_closing_placeholder_discards_late_result(self):
        self.view.close()
        remaining = self.window.views()
        self.future.set_result(module.Revision("/repo/test", "abc", None, "done"))
        self.ticks.pop()()
        self.assertEqual(self.window.views(), remaining)
        self.assertFalse(self.ticks)

    def test_existing_revision_takes_focus_from_waiting_view(self):
        existing = self.make_existing_revision()
        self.window.focus_view(self.view)
        self.finish_with_existing_revision()

        self.assertFalse(self.view.is_valid())
        self.assertEqual(self.window.active_view(), existing)

    def test_existing_revision_does_not_take_focus_from_another_view(self):
        self.make_existing_revision()
        other = self.window.new_file()
        self.addCleanup(other.close)
        self.finish_with_existing_revision()

        self.assertFalse(self.view.is_valid())
        self.assertEqual(self.window.active_view(), other)

    def test_missing_revision_removes_waiting_view(self):
        self.future.set_result(None)
        self.ticks.pop()()
        self.assertFalse(self.view.is_valid())

    def test_unexpected_failure_closes_waiting_view_and_propagates(self):
        self.future.set_exception(RuntimeError("load failed"))
        with self.assertRaisesRegex(RuntimeError, "load failed"):
            self.ticks.pop()()
        self.assertFalse(self.view.is_valid())

    def test_abort_removes_waiting_view(self):
        self.future.set_exception(module.AbortedGitCall("Aborted", show_panel=False))
        self.ticks.pop()()
        self.assertFalse(self.view.is_valid())
        self.assertFalse(self.ticks)


    def make_existing_revision(self):
        existing = self.window.new_file()
        existing.set_scratch(True)
        self.addCleanup(existing.close)
        settings = existing.settings()
        settings.set("git_savvy.repo_path", sublime.packages_path())
        settings.set("git_savvy.file_path", "/repo/test")
        settings.set("git_savvy.show_file_at_commit_view.commit", "abc")
        settings.set("git_savvy.show_file_at_commit_view", True)
        return existing

    def finish_with_existing_revision(self):
        self.future.set_result(module.Revision("/repo/test", "abc", None, None))
        self.ticks.pop()()


class TestOpenFileRevision(DeferrableTestCase):
    def test_constructor_loads_historical_path_off_main_thread(self):
        window = sublime.active_window()
        source = window.new_file()
        source.set_scratch(True)
        self.addCleanup(source.close)
        self.addCleanup(unstub)
        source.settings().set("git_savvy.repo_path", sublime.packages_path())
        path = "/repo/current.py"
        threads = []

        def resolve(*args):
            threads.append(current_thread())
            return "/repo/older.py"

        when(module.GitCommand).to_short_hash("123abcd").thenReturn("123abcd")
        when(module.GitCommand).filename_at_head(path, "123abcd").thenReturn(path)
        when(module.GitCommand).filename_at_commit(path, "123abcd").thenAnswer(resolve)
        when(module.GitCommand).get_file_content_at_commit("/repo/older.py", "123abcd").thenReturn("old text")
        when(module).enqueue_on_worker(...)

        window.run_command("gs_show_file_at_commit", {"filepath": path, "commit_hash": "123abcd"})

        result = window.active_view()
        self.addCleanup(result.close)
        self.assertEqual(result.substr(sublime.Region(0, result.size())), "old text")
        self.assertEqual(len(threads), 1)
        self.assertIsNot(threads[0], current_thread())
