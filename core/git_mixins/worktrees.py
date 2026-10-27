from __future__ import annotations
import configparser
from copy import deepcopy
from glob import glob
from itertools import count
import os
import re

from typing import Callable, Iterable, NamedTuple

import sublime

from GitSavvy.core.fns import filter_
from GitSavvy.core.caches import cache_in_store_as
from GitSavvy.core.git_command import mixin_base
from GitSavvy.core.types import FullHash, ShortHash
from GitSavvy.core.utils import open_worktree_in_new_window


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
    is_current: bool
    locked: str | bool
    prunable: str | bool

    @property
    def is_detached(self) -> bool:
        return not self.branch_name


class WorktreeLocation(NamedTuple):
    path: str
    branch_name: str | None
    detached_commit: FullHash | None
    is_current: bool

    @property
    def is_detached(self) -> bool:
        return self.branch_name is None


class WorktreesMixin(mixin_base):
    @cache_in_store_as("worktree_locations")
    def get_worktree_locations(self) -> list[WorktreeLocation]:
        """Read checkout locations and HEAD identities without resolving refs.

        This is not a replacement for Git's complete worktree inventory. Skip
        unreadable registrations and layouts whose checkout path is unknown.
        No lock/prune status or attached commit hashes are inferred. Reftable
        HEADs are not supported by this loose-HEAD reader.
        """
        common_dir = self.git_common_dir
        if os.path.isdir(os.path.join(common_dir, "reftable")):
            return []
        entries: list[tuple[str, str]] = []
        if _paths_are_equal(self.git_dir, common_dir):
            if not _paths_are_equal(self.repo_path, common_dir):
                entries.append((common_dir, self.repo_path))
        elif main_path := main_worktree_path(common_dir):
            entries.append((common_dir, main_path))
        try:
            with os.scandir(os.path.join(common_dir, "worktrees")) as children:
                for child in children:
                    if child.is_dir():
                        gitfile = read_worktree_file(os.path.join(child.path, "gitdir"))
                        if gitfile:
                            if not os.path.isabs(gitfile):
                                gitfile = os.path.normpath(os.path.join(child.path, gitfile))
                            entries.append((child.path, os.path.dirname(gitfile)))
        except OSError:
            pass

        locations = []
        for git_dir, path in entries:
            head = read_worktree_file(os.path.join(git_dir, "HEAD"))
            if head.startswith("ref: refs/heads/"):
                branch = head[len("ref: refs/heads/"):]
                if branch:
                    locations.append(WorktreeLocation(
                        path, branch, None, _paths_are_equal(git_dir, self.git_dir)
                    ))
            elif re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", head):
                locations.append(WorktreeLocation(
                    path, None, FullHash(head), _paths_are_equal(git_dir, self.git_dir)
                ))
        return locations

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
            is_current = path == normalized_repo_path
            locked = info.get("locked", False)
            prunable = info.get("prunable", False)

            worktrees.append(Worktree(
                path, commit_hash, branch_name, is_current,
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
        self._copy_active_project_to_worktree(worktree_path)
        return worktree_path

    def open_worktree_in_new_window(
        self, path: str, *, then: Callable[[sublime.Window], None] | None = None
    ) -> None:
        self._copy_active_project_to_worktree(path)
        open_worktree_in_new_window(path, then=then)

    def move_worktree(self, path: str, new_path: str) -> None:
        self.git("worktree", "move", path, new_path)

    def remove_worktree(self, path: str, *, force: bool = False, unlock: bool = False) -> None:
        if unlock:
            self.git_throwing_silently("worktree", "unlock", path)
        self.git_throwing_silently("worktree", "remove", "-f" if force else None, path)

    def _copy_active_project_to_worktree(self, path: str) -> None:
        if (
            not self.app_settings.get("copy_active_project_file_to_new_worktrees", True)
            or glob(os.path.join(path, "*.sublime-project"))
        ):
            return

        if project := self._project_for_worktree():
            self._copy_project_to_worktree(project, path)

    def _project_for_worktree(self) -> tuple[str, dict] | None:
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
        _, source_data = project
        filename = os.path.basename(os.path.normpath(worktree_path)) + ".sublime-project"
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


def main_worktree_path(common_dir: str) -> str | None:
    """Recognize a conventional main checkout, never guess a separate gitdir's parent."""
    if os.path.isfile(os.path.join(common_dir, "config.worktree")):
        return None
    config = configparser.RawConfigParser(
        strict=False, allow_no_value=True, inline_comment_prefixes=("#", ";")
    )
    try:
        with open(os.path.join(common_dir, "config"), encoding="utf-8") as f:
            config.read_file(f)
        core = next((section for section in config.sections() if section.lower() == "core"), "core")
        bare = config.get(core, "bare", fallback="false")
        if bare is None or config.BOOLEAN_STATES.get(bare.strip('"').lower()) is not False:
            return None
        # Includes and explicit worktree paths need Git's config/path semantics.
        if config.has_option(core, "worktree") or any(
            section.lower() == "include" or section.lower().startswith("includeif ")
            for section in config.sections()
        ):
            return None
    except (OSError, UnicodeError, configparser.Error):
        return None
    if os.path.basename(os.path.normpath(common_dir)) == ".git":
        return os.path.dirname(os.path.normpath(common_dir))
    return None


def read_worktree_file(path: str) -> str:
    try:
        with open(path, encoding="utf-8") as f:
            return f.read().rstrip("\r\n")
    except (OSError, UnicodeError):
        return ""


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
