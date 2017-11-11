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
                try:
                    autocrlf = self.git("config", "--global", "core.autocrlf").strip() == "true"
                except Exception:
                    autocrlf = False
                linesep = os.linesep if autocrlf else "\n"
            else:
                linesep = os.linesep

        with util.file.safe_open(os.path.join(self.repo_path, ".gitignore"), "at") as ignore_file:
            ignore_file.write(linesep + path_or_pattern + linesep)
