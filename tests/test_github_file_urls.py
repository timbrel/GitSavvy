from __future__ import annotations

import os
from unittest import TestCase

import sublime
from unittesting import DeferrableTestCase

from GitSavvy.core.git_mixins.branches import Upstream
from GitSavvy.core.git_mixins.history import FileHistoryInfo, file_history_cache
from GitSavvy.github.commands import open_on_remote
from GitSavvy.github.commands.open_on_remote import (
    FileUrlBranches, gs_github_open_file_in_browser, matching_lines, parse_branch_snapshot
)
from GitSavvy.tests.mockito import mock, unstub, verify, when
from GitSavvy.tests.parameterized import parameterized as p


class FileUrlTestCase(DeferrableTestCase):
    def setUp(self) -> None:
        self.addCleanup(unstub)
        self.view = sublime.active_window().new_file()
        self.view.set_scratch(True)
        self.addCleanup(self.view.close)
        self.command = gs_github_open_file_in_browser(self.view)
        self.command.fpath = ["file.md"]
        self.command.preselect = False
        self.command.remotes = {"origin": "https://github.com/owner/repo"}
        # The content caches are keyed by commit, not by repository or command.
        self.published = f"{self.view.id() * 2:040x}"
        self.unpublished = f"{self.view.id() * 2 + 1:040x}"
        when(self.command).get_repo_path().thenReturn(
            os.path.join(sublime.packages_path(), "file-url-test-{}".format(self.view.id()))
        )
        when(self.command).git(...).thenRaise(AssertionError("Unexpected Git query"))
        when(self.command).guess_default_branch("origin").thenReturn("origin/main")
        when(self.command).computed_urls(...).thenReturn(None)
        when(open_on_remote).flash(self.view, ...).thenReturn(None)
        when(sublime).ok_cancel_dialog(...).thenReturn(False)

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

    def historical_file(
        self, commit: str, path: str = "file.md", historical_path: str = "file.md"
    ) -> None:
        self.view.settings().set("git_savvy.show_file_at_commit_view", True)
        self.view.settings().set("git_savvy.show_file_at_commit_view.commit", commit)
        self.command.fpath = [path]
        key = (commit, self.command.to_full_path(path))
        file_history_cache[key] = FileHistoryInfo(self.command.to_full_path(historical_path), None)
        self.addCleanup(file_history_cache.pop, key, None)


