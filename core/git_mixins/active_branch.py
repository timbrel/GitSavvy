from GitSavvy.core.git_command import mixin_base
from .. import store


MYPY = False
if MYPY:
    from typing import List


class ActiveBranchMixin(mixin_base):

    def get_commit_hash_for_head(self):
        """
        Get the SHA1 commit hash for the commit at HEAD.
        """
        return self.git("rev-parse", "HEAD").strip()

    def get_latest_commit_msg_for_head(self):
        """
        Get last commit msg for the commit at HEAD.
        """
        stdout = self.git(
            "log",
            "-n 1",
            "--pretty=format:%h %s",
            "--abbrev-commit",
            throw_on_error=False
        ).strip()
        if stdout:
            try:
                short_hash = stdout.split(maxsplit=1)[0]
            except IndexError:
                pass
            else:
                store.update_state(self.repo_path, {"short_hash_length": len(short_hash)})

        return stdout or "No commits yet."

    def get_latest_commits(self, max_items=5):
        # type: (int) -> List[str]
        lines = [
            line.split("%00")
            for line in self.git(
                "log",
                "-n", "100",
                (
                    "--format="
                    "%h%00"
                    "%d%00"
                    "%s"
                ),
                throw_on_error=False
            ).strip().splitlines()
        ]
        try:
            short_hash = lines[0][0]
        except IndexError:
            pass
        else:
            store.update_state(self.repo_path, {"short_hash_length": len(short_hash)})

        def _postprocess(lines):
            for idx, (h, d, s) in enumerate(lines):
                if d and "HEAD" not in d:
                    if idx > max_items:
                        yield "\u200B ⋮"
                    yield "{} \u200B{}".format(h, d.lstrip())
                    break

                elif idx < max_items:
                    yield "{} {}".format(h, s)

        return list(_postprocess(lines)) or ["No commits yet."]
