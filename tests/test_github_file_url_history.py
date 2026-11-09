from __future__ import annotations

import os
import subprocess
import tempfile

import sublime
from unittesting import DeferrableTestCase

from GitSavvy.github.commands.open_on_remote import gs_github_open_file_in_browser
from GitSavvy.tests.mockito import unstub, when


class TestPublishedFileUrlHistory(DeferrableTestCase):
    """Check Git's merge-base/containment semantics, not URL formatting or file I/O."""

    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.addCleanup(unstub)
        self.view = sublime.active_window().new_file()
        self.view.set_scratch(True)
        self.addCleanup(self.view.close)
        self.command = gs_github_open_file_in_browser(self.view)
        when(self.command).git(...).thenAnswer(self.git)
        when(self.command).guess_default_branch("origin").thenReturn("origin/main")
        # Only the commit graph matters. No checkout, config writes, or index needed.
        self.git("init", "--bare", "--initial-branch=topic")
        self.tree = self.git("mktree", stdin="").strip()
        self.published = self.commit("Published")
        self.feature = self.commit("Feature", self.published)
        self.set_refs({"HEAD": self.feature, "refs/remotes/origin/main": self.published})

    def test_stack_opens_nearest_published_branch(self) -> None:
        local = self.commit("Local work above feature", self.feature)
        self.set_refs({"HEAD": local, "refs/remotes/origin/ci-status": self.feature})

        self.assertEqual(self.command.published_revision("origin"), ("ci-status", self.feature))

    def test_merged_feature_prefers_default_over_old_feature_tip(self) -> None:
        merge = self.commit("Merge feature", self.published, self.feature)
        local = self.commit("Local work above merged feature", self.feature)
        self.set_refs({
            "HEAD": local,
            "refs/remotes/origin/main": merge,
            "refs/remotes/origin/ci-status": self.feature
        })
        self.git("symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")

        self.assertEqual(self.command.published_revision("origin"), ("main", merge))

    def test_unmerged_feature_selects_its_branch_even_when_tip_has_moved_on(self) -> None:
        remote = self.commit("More feature work", self.feature)
        local = self.commit("Local work above feature", self.feature)
        self.set_refs({"HEAD": local, "refs/remotes/origin/next": remote})

        self.assertEqual(self.command.published_revision("origin"), ("next", remote))

    def test_shared_ancestor_selects_default_tip_not_ancestor(self) -> None:
        remote = self.commit("Remote-only work", self.published)
        self.set_refs({"refs/remotes/origin/main": remote})

        self.assertEqual(self.command.published_revision("origin"), ("main", remote))

    def commit(self, message: str, *parents: str) -> str:
        return self.git(
            "commit-tree", self.tree,
            *(arg for parent in parents for arg in ("-p", parent)),
            stdin=message + "\n"
        ).strip()

    def set_refs(self, refs: dict[str, str]) -> None:
        self.git("update-ref", "--stdin", stdin="".join(
            f"update {ref} {commit}\n" for ref, commit in refs.items()
        ))

    def git(self, *args: str, stdin: str | None = None, throw_on_error: bool = True, **kwargs) -> str:
        result = subprocess.run(
            ["git", *args], cwd=self.directory.name,
            input=stdin.encode("utf-8") if stdin is not None else None,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=throw_on_error,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            env=dict(
                os.environ,
                GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1",
                GIT_AUTHOR_NAME="File URL Test", GIT_AUTHOR_EMAIL="file-url@example.invalid",
                GIT_COMMITTER_NAME="File URL Test", GIT_COMMITTER_EMAIL="file-url@example.invalid"
            )
        )
        return result.stdout.decode("utf-8")
