from GitSavvy.core.git_mixins.branches import Upstream

from typing import Dict, Optional, TYPE_CHECKING
name = str
url = str

if TYPE_CHECKING:
    from GitSavvy.core.git_command import GitCommand
    base = GitCommand
else:
    base = object


NOTSET = "<NOTSET>"
UPSTREAM_NOT_SET = Upstream("", "", "", "")


class GithubRemotesMixin(base):
    def read_gitsavvy_config(self):
        # type: () -> Dict[str, str]
        return dict(
            line[9:].split()
            for line in self.git(
                "config",
                "--get-regex",
                r"^gitsavvy\..*",
                throw_on_error=False
            ).splitlines()
        )

    def get_integrated_branch_name(self):
        # type: () -> Optional[str]
        return self.read_gitsavvy_config().get("ghbranch")

    def get_integrated_remote_name(
        self,
        remotes,
        current_upstream=UPSTREAM_NOT_SET,
        configured_remote_name=NOTSET
    ):
        # type: (Dict[name, url], Optional[Upstream], Optional[str]) -> name
        if len(remotes) == 0:
            raise ValueError("GitHub integration will not function when no remotes defined.")

        if len(remotes) == 1:
            return list(remotes.keys())[0]

        if configured_remote_name is NOTSET:
            configured_remote_name = self.read_gitsavvy_config().get("ghremote")
        if configured_remote_name in remotes:
            return configured_remote_name

        for name in ("upstream", "origin"):
            if name in remotes:
                return name

        if current_upstream is UPSTREAM_NOT_SET:
            current_upstream = self.get_upstream_for_active_branch()
        if current_upstream:
            return current_upstream.remote

        raise ValueError("Cannot determine GitHub integrated remote.")

    def get_integrated_remote_url(self):
        # type: () -> url
        remotes = self.get_remotes()
        configured_remote_name = self.get_integrated_remote_name(remotes)
        return remotes[configured_remote_name]

    def guess_github_remote(self, remotes):
        # type: (Dict[name, url]) -> Optional[name]
        if len(remotes) == 1:
            return list(remotes.keys())[0]

        upstream = self.get_upstream_for_active_branch()
        integrated_remote = self.get_integrated_remote_name(remotes, current_upstream=upstream)
        if upstream:
            tracked_remote = upstream.remote
            if tracked_remote != integrated_remote:
                return None

        return integrated_remote
