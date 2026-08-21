import os

from unittesting import DeferrableTestCase

from GitSavvy.core import utils
from GitSavvy.core.caches import (
    cached_until_focus_switch,
    until_focus_switch_cache,
)
from GitSavvy.tests.mockito import expect, mock, patch, unstub, verify, when


class TestOpenWorktreeInNewWindow(DeferrableTestCase):
    def tearDown(self):
        unstub()

    def test_opens_a_single_project_and_calls_back_with_the_new_window(self):
        path = "/repo"
        project_file = os.path.join(path, "repo.sublime-project")
        source_window = mock()
        project_window = mock()
        callback = mock()

        when(utils).glob(os.path.join(path, "*.sublime-project")).thenReturn([project_file])
        when(utils.sublime).windows().thenReturn(
            [source_window],
            [source_window, project_window]
        )
        when(utils.sublime).active_window().thenReturn(source_window)
        when(project_window).project_file_name().thenReturn(project_file)
        expect(source_window).run_command("open_project_or_workspace", {
            "file": project_file,
            "new_window": True
        }).thenReturn(None)
        expect(callback).on_open(project_window).thenReturn(None)
        patch(utils.runtime.on_worker, lambda fn: fn)

        utils.open_worktree_in_new_window(path, then=callback.on_open)

        verify(source_window).run_command("open_project_or_workspace", {
            "file": project_file,
            "new_window": True
        })
        verify(callback).on_open(project_window)

    def test_falls_back_if_the_project_file_is_not_unique(self):
        path = "/repo"
        callback = mock()
        when(utils).glob(os.path.join(path, "*.sublime-project")).thenReturn([
            os.path.join(path, "one.sublime-project"),
            os.path.join(path, "two.sublime-project")
        ])
        expect(utils).open_folder_in_new_window(path, then=callback).thenReturn(None)

        utils.open_worktree_in_new_window(path, then=callback)

        verify(utils).open_folder_in_new_window(path, then=callback)


class Repo:
    def __init__(self, repo_path: str = "/repo"):
        self.repo_path = repo_path
        self.calls = 0

    def expensive(self, kind: str = "remote") -> str:
        self.calls += 1
        return f"{kind}-url"


class TestCachedUntilFocusSwitch(DeferrableTestCase):
    def setUp(self):
        until_focus_switch_cache.clear()

    def test_direct_call_with_bound_method_caches_and_returns(self):
        repo = Repo()

        first = cached_until_focus_switch(repo.expensive)
        second = cached_until_focus_switch(repo.expensive)

        self.assertEqual(first, "remote-url")
        self.assertEqual(second, "remote-url")
        self.assertEqual(repo.calls, 1)

    def test_direct_call_normalizes_positional_and_keyword(self):
        repo = Repo()

        cached_until_focus_switch(repo.expensive, "x")
        cached_until_focus_switch(repo.expensive, kind="x")

        self.assertEqual(repo.calls, 1)

    def test_decorator_form_caches_per_instance(self):
        @cached_until_focus_switch
        def fetch(self, kind="remote"):
            self.calls += 1
            return f"{kind}-url"

        a = Repo("/a")
        b = Repo("/b")

        self.assertEqual(fetch(a), "remote-url")
        self.assertEqual(fetch(a), "remote-url")
        self.assertEqual(fetch(b), "remote-url")

        self.assertEqual(a.calls, 1)
        self.assertEqual(b.calls, 1)

    def test_decorator_form_normalizes_positional_and_keyword(self):
        @cached_until_focus_switch
        def fetch(self, kind="remote"):
            self.calls += 1
            return f"{kind}-url"

        repo = Repo()
        fetch(repo, "x")
        fetch(repo, kind="x")

        self.assertEqual(repo.calls, 1)
