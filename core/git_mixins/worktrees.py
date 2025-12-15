from __future__ import annotations
from itertools import count
import os

from typing import NamedTuple

import sublime

from GitSavvy.core.fns import filter_
from GitSavvy.core.utils import cache_in_store_as
from GitSavvy.core.git_command import mixin_base


class Worktree(NamedTuple):
    path: str
    commit_hash: str
    branch_name: str | None
    is_main: bool
    locked: str | bool
    prunable: str | bool

    @property
    def is_detached(self) -> bool:
        return not self.branch_name


class WorktreesMixin(mixin_base):
    @cache_in_store_as("worktrees")
    def get_worktrees(self) -> list[Worktree]:
        normalized_repo_path = self.repo_path.replace("\\", "/")
        worktrees: list[Worktree] = []

        def commit_current(info):
            if not info or info.get("bare"):
                return

            path = info["path"]
            commit_hash = info["commit_hash"]
            branch_name = info.get("branch")
            is_main = path == normalized_repo_path
            locked = info.get("locked", False)
            prunable = info.get("prunable", False)

            worktrees.append(Worktree(
                path, commit_hash, branch_name, is_main,
                locked, prunable
            ))

        current: dict = {}
        stdout: str = self.git("worktree", "list", "--porcelain", "-z")
        for field in filter_(stdout.split("\0")):
            if field.startswith("worktree "):
                commit_current(current)
                current = {}
                current["path"] = field[len("worktree "):].strip()
            elif field.startswith("HEAD "):
                current["commit_hash"] = field[len("HEAD "):].strip()
            elif field.startswith("branch "):
                branch_ref = field[len("branch "):].strip()
                current["branch"] = branch_ref[len("refs/heads/"):]
            elif field == "bare":
                current["bare"] = True
            elif field.startswith("locked"):
                current["locked"] = field[len("locked"):].strip() or True
            elif field.startswith("prunable"):
                current["prunable"] = field[len("prunable"):].strip() or True

        commit_current(current)
        return worktrees

    def create_new_worktree(self, start_point: str, worktree_path: str = None) -> str:
        if not worktree_path:
            if self.repo_path.startswith(sublime.packages_path()):
                base = self.default_project_root()
            else:
                base = os.path.dirname(self.repo_path)

            project_name = os.path.basename(self.repo_path)
            suffix, c = "", count(1)
            while True:
                worktree_path = f"{base}{os.path.sep}{project_name}-{start_point}{suffix}"
                if os.path.exists(worktree_path):
                    suffix = f"-{next(c)}"
                else:
                    break

        self.git("worktree", "add", worktree_path, start_point)
        return worktree_path

    def remove_worktree(self, path: str, *, force: bool = False):
        self.git("worktree", "remove", "-f" if force else None, path)
