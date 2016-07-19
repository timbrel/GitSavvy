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

    def get_integrated_remote_name(self):
        return self.git(
            "config",
            "--local",
            "--get",
            "GitSavvy.ghRemote",
            throw_on_stderr=False
            ).strip()

    def get_integrated_remote_url(self):
        remotes = self.get_remotes()
        if not remotes:
            raise ValueError("GitHub integration will not function when no remotes defined.")

        configured_remote_name = self.get_integrated_remote_name()
        return (remotes[configured_remote_name]
                if configured_remote_name in remotes
                else remotes["origin"])
