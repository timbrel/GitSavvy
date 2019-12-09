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
        with util.file.safe_open(gitignore, "at", encoding="utf-8", newline=linesep) as ignore_file:
            ignore_file.write("\n" + path_or_pattern + "\n")
