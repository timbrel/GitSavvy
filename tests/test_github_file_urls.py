from __future__ import annotations

import os
import subprocess
import tempfile

import sublime
from unittesting import DeferrableTestCase

from GitSavvy.core.git_mixins.branches import Upstream
from GitSavvy.core.git_mixins.history import FileHistoryInfo, file_history_cache
from GitSavvy.github.commands import open_on_remote
from GitSavvy.github.commands.open_on_remote import gs_github_open_file_in_browser
from GitSavvy.tests.mockito import mock, unstub, verify, when
from GitSavvy.tests.parameterized import parameterized as p


class TestGitHubFileUrls(DeferrableTestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.addCleanup(unstub)
        self.view = sublime.active_window().new_file()
        self.view.set_scratch(True)
        self.addCleanup(self.view.close)
        self.command = FileUrlCommand(self.view)
        self.command.directory = self.directory.name
        self.command.git_calls = []
        self.command.fpath = ["file.md"]
        self.command.preselect = False
        self.command.remotes = {"origin": "https://github.com/owner/repo"}
        when(self.command).get_repo_path().thenReturn(self.directory.name)
        when(self.command).guess_default_branch("origin").thenReturn("origin/main")
        when(self.command).computed_urls(...).thenReturn(None)
        when(open_on_remote).flash(self.view, ...).thenReturn(None)
        when(sublime).ok_cancel_dialog(...).thenReturn(False)
        self.git("init")
        self.git("config", "user.name", "File URL Test")
        self.git("config", "user.email", "file-url@example.invalid")
        self.git("config", "core.autocrlf", "false")
        self.git("remote", "add", "origin", "https://github.com/owner/repo")
        self.write_file("file.md", "first\nsecond\nthird\n")
        self.git("add", ".")
        self.git("commit", "-m", f"Published file in {self.directory.name}")
        self.published = self.git("rev-parse", "HEAD").strip()
        self.git("update-ref", "refs/remotes/origin/main", self.published)
        self.write_file("file.md", "local commit\nfirst\nsecond\nthird\n")
        self.git("commit", "-am", "Unpublished edit")
        self.unpublished = self.git("rev-parse", "HEAD").strip()
        self.write_file("file.md", "dirty\nlocal commit\nfirst\nsecond\nthird\n")

    def test_dirty_file_on_unpublished_branch_opens_remote_default(self) -> None:
        self.command.open_file_on_remote("origin")

        self.assert_url("main")

    def test_uses_upstream_branch_on_selected_remote(self) -> None:
        self.git("update-ref", "refs/remotes/origin/topic", self.published)
        when(self.command).get_upstream_for_active_branch().thenReturn(
            Upstream("origin", "topic", "origin/topic", "ahead 1")
        )

        self.command.git_calls.clear()
        self.command.open_file_on_remote("origin")

        self.assert_url("topic")
        self.assertEqual([args[0] for args in self.command.git_calls], ["for-each-ref", "ls-tree"])
        verify(self.command, times=0).guess_default_branch(...)

    @p.expand([
        (Upstream("fork", "topic", "fork/topic", ""),),
        (Upstream(".", "main", "main", ""),),
        (Upstream("origin", "topic", "origin/topic", "gone"),)
    ])
    def test_other_or_gone_upstream_uses_remote_default(self, upstream: Upstream) -> None:
        when(self.command).get_upstream_for_active_branch().thenReturn(upstream)

        self.command.open_file_on_remote("origin")

        self.assert_url("main")

    def test_default_branch_from_remote_lookup_is_unqualified(self) -> None:
        when(self.command).guess_default_branch("origin").thenReturn("main")

        self.command.open_file_on_remote("origin")

        self.assert_url("main")

    def test_branch_names_with_slashes_and_hashes_are_escaped(self) -> None:
        self.git("update-ref", "refs/remotes/origin/topic/issue#42", self.published)
        when(self.command).get_upstream_for_active_branch().thenReturn(
            Upstream("origin", "topic/issue#42", "origin/topic/issue#42", "")
        )

        self.command.open_file_on_remote("origin")

        self.assert_url("topic%2Fissue%2342")

    def test_unknown_default_branch_does_not_guess_master(self) -> None:
        self.git("update-ref", "-d", "refs/remotes/origin/main")
        when(self.command).guess_default_branch("origin").thenReturn(None)

        self.command.open_file_on_remote("origin")

        verify(self.command, times=0).computed_urls(...)
        verify(open_on_remote).flash(self.view, "Could not determine the default branch of origin.")

    def test_unfetched_branch_asks_to_fetch(self) -> None:
        self.git("update-ref", "-d", "refs/remotes/origin/main")
        when(self.command).guess_default_branch("origin").thenReturn("origin/unfetched")

        self.command.open_file_on_remote("origin")

        verify(self.command, times=0).computed_urls(...)
        verify(open_on_remote).flash(
            self.view, "origin/unfetched is not available locally. Fetch origin first."
        )

    def test_stack_opens_nearest_published_branch(self) -> None:
        self.git("update-ref", "refs/remotes/origin/ci-status", self.unpublished)
        self.git("commit", "--allow-empty", "-m", "Work above ci-status")

        self.command.open_file_on_remote("origin")

        self.assert_url("ci-status")

    def test_local_only_stacked_branch_does_not_count_as_published(self) -> None:
        self.git("branch", "ci-status", self.unpublished)
        self.git("commit", "--allow-empty", "-m", "Work above ci-status")

        self.command.open_file_on_remote("origin")

        self.assert_url("main")

    def test_closer_branch_on_another_remote_is_not_selected(self) -> None:
        self.git("update-ref", "refs/remotes/fork/ci-status", self.unpublished)

        self.command.open_file_on_remote("origin")

        self.assert_url("main")

    def test_default_branch_wins_tie_and_remote_head_is_not_a_branch(self) -> None:
        self.git("update-ref", "refs/remotes/origin/aaa", self.published)
        self.git("symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")

        self.command.open_file_on_remote("origin")

        self.assert_url("main")

    def test_shared_ancestor_selects_default_branch_when_it_has_moved_on(self) -> None:
        tree = self.git("rev-parse", self.published + "^{tree}").strip()
        other = self.command.git(
            "commit-tree", tree, "-p", self.published, stdin="Remote-only work\n"
        ).strip()
        self.git("update-ref", "refs/remotes/origin/main", other)

        self.command.open_file_on_remote("origin")

        self.assert_url("main")

    def test_merged_feature_prefers_default_over_old_feature_tip(self) -> None:
        tree = self.git("rev-parse", self.unpublished + "^{tree}").strip()
        merge = self.command.git(
            "commit-tree", tree, "-p", self.published, "-p", self.unpublished,
            stdin="Merge feature\n"
        ).strip()
        self.git("update-ref", "refs/remotes/origin/main", merge)
        self.git("update-ref", "refs/remotes/origin/ci-status", self.unpublished)
        self.git("symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
        self.git("commit", "--allow-empty", "-m", "Local work above merged feature")

        self.command.open_file_on_remote("origin")

        self.assert_url("main")

    def test_unmerged_feature_selects_its_branch_even_when_tip_has_moved_on(self) -> None:
        tree = self.git("rev-parse", self.unpublished + "^{tree}").strip()
        other = self.command.git(
            "commit-tree", tree, "-p", self.unpublished, stdin="More feature work\n"
        ).strip()
        self.git("update-ref", "refs/remotes/origin/next", other)
        self.git("commit", "--allow-empty", "-m", "Local work above next")

        self.command.open_file_on_remote("origin")

        self.assert_url("next")

    def test_selection_maps_to_new_default_branch_contents_not_shared_ancestor(self) -> None:
        blob = self.command.git(
            "hash-object", "-w", "--stdin", stdin="remote\nchanges\nfirst\nsecond\nthird\n"
        ).strip()
        tree = self.command.git("mktree", stdin=f"100644 blob {blob}\tfile.md\n").strip()
        self.assertEqual(self.git("ls-tree", "--name-only", "-z", tree), "file.md\0")
        other = self.command.git(
            "commit-tree", tree, "-p", self.published, stdin="Remote edit\n"
        ).strip()
        self.git("update-ref", "refs/remotes/origin/main", other)
        self.select_lines("local commit\nfirst\nsecond\nthird\n", 3, 3)

        self.command.open_file_on_remote("origin")

        self.assert_url("main", "#L4-L4")

    def test_published_branch_does_not_require_knowing_remote_default(self) -> None:
        when(self.command).guess_default_branch("origin").thenReturn(None)

        self.command.open_file_on_remote("origin")

        self.assert_url("main")

    def test_unrelated_history_falls_back_to_remote_default(self) -> None:
        tree = self.git("rev-parse", self.published + "^{tree}").strip()
        other = self.command.git("commit-tree", tree, stdin="Unrelated history\n").strip()
        self.git("update-ref", "refs/remotes/origin/main", other)

        self.command.open_file_on_remote("origin")

        self.assert_url("main")

    def test_new_file_is_not_opened_at_nonexistent_path(self) -> None:
        self.command.fpath = ["new.md"]

        self.command.open_file_on_remote("origin")

        verify(self.command, times=0).computed_urls(...)
        verify(open_on_remote).flash(self.view, "new.md does not exist at main on origin.")

    def test_multiple_files_use_the_same_remote_branch(self) -> None:
        self.write_file("other.md", "published\n")
        self.git("add", "other.md")
        self.git("commit", "-m", "Publish other file")
        self.git("update-ref", "refs/remotes/origin/main", "HEAD")
        self.command.fpath = ["file.md", "other.md"]

        self.command.open_file_on_remote("origin")

        verify(self.command).computed_urls([
            "https://github.com/owner/repo/blob/main/file.md",
            "https://github.com/owner/repo/blob/main/other.md"
        ])

    def test_selected_lines_are_mapped_from_unsaved_buffer(self) -> None:
        self.select_lines("unsaved\ndirty\nlocal commit\nfirst\nsecond\nthird\n", 5, 6)

        self.command.open_file_on_remote("origin")

        self.assert_url("main", "#L2-L3")

    def test_changed_selected_lines_open_nearby(self) -> None:
        self.select_lines("first\nonly local\nthird\n", 1, 2)

        self.command.open_file_on_remote("origin")

        self.assert_url("main", "#L1-L2")
        verify(open_on_remote, times=0).flash(...)

    @p.expand([
        ("unrelated contents\n", 1, 1),
        ("", 1, 1)
    ])
    def test_no_matching_lines_opens_without_highlight(self, content, start, end) -> None:
        self.select_lines(content, start, end)

        self.command.open_file_on_remote("origin")

        self.assert_url("main")
        verify(open_on_remote, times=0).flash(...)

    @p.expand([
        ("local\nfirst\nsecond\nthird\n", 1, 1, "#L1-L1"),
        ("first\nsecond\nthird\nlocal\n", 4, 4, "#L3-L3"),
        ("first\nthird\n", 2, 2, "#L3-L3"),
        ("first\nlocal\nthird\n", 3, 1, "#L1-L3")
    ])
    def test_line_mapping_stays_in_bounds(self, content, start, end, lines) -> None:
        self.select_lines(content, min(start, end), max(start, end))
        if start > end:
            selection = self.view.sel()[0]
            self.view.sel().clear()
            self.view.sel().add(sublime.Region(selection.b, selection.a))

        self.command.open_file_on_remote("origin")

        self.assert_url("main", lines)

    def test_published_historical_file_preserves_revision_and_selection(self) -> None:
        self.select_lines("first\nsecond\nthird\n", 2, 2)
        self.historical_file(self.published)
        when(self.command).get_upstream_for_active_branch().thenRaise(AssertionError("Not needed"))

        self.command.open_file_on_remote("origin")

        self.assert_url(self.published, "#L2-L2")
        verify(self.command, times=0).get_upstream_for_active_branch()
        verify(self.command, times=0).guess_default_branch(...)

    def test_historical_file_does_not_fall_back_to_published_ancestor(self) -> None:
        self.view.settings().set("git_savvy.show_file_at_commit_view", True)
        self.view.settings().set("git_savvy.show_file_at_commit_view.commit", self.unpublished)
        when(self.command).get_upstream_for_active_branch().thenReturn(
            Upstream("origin", "main", "origin/main", "ahead 1")
        )

        self.command.open_file_on_remote("origin")

        verify(self.command, times=0).computed_urls(...)
        verify(self.command, times=0).get_upstream_for_active_branch()
        verify(sublime, times=0).ok_cancel_dialog(...)
        verify(open_on_remote).flash(self.view, "This revision has not been pushed to origin yet.")

    def test_historical_filename_reuses_history_cache_without_existence_query(self) -> None:
        self.historical_file(self.published, "renamed.md", "file.md")
        self.command.git_calls.clear()

        self.command.open_file_on_remote("origin")

        self.assert_url(self.published)
        self.assertEqual([args[0] for args in self.command.git_calls], ["branch"])

    def test_complete_buffer_command_uses_one_lightweight_branch_query(self) -> None:
        self.select_lines("first\nsecond\nthird\n", 2, 2)
        calls = self.run_command(preselect=True)

        self.assert_url("main", "#L2-L2")
        self.assertEqual(calls, ["config", "for-each-ref", "merge-base", "ls-tree", "show"])
        branch_query = next(args for args in self.command.git_calls if args[0] == "for-each-ref")
        self.assertNotIn("ahead-behind", " ".join(branch_query))

    def test_complete_upstream_command_skips_history_search_and_ref_resolution(self) -> None:
        self.git("branch", "--set-upstream-to=origin/main")
        self.select_lines("first\nsecond\nthird\n", 2, 2)

        calls = self.run_command(preselect=True)

        self.assert_url("main", "#L2-L2")
        self.assertEqual(calls, ["config", "for-each-ref", "ls-tree", "show"])

    def test_complete_command_reads_nested_upstream_name(self) -> None:
        self.git("update-ref", "refs/remotes/origin/topic/nested", self.published)
        self.git("branch", "--set-upstream-to=origin/topic/nested")

        calls = self.run_command()

        self.assert_url("topic%2Fnested")
        self.assertEqual(calls, ["config", "for-each-ref", "ls-tree"])

    def test_empty_published_file_opens_without_highlight(self) -> None:
        self.write_file("file.md", "")
        self.git("commit", "-am", "Publish empty file")
        self.git("update-ref", "refs/remotes/origin/main", "HEAD")
        self.select_lines("only local\n", 1, 1)

        self.run_command(preselect=True)

        self.assert_url("main")

    def test_status_command_reuses_branch_store(self) -> None:
        self.use_status_snapshot()

        calls = self.run_command()

        self.assert_url("main")
        self.assertEqual(calls, ["config", "merge-base", "ls-tree"])

    def test_status_upstream_uses_stored_remote_tip(self) -> None:
        self.use_status_snapshot(Upstream("origin", "main", "origin/main", "ahead 1"))

        calls = self.run_command()

        self.assert_url("main")
        self.assertEqual(calls, ["config", "ls-tree"])

    def test_status_without_snapshot_queries_branches(self) -> None:
        self.view.settings().set("git_savvy.status_view", True)
        when(self.command).current_state().thenReturn({})

        calls = self.run_command()

        self.assert_url("main")
        self.assertEqual(calls, ["config", "for-each-ref", "merge-base", "ls-tree"])

    def test_buffer_does_not_reuse_stale_status_snapshot(self) -> None:
        self.use_status_snapshot(Upstream("origin", "missing", "origin/missing", ""))
        self.view.settings().erase("git_savvy.status_view")

        calls = self.run_command()

        self.assert_url("main")
        self.assertEqual(calls, ["config", "for-each-ref", "merge-base", "ls-tree"])

    def test_repeated_open_caches_file_existence_and_contents(self) -> None:
        self.select_lines("first\nsecond\nthird\n", 2, 2)
        self.run_command(preselect=True)

        calls = self.run_command(preselect=True)

        self.assertEqual(calls, ["config", "for-each-ref", "merge-base"])
        verify(self.command, times=2).computed_urls([
            "https://github.com/owner/repo/blob/main/file.md#L2-L2"
        ])

    def test_new_invocation_observes_moved_tip_and_does_not_reuse_old_existence(self) -> None:
        self.git("branch", "--set-upstream-to=origin/main")
        self.run_command()
        os.remove(os.path.join(self.directory.name, "file.md"))
        self.git("add", "-u")
        self.git("commit", "-m", "Remove file")
        self.git("update-ref", "refs/remotes/origin/main", "HEAD")

        calls = self.run_command()

        verify(self.command, times=1).computed_urls(...)
        verify(open_on_remote).flash(self.view, "file.md does not exist at main on origin.")
        self.assertEqual(calls, ["config", "for-each-ref", "ls-tree"])

    def run_command(self, preselect: bool = False) -> list[str]:
        self.command.git_calls.clear()
        self.command.run.__wrapped__(self.command, None, fpath="file.md", preselect=preselect)
        return [args[0] for args in self.command.git_calls]

    def use_status_snapshot(self, upstream: Upstream | None = None) -> None:
        self.view.settings().set("git_savvy.status_view", True)
        when(self.command).current_state().thenReturn({"branches": [
            mock({"active": True, "is_remote": False, "upstream": upstream}),
            mock({"active": False, "is_remote": True, "canonical_name": "origin/main",
                  "commit_hash": self.published})
        ]})

    def historical_file(
        self, commit: str, path: str = "file.md", historical_path: str = "file.md"
    ) -> None:
        self.view.settings().set("git_savvy.show_file_at_commit_view", True)
        self.view.settings().set("git_savvy.show_file_at_commit_view.commit", commit)
        self.command.fpath = [path]
        anchor = self.command.to_full_path(path)
        file_history_cache[(commit, anchor)] = FileHistoryInfo(
            self.command.to_full_path(historical_path), None
        )

    def assert_url(self, revision: str, lines: str = "") -> None:
        verify(self.command).computed_urls([
            f"https://github.com/owner/repo/blob/{revision}/file.md{lines}"
        ])

    def select_lines(self, content: str, start: int, end: int) -> None:
        self.view.run_command("append", {"characters": content})
        self.view.sel().clear()
        self.view.sel().add(sublime.Region(
            self.view.text_point(start - 1, 0), self.view.text_point(end, 0)
        ))
        self.command.preselect = True

    def git(self, *args: str) -> str:
        return self.command.git(*args)

    def write_file(self, name: str, content: str) -> None:
        with open(os.path.join(self.directory.name, name), "w", encoding="utf-8") as f:
            f.write(content)


class FileUrlCommand(gs_github_open_file_in_browser):
    directory: str
    git_calls: list[tuple[str, ...]]

    def git(self, *args, **kwargs):
        self.git_calls.append(args)
        stdin = kwargs.get("stdin")
        # Text-mode pipes translate LF to CRLF on Windows, corrupting mktree input.
        result = subprocess.run(
            ["git", *[arg for arg in args if arg is not None]],
            cwd=self.directory, input=stdin.encode("utf-8") if stdin is not None else None,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            check=kwargs.get("throw_on_error", True),
            env=dict(os.environ, GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")
        )
        return result.stdout.decode("utf-8")
