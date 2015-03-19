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
        self.git("fetch", "--prune" if prune else None, remote)

    def get_remote_branches(self):
        """
        Return a list of all known branches on remotes.
        """
        stdout = self.git("branch", "-r", "--no-color", "--no-column")
        return [branch.strip() for branch in stdout.split("\n") if branch]

    def pull(self, remote=None, branch=None):
        """
        Pull from the specified remote and branch if provided, otherwise
        perform default `git pull`.
        """
        self.git("pull", remote, branch)

    def push(self, remote=None, branch=None, force=False):
        """
        Push to the specified remote and branch if provided, otherwise
        perform default `git push`.
        """
        self.git("push", "--force" if force else None, remote, branch)
