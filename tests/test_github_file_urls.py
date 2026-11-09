from __future__ import annotations

import os
import subprocess
import tempfile

import sublime
from unittesting import DeferrableTestCase

from GitSavvy.core.git_mixins.branches import Upstream
from GitSavvy.github.commands import open_on_remote
from GitSavvy.github.commands.open_on_remote import gs_github_open_file_in_browser
from GitSavvy.tests.mockito import unstub, verify, when
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
        self.command.fpath = ["file.md"]
        self.command.preselect = False
        self.command.remotes = {"origin": "https://github.com/owner/repo"}
        when(self.command).get_repo_path().thenReturn(self.directory.name)
        when(self.command).get_upstream_for_active_branch().thenReturn(None)
        when(self.command).guess_default_branch("origin").thenReturn("origin/main")
        when(self.command).computed_urls(...).thenReturn(None)
        when(open_on_remote).flash(self.view, ...).thenReturn(None)
        when(sublime).ok_cancel_dialog(...).thenReturn(False)
        self.git("init")
        self.git("config", "user.name", "File URL Test")
        self.git("config", "user.email", "file-url@example.invalid")
        self.git("config", "core.autocrlf", "false")
        self.write_file("file.md", "first\nsecond\nthird\n")
        self.git("add", ".")
        self.git("commit", "-m", "Published file")
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

        self.command.open_file_on_remote("origin")

        self.assert_url("topic")

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
        when(self.command).guess_default_branch("origin").thenReturn(None)

        self.command.open_file_on_remote("origin")

        verify(self.command, times=0).computed_urls(...)
        verify(open_on_remote).flash(self.view, "Could not determine the default branch of origin.")

    def test_unfetched_branch_asks_to_fetch(self) -> None:
        when(self.command).guess_default_branch("origin").thenReturn("origin/unfetched")

        self.command.open_file_on_remote("origin")

        verify(self.command, times=0).computed_urls(...)
        verify(open_on_remote).flash(
            self.view, "origin/unfetched is not available locally. Fetch origin first."
        )

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

    def test_changed_selected_lines_are_not_mapped_to_unrelated_lines(self) -> None:
        self.select_lines("first\nonly local\nthird\n", 1, 2)

        self.command.open_file_on_remote("origin")

        verify(self.command, times=0).computed_urls(...)
        verify(open_on_remote).flash(
            self.view, "The selected lines are not published on origin/main."
        )

    def test_published_historical_file_preserves_revision_and_selection(self) -> None:
        self.select_lines("first\nsecond\nthird\n", 2, 2)
        self.view.settings().set("git_savvy.show_file_at_commit_view", True)
        self.view.settings().set("git_savvy.show_file_at_commit_view.commit", self.published)

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

    def git(self, *args, **kwargs):
        result = subprocess.run(
            ["git", *[arg for arg in args if arg is not None]],
            cwd=self.directory, input=kwargs.get("stdin"),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            check=kwargs.get("throw_on_error", True),
            env=dict(os.environ, GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")
        )
        return result.stdout
