from __future__ import annotations

import os
import subprocess
import tempfile
from types import SimpleNamespace

import sublime
from unittesting import DeferrableTestCase

from GitSavvy.core.git_command import GitCommand
from GitSavvy.core.git_mixins import status
from GitSavvy.core.interfaces.status_main_actions import gs_status_action_menu
from GitSavvy.tests.mockito import unstub, when
from GitSavvy.tests.parameterized import parameterized as p


CASES = [
    (ref_format, operation)
    for ref_format in ("files", "reftable")
    for operation in ("merge", "cherry-pick", "revert")
]
LABELS = {"merge": "merging", "cherry-pick": "cherry-picking", "revert": "reverting"}


class TestStoppedOperations(DeferrableTestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory(prefix="gitsavvy-operations-")
        self.addCleanup(directory.cleanup)
        self.addCleanup(unstub)
        self.repo = os.path.join(directory.name, "repo")
        # Keep the test away from GitHub; it would only see a local repo anyway.
        when(status.ci_status).on_head_observed(...).thenReturn(None)

    @p.expand(CASES)
    def test_status_snapshot_knows_the_stopped_operation(self, ref_format: str, operation: str) -> None:
        commit = self.stop_in(operation, ref_format)
        git = self.command(GitCommand())
        git.get_working_dir_status()

        state = git.current_state()
        head = state["head"]
        self.assertEqual(operation == "merge", head.in_merge())
        self.assertEqual(operation == "cherry-pick", head.in_cherry_pick())
        self.assertEqual(operation == "revert", head.in_revert())
        self.assertFalse(head.in_rebase() or head.in_bisect())
        label, short = LABELS[operation], git.to_short_hash(commit)
        self.assertIn("{} {}.".format(label.capitalize(), short), state["long_status"])
        self.assertEqual("main* ({} {})".format(label, short), state["short_status"])

        self.git(operation, "--abort")
        git.get_working_dir_status()
        head = git.current_state()["head"]
        self.assertFalse(head.in_merge() or head.in_cherry_pick() or head.in_revert())

    @p.expand(CASES)
    def test_action_menu_follows_the_dashboard_snapshot(self, ref_format: str, operation: str) -> None:
        self.stop_in(operation, ref_format)
        git = self.command(GitCommand())
        git.get_working_dir_status()

        view = sublime.active_window().new_file()
        view.set_scratch(True)
        self.addCleanup(view.close)
        menu = self.command(gs_status_action_menu(view))
        menu.interface = SimpleNamespace(state=git.current_state())
        name = operation.capitalize()
        expected = ["--continue", "--abort"] if operation == "merge" else ["--continue", "--abort", "--skip"]
        self.assertEqual(
            [name + " " + action for action in expected],
            [label for label, _ in menu.standard_status_actions()[:len(expected)]]
        )

    def stop_in(self, operation: str, ref_format: str) -> str:
        """Leave `operation` stopped on a conflict; return the commit it applies."""
        if ref_format == "reftable":
            version = self.git("version").split()[2].split(".")
            if tuple(map(int, version[:2])) < (2, 45):
                self.skipTest("Reftable repositories require Git 2.45+")
        self.git("init", "--initial-branch=main", "--ref-format=" + ref_format, self.repo)
        self.commit("base\n", "Base")
        if operation in ("merge", "cherry-pick"):
            self.git("checkout", "-b", "side")
            target = self.commit("side\n", "Side change")
            self.git("checkout", "main")
            self.commit("main\n", "Main change")
        else:
            target = self.commit("first\n", "First change")
            self.commit("second\n", "Second change")
        self.git(operation, "--no-edit", target, check=False)
        return target

    def commit(self, content: str, message: str) -> str:
        with open(os.path.join(self.repo, "file.txt"), "w", encoding="utf-8", newline="\n") as f:
            f.write(content)
        self.git("add", "file.txt")
        self.git("commit", "-m", message)
        return self.git("rev-parse", "HEAD").strip()

    def command(self, command):
        when(command).get_repo_path().thenReturn(self.repo)
        return command

    def git(self, *args: str, check: bool = True) -> str:
        result = subprocess.run(
            ["git", "-c", "user.name=Test", "-c", "user.email=test@example.com", *args],
            cwd=self.repo if os.path.isdir(self.repo) else None,
            capture_output=True, check=check
        )
        return result.stdout.decode("utf-8")
