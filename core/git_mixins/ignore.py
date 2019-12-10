import os
from ...common import util


linesep = None


class IgnoreMixin():

    def add_ignore(self, path_or_pattern):
        """
        Add the provided relative path or pattern to the repo's `.gitignore` file.
        """
        global linesep

        if not linesep:
            # Use native line ending on Windows only when `autocrlf` is set to `true`.
            if os.name == "nt":
                autocrlf = self.git("config", "--global", "core.autocrlf",
                                    throw_on_stderr=False).strip() == "true"
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
