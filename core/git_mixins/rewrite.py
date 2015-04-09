import os
from types import SimpleNamespace


class ChangeTemplate(SimpleNamespace):
    # orig_hash
    do_commit = True
    msg = None
    datetime = None
    author = None


class RewriteMixin():

    ChangeTemplate = ChangeTemplate

    def rewrite_active_branch(self, base_commit, commit_chain):
        branch_name = self.get_current_branch_name()

        # Detach HEAD to base commit.
        self.checkout_ref(base_commit)

        # Apply each commit to HEAD in order.
        try:
            for commit in commit_chain:
                self.git(
                    "cherry-pick",
                    "--allow-empty",
                    "--allow-empty-message",
                    "--no-commit",
                    commit.orig_hash
                    )

                # If squashing one commit into the next, do_commit should be
                # False so that it's changes are included in the next commit.
                if commit.do_commit:
                    self.git(
                        "commit",
                        "--author",
                        commit.author,
                        "--date",
                        commit.datetime,
                        "-F",
                        "-",
                        stdin=commit.msg
                        )

            self.git("branch", "-f", branch_name, "HEAD")

        except Exception as e:
            raise e

        finally:
            # Whether on success or failure, always re-checkout the branch.  On success,
            # this will be the re-written branch.  On failure, this will be the original
            # branch (since re-defining the branch ref is the last step).

            self.git("reset", "--hard")
            self.git("checkout", branch_name)

    @property
    def _rebase_dir(self):
        return os.path.join(self.repo_path, ".git", "rebase-apply")

    def in_rebase(self):
        return os.path.isdir(self._rebase_dir)

    def rebase_orig_head(self):
        path = os.path.join(self._rebase_dir, "orig-head")
        with open(path, "r") as f:
            return f.read().strip()

    def rebase_conflict_at(self):
        path = os.path.join(self._rebase_dir, "original-commit")
        with open(path, "r") as f:
            return f.read().strip()

    def rebase_branch_name(self):
        path = os.path.join(self._rebase_dir, "head-name")
        with open(path, "r") as f:
            return f.read().strip().replace("refs/heads/", "")

    def rebase_onto_commit(self):
        path = os.path.join(self._rebase_dir, "onto")
        with open(path, "r") as f:
            return f.read().strip()

    def rebase_rewritten(self):
        path = os.path.join(self._rebase_dir, "rewritten")
        try:
            with open(path, "r") as f:
                entries = f.read().strip().split("\n")
                return (entry.split(" ") for entry in entries)
        except FileNotFoundError:
            return []
