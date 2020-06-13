class GithubRemotesMixin():

    def get_integrated_branch_name(self):
        configured_branch_name = self.git(
            "config",
            "--local",
            "--get",
            "GitSavvy.ghBranch",
            throw_on_stderr=False
        ).strip()
        if configured_branch_name:
            return configured_branch_name
        else:
            return "master"

    def get_integrated_remote_name(self, remotes=None):
        if remotes is None:
            remotes = self.get_remotes()
        configured_remote_name = self.git(
            "config",
            "--local",
            "--get",
            "GitSavvy.ghRemote",
            throw_on_stderr=False
        ).strip()

        if len(remotes) == 0:
            raise ValueError("GitHub integration will not function when no remotes defined.")

        if configured_remote_name and configured_remote_name in remotes:
            return configured_remote_name
        elif len(remotes) == 1:
            return list(remotes.keys())[0]
        elif "origin" in remotes:
            return "origin"
        elif self.get_upstream_for_active_branch():
            # fall back to the current active remote
            return self.get_upstream_for_active_branch().split("/")[0]
        else:
            raise ValueError("Cannot determine GitHub integrated remote.")

    def get_integrated_remote_url(self):
        configured_remote_name = self.get_integrated_remote_name()
        remotes = self.get_remotes()
        return remotes[configured_remote_name]

    def guess_github_remote(self):
        upstream = self.get_upstream_for_active_branch()
        integrated_remote = self.get_integrated_remote_name()
        remotes = self.get_remotes()

        if len(self.remotes) == 1:
            return list(remotes.keys())[0]
        elif upstream:
            tracked_remote = upstream.split("/")[0] if upstream else None

            if tracked_remote and tracked_remote == integrated_remote:
                return tracked_remote
            else:
                return None
        else:
            return integrated_remote
