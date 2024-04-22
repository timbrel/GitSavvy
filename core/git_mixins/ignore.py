import os
from ...common import util

from GitSavvy.core.git_command import mixin_base
from GitSavvy.core.utils import cache_in_store_as

from typing import List


linesep = None


class IgnoreMixin(mixin_base):
    @cache_in_store_as("skipped_files")
    def get_skipped_files(self) -> List[str]:
        """Return all files for which skip-worktree is set."""
        return [
            file_path
            for line in self.git("ls-files", "-v").splitlines()
            if line.startswith("S")
            if (file_path := line[2:])
        ]

    def set_skip_worktree(self, *file_paths: str) -> None:
        self.git("update-index", "--skip-worktree", *file_paths)

    def unset_skip_worktree(self, *file_paths: str) -> None:
        self.git("update-index", "--no-skip-worktree", *file_paths)

    def add_ignore(self, path_or_pattern):
        """
        Add the provided relative path or pattern to the repo's `.gitignore` file.
        """
        global linesep

        if not linesep:
            # Use native line ending on Windows only when `autocrlf` is set to `true`.
            if os.name == "nt":
                autocrlf = self.git("config", "--global", "core.autocrlf",
                                    throw_on_error=False).strip() == "true"
                linesep = os.linesep if autocrlf else "\n"
            else:
                linesep = os.linesep

        gitignore = os.path.join(self.repo_path, ".gitignore")
        if os.path.exists(gitignore):
            with util.file.safe_open(gitignore, "r", encoding="utf-8") as fp:
                ignore_lines = fp.read().splitlines()
        else:
            ignore_lines = []

        ignore_lines += [path_or_pattern, ""]
        with util.file.safe_open(gitignore, "w", encoding="utf-8", newline=linesep) as fp:
            fp.write("\n".join(ignore_lines))
