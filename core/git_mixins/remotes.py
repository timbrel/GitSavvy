import re
from collections import OrderedDict


class RemotesMixin():

    def get_remotes(self):
        """
        Get a list of remotes, provided as tuples of remote name and remote
        url/resource.
        """
        entries = self.git("remote", "-v").splitlines()
        return OrderedDict(re.match("([0-9a-zA-Z_-]+)\t([^ ]+)", entry).groups() for entry in entries)

    def fetch(self, remote=None, prune=True):
        """
        If provided, fetch all changes from `remote`.  Otherwise, fetch
        changes from all remotes.
        """
        self.git("fetch", "--prune" if prune else None, remote if remote else "--all")

    def list_remote_branches(self, remote=None):
        """
        Return a list of all known branches on all remotes, or a specified remote.
        """
        stdout = self.git("branch", "-r", "--no-color")
        branches = [branch.strip() for branch in stdout.split("\n") if branch]

        if remote:
            branches = [branch for branch in branches if branch.startswith(remote+"/")]

        # Clean up "origin/HEAD -> origin/master" to "origin/master" if present.
        for idx, branch_name in enumerate(branches):
            if "origin/HEAD -> " in branch_name:
                branches[idx] = branch_name[15:]

        # Remove any duplicate branch names.
        return [branch for idx, branch in enumerate(branches) if branches.index(branch) == idx]

    def pull(self, remote=None, branch=None):
        """
        Pull from the specified remote and branch if provided, otherwise
        perform default `git pull`.
        """
        self.git("pull", remote, branch)

    def push(self, remote=None, branch=None, force=False, local_branch=None, set_upstream=False):
        """
        Push to the specified remote and branch if provided, otherwise
        perform default `git push`.
        """
        return self.git(
            "push",
            "--force" if force else None,
            "--set-upstream" if set_upstream else None,
            remote,
            branch if not local_branch else "{}:{}".format(local_branch, branch)
            )

    def get_upstream_for_active_branch(self):
        """
        Return ref for remote tracking branch.
        """
        return self.git("rev-parse", "--abbrev-ref", "--symbolic-full-name",
                        "@{u}", throw_on_stderr=False).strip()

    def get_active_remote_branch(self):
        """
        Return named tuple of the upstream for active branch.
        """
        upstream = self.get_upstream_for_active_branch()
        for branch in self.get_branches():
            if branch.name_with_remote == upstream:
                return branch
        return None