class TestGitHubFileUrls(FileUrlTestCase):
    def setUp(self) -> None:
        super().setUp()
        when(self.command).get_upstream_for_active_branch().thenReturn(None)
        when(self.command).branch_snapshot().thenReturn(
            FileUrlBranches(None, {"origin/main": self.published})
        )
        when(self.command).published_branch_from_history("origin", ...).thenReturn(
            ("main", self.published)
        )
        when(self.command).file_paths_at_commit(self.published, ("file.md",)).thenReturn({"file.md"})
        when(self.command).get_file_content_at_commit("file.md", self.published).thenReturn(
            "first\nsecond\nthird\n"
        )
        when(self.command).remotes_containing_commit(self.published).thenReturn(["origin"])

    def test_unpublished_branch_opens_remote_default(self) -> None:
        self.command.open_file_on_remote("origin")

        self.assert_url("main")
        verify(self.command).published_branch_from_history("origin", "main")

    @p.expand([
        ("topic", "topic"),
        ("topic/issue#42", "topic%2Fissue%2342")
    ])
    def test_uses_upstream_branch_on_selected_remote(self, branch: str, escaped: str) -> None:
        when(self.command).get_upstream_for_active_branch().thenReturn(
            Upstream("origin", branch, "origin/" + branch, "ahead 1")
        )
        when(self.command).branch_snapshot().thenReturn(
            FileUrlBranches(None, {"origin/" + branch: self.published})
        )

        self.command.open_file_on_remote("origin")

        self.assert_url(escaped)
        verify(self.command, times=0).guess_default_branch(...)
        verify(self.command, times=0).published_branch_from_history(...)

    @p.expand([
        (Upstream("fork", "topic", "fork/topic", ""),),
        (Upstream(".", "main", "main", ""),),
        (Upstream("origin", "topic", "origin/topic", "gone"),)
    ])
    def test_other_or_gone_upstream_uses_remote_default(self, upstream: Upstream) -> None:
        when(self.command).get_upstream_for_active_branch().thenReturn(upstream)

        self.command.open_file_on_remote("origin")

        self.assert_url("main")
        verify(self.command).published_branch_from_history("origin", "main")

    def test_default_branch_from_remote_lookup_is_unqualified(self) -> None:
        when(self.command).guess_default_branch("origin").thenReturn("main")

        self.command.open_file_on_remote("origin")

        self.assert_url("main")
        verify(self.command).published_branch_from_history("origin", "main")

    def test_unknown_default_branch_does_not_guess_master(self) -> None:
        when(self.command).guess_default_branch("origin").thenReturn(None)
        when(self.command).published_branch_from_history("origin", None).thenReturn(None)

        self.command.open_file_on_remote("origin")

        verify(self.command, times=0).computed_urls(...)
        verify(open_on_remote).flash(self.view, "Could not determine the default branch of origin.")

    def test_unfetched_branch_asks_to_fetch(self) -> None:
        when(self.command).guess_default_branch("origin").thenReturn("origin/unfetched")
        when(self.command).published_branch_from_history("origin", "unfetched").thenReturn(None)

        self.command.open_file_on_remote("origin")

        verify(self.command, times=0).computed_urls(...)
        verify(open_on_remote).flash(
            self.view, "origin/unfetched is not available locally. Fetch origin first."
        )

    def test_new_file_is_not_opened_at_nonexistent_path(self) -> None:
        self.command.fpath = ["new.md"]
        when(self.command).file_paths_at_commit(self.published, ("new.md",)).thenReturn(set())

        self.command.open_file_on_remote("origin")

        verify(self.command, times=0).computed_urls(...)
        verify(open_on_remote).flash(self.view, "new.md does not exist at main on origin.")

    def test_multiple_files_use_the_same_remote_branch(self) -> None:
        self.command.fpath = ["file.md", "other.md"]
        when(self.command).file_paths_at_commit(
            self.published, ("file.md", "other.md")
        ).thenReturn({"file.md", "other.md"})

        self.command.open_file_on_remote("origin")

        verify(self.command).computed_urls([
            "https://github.com/owner/repo/blob/main/file.md",
            "https://github.com/owner/repo/blob/main/other.md"
        ])
        verify(self.command, times=1).published_branch_from_history(...)

    def test_selected_lines_are_mapped_from_unsaved_buffer(self) -> None:
        self.select_lines("unsaved\ndirty\nlocal commit\nfirst\nsecond\nthird\n", 5, 6)

        self.command.open_file_on_remote("origin")

        self.assert_url("main", "#L2-L3")

    def test_reversed_selection_is_normalized(self) -> None:
        self.select_lines("first\nlocal\nthird\n", 1, 3)
        selection = self.view.sel()[0]
        self.view.sel().clear()
        self.view.sel().add(sublime.Region(selection.b, selection.a))

        self.command.open_file_on_remote("origin")

        self.assert_url("main", "#L1-L3")

    def test_no_matching_lines_opens_without_highlight(self) -> None:
        self.select_lines("unrelated contents\n", 1, 1)

        self.command.open_file_on_remote("origin")

        self.assert_url("main")
        verify(open_on_remote, times=0).flash(...)

    def test_published_historical_file_preserves_revision_and_selection(self) -> None:
        self.select_lines("first\nsecond\nthird\n", 2, 2)
        self.historical_file(self.published)

        self.command.open_file_on_remote("origin")

        self.assert_url(self.published, "#L2-L2")
        verify(self.command, times=0).get_upstream_for_active_branch()
        verify(self.command, times=0).guess_default_branch(...)
        verify(self.command, times=0).get_file_content_at_commit(...)

    def test_historical_file_does_not_fall_back_to_published_ancestor(self) -> None:
        self.historical_file(self.unpublished)
        when(self.command).remotes_containing_commit(self.unpublished).thenReturn([])

        self.command.open_file_on_remote("origin")

        verify(self.command, times=0).computed_urls(...)
        verify(self.command, times=0).get_upstream_for_active_branch()
        verify(self.command, times=0).published_branch_from_history(...)
        verify(sublime, times=0).ok_cancel_dialog(...)
        verify(open_on_remote).flash(self.view, "This revision has not been pushed to origin yet.")


