from __future__ import annotations

from concurrent.futures import Future
from threading import current_thread

import sublime
from unittesting import DeferrableTestCase

from GitSavvy.tests.mockito import unstub, verify, when
from GitSavvy.core.interfaces import show_file_at_commit as module
from GitSavvy.core.view import Position


class TestShowFileAtCommitPosition(DeferrableTestCase):
    def setUp(self) -> None:
        self.addCleanup(unstub)
        self.cmd = module.GitCommand()

    def test_maps_row_using_historical_paths_and_preserves_column_and_offset(self):
        when(self.cmd).filename_at_commit("/repo/current.py", "older").thenReturn(
            "/repo/old.py"
        )
        when(self.cmd).filename_at_commit("/repo/current.py", "newer").thenReturn(
            "/repo/new.py"
        )
        when(self.cmd).find_matching_lineno_between_files(
            ("older", "/repo/old.py"), ("newer", "/repo/new.py"), 4
        ).thenReturn(12)

        position = module.map_position(
            self.cmd, "/repo/current.py", "older", "newer", Position(3, 7, 20.5)
        )

        self.assertEqual(position, Position(11, 7, 20.5))
        verify(self.cmd).filename_at_commit("/repo/current.py", "older")
        verify(self.cmd).filename_at_commit("/repo/current.py", "newer")
        verify(self.cmd).find_matching_lineno_between_files(
            ("older", "/repo/old.py"), ("newer", "/repo/new.py"), 4
        )

    def test_missing_position_does_not_query_git(self):
        when(self.cmd).filename_at_commit(...).thenRaise(
            AssertionError("Unexpected lookup")
        )
        when(self.cmd).find_matching_lineno_between_files(...).thenRaise(
            AssertionError("Unexpected line mapping")
        )

        position = module.map_position(
            self.cmd, "/repo/current.py", "older", "newer", None
        )

        self.assertIsNone(position)
        verify(self.cmd, times=0).filename_at_commit(...)
        verify(self.cmd, times=0).find_matching_lineno_between_files(...)


