import re

from GitSavvy.core.git_command import BranchesMixin, _GitCommand
from GitSavvy.core.fns import filter_
from GitSavvy.core.utils import cache_in_store_as, yes_no_switch


from typing import Dict
name = str
url = str


class RemotesMixin(BranchesMixin, _GitCommand):

    @cache_in_store_as("remotes")
    def get_remotes(self):
        # type: () -> Dict[name, url]
        """
        Get a list of remotes, provided as tuples of remote name and remote
        url/resource.
        """
        return {
            key[7:-4]: url
            for key, url in (
                entry.split(maxsplit=1)
                for entry in self.git(
                    "config",
                    "--get-regexp",
                    r"^remote\..*\.url",
                    throw_on_error=False).strip().splitlines()
            )
        }

    def fetch(self, remote=None, refspec=None, prune=True, local_branch=None, remote_branch=None):
        # type: (str, str, bool, str, str) -> None
        """
        If provided, fetch all changes from `remote`.  Otherwise, fetch
        changes from all remotes.
        """
        if remote is None:
            if refspec is not None:
                raise TypeError("do not set `refspec` when `remote` is `None`")
            if local_branch is not None:
                raise TypeError("do not set `local_branch` when `remote` is `None`")
            if remote_branch is not None:
                raise TypeError("do not set `remote_branch` when `remote` is `None`")
        if refspec is not None:
            if local_branch is not None:
                raise TypeError("do not set `local_branch` when `refspec` is set")
            if remote_branch is not None:
                raise TypeError("do not set `remote_branch` when `refspec` is set")

        if refspec is None:
            refspec = ":".join(filter_((remote_branch, local_branch)))

        self.git(
            "fetch",
            "--prune" if prune else None,
            remote if remote else "--all",
            refspec or None,
        )

    def pull(self, remote=None, remote_branch=None, rebase=None):
        """
        Pull from the specified remote and branch if provided, otherwise
        perform default `git pull`.
        """
        return self.git(
            "pull",
            yes_no_switch("--rebase", rebase),
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
