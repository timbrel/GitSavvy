import sublime

from ...common import util
from .. import github, git_mixins
from GitSavvy.core.base_commands import GsWindowCommand
from GitSavvy.core.runtime import on_worker


START_CREATE_MESSAGE = "Forking {repo} ..."
END_CREATE_MESSAGE = "Fork created successfully."


__all__ = ['gs_github_create_fork']


class gs_github_create_fork(GsWindowCommand, git_mixins.GithubRemotesMixin):

    @on_worker
    def run(self, default_branch_only=None):
        remotes = self.get_remotes()
        base_remote_name = self.get_integrated_remote_name(remotes)
        base_remote_url = remotes[base_remote_name]
        base_remote = github.parse_remote(base_remote_url)

        if default_branch_only is None:
            default_branch_only = self.savvy_settings.get("sparse_fork", True)
        self.window.status_message(START_CREATE_MESSAGE.format(repo=base_remote.url))
        result = github.create_fork(base_remote, default_branch_only=default_branch_only)
        util.debug.add_to_log({"github: fork result": result})

        url = (
            result["ssh_url"]
            if base_remote_url.startswith("git@")
            else result["clone_url"]
        )
        for remote_name, remote_url in remotes.items():
            if remote_url == url:
                sublime.ok_cancel_dialog(
                    "You forked previously!  "
                    "The fork is available under the name '{}'."
                    .format(remote_name)
                )
                break
        else:
            self.window.status_message(END_CREATE_MESSAGE)
            self.window.run_command("gs_remote_add", {
                "url": url,
                "set_as_push_default": True,
                "ignore_tags": True
            })