class TestFileUrlQueries(FileUrlTestCase):
    """Run the command and its caches, but answer Git queries without spawning Git."""

    def setUp(self) -> None:
        super().setUp()
        when(self.command).git(
            "config", "--get-regexp",
            r"^(remote\.|gitsavvy\.|url\..*\.insteadof$|.*\.pushdefault$)",
            throw_on_error=False
        ).thenReturn("remote.origin.url https://github.com/owner/repo\n")
        self.stub_branches()
        when(self.command).git(
            "merge-base", "--all", "HEAD", self.published,
            throw_on_error=False, show_panel_on_error=False
        ).thenReturn(self.published + "\n")
        when(self.command).git(
            "ls-tree", "-r", "-z", "--name-only", self.published, "--", "file.md"
        ).thenReturn("file.md\0")
        when(self.command).git("show", f"{self.published}:file.md").thenReturn(
            "first\nsecond\nthird\n"
        )

    def test_complete_buffer_command_uses_one_lightweight_branch_query(self) -> None:
        self.select_lines("first\nsecond\nthird\n", 2, 2)

        self.run_command(preselect=True)

        self.assert_url("main", "#L2-L2")
        verify(self.command, times=5).git(...)
        verify(self.command).git("for-each-ref", BRANCH_FORMAT, "refs/heads", "refs/remotes")

    def test_complete_upstream_command_skips_history_search_and_ref_resolution(self) -> None:
        self.stub_branches(upstream="main")
        self.select_lines("first\nsecond\nthird\n", 2, 2)

        self.run_command(preselect=True)

        self.assert_url("main", "#L2-L2")
        verify(self.command, times=4).git(...)
        verify(self.command, times=0).git("merge-base", ...)

    def test_complete_command_reads_nested_upstream_name(self) -> None:
        self.stub_branches(branch="topic/nested", upstream="topic/nested")

        self.run_command()

        self.assert_url("topic%2Fnested")
        verify(self.command, times=3).git(...)

    def test_status_command_reuses_branch_store(self) -> None:
        self.use_status_snapshot()

        self.run_command()

        self.assert_url("main")
        verify(self.command, times=3).git(...)
        verify(self.command, times=0).git("for-each-ref", ...)

    def test_status_upstream_uses_stored_remote_tip(self) -> None:
        self.use_status_snapshot(Upstream("origin", "main", "origin/main", "ahead 1"))

        self.run_command()

        self.assert_url("main")
        verify(self.command, times=2).git(...)
        verify(self.command, times=0).git("for-each-ref", ...)
        verify(self.command, times=0).git("merge-base", ...)

    def test_status_without_snapshot_queries_branches(self) -> None:
        self.view.settings().set("git_savvy.status_view", True)
        when(self.command).current_state().thenReturn({})

        self.run_command()

        self.assert_url("main")
        verify(self.command, times=4).git(...)
        verify(self.command).git("for-each-ref", BRANCH_FORMAT, "refs/heads", "refs/remotes")

    def test_buffer_does_not_reuse_stale_status_snapshot(self) -> None:
        self.use_status_snapshot(Upstream("origin", "missing", "origin/missing", ""))
        self.view.settings().erase("git_savvy.status_view")

        self.run_command()

        self.assert_url("main")
        verify(self.command, times=4).git(...)
        verify(self.command).git("for-each-ref", BRANCH_FORMAT, "refs/heads", "refs/remotes")

    def test_repeated_open_caches_file_existence_and_contents(self) -> None:
        self.select_lines("first\nsecond\nthird\n", 2, 2)
        self.run_command(preselect=True)

        self.run_command(preselect=True)

        verify(self.command, times=8).git(...)
        verify(self.command, times=1).git("ls-tree", ...)
        verify(self.command, times=1).git("show", ...)
        verify(self.command, times=2).computed_urls([
            "https://github.com/owner/repo/blob/main/file.md#L2-L2"
        ])

    def test_new_invocation_observes_moved_tip_and_does_not_reuse_old_existence(self) -> None:
        self.stub_branches(upstream="main")
        self.run_command()
        self.stub_branches(upstream="main", tip=self.unpublished)
        when(self.command).git(
            "ls-tree", "-r", "-z", "--name-only", self.unpublished, "--", "file.md"
        ).thenReturn("")

        self.run_command()

        verify(self.command, times=1).computed_urls(...)
        verify(open_on_remote).flash(self.view, "file.md does not exist at main on origin.")
        verify(self.command, times=6).git(...)
        verify(self.command, times=2).git("ls-tree", ...)

    def test_selection_uses_remote_tip_contents_not_shared_ancestor(self) -> None:
        self.stub_branches(tip=self.unpublished)
        when(self.command).git(
            "merge-base", "--all", "HEAD", self.unpublished,
            throw_on_error=False, show_panel_on_error=False
        ).thenReturn(self.published + "\n")
        when(self.command).git(
            "ls-tree", "-r", "-z", "--name-only", self.unpublished, "--", "file.md"
        ).thenReturn("file.md\0")
        when(self.command).git("show", f"{self.unpublished}:file.md").thenReturn(
            "remote\nchanges\nfirst\nsecond\nthird\n"
        )
        self.select_lines("local commit\nfirst\nsecond\nthird\n", 3, 3)

        self.run_command(preselect=True)

        self.assert_url("main", "#L4-L4")
        verify(self.command).git("show", f"{self.unpublished}:file.md")
        verify(self.command, times=0).git("show", f"{self.published}:file.md")

    def test_history_search_ignores_local_branches_and_other_remotes(self) -> None:
        when(self.command).git(
            "for-each-ref", BRANCH_FORMAT, "refs/heads", "refs/remotes"
        ).thenReturn(
            f"*\0refs/heads/ci-status\0{self.unpublished}\0\0\0\n"
            f" \0refs/remotes/fork/ci-status\0{self.unpublished}\0\0\0\n"
            f" \0refs/remotes/origin/main\0{self.published}\0\0\0\n"
        )

        self.run_command()

        self.assert_url("main")
        verify(self.command).git(
            "merge-base", "--all", "HEAD", self.published,
            throw_on_error=False, show_panel_on_error=False
        )

    def test_default_branch_wins_tie_and_remote_head_is_not_a_branch(self) -> None:
        when(self.command).git(
            "for-each-ref", BRANCH_FORMAT, "refs/heads", "refs/remotes"
        ).thenReturn(
            f"*\0refs/heads/topic\0{self.unpublished}\0\0\0\n"
            f" \0refs/remotes/origin/aaa\0{self.published}\0\0\0\n"
            f" \0refs/remotes/origin/HEAD\0{self.published}\0\0\0refs/remotes/origin/main\n"
            f" \0refs/remotes/origin/main\0{self.published}\0\0\0\n"
        )

        self.run_command()

        self.assert_url("main")
        verify(self.command).git(
            "merge-base", "--all", "HEAD", self.published,
            throw_on_error=False, show_panel_on_error=False
        )

    def test_published_branch_does_not_require_knowing_remote_default(self) -> None:
        when(self.command).guess_default_branch("origin").thenReturn(None)

        self.run_command()

        self.assert_url("main")

    def test_no_remote_tips_skips_history_search(self) -> None:
        when(self.command).git(
            "for-each-ref", BRANCH_FORMAT, "refs/heads", "refs/remotes"
        ).thenReturn(f"*\0refs/heads/topic\0{self.unpublished}\0\0\0\n")

        self.assertIsNone(self.command.published_branch_from_history("origin", "main"))
        verify(self.command, times=0).git("merge-base", ...)

    def test_unrelated_history_falls_back_to_default_tip(self) -> None:
        when(self.command).git(
            "merge-base", "--all", "HEAD", self.published,
            throw_on_error=False, show_panel_on_error=False
        ).thenReturn("")

        self.run_command()

        self.assert_url("main")

    def test_historical_filename_reuses_history_cache_without_existence_query(self) -> None:
        self.historical_file(self.published, "renamed.md", "file.md")
        when(self.command).git(
            "branch", None, "-r", "--contains", self.published
        ).thenReturn("  origin/main\n")

        self.command.open_file_on_remote("origin")

        self.assert_url(self.published)
        verify(self.command, times=1).git(...)
        verify(self.command, times=0).git("ls-tree", ...)

    def run_command(self, preselect: bool = False) -> None:
        self.command.run.__wrapped__(self.command, None, fpath="file.md", preselect=preselect)

    def stub_branches(
        self, branch: str = "main", upstream: str = "", tip: str | None = None
    ) -> None:
        upstream_ref = "refs/remotes/origin/" + upstream if upstream else ""
        remote = "origin" if upstream else ""
        when(self.command).git(
            "for-each-ref", BRANCH_FORMAT, "refs/heads", "refs/remotes"
        ).thenReturn(
            f"*\0refs/heads/topic\0{self.unpublished}\0{upstream_ref}\0{remote}\0\n"
            f" \0refs/remotes/origin/{branch}\0{tip or self.published}\0\0\0\n"
        )

    def use_status_snapshot(self, upstream: Upstream | None = None) -> None:
        self.view.settings().set("git_savvy.status_view", True)
        when(self.command).current_state().thenReturn({"branches": [
            mock({"active": True, "is_remote": False, "upstream": upstream}),
            mock({"active": False, "is_remote": True, "canonical_name": "origin/main",
                  "commit_hash": self.published})
        ]})


