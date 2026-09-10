from __future__ import annotations
from copy import deepcopy
from itertools import count
import os

from typing import Iterable, NamedTuple

import sublime

from GitSavvy.core.fns import filter_
from GitSavvy.core.caches import cache_in_store_as
from GitSavvy.core.git_command import mixin_base
from GitSavvy.core.types import FullHash, ShortHash


WORKTREE_LIST_SUPPORTS_Z_FORMAT = (2, 36, 0)
WORKTREE_LIST_PORCELAIN_FIELD_PREFIXES = (
    "worktree ",
    "HEAD ",
    "branch ",
    "locked",
    "prunable",
    "bare",
    "detached"
)


class Worktree(NamedTuple):
    path: str
    commit_hash: FullHash
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
        if self.git_version < WORKTREE_LIST_SUPPORTS_Z_FORMAT:
            return self._get_worktrees_without_z()

        stdout: str = self.git("worktree", "list", "--porcelain", "-z")
        return self._parse_worktree_entries(stdout.split("\0"))

    def _get_worktrees_without_z(self) -> list[Worktree]:
        stdout: str = self.git("worktree", "list", "--porcelain")
        fields = stdout.splitlines()
        if any(_looks_malformed_non_z_field(field) for field in filter_(fields)):
            return []

        return self._parse_worktree_entries(fields)

    def _parse_worktree_entries(self, fields: Iterable[str]) -> list[Worktree]:
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
        for field in filter_(fields):
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

    def create_new_worktree(
        self, start_point: ShortHash, worktree_path: str | None = None
    ) -> str:
        # Note: `start_point: ShortHash` is a shortcut for the automatic `worktree_path`
        #       computation.  Change when needed.
        project = (
            self._project_for_new_worktree()
            if self.app_settings.get("copy_active_project_file_to_new_worktrees", True)
            else None
        )
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
        if project:
            self._copy_project_to_worktree(project, worktree_path)
        return worktree_path

    def move_worktree(self, path: str, new_path: str) -> None:
        self.git("worktree", "move", path, new_path)

    def remove_worktree(self, path: str, *, force: bool = False):
        self.git("worktree", "remove", "-f" if force else None, path)

    def _project_for_new_worktree(self) -> tuple[str, dict] | None:
        window = self.some_window()
        project_file = window.project_file_name()
        if (
            not project_file
            or not project_file.endswith(".sublime-project")
            or not _paths_are_equal(os.path.dirname(project_file), self.repo_path)
        ):
            return None

        filename = os.path.basename(project_file)
        if self.git("ls-files", "-z", "--", filename):
            return None

        project_data = window.project_data()
        if project_data is None:
            return None
        return filename, project_data

    def _copy_project_to_worktree(
        self, project: tuple[str, dict], worktree_path: str
    ) -> None:
        filename, source_data = project
        destination = os.path.join(worktree_path, filename)
        if os.path.exists(destination):
            return

        project_data = deepcopy(source_data)
        for folder in project_data.get("folders", []):
            path = folder.get("path")
            if path and os.path.isabs(path) and _paths_are_equal(path, self.repo_path):
                folder["path"] = "."

        with open(destination, "w", encoding="utf-8", newline="\n") as file:
            file.write(sublime.encode_value(project_data, pretty=True))
            file.write("\n")


def _looks_malformed_non_z_field(field: str) -> bool:
    return (
        field.startswith('worktree "')
        or not field.startswith(WORKTREE_LIST_PORCELAIN_FIELD_PREFIXES)
    )


def _paths_are_equal(path_a: str, path_b: str) -> bool:
    return (
        os.path.normcase(os.path.realpath(path_a))
        == os.path.normcase(os.path.realpath(path_b))
    )
