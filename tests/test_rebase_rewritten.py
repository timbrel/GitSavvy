from __future__ import annotations

from pathlib import Path
import subprocess
import tempfile

from unittesting import DeferrableTestCase

from GitSavvy.core.git_command import GitCommand
from GitSavvy.tests.mockito import unstub, when
from GitSavvy.tests.parameterized import parameterized as p


class TestRebaseRewritten(DeferrableTestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory(prefix="gitsavvy-rewritten-")
        self.addCleanup(temp.cleanup)
        self.addCleanup(unstub)
        self.root = Path(temp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.git("-c", "init.defaultBranch=main", "init", "--template=")
        self.git("config", "user.name", "GitSavvy test")
        self.git("config", "user.email", "test@example.invalid")
        self.git("config", "commit.gpgSign", "false")
        self.git("config", "core.autocrlf", "false")
        self.command = GitCommand()
        when(self.command).get_repo_path().thenAnswer(lambda: str(self.repo))
        when(self.command).git(...).thenRaise(AssertionError("Metadata reader must not launch Git"))

    @p.expand([(backend, linked) for backend in ("merge", "apply") for linked in (False, True)])
    def test_completed_commit_during_a_real_conflicted_rebase(self, backend: str, linked: bool) -> None:
        (self.repo / "conflict.txt").write_text("original\n", encoding="utf-8")
        self.git("add", ".")
        self.git("commit", "-m", "Shared base")
        self.git("checkout", "-b", "topic")
        (self.repo / "first.txt").write_text("first change\n", encoding="utf-8")
        self.git("add", ".")
        self.git("commit", "-m", "First topic commit")
        original = self.git("rev-parse", "HEAD").strip()
        (self.repo / "conflict.txt").write_text("topic version\n", encoding="utf-8")
        self.git("commit", "-am", "Conflicting topic commit")
        self.git("checkout", "main")
        (self.repo / "conflict.txt").write_text("main version\n", encoding="utf-8")
        self.git("commit", "-am", "Advance main")
        if linked:
            self.git("worktree", "add", str(self.root / "linked"), "topic")
            self.repo = self.root / "linked"
        else:
            self.git("checkout", "topic")

        self.git("rebase", "--" + backend, "main", expected_code=1)
        self.assertEqual("conflict.txt\n", self.git("diff", "--name-only", "--diff-filter=U"))
        rewritten = self.git("rev-parse", "HEAD").strip()
        self.assertNotEqual(original, rewritten)
        self.assertEqual({original: rewritten}, self.command.rebase_rewritten())

    @p.expand([
        (layout, width)
        for layout in ("modern", "legacy-merge", "apply", "replay")
        for width in (40, 64)
    ])
    def test_supported_metadata_layouts(self, layout: str, width: int) -> None:
        old, new = "a" * width, "b" * width
        directory = self.repo / ".git" / {
            "modern": "rebase-merge", "legacy-merge": "rebase-merge",
            "apply": "rebase-apply", "replay": "rebase-replay"
        }[layout]
        directory.mkdir()
        if layout in ("modern", "apply"):
            name = "rewritten-list" if layout == "modern" else "rewritten"
            (directory / name).write_text(old + " " + new + "\n", encoding="utf-8")
            if layout == "modern":
                # The modern file takes precedence over any legacy leftovers.
                (directory / "rewritten").mkdir()
                (directory / "rewritten" / old).write_text("c" * width, encoding="utf-8")
        else:
            (directory / "rewritten").mkdir()
            (directory / "rewritten" / old).write_text(new + "\n", encoding="utf-8")
        self.assertEqual({old: new}, self.command.rebase_rewritten())

    def test_missing_or_empty_modern_metadata_has_no_completed_commits(self) -> None:
        self.assertEqual({}, self.command.rebase_rewritten())
        directory = self.repo / ".git" / "rebase-merge"
        directory.mkdir()
        self.assertEqual({}, self.command.rebase_rewritten())
        (directory / "rewritten-list").write_text("\n\n", encoding="utf-8")
        self.assertEqual({}, self.command.rebase_rewritten())

    def git(self, *args: str, expected_code: int = 0) -> str:
        result = subprocess.run(
            ["git", *args], cwd=str(self.repo), stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, encoding="utf-8", timeout=20
        )
        self.assertEqual(expected_code, result.returncode, result.stderr)
        return result.stdout