class TestMatchingLines(TestCase):
    @p.expand([
        ("unsaved\ndirty\nlocal commit\nfirst\nsecond\nthird\n", 5, 6, (2, 3)),
        ("first\nonly local\nthird\n", 1, 2, (1, 2)),
        ("unrelated contents\n", 1, 1, None),
        ("", 1, 1, None),
        ("local\nfirst\nsecond\nthird\n", 1, 1, (1, 1)),
        ("first\nsecond\nthird\nlocal\n", 4, 4, (3, 3)),
        ("first\nthird\n", 2, 2, (3, 3)),
        ("first\nlocal\nthird\n", 1, 3, (1, 3))
    ])
    def test_maps_lines(
        self, content: str, start: int, end: int, expected: tuple[int, int] | None
    ) -> None:
        self.assertEqual(matching_lines(content, "first\nsecond\nthird\n", start, end), expected)

    def test_empty_published_file_has_no_matching_lines(self) -> None:
        self.assertIsNone(matching_lines("only local\n", "", 1, 1))


class TestParseFileUrlBranches(TestCase):
    def test_ignores_local_tips_and_symbolic_remote_head(self) -> None:
        self.assertEqual(parse_branch_snapshot(
            "*\0refs/heads/topic\0local-tip\0\0\0\n"
            " \0refs/remotes/origin/HEAD\0main-tip\0\0\0refs/remotes/origin/main\n"
            " \0refs/remotes/origin/main\0main-tip\0\0\0\n"
        ), FileUrlBranches(None, {"origin/main": "main-tip"}))

    @p.expand([
        ("refs/remotes/origin/topic/nested", "origin",
         Upstream("origin", "topic/nested", "origin/topic/nested", "")),
        ("refs/remotes/origin/missing", "origin",
         Upstream("origin", "missing", "origin/missing", "gone")),
        ("refs/heads/main", ".", Upstream(".", "main", "main", ""))
    ])
    def test_reads_upstream(self, ref: str, remote: str, expected: Upstream) -> None:
        snapshot = parse_branch_snapshot(
            f"*\0refs/heads/topic\0local-tip\0{ref}\0{remote}\0\n"
            " \0refs/remotes/origin/topic/nested\0remote-tip\0\0\0\n"
        )
        self.assertEqual(snapshot.upstream, expected)


BRANCH_FORMAT = (
    "--format=%(HEAD)%00%(refname)%00%(objectname)%00%(upstream)"
    "%00%(upstream:remotename)%00%(symref)"
)
