from __future__ import annotations

from typing import NamedTuple

from GitSavvy.core.fns import filter_
from GitSavvy.core.utils import cache_in_store_as
from GitSavvy.core.git_command import mixin_base


class Worktree(NamedTuple):
    path: str
    commit_hash: str
    branch_name: str | None
    detached: bool


class WorktreesMixin(mixin_base):
    @cache_in_store_as("worktrees")
    def get_worktrees(self) -> list[Worktree]:
        stdout = self.git("worktree", "list", "--porcelain", "-z")
        fields: list[str] = list(filter_(stdout.split("\0")))

        worktrees: list[Worktree] = []
        current: dict = {}

        def commit_current():
            if not current:
                return
            if current.get("bare"):
                current.clear()
                return
            path = current.get("path")
            commit_hash = current.get("commit_hash", "")
            branch_name = current.get("branch")
            detached = current.get("detached", False)

            if not branch_name:
                detached = True

            if path:
                worktrees.append(Worktree(path, commit_hash, branch_name, detached))

            current.clear()

        for field in fields:
            if field.startswith("worktree "):
                commit_current()
                current["path"] = field[len("worktree "):].strip()
            elif field.startswith("HEAD "):
                current["commit_hash"] = field[len("HEAD "):].strip()
            elif field.startswith("branch "):
                branch_ref = field[len("branch "):].strip()
                current["branch"] = branch_ref[len("refs/heads/"):]
            elif field == "detached":
                current["detached"] = True
            elif field == "bare":
                current["bare"] = True
            # Ignore other keys such as "locked" and "prunable".

        commit_current()
        return worktrees
