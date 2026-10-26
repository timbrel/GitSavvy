from __future__ import annotations

import os
import tempfile

import sublime
from unittesting import DeferrableTestCase

from GitSavvy.tests.mockito import mock, unstub, verify, when
from GitSavvy.tests.parameterized import parameterized as p
from GitSavvy.core.commands.log_graph import _find_symbol
from GitSavvy.core.commands.log_graph_renderer import (
    make_worktree_branch_marker, gs_log_graph_refresh, has_main_worktree
)
from GitSavvy.core.commands.log_graph_smart_copy import (
    read_branches_in_region, read_commit_message, read_tags_in_region
)


class TestWorktreeDecorations(DeferrableTestCase):
    @p.expand([
        ("feature, bookmark, origin/feature, tag: v1", "feature ■, bookmark, origin/feature, tag: v1"),
        ("bookmark", "bookmark"),
        ("HEAD -> main, feature", "HEAD -> main, feature ■"),
        ("HEAD* -> feature, bookmark", "HEAD* -> feature ■, bookmark"),
        ("tag: feature, origin/feature, feature-other", "tag: feature, origin/feature, feature-other"),
        ("feature, other", "feature ■, other ■"),
        ("bug.fix, bug-fix, feat+one", "bug.fix ■, bug-fix, feat+one ■"),
        ("", "")
    ])
    def test_marks_only_whole_branch_decorations(self, decoration: str, expected: str) -> None:
        marker = make_worktree_branch_marker({"feature", "other", "bug.fix", "feat+one"})
        assert marker is not None
        self.assertEqual(expected, marker(decoration))

    def test_no_branches_needs_no_marker_function(self) -> None:
        self.assertIsNone(make_worktree_branch_marker(set()))


