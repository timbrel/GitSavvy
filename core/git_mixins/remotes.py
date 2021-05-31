import re
from collections import OrderedDict

MYPY = False
if MYPY:
    from typing import Dict
    from GitSavvy.core.git_command import (
        BranchesMixin,
        _GitCommand,
    )
    name = str
    url = str

    class mixin_base(
        BranchesMixin,
        _GitCommand,
    ):
        pass

else:
    mixin_base = object


class RemotesMixin(mixin_base):

    def get_remotes(self):
        # type: () -> Dict[name, url]
        """
        Get a list of remotes, provided as tuples of remote name and remote
        url/resource.
        """
        entries = self.git("remote", "-v").splitlines()
        return OrderedDict(entry.split()[:2] for entry in entries)

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

    def pull(self, remote=None, remote_branch=None, rebase=False):
        """
        Pull from the specified remote and branch if provided, otherwise
        perform default `git pull`.
        """
        return self.git(
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
        # Do not return the output. It is always empty since the output
        # of "git push" actually goes to stderr.
        self.git(
            "push",
            "--force" if force else None,
            "--force-with-lease" if force_with_lease else None,
            "--set-upstream" if set_upstream else None,
            remote,
            branch if not remote_branch else "{}:{}".format(branch, remote_branch)
        )

    def project_name_from_url(self, input_url):
        # URLs can come in one of following formats format
        # https://github.com/timbrel/GitSavvy.git
        #     git@github.com:divmain/GitSavvy.git
        # Kind of funky, but does the job
        _split_url = re.split('/|:', input_url)
        _split_url = re.split(r'\.', _split_url[-1])
        return _split_url[0] if len(_split_url) >= 1 else ''

    def username_from_url(self, input_url):
        # URLs can come in one of following formats format
        # https://github.com/timbrel/GitSavvy.git
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
