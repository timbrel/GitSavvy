"""Scenario checks for SHA-256 and reftable repositories.

Not part of the default test run; the file name does not match `test*.py`.
Run it while working on object-format or ref-storage support:

    ut-run-tests . --file tests/git3_scenarios.py

No Git calls or Sublime commands are mocked. Modal dialogs are recorded and
rejected so a failure cannot leave the headless editor waiting for a click.
"""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import tempfile
import time

import sublime
from unittesting import DeferrableTestCase

from GitSavvy.core import app_state, runtime
from GitSavvy.tests.mockito import unstub, when
from GitSavvy.tests.parameterized import parameterized as p


FORMATS = [("sha1", "files"), ("sha256", "files"), ("sha1", "reftable"), ("sha256", "reftable")]
ABBREVIATIONS = [
    (object_format, ref_format, abbreviation)
    for object_format, ref_format in FORMATS
    for abbreviation in ("default", "full")
]


class TestFutureRepositories(DeferrableTestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="gitsavvy-git3-")
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name) / "repo"
        self.repo.mkdir()
        self.window = None
        for key in ("last_blame_format", "last_compact_blame_format"):
            previous = app_state.get(key, "hash")
            self.addCleanup(app_state.set, key, previous)
            app_state.set(key, "hash")
        self.dialogs = []
        self.addCleanup(unstub)
        when(sublime).ok_cancel_dialog(...).thenAnswer(self.reject_dialog)
        when(sublime).error_message(...).thenAnswer(self.reject_dialog)

    def tearDown(self):
        yield from self.drain_worker()
        if self.window:
            self.close_window()
        yield from self.drain_worker()
        self.assertEqual([], self.dialogs, "Unexpected modal dialogs")

    @p.expand(FORMATS)
    def test_status_graph_and_commit(self, object_format: str, ref_format: str):
        self.create_repo(object_format, ref_format)
        self.write_file("tracked.txt", "first line\n")
        self.git("add", "tracked.txt")
        self.git("commit", "-m", "Git 3 initial commit")
        commit = self.git("rev-parse", "HEAD").strip()
        self.assertEqual(len(commit), 64 if object_format == "sha256" else 40)
        self.assertEqual(self.git("rev-parse", "--show-ref-format").strip(), ref_format)
        self.write_file("tracked.txt", "first line\nsecond line\n")
        yield from self.open_window()

        self.window.run_command("gs_show_status")
        status = yield from self.await_view("git_savvy.status_view", "tracked.txt")
        self.assertIn("main", self.contents(status))

        self.window.run_command("gs_graph", {"repo_path": str(self.repo)})
        graph = yield from self.await_view("git_savvy.log_graph_view", "Git 3 initial commit")
        self.assertIn("main", self.contents(graph))

        self.window.run_command("gs_show_commit", {"commit_hash": commit})
        view = yield from self.await_view("git_savvy.show_commit_view", "+first line")
        self.assertIn(commit, self.contents(view))
        self.assertIn("Git 3 initial commit", view.name())

    @p.expand(FORMATS)
    def test_blame_opens_the_selected_commit(self, object_format: str, ref_format: str):
        self.create_repo(object_format, ref_format)
        self.write_file("tracked.txt", "first line\n")
        self.git("add", "tracked.txt")
        self.git("commit", "-m", "Git 3 blamed commit")
        commit = self.git("rev-parse", "HEAD").strip()
        yield from self.open_window()
        source = self.window.open_file(str(self.repo / "tracked.txt"))
        yield lambda: not source.is_loading()
        source.run_command("gs_blame")
        blame = yield from self.await_view("git_savvy.blame_view", "first line")
        blame.sel().clear()
        blame.sel().add(sublime.Region(blame.find("first line", 0, sublime.LITERAL).begin()))
        blame.run_command("gs_blame_open_commit")
        commit_view = yield from self.await_view("git_savvy.show_commit_view", "+first line")
        self.assertIn(commit, self.contents(commit_view))

    @p.expand(FORMATS)
    def test_blame_uncommitted_line(self, object_format: str, ref_format: str):
        self.create_repo(object_format, ref_format)
        self.write_file("tracked.txt", "first line\n")
        self.git("add", "tracked.txt")
        self.git("commit", "-m", "Git 3 blamed commit")
        self.write_file("tracked.txt", "first line\nnew uncommitted line\n")
        yield from self.open_window()
        source = self.window.open_file(str(self.repo / "tracked.txt"))
        yield lambda: not source.is_loading()
        source.run_command("gs_blame")
        blame = yield from self.await_view("git_savvy.blame_view", "Not committed yet")
        self.assertIn("new uncommitted line", self.contents(blame))
        blame.sel().clear()
        blame.sel().add(sublime.Region(blame.find("new uncommitted line", 0, sublime.LITERAL).begin()))
        blame.run_command("gs_blame_open_commit")
        self.assertEqual(blame, self.window.active_view())
        blame.run_command("gs_blame_toggle_verbose_format")
        yield from self.await_view("git_savvy.blame_view", "Git 3 blamed commit")
        self.assertIn("Not committed yet", self.contents(blame))

    @p.expand(FORMATS)
    def test_graph_opens_full_length_hash(self, object_format: str, ref_format: str):
        self.create_repo(object_format, ref_format)
        self.git("config", "core.abbrev", "64" if object_format == "sha256" else "40")
        self.write_file("tracked.txt", "first line\n")
        self.git("add", "tracked.txt")
        self.git("commit", "-m", "Git 3 full hash")
        commit = self.git("rev-parse", "HEAD").strip()
        yield from self.open_window()
        self.window.run_command("gs_graph", {"repo_path": str(self.repo)})
        graph = yield from self.await_view("git_savvy.log_graph_view", commit)
        self.assertIn(commit, self.contents(graph))
        self.assertEqual([commit], [graph.substr(r) for r in graph.find_by_selector(
            "constant.numeric.graph.commit-hash.git-savvy"
        )])
        graph.sel().clear()
        graph.sel().add(sublime.Region(graph.find(commit, 0, sublime.LITERAL).begin()))
        graph.run_command("gs_log_graph_open_commit")
        view = yield from self.await_view("git_savvy.show_commit_view", "+first line")
        self.assertIn(commit, self.contents(view))

    @p.expand(FORMATS)
    def test_graph_marks_linked_worktrees(self, object_format: str, ref_format: str):
        self.create_repo(object_format, ref_format)
        self.write_file("tracked.txt", "first line\n")
        self.git("add", "tracked.txt")
        self.git("commit", "-m", "Git 3 worktrees")
        self.git("worktree", "add", "-b", "topic", str(Path(self.temp.name) / "linked"))
        self.git("worktree", "add", "--detach", str(Path(self.temp.name) / "detached"))
        yield from self.open_window()
        self.window.run_command("gs_graph", {"repo_path": str(self.repo), "all": True})
        graph = yield from self.await_view("git_savvy.log_graph_view", "Git 3 worktrees")
        self.assertIn("topic ■", self.contents(graph))
        self.assertEqual(2, self.contents(graph).count("■"))

    @p.expand(FORMATS)
    def test_stage_diff_and_commit(self, object_format: str, ref_format: str):
        self.create_repo(object_format, ref_format)
        self.write_file("tracked.txt", "first line\n")
        self.git("add", "tracked.txt")
        self.git("commit", "-m", "Git 3 initial commit")
        self.write_file("tracked.txt", "first line\nsecond line\n")
        yield from self.open_window()
        self.window.run_command("gs_show_status")
        status = yield from self.await_view("git_savvy.status_view", "UNSTAGED:")
        status.run_command("gs_status_stage_all_files")
        yield from self.await_view("git_savvy.status_view", "\n  STAGED:\n")
        yield from self.drain_worker()
        self.assertEqual("M  tracked.txt\n", self.git("status", "--porcelain"))
        self.window.run_command("gs_diff", {"in_cached_mode": True})
        yield from self.await_view("git_savvy.diff_view", "+second line")
        self.window.run_command("gs_commit", {"initial_text": "Git 3 UI commit"})
        commit_view = yield from self.await_view("git_savvy.commit_view", "Git 3 UI commit")
        commit_view.run_command("gs_commit_view_do_commit")
        yield lambda: not commit_view.is_valid()
        self.assertEqual("Git 3 UI commit", self.git("log", "-1", "--format=%s").strip())
        self.assertEqual("first line\nsecond line\n", self.git("show", "HEAD:tracked.txt"))
        self.assertEqual("", self.git("status", "--porcelain"))

    @p.expand([
        (object_format, ref_format, layout)
        for object_format, ref_format in FORMATS
        for layout in ("alias", "separate-gitdir", "linked-separate-gitdir")
    ])
    def test_graph_does_not_mark_current_checkout(
        self, object_format: str, ref_format: str, layout: str
    ):
        self.create_repo(object_format, ref_format)
        self.git("commit", "--allow-empty", "-m", "Git 3 path identity")
        if layout != "alias":
            self.git("init", "--object-format=" + object_format, "--ref-format=" + ref_format,
                     "--separate-git-dir", str(Path(self.temp.name) / "metadata"))
            graph_path = str(self.repo)
        else:
            graph_path = os.path.join(str(self.repo), "..", "repo")
        linked = str(Path(self.temp.name) / "linked")
        self.git("worktree", "add", "-b", "topic", linked)
        expected_marker = "topic ■"
        if layout == "linked-separate-gitdir":
            graph_path = linked
            self.git("worktree", "add", "-b", "other", str(Path(self.temp.name) / "other"))
            expected_marker = "other ■"
        yield from self.open_window()
        self.window.run_command("gs_graph", {"repo_path": graph_path, "all": True})
        graph = yield from self.await_view("git_savvy.log_graph_view", "Git 3 path identity")
        self.assertIn(expected_marker, self.contents(graph))
        self.assertEqual(1, self.contents(graph).count("■"))

    @p.expand(FORMATS)
    def test_blame_scopes_and_opens_full_hashes(self, object_format: str, ref_format: str):
        commit = self.create_topic_commit(object_format, ref_format)
        yield from self.open_window()
        source = self.window.open_file(str(self.repo / "tracked.txt"))
        yield lambda: not source.is_loading()
        source.run_command("gs_blame")
        blame = yield from self.await_view("git_savvy.blame_view", "changed line")
        self.assert_scoped(blame, commit, "constant.numeric.commit-hash.git-savvy")

        self.place_cursor(blame, "changed line")
        blame.run_command("gs_blame_open_commit")
        yield from self.assert_opens_topic_commit(commit)

    @p.expand(FORMATS)
    def test_status_scopes_and_opens_full_hashes(self, object_format: str, ref_format: str):
        commit = self.create_topic_commit(object_format, ref_format)
        yield from self.open_window()
        self.window.run_command("gs_show_status")
        status = yield from self.await_view("git_savvy.status_view", "Git 3 syntax change")
        self.assert_scoped(status, commit, "constant.other.git-savvy.sha1")

        self.place_cursor(status, commit)
        status.run_command("gs_interface_show_commit")
        yield from self.assert_opens_topic_commit(commit)

    @p.expand(FORMATS)
    def test_rebase_scopes_and_opens_full_hashes(self, object_format: str, ref_format: str):
        commit = self.create_topic_commit(object_format, ref_format)
        yield from self.open_window()
        self.window.run_command("gs_show_rebase")
        rebase = yield from self.await_view("git_savvy.rebase_view", "Git 3 syntax change")
        self.assert_scoped(rebase, commit, "support.type.git-savvy.rebase.commit_hash")

        self.place_cursor(rebase, commit)
        rebase.run_command("gs_rebase_show_commit")
        yield from self.assert_opens_topic_commit(commit)

    @p.expand(FORMATS)
    def test_branch_dashboard_scopes_full_hashes_and_checks_out(
        self, object_format: str, ref_format: str
    ):
        commit = self.create_topic_commit(object_format, ref_format)
        # The same commit is listed for `topic`, the linked worktree's
        # branch, and the detached worktree.
        self.git("worktree", "add", "-b", "linked", str(Path(self.temp.name) / "linked"))
        self.git("worktree", "add", "--detach", str(Path(self.temp.name) / "detached"))
        self.git("checkout", "main")
        yield from self.open_window()
        self.window.run_command("gs_show_branch")
        branches = yield from self.await_view("git_savvy.branch_view", "DETACHED")
        yield from self.drain_worker()
        hashes = branches.find_by_selector("constant.other.git-savvy.branches.branch.sha1")
        self.assertEqual(3, [branches.substr(r) for r in hashes].count(commit))

        self.place_cursor(branches, " topic ")
        branches.run_command("gs_branches_checkout")
        yield from self.drain_worker()
        self.assertEqual("refs/heads/topic", self.git("symbolic-ref", "HEAD").strip())

    @p.expand(FORMATS)
    def test_tags_scope_full_hashes(self, object_format: str, ref_format: str):
        commit = self.create_topic_commit(object_format, ref_format)
        self.git("tag", "v3-preview")
        yield from self.open_window()
        self.window.run_command("gs_show_tags")
        tags = yield from self.await_view("git_savvy.tags_view", "v3-preview")
        yield from self.drain_worker()
        hashes = tags.find_by_selector("constant.other.git-savvy.tags.sha1")
        self.assertEqual([commit], [tags.substr(r) for r in hashes])

    @p.expand(FORMATS)
    def test_output_panel_scopes_full_hashes(self, object_format: str, ref_format: str):
        commit = self.create_topic_commit(object_format, ref_format)
        yield from self.open_window()
        self.window.run_command("gs_custom", {
            "args": ["log", "-1", "--format=%H"], "output_to_panel": True
        })
        yield from self.drain_worker()
        panel = self.window.find_output_panel("GitSavvy")
        self.assertIsNotNone(panel)
        self.assert_scoped(panel, commit, "constant.numeric.graph.commit-hash.git-savvy")

    @p.expand(FORMATS)
    def test_graph_abbreviates_hashes_by_default(self, object_format: str, ref_format: str):
        self.create_repo(object_format, ref_format)
        self.git("config", "--unset", "core.abbrev")
        self.git("commit", "--allow-empty", "-m", "Git 3 short graph")
        commit = self.git("rev-parse", "HEAD").strip()
        yield from self.open_window()
        self.window.run_command("gs_graph", {"repo_path": str(self.repo)})
        graph = yield from self.await_view("git_savvy.log_graph_view", "Git 3 short graph")
        hashes = graph.find_by_selector("constant.numeric.graph.commit-hash.git-savvy")
        self.assertEqual(1, len(hashes))
        displayed = graph.substr(hashes[0])
        self.assertTrue(commit.startswith(displayed))
        self.assertLess(len(displayed), len(commit))
        self.assertGreaterEqual(len(displayed), 7)

    @p.expand(ABBREVIATIONS)
    def test_line_history_scopes_full_hashes(
        self, object_format: str, ref_format: str, abbreviation: str
    ):
        original, changed = self.create_line_change(object_format, ref_format, abbreviation)
        yield from self.open_window()
        history = yield from self.open_line_history_of_first_line()
        self.assert_scoped(history, changed, "meta.commit-info.header.sha.git-savvy")
        self.assert_scoped(history, original, "meta.commit-info.header.sha.git-savvy")
        self.assertEqual(2, len(history.find_by_selector("meta.commit-info.header.sha.git-savvy")))
        self.assertEqual(2, len(history.find_by_selector("meta.diff.range.unified")))
        self.assert_scoped(history, "+new line", "markup.inserted.diff")
        self.assert_scoped(history, "-old line", "markup.deleted.diff")

    @p.expand(ABBREVIATIONS)
    def test_line_history_opens_a_commit_and_its_successor(
        self, object_format: str, ref_format: str, abbreviation: str
    ):
        original, changed = self.create_line_change(object_format, ref_format, abbreviation)
        yield from self.open_window()
        history = yield from self.open_line_history_of_first_line()

        self.place_cursor(history, "+old line")
        history.run_command("gs_line_history_open_commit")
        patch = yield from self.await_view("git_savvy.show_commit_view", "+old line")
        self.assert_scoped(patch, original, "meta.commit-info.header.sha.git-savvy")

        patch.run_command("gs_show_commit_open_next_commit")
        patch = yield from self.await_view("git_savvy.show_commit_view", "+new line")
        self.assert_scoped(patch, changed, "meta.commit-info.header.sha.git-savvy")
        self.assert_scoped(patch, "+new line", "markup.inserted.diff")
        # `index <from>..<to>` abbreviates blob IDs like commits.
        self.assert_scoped(patch, self.blob_id(original, abbreviation),
                           "constant.numeric.hex.from-commit.diff")
        self.assert_scoped(patch, self.blob_id(changed, abbreviation),
                           "constant.numeric.hex.to-commit.diff")

    @p.expand(ABBREVIATIONS)
    def test_line_history_from_a_patch(
        self, object_format: str, ref_format: str, abbreviation: str
    ):
        original, changed = self.create_line_change(object_format, ref_format, abbreviation)
        yield from self.open_window()
        self.window.run_command("gs_show_commit", {"commit_hash": changed})
        patch = yield from self.await_view("git_savvy.show_commit_view", "+new line")

        self.place_cursor(patch, "+new line")
        patch.run_command("gs_line_history")
        from_patch = yield from self.await_view("git_savvy.line_history_view", "+old line")
        self.assert_scoped(from_patch, changed, "meta.commit-info.header.sha.git-savvy")
        self.assert_scoped(from_patch, original, "meta.commit-info.header.sha.git-savvy")

    def test_init_uses_preview_defaults(self):
        if os.environ.get("GITSAVVY_GIT3_PREVIEW") != "1":
            self.skipTest("Requires the isolated breaking-build image")
        self.git("init", "--template=")
        self.assertEqual(["sha256", "reftable"], self.git(
            "rev-parse", "--show-object-format", "--show-ref-format"
        ).splitlines())
        self.assertEqual("refs/heads/main", self.git("symbolic-ref", "HEAD").strip())
        self.write_file("new.txt", "unborn repository\n")
        yield from self.open_window()
        self.window.run_command("gs_show_status")
        status = yield from self.await_view("git_savvy.status_view", "new.txt")
        self.assertIn("main", self.contents(status))

    def create_repo(self, object_format: str, ref_format: str) -> None:
        version = self.git("version").split()[2].split(".")
        if tuple(map(int, version[:2])) < (2, 45):
            self.skipTest("Storage-format matrix requires Git 2.45+")
        self.git("init", "--template=", "--initial-branch=main", "--object-format=" + object_format,
                 "--ref-format=" + ref_format)
        self.assertEqual([object_format, ref_format], self.git(
            "rev-parse", "--show-object-format", "--show-ref-format"
        ).splitlines())
        self.git("config", "user.name", "GitSavvy compatibility test")
        self.git("config", "user.email", "git3@example.invalid")
        self.git("config", "commit.gpgSign", "false")
        self.git("config", "core.abbrev", "12")

    def open_window(self):
        previous = sublime.active_window()
        sublime.run_command("new_window")
        yield lambda: sublime.active_window() != previous
        self.window = sublime.active_window()
        self.window.set_project_data({"folders": [{"path": str(self.repo)}]})
        yield lambda: bool(self.window.folders())

    def await_view(self, flag: str, needle: str):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            view = self.window.active_view()
            if view and view.settings().get(flag) and needle in self.contents(view):
                print("GIT3 VIEW", flag, view.name(), repr(self.contents(view)))
                return view
            yield 20
        self.fail("{} did not show {!r}; views: {}".format(
            flag, needle, [(v.name(), self.contents(v)) for v in self.window.views()]
        ))

    def reject_dialog(self, message: str, *args, **kwargs) -> bool:
        self.dialogs.append(message)
        return False

    def drain_worker(self):
        done = []
        runtime.run_when_worker_is_idle(lambda: done.append(True))
        yield lambda: bool(done)

    def close_window(self) -> None:
        for view in self.window.views():
            view.set_scratch(True)
            view.close()
        self.window.run_command("close_window")

    def write_file(self, name: str, content: str) -> None:
        (self.repo / name).write_text(content, encoding="utf-8")

    def git(self, *args: str) -> str:
        result = subprocess.run(
            ["git", *args], cwd=str(self.repo), stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, encoding="utf-8", timeout=20
        )
        if result.returncode:
            self.fail("git {}: {}".format(" ".join(args), result.stderr))
        return result.stdout

    def assert_scoped(self, view: sublime.View, text: str, selector: str) -> None:
        region = view.find(text, 0, sublime.LITERAL)
        self.assertTrue(region, "Missing {!r} in {}".format(text, self.contents(view)))
        for point in (region.begin(), region.end() - 1):
            self.assertTrue(view.match_selector(point, selector),
                            "{!r}: expected {}, got {}".format(text, selector, view.scope_name(point)))

    def create_topic_commit(self, object_format: str, ref_format: str) -> str:
        """Commit a change on `topic` and print every hash in full."""
        self.create_repo(object_format, ref_format)
        self.git("config", "core.abbrev", "64" if object_format == "sha256" else "40")
        self.write_file("tracked.txt", "first line\n")
        self.git("add", "tracked.txt")
        self.git("commit", "-m", "Git 3 syntax base")
        self.git("checkout", "-b", "topic")
        self.write_file("tracked.txt", "changed line\n")
        self.git("commit", "-am", "Git 3 syntax change")
        return self.git("rev-parse", "HEAD").strip()

    def assert_opens_topic_commit(self, commit: str):
        patch = yield from self.await_view("git_savvy.show_commit_view", "+changed line")
        self.assert_scoped(patch, commit, "meta.commit-info.header.sha.git-savvy")

    def create_line_change(
        self, object_format: str, ref_format: str, abbreviation: str
    ) -> tuple[str, str]:
        """Commit the first line twice; return (original, changed)."""
        self.create_repo(object_format, ref_format)
        if abbreviation == "default":
            self.git("config", "--unset", "core.abbrev")
        else:
            self.git("config", "core.abbrev", "64" if object_format == "sha256" else "40")
        self.write_file("tracked.txt", "old line\nstable line\n")
        self.git("add", "tracked.txt")
        self.git("commit", "-m", "Original selected line")
        original = self.git("rev-parse", "HEAD").strip()
        self.write_file("tracked.txt", "new line\nstable line\n")
        self.git("commit", "-am", "Changed selected line")
        changed = self.git("rev-parse", "HEAD").strip()
        return original, changed

    def open_line_history_of_first_line(self):
        source = self.window.open_file(str(self.repo / "tracked.txt"))
        yield lambda: not source.is_loading()
        source.sel().clear()
        source.sel().add(sublime.Region(0))
        source.run_command("gs_line_history")
        return (yield from self.await_view("git_savvy.line_history_view", "+old line"))

    def blob_id(self, revision: str, abbreviation: str) -> str:
        blob = self.git("rev-parse", revision + ":tracked.txt").strip()
        if abbreviation == "full":
            return blob
        return self.git("rev-parse", "--short", blob).strip()

    def place_cursor(self, view: sublime.View, text: str) -> None:
        region = view.find(text, 0, sublime.LITERAL)
        self.assertTrue(region, "Missing {!r} in {}".format(text, self.contents(view)))
        view.sel().clear()
        view.sel().add(sublime.Region(region.begin()))

    def contents(self, view: sublime.View) -> str:
        return view.substr(sublime.Region(0, view.size()))