class TestGraphWorktreeHeads(DeferrableTestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.addCleanup(unstub)
        self.common_dir = directory.name
        self.command = mock({"git_common_dir": self.common_dir, "git_dir": self.common_dir})
        when(self.command).get_worktrees().thenReturn([])
        self.write_head(self.common_dir, "ref: refs/heads/main\n")
        with open(os.path.join(self.common_dir, "config"), "w", encoding="utf-8") as f:
            f.write("[core]\n\tbare = false\n")

    @p.expand(["missing", "empty"])
    def test_no_linked_worktrees_needs_no_git_query(self, layout: str) -> None:
        if layout == "empty":
            os.mkdir(os.path.join(self.common_dir, "worktrees"))
        self.assertEqual((set(), set()), self.markers())
        verify(self.command, times=0).get_worktrees()

    def test_reads_attached_and_detached_heads_without_resolving_refs(self) -> None:
        self.write_head(self.linked_dir("feature"), "ref: refs/heads/feature\n")
        self.write_head(self.linked_dir("detached"), "a" * 40 + "\n")
        self.write_head(self.linked_dir("sha256"), "b" * 64 + "\n")
        self.assertEqual(({"feature"}, {"a" * 40, "b" * 64}), self.markers())
        verify(self.command, times=0).get_worktrees()

    def test_linked_checkout_skips_itself_and_includes_main_checkout(self) -> None:
        current = self.linked_dir("current")
        self.write_head(current, "ref: refs/heads/current\n")
        self.write_head(self.linked_dir("other"), "ref: refs/heads/other\n")
        self.command.git_dir = os.path.join(current, ".")
        self.assertEqual(({"main", "other"}, set()), self.markers())

    def test_bare_repository_has_no_main_checkout(self) -> None:
        with open(os.path.join(self.common_dir, "config"), "w", encoding="utf-8") as f:
            f.write("[core]\n\tbare = true\n")
        current = self.linked_dir("current")
        self.write_head(current, "ref: refs/heads/feature")
        self.command.git_dir = current
        self.assertEqual((set(), set()), self.markers())
        self.write_head(self.linked_dir("other"), "ref: refs/heads/other")
        self.assertEqual(({"other"}, set()), self.markers())
        verify(self.command, times=0).get_worktrees()

    def test_linked_checkout_includes_detached_main_checkout(self) -> None:
        current = self.linked_dir("current")
        self.write_head(current, "c" * 40)
        self.write_head(self.common_dir, "a" * 40)
        self.command.git_dir = current
        self.assertEqual((set(), {"a" * 40}), self.markers())

    def test_observes_checkout_changes_on_refresh(self) -> None:
        path = self.linked_dir("agent")
        self.write_head(path, "ref: refs/heads/feature")
        self.assertEqual(({"feature"}, set()), self.markers())
        self.write_head(path, "a" * 40)
        self.assertEqual((set(), {"a" * 40}), self.markers())
        os.remove(os.path.join(path, "HEAD"))
        os.rmdir(path)
        self.assertEqual((set(), set()), self.markers())

    def test_skips_missing_unreadable_and_empty_heads(self) -> None:
        os.makedirs(self.linked_dir("missing"))
        os.makedirs(os.path.join(self.linked_dir("unreadable"), "HEAD"))
        for name, contents in [
            ("empty", ""), ("empty-branch", "ref: refs/heads/"), ("encoding", b"\xff")
        ]:
            self.write_head(self.linked_dir(name), contents)
        self.write_head(self.linked_dir("valid"), "ref: refs/heads/feature")
        self.assertEqual(({"feature"}, set()), self.markers())

    def test_scan_failure_still_allows_the_other_main_checkout(self) -> None:
        self.command.git_dir = self.linked_dir("current")
        when(os).scandir(os.path.join(self.common_dir, "worktrees")).thenRaise(PermissionError)
        self.assertEqual(({"main"}, set()), self.markers())

    def markers(self) -> tuple[set[str], set[str]]:
        return gs_log_graph_refresh.get_worktree_markers(self.command)

    def linked_dir(self, name: str) -> str:
        return os.path.join(self.common_dir, "worktrees", name)

    def write_head(self, directory: str, contents: str | bytes) -> None:
        os.makedirs(directory, exist_ok=True)
        data = contents.encode("utf-8") if isinstance(contents, str) else contents
        with open(os.path.join(directory, "HEAD"), "wb") as f:
            f.write(data)


class TestMainWorktreeConfig(DeferrableTestCase):
    @p.expand([
        ("[core]\nbare = true\n", False),
        ("[core]\nbare\n", False),
        ("[core]\nbare = false\n", True),
        ('[CoRe]\nBaRe = "false" # comment\n', True),
        ("[core]\nbare = on\n", False),
        ("[core]\nbare = off\n", True),
        ("[core]\nbare = true\nbare = false\n", True),
        ("[core]\nbare = false\nbare = true\n", False),
        ("[core]\nrepositoryformatversion = 0\n", True)
    ])
    def test_reads_git_boolean_without_spawning_git(self, contents, expected):
        with tempfile.TemporaryDirectory() as directory:
            with open(os.path.join(directory, "config"), "w", encoding="utf-8") as f:
                f.write(contents)
            self.assertEqual(expected, has_main_worktree(directory))

    def test_missing_config_does_not_invent_a_main_checkout(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            self.assertFalse(has_main_worktree(directory))


class TestWorktreeDecorationReaders(DeferrableTestCase):
    @p.expand([
        ("feature ■, bookmark, origin/feature, tag: v1, ■", ["feature", "bookmark", "origin/feature"], ["v1"]),
        ("bookmark, ■", ["bookmark"], []),
        ("■", [], []),
        ("HEAD* -> main, feature ■", ["main", "feature"], [])
    ])
    def test_scopes_preserve_copy_and_navigation_targets(self, decoration, branches, tags):
        view = sublime.active_window().new_file()
        self.addCleanup(self.close_view, view)
        view.assign_syntax("Packages/GitSavvy/syntax/graph.sublime-syntax")
        view.run_command("append", {"characters": "\n  REPO: /repo\n\n● ac2a8a67 ({}) Subject\n".format(decoration)})
        yield lambda: bool(view.find_by_selector("meta.graph.message.git-savvy"))
        line = view.line(view.find("ac2a8a67", 0))
        self.assertEqual(branches, [r.text for r in read_branches_in_region(view, line)])
        self.assertEqual(tags, [r.text for r in read_tags_in_region(view, line)])
        self.assertEqual("Subject", read_commit_message(view, line).text.strip())
        symbols = [name for _, name in view.symbols()]
        self.assertNotIn("■", symbols)
        for branch in branches:
            self.assertIn(branch, symbols)
            self.assertEqual("ac2a8a67", view.substr(_find_symbol(view, branch)))

    def close_view(self, view: sublime.View) -> None:
        view.set_scratch(True)
        view.close()
