
MYPY = False
if MYPY:
    from typing import Dict, Optional
    from GitSavvy.core.git_command import GitCommand
    name = str
    url = str

    base = GitCommand
else:
    base = object


class GithubRemotesMixin(base):
    def get_integrated_branch_name(self):
        # type: () -> str
        configured_branch_name = self.git(
            "config",
            "--local",
            "--get",
            "GitSavvy.ghBranch",
            throw_on_stderr=False
        ).strip()
        return configured_branch_name or "master"

    def get_integrated_remote_name(self, remotes):
        # type: (Dict[name, url]) -> name
        if len(remotes) == 0:
            raise ValueError("GitHub integration will not function when no remotes defined.")

        if len(remotes) == 1:
            return list(remotes.keys())[0]

        configured_remote_name = self.git(
            "config",
            "--local",
            "--get",
            "GitSavvy.ghRemote",
            throw_on_stderr=False
        ).strip()
        if configured_remote_name in remotes:
            return configured_remote_name

        for name in ("upstream", "origin"):
            if name in remotes:
                return name

        current_upstream = self.get_upstream_for_active_branch()
        if current_upstream:
            return current_upstream.split("/")[0]

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

        integrated_remote = self.get_integrated_remote_name(remotes)
        upstream = self.get_upstream_for_active_branch()
        if upstream:
            tracked_remote = upstream.split("/")[0]
            if tracked_remote != integrated_remote:
                return None

        return integrated_remote
