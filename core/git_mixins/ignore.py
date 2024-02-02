import os
from ...common import util

from GitSavvy.core import store
from GitSavvy.core.git_command import mixin_base

from typing import List


linesep = None


class IgnoreMixin(mixin_base):
    def get_skipped_files(self) -> List[str]:
        """Return all files for which skip-worktree is set."""
        skipped_files = [
            file_path
            for line in self.git("ls-files", "-v").splitlines()
            if line.startswith("S")
            if (file_path := line[2:])
        ]
        store.update_state(self.repo_path, {
            "skipped_files": skipped_files,
        })
        return skipped_files

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
