import os
import shutil
from types import SimpleNamespace
import re
from ..exceptions import GitSavvyError

fixup_command = re.compile("^fixup! (.*)")


class RewriteTemplate(SimpleNamespace):
    # orig_hash
    do_commit = True
    msg = None
    datetime = None
    author = None


class RewriteMixin():

    def log_rebase(self, start, end="HEAD", preserve=False):
        return self.log(
            start_end=(start, end),
            reverse=True,
            first_parent=preserve,
            no_merges=not preserve,
            topo_order=True)

    def perpare_rewrites(self, entries, autosquash=False):
        commit_chain = [
            RewriteTemplate(orig_hash=entry.long_hash,
                            do_commit=True,
                            msg=entry.raw_body,
                            datetime=entry.datetime,
                            author="{} <{}>".format(entry.author, entry.email))
            for entry in entries
        ]
        if autosquash:
            self._auto_squash(commit_chain)

        return commit_chain

    def _auto_squash(self, commit_chain):
        fixup_idx = len(commit_chain) - 1
        while fixup_idx > 0:
            msg = commit_chain[fixup_idx].msg
            m = fixup_command.match(msg)
            if m:
                orig_msg = m.group(1)
                orig_commit_indx = fixup_idx - 1
                while orig_commit_indx >= 0:
                    if commit_chain[orig_commit_indx].msg.startswith(orig_msg):
                        break
                    orig_commit_indx = orig_commit_indx - 1
                if orig_commit_indx >= 0:
                    commit_chain.insert(orig_commit_indx+1, commit_chain.pop(fixup_idx))
                    commit_chain[orig_commit_indx].do_commit = False
                    if fixup_idx - orig_commit_indx >= 2:
                        # if the fixup commit moves, do not decrease fixup_idx
                        continue
            fixup_idx = fixup_idx - 1
        orig_commit_indx = 0
        while orig_commit_indx < len(commit_chain) - 1:
            if not commit_chain[orig_commit_indx].do_commit:
                fixup_idx = orig_commit_indx + 1
                while fixup_idx <= len(commit_chain) - 1:
                    if commit_chain[fixup_idx].do_commit:
                        break
                commit_chain[fixup_idx].msg = commit_chain[orig_commit_indx].msg
                commit_chain[fixup_idx].datetime = commit_chain[orig_commit_indx].datetime
                commit_chain[fixup_idx].author = commit_chain[orig_commit_indx].author
                orig_commit_indx = fixup_idx
            orig_commit_indx = orig_commit_indx + 1

    def _replay_commit(self, commit):
        if self.commit_is_merge(commit.orig_hash):
            # a merge commit
            parents = self.commit_parents(commit.orig_hash)
            merge_commits = self.perpare_rewrites(self.log_merge(commit.orig_hash))
            # replay each of the commits in the merge, commit the changes if the merge is commited
            for c in merge_commits:
                c.do_commit = commit.do_commit
                self._replay_commit(c)

            if commit.do_commit:
                rewritten = self.rebase_rewritten()
                new_parents = []
                # check if the parents have been rewriteen
                for p in parents:
                    if p in rewritten:
                        new_parents.append(rewritten[p])
                    else:
                        new_parents.append(p)
                # merge the other parents onto the main parent
                self.checkout_ref(new_parents[0])
                self.git(
                    "merge",
                    "--no-commit",
                    "--no-ff",
                    *new_parents[1:])
        else:
            # normal commit
            self.git(
                "cherry-pick",
                "--allow-empty",
                "--allow-empty-message",
                "--no-commit",
                commit.orig_hash)

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
                stdin=commit.msg)

            # the original hash is rewritten, the information is stored under the directory
            # self._rebase_replay_dir
            self.rewrite_meta_data(commit.orig_hash, self.git("rev-parse", "HEAD").strip())

    def rewrite_active_branch(self, base_commit, commit_chain):
        branch_name = self.get_current_branch_name()

        # Detach HEAD to base commit.
        self.checkout_ref(base_commit)

        # Apply each commit to HEAD in order.
        try:
            for commit in commit_chain:
                self._replay_commit(commit)

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
            entries = []
            for sha in os.listdir(path):
                with open(os.path.join(path, sha), "r") as f:
                    newsha = f.read().strip()
                    if newsha:
                        entries.append([sha, newsha])
            return dict(entries)
        elif self.in_rebase_apply():
            path = os.path.join(self._rebase_apply_dir, "rewritten")
            try:
                with open(path, "r") as f:
                    entries = f.read().strip().split("\n")
                    return [entry.split(" ") for entry in entries]
            except FileNotFoundError:
                return dict()
        else:
            path = os.path.join(self._rebase_replay_dir, "rewritten")
            entries = []
            if os.path.exists(path):
                for sha in os.listdir(path):
                    with open(os.path.join(path, sha), "r") as f:
                        newsha = f.read().strip()
                        if newsha:
                            entries.append([sha, newsha])
                return dict(entries)
            else:
                return dict()

    def rewrite_meta_data(self, old_hash, new_hash):
        path = os.path.join(self._rebase_replay_dir, "rewritten")
        if not os.path.exists(path):
            os.makedirs(path)
        with open(os.path.join(path, old_hash), "w") as f:
            f.write(new_hash)
