class GithubRemotesMixin():

    def get_integrated_remote_name(self):
        configured_remote_name = self.git(
            "config",
            "--local",
            "--get",
            "GitSavvy.ghRemote",
            throw_on_stderr=False
            ).strip()
        remotes = self.get_remotes()

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
