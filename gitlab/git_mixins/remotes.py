class GitLabRemotesMixin():

    def get_integrated_branch_name(self):
        configured_branch_name = self.git(
            "config",
            "--local",
            "--get",
            "GitSavvy.glBranch",
            throw_on_error=False
        ).strip()
        if configured_branch_name:
            return configured_branch_name
        else:
            return "master"

    def get_integrated_remote_name(self):
        configured_remote_name = self.git(
            "config",
            "--local",
            "--get",
            "GitSavvy.glRemote",
            throw_on_error=False
        ).strip()
        remotes = self.get_remotes()

        if len(remotes) == 0:
            raise ValueError("GitLab integration will not function when no remotes defined.")

        if configured_remote_name and configured_remote_name in remotes:
            return configured_remote_name
        if len(remotes) == 1:
            return list(remotes.keys())[0]
        if "origin" in remotes:
            return "origin"
        upstream = self.get_upstream_for_active_branch_()
        if upstream:
            return upstream.remote
        raise ValueError("Cannot determine GitLab integrated remote.")

    def get_integrated_remote_url(self):
        configured_remote_name = self.get_integrated_remote_name()
        remotes = self.get_remotes()
        return remotes[configured_remote_name]

    def guess_gitlab_remote(self):

        remotes = self.get_remotes()
        if len(remotes) == 1:
            return list(remotes.keys())[0]
        integrated_remote = self.get_integrated_remote_name()
        upstream = self.get_upstream_for_active_branch_()
        if upstream:
            tracked_remote = upstream.remote
            if tracked_remote == integrated_remote:
                return tracked_remote
            else:
                return None
        else:
            return integrated_remote
