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

            self.git("checkout", branch_name)
