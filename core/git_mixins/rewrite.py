import os
import shutil
from types import SimpleNamespace


class RewriteTemplate(SimpleNamespace):
    # orig_hash
    do_commit = True
    msg = None
    datetime = None
    author = None

    def __getattribute__(self, key):
        """
        Check the attribute exists, return None if key not found
        """
        try:
            return super().__getattribute__(key)
        except AttributeError:
            return None


class RewriteMixin():

    def log_rebase(self, start, end="HEAD", preserve=False):
        return self.log(
            start_end=(start, end),
            reverse=True,
            first_parent=preserve,
            no_merges=not preserve,
            topo_order=True)

    def perpare_rewrites(self, entries):
        commit_chain = [
            RewriteTemplate(orig_hash=entry.long_hash,
                            do_commit=True,
                            msg=entry.raw_body,
                            datetime=entry.datetime,
                            author="{} <{}>".format(entry.author, entry.email))
            for entry in entries
        ]

        return commit_chain

    def _write_commit(self, commit):
        self.git(
            "commit",
            "--author",
            commit.author,
            "--date",
            commit.datetime,
            "-F",
            "-",
            stdin=commit.msg)
        # the original hash is rewritten, the information is stored under the directory
        # self._rebase_replay_dir
        self.rewrite_meta_data(commit.orig_hash, self.get_commit_hash_for_head())

    def _can_fast_forward(self, commit):
        if commit.modified:
            return False
        elif self.commit_is_merge(commit.orig_hash):
            parents = self.commit_parents(commit.orig_hash)
            for p in parents:
                if p in self.rebase_rewritten() or p in self._commit_parents_mapping:
                    return False
            return True
        else:
            first_parent = self.commit_parents(commit.orig_hash)[0]
            return first_parent == self.get_commit_hash_for_head()

    def _replay_single_commit(self, commit):
        # If squashing one commit into the next, do_commit should be
        # False so that it's changes are included in the next commit.
        # If the commit parent is the same as HEAD, do fast forward.

        if commit.do_commit and self._can_fast_forward(commit):
            # fast forward cherry pick
            self.git(
                "cherry-pick",
                "--allow-empty",
                "--allow-empty-message",
                "--ff",
                commit.orig_hash)
        else:
            self.git(
                "cherry-pick",
                "--allow-empty",
                "--allow-empty-message",
                "--no-commit",
                commit.orig_hash)
            if commit.do_commit:
                self._write_commit(commit)

    def _replay_merge_commit(self, commit):
        if commit.squashed:
            # if the merge is squashed, just commit
            self._write_commit(commit)
        elif commit.do_commit and self._can_fast_forward(commit):
            self.git("merge", "--ff", commit.orig_hash)
        else:
            # merge the other parents onto the first parent
            parents = self._commit_new_parents(commit.orig_hash)
            self.checkout_ref(parents[0])
            self.git("merge", "--no-commit", "--no-ff", *parents[1:])
            self._write_commit(commit)

    def _replay_commit(self, commit, top_level=False):
        if self.commit_is_merge(commit.orig_hash):
            # a merge commit
            if top_level:
                # commits within a merge is not listed in top level commit_chain
                # they have to be replyed explictly
                merge_commits = self.perpare_rewrites(self.log_merge(commit.orig_hash))
                for c in merge_commits:
                    if commit.squashed:
                        # do not commit if the merge is to be squashed
                        c.do_commit = False
                    else:
                        # replay each of the commits in the merge, commit the
                        # changes if the merge is commited
                        c.do_commit = commit.do_commit
                    self._replay_commit(c)

            self._replay_merge_commit(commit)
        else:
            # normal commit
            self._replay_single_commit(commit)

    def _commit_new_parents(self, commit_hash):
        """
        The parents of a commit could be rewritten earlier or being remapped if the
        commits have moved. This function return the new parents of a commit.
        """
        parents = [
            self._commit_parents_mapping[p] if p in self._commit_parents_mapping else p
            for p in self.commit_parents(commit_hash)
        ]

        rewritten = self.rebase_rewritten()
        # check if the parents have been rewriteen
        return [rewritten[p] if p in rewritten else p for p in parents]

    def rewrite_active_branch(self, base_commit, commit_chain):
        # `_commit_parents_mapping` is used to store new parents of the commits,
        # it is needed if the commits have moved.
        self._commit_parents_mapping = {}
        for idx, commit in enumerate(commit_chain):
            first_parent = self.commit_parents(commit.orig_hash)[0]
            if idx == 0:
                self._commit_parents_mapping.update({first_parent: base_commit})
            else:
                self._commit_parents_mapping.update({first_parent: commit_chain[idx - 1].orig_hash})

        branch_name = self.get_current_branch_name()

        # Detach HEAD to base commit.
        self.checkout_ref(base_commit)

        # Apply each commit to HEAD in order.
        try:
            for commit in commit_chain:
                self._replay_commit(commit, top_level=True)

            self.git("branch", "-f", branch_name, "HEAD")

        except Exception as e:
            raise e

        finally:
            # Whether on success or failure, always re-checkout the branch.  On success,
            # this will be the re-written branch.  On failure, this will be the original
            # branch (since re-defining the branch ref is the last step).

            self.git("reset", "--hard")
            self.git("checkout", branch_name)
            if os.path.exists(self._rebase_replay_dir):
                shutil.rmtree(self._rebase_replay_dir)

    @property
    def _rebase_replay_dir(self):
        """
        A directory to store meta data for `rewrite_active_branch`
        """
        return os.path.join(self.repo_path, ".git", "rebase-replay")

    @property
    def _rebase_apply_dir(self):
        return os.path.join(self.repo_path, ".git", "rebase-apply")

    @property
    def _rebase_merge_dir(self):
        return os.path.join(self.repo_path, ".git", "rebase-merge")

    @property
    def _rebase_dir(self):
        return self._rebase_merge_dir if self.in_rebase_merge() else self._rebase_apply_dir

    def in_rebase_merge(self):
        return os.path.isdir(self._rebase_merge_dir)

    def in_rebase_apply(self):
        return os.path.isdir(self._rebase_apply_dir)

    def in_rebase(self):
        return self.in_rebase_apply() or self.in_rebase_merge()

    def rebase_orig_head(self):
        path = os.path.join(self._rebase_dir, "orig-head")
        with open(path, "r") as f:
            return f.read().strip()

    def rebase_conflict_at(self):
        if self.in_rebase_merge():
            path = os.path.join(self._rebase_merge_dir, "current-commit")
        else:
            path = os.path.join(self._rebase_apply_dir, "original-commit")
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
        if self.in_rebase_merge():
            path = os.path.join(self._rebase_merge_dir, "rewritten")
            if not os.path.exists(path):
                return dict()
            entries = []
            for sha in os.listdir(path):
                with open(os.path.join(path, sha), "r") as f:
                    newsha = f.read().strip()
                    if newsha:
                        entries.append([sha, newsha])
            return dict(entries)
        elif self.in_rebase_apply():
            path = os.path.join(self._rebase_apply_dir, "rewritten")
            if not os.path.exists(path):
                return dict()
            with open(path, "r") as f:
                entries = f.read().strip().split("\n")
                return [entry.split(" ") for entry in entries]
        else:
            path = os.path.join(self._rebase_replay_dir, "rewritten")
            if not os.path.exists(path):
                return dict()
            entries = []
            for sha in os.listdir(path):
                with open(os.path.join(path, sha), "r") as f:
                    newsha = f.read().strip()
                    if newsha:
                        entries.append([sha, newsha])
            return dict(entries)

    def rewrite_meta_data(self, old_hash, new_hash):
        path = os.path.join(self._rebase_replay_dir, "rewritten")
        if not os.path.exists(path):
            os.makedirs(path)
        with open(os.path.join(path, old_hash), "w") as f:
            f.write(new_hash)