class TestShowFileAtCommitRefresh(DeferrableTestCase):
    def setUp(self) -> None:
        self.view = sublime.active_window().new_file()
        self.view.set_scratch(True)
        self.addCleanup(self.view.close)
        self.addCleanup(unstub)
        self.addCleanup(module.views_with_reference_document.discard, self.view)
        settings = self.view.settings()
        settings.set("git_savvy.repo_path", sublime.packages_path())
        settings.set("git_savvy.file_path", "/repo/current.py")
        settings.set("git_savvy.show_file_at_commit_view.commit", "abc123")
        self.cmd = module.GitContext.for_view(self.view)
        when(module.GitContext).for_view(self.view).thenReturn(self.cmd)
        self.queued = []
        self.events = []
        when(module).enqueue_on_worker(...).thenAnswer(
            lambda fn, *args: self.queued.append((fn, args))
        )
        when(self.cmd).filename_at_commit("/repo/current.py", "abc123").thenAnswer(
            lambda *args: self.record("resolve", "/repo/historical.py")
        )
        when(self.cmd).get_file_content_at_commit(
            "/repo/historical.py", "abc123"
        ).thenAnswer(lambda *args: self.record("load", "historical content"))
        when(module).render(...).thenAnswer(
            lambda *args: self.record("render")
        )
        when(self.view).reset_reference_document().thenAnswer(
            lambda: self.record("reset reference")
        )

    def test_refreshes_inline_and_defers_details_and_reference(self) -> None:
        position = Position(3, 2, 0)
        settings = self.view.settings()
        settings.set("git_savvy.show_file_at_commit_view.commit", "older")

        module.refresh_view(self.view, "abc123", position)

        self.assertEqual(
            settings.get("git_savvy.show_file_at_commit_view.commit"), "abc123"
        )
        self.assertEqual(self.events, ["resolve", "load", "render", "reset reference"])
        verify(module).render(self.view, "historical content", position)
        self.assert_deferred_updates()

    def test_log_highlight_queues_refresh_at_the_call_site(self) -> None:
        settings = self.view.settings()
        settings.set("git_savvy.show_file_at_commit_view.commit", "older")
        cmd = module.gs_show_file_at_commit_open_log(self.view)
        when(cmd).to_short_hash("abc123").thenReturn("abc123")
        when(module).capture_cur_position(self.view).thenReturn(None)

        cmd._on_highlight("abc123")

        self.assertEqual(self.events, [])
        self.assertEqual(
            settings.get("git_savvy.show_file_at_commit_view.commit"), "older"
        )
        verify(module.GitContext, times=0).for_view(self.view)
        self.assertEqual(self.queued, [
            (module.refresh_view, (self.view, "abc123", None)),
        ])
        fn, args = self.queued.pop(0)
        fn(*args)

        self.assertEqual(
            settings.get("git_savvy.show_file_at_commit_view.commit"), "abc123"
        )
        self.assertEqual(self.events, ["resolve", "load", "render", "reset reference"])
        self.assert_deferred_updates()

    def test_worker_refresh_runs_inline_on_worker(self):
        context_threads = []
        worker_threads = []

        def create_context(view):
            context_threads.append(current_thread())
            return self.cmd

        when(module.GitContext).for_view(self.view).thenAnswer(create_context)
        future = Future()

        def refresh():
            try:
                worker_threads.append(current_thread())
                module.refresh_view(self.view, "abc123")
                future.set_result(len(self.queued))
            except Exception as e:
                future.set_exception(e)

        sublime.set_timeout_async(refresh)
        yield future.done

        self.assertEqual(future.result(), 2)
        self.assertEqual(context_threads, worker_threads)
        self.assertEqual(self.events, ["resolve", "load", "render", "reset reference"])
        self.assert_deferred_updates()

    def test_reference_only_refresh_resets_inline_and_defers_loading(self) -> None:
        module.refresh_reference_document(self.view)

        verify(module.GitContext).for_view(self.view)
        self.assertEqual(self.events, ["reset reference"])
        self.assertEqual(self.queued, [(
            module.update_reference_document,
            (self.cmd, self.view, "abc123", "/repo/current.py")
        )])

    def test_activation_refreshes_reference_document_directly(self) -> None:
        listener = module.RenewReferenceDocument(self.view)

        listener.on_activated()

        verify(module.GitContext).for_view(self.view)
        self.assertEqual(self.events, ["reset reference"])
        self.assertEqual(len(self.queued), 1)

    def test_activation_keeps_existing_reference_document(self) -> None:
        module.views_with_reference_document.add(self.view)
        listener = module.RenewReferenceDocument(self.view)

        listener.on_activated()

        self.assertEqual(self.events, [])
        self.assertEqual(self.queued, [])

    def test_reference_document_loads_previous_historical_path(self) -> None:
        when(module).get_previous_commit(
            self.cmd, self.view, "abc123", "/repo/current.py"
        ).thenReturn("older")
        when(self.cmd).filename_at_commit(
            "/repo/current.py", "older"
        ).thenReturn("/repo/old.py")
        when(self.cmd).get_file_content_at_commit(
            "/repo/old.py", "older"
        ).thenReturn("previous content")
        when(self.view).set_reference_document("previous content")

        module.update_reference_document(
            self.cmd, self.view, "abc123", "/repo/current.py"
        )

        verify(self.view).set_reference_document("previous content")
        self.assertIn(self.view, module.views_with_reference_document)

    def test_initial_revision_uses_empty_reference_document(self) -> None:
        when(module).get_previous_commit(
            self.cmd, self.view, "abc123", "/repo/current.py"
        ).thenReturn(None)
        when(self.view).set_reference_document("")

        module.update_reference_document(
            self.cmd, self.view, "abc123", "/repo/current.py"
        )

        verify(self.view).set_reference_document("")
        verify(self.cmd, times=0).filename_at_commit(...)
        verify(self.cmd, times=0).get_file_content_at_commit(...)
        self.assertIn(self.view, module.views_with_reference_document)

    def assert_deferred_updates(self) -> None:
        args = (self.cmd, self.view, "abc123", "/repo/current.py")
        self.assertEqual(self.queued, [
            (module.update_commit_details, args),
            (module.update_reference_document, args),
        ])

    def record(self, event, result=None):
        self.events.append(event)
        return result
