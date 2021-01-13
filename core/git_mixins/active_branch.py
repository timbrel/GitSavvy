from GitSavvy.core.git_command import mixin_base
from .. import store


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
