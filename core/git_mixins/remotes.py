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

    def fetch(self, remote=None, prune=True, branch=None, remote_branch=None):
        """
        If provided, fetch all changes from `remote`.  Otherwise, fetch
        changes from all remotes.
        """
        self.git(
            "fetch",
            "--prune" if prune else None,
            remote if remote else "--all",
            branch if not remote_branch else "{}:{}".format(remote_branch, branch)
        )

    def list_remote_branches(self, remote=None):
        """
        Return a list of all known branches on all remotes, or a specified remote.
        """
        stdout = self.git("branch", "-r", "--no-color")
        branches = [branch.strip() for branch in stdout.split("\n") if branch]

        if remote:
            branches = [branch for branch in branches if branch.startswith(remote + "/")]

        # Clean up "origin/HEAD -> origin/master" to "origin/master" if present.
        for idx, branch_name in enumerate(branches):
            if "origin/HEAD -> " in branch_name:
                branches[idx] = branch_name[15:]

        # Remove any duplicate branch names.
        return [branch for idx, branch in enumerate(branches) if branches.index(branch) == idx]

    def pull(self, remote=None, remote_branch=None, rebase=False):
        """
        Pull from the specified remote and branch if provided, otherwise
        perform default `git pull`.
        """
        self.git(
            "pull",
            "--rebase" if rebase else None,
            remote if remote else None,
            remote_branch if remote and remote_branch else None
        )

    def push(
            self,
            remote=None,
            branch=None,
            force=False,
            force_with_lease=False,
            remote_branch=None,
            set_upstream=False):
        """
        Push to the specified remote and branch if provided, otherwise
        perform default `git push`.
        """
        return self.git(
            "push",
            "--force" if force else None,
            "--force-with-lease" if force_with_lease else None,
            "--set-upstream" if set_upstream else None,
            remote,
            branch if not remote_branch else "{}:{}".format(branch, remote_branch)
        )

    def project_name_from_url(self, input_url):
        # URLs can come in one of following formats format
        # https://github.com/divmain/GitSavvy.git
        #     git@github.com:divmain/GitSavvy.git
        # Kind of funky, but does the job
        _split_url = re.split('/|:', input_url)
        _split_url = re.split(r'\.', _split_url[-1])
        return _split_url[0] if len(_split_url) >= 1 else ''

    def username_from_url(self, input_url):
        # URLs can come in one of following formats format
        # https://github.com/divmain/GitSavvy.git
        #     git@github.com:divmain/GitSavvy.git
        # Kind of funky, but does the job
        _split_url = re.split('/|:', input_url)
        return _split_url[-2] if len(_split_url) >= 2 else ''

    def remotes_containing_commit(self, commit_hash):
        """
        Return a list of remotes which contain a particular commit.
        """
        return list(set([
            branch.split("/")[0]
            for branch in self.branches_containing_commit(commit_hash, remote_only=True)
        ]))
