from __future__ import annotations

import os
import tempfile

from unittesting import DeferrableTestCase

from GitSavvy.core.interfaces.log_graph_renderer import gs_log_graph_refresh, prelude
from GitSavvy.tests.mockito import mock, unstub, verify, when
from GitSavvy.tests.parameterized import parameterized as p


class StopBeforeGraphRead(Exception):
    pass


class TestGraphReflogDecorations(DeferrableTestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.addCleanup(unstub)
        self.repo_path = directory.name
        self.common_dir = os.path.join(self.repo_path, ".git")
        self.linked_path = os.path.join(self.repo_path, "linked")
        self.linked_dir = os.path.join(self.common_dir, "worktrees", "linked")
        os.makedirs(self.linked_dir)
        os.makedirs(self.linked_path)
        self.write_file(os.path.join(self.linked_path, ".git"), f"gitdir: {self.linked_dir}\n")
        self.write_file(os.path.join(self.linked_dir, "commondir"), "../..\n")
        self.write_reflog(self.common_dir, "HEAD", ["a" * 40, "b" * 40])
        self.write_reflog(self.linked_dir, "HEAD", ["c" * 40, "d" * 40])
        self.write_reflog(self.common_dir, "refs/heads/main", ["e" * 40, "f" * 40])
        self.write_reflog(self.common_dir, "refs/heads/topic/nested", ["1" * 40])
        self.settings = {}
        self.settings["git_savvy.log_graph_view.apply_filters"] = True
        self.view = mock()
        when(self.view).settings().thenReturn(self.settings)
        self.command = gs_log_graph_refresh(self.view)
        when(self.command).current_state().thenReturn({})
        when(self.command).git(...).thenRaise(AssertionError("Unexpected Git query"))
        when(self.command).to_short_hash(...).thenAnswer(lambda commit: commit[:7])
        when(self.command).get_worktree_markers().thenRaise(StopBeforeGraphRead)

    @p.expand([
        (False, "HEAD@{0}", "b" * 40),
        (False, "HEAD@{1}", "a" * 40),
        (True, "HEAD@{0}", "d" * 40),
        (True, "HEAD@{1}", "c" * 40),
        (False, "main@{0}", "f" * 40),
        (True, "main@{1}", "e" * 40),
        (False, "refs/heads/main@{1}", "e" * 40),
        (True, "refs/heads/main@{0}", "f" * 40),
        (True, "topic/nested@{0}", "1" * 40),
    ])
    def test_resolves_reflog_decorations_from_direct_paths(self, linked, selector, expected) -> None:
        self.settings["git_savvy.log_graph_view.filters"] = selector
        self.resolve_decorations(linked)
        verify(self.command).to_short_hash(expected)
        verify(self.command, times=0).git(...)

    def test_missing_and_exhausted_selectors_do_not_hide_valid_selectors(self) -> None:
        self.settings["git_savvy.log_graph_view.filters"] = "missing@{0} HEAD@{20} main@{1}"
        self.settings["git_savvy.log_graph_view.branches"] = ["HEAD@{0}"]
        self.resolve_decorations(linked=True)
        verify(self.command).to_short_hash("e" * 40)
        verify(self.command).to_short_hash("d" * 40)
        verify(self.command, times=2).to_short_hash(...)
        verify(self.command, times=0).git(...)

    def test_reftable_decorations_use_quiet_queries_and_omit_failures(self) -> None:
        os.makedirs(os.path.join(self.common_dir, "reftable"))
        self.settings["git_savvy.log_graph_view.filters"] = "HEAD@{0} HEAD@{999999} HEAD@{0}"
        when(self.command).git(
            "rev-parse", "--verify", "--quiet", "HEAD@{0}^{commit}",
            throw_on_error=False, show_panel_on_error=False
        ).thenReturn("9" * 40 + "\n")
        when(self.command).git(
            "rev-parse", "--verify", "--quiet", "HEAD@{999999}^{commit}",
            throw_on_error=False, show_panel_on_error=False
        ).thenReturn("")
        self.resolve_decorations(linked=True)
        verify(self.command, times=1).to_short_hash("9" * 40)
        verify(self.command, times=1).to_short_hash(...)
        verify(self.command, times=2).git(...)

    def test_no_selectors_needs_no_git_query(self) -> None:
        self.resolve_decorations(linked=False)
        verify(self.command, times=0).to_short_hash(...)
        verify(self.command, times=0).git(...)

    def resolve_decorations(self, linked: bool) -> None:
        when(self.command).get_repo_path().thenReturn(self.linked_path if linked else self.repo_path)
        with self.assertRaises(StopBeforeGraphRead):
            self.command.run_impl(False, True, prelude(self.view), lambda: False)

    def write_reflog(self, git_dir: str, ref: str, hashes: list[str]) -> None:
        contents = "".join(
            f"{'0' * 40} {commit} Tester <test@example.com> 1700000000 +0000\tcommit: test\n"
            for commit in hashes
        )
        self.write_file(os.path.join(git_dir, "logs", ref), contents)

    def write_file(self, path: str, contents: str) -> None:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(contents)
