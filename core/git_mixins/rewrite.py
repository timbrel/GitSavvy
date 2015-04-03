from collections import namedtuple


CommitTemplate = namedtuple("CommitTemplate", (
    "orig_hash",
    "do_commit",  # True, False
    "msg",        # String or None
    "datetime",   # String or None
    "author",     # String or None
    ))

CommitTemplate.__new__.__defaults__ = (None, ) * 3


class RewriteMixin():

    CommitTemplate = CommitTemplate

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
                    "--no-commit",
                    commit.orig_hash
                    )

                # If squashing one commit into the next, do_commit should be
                # False so that it's changes are included in the next commit.
                if commit.do_commit:
                    non_default_msg = commit.msg is not None

                    self.git(
                        "commit",
                        # Re-use commit data and metadata from original commit hash.
                        "-C",
                        commit.orig_hash,
                        "-F" if non_default_msg else None,
                        "-" if non_default_msg else None,
                        stdin=commit.msg if non_default_msg else None
                        )

            self.git("branch", "-f", branch_name, "HEAD")

        except Exception as e:
            raise e

        finally:
            # Whether on success or failure, always re-checkout the branch.  On success,
            # this will be the re-written branch.  On failure, this will be the original
            # branch (since re-defining the branch ref is the last step).

            self.git("checkout", branch_name)
