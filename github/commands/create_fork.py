from sublime_plugin import WindowCommand

from ...common import util
from ...core.git_command import GitCommand
from .. import github, git_mixins
from GitSavvy.core.runtime import enqueue_on_worker


START_CREATE_MESSAGE = "Forking {repo} ..."
END_CREATE_MESSAGE = "Fork created successfully."


__all__ = ['gs_github_create_fork']


class gs_github_create_fork(
    WindowCommand,
    git_mixins.GithubRemotesMixin,
    GitCommand,
):

    def run(self):
        enqueue_on_worker(self.run_async)

    def run_async(self):
        remotes = self.get_remotes()
        base_remote_name = self.get_integrated_remote_name(remotes)
        base_remote_url = remotes[base_remote_name]
        base_remote = github.parse_remote(base_remote_url)

        self.window.status_message(START_CREATE_MESSAGE.format(repo=base_remote.url))
        result = github.create_fork(base_remote)
        self.window.status_message(END_CREATE_MESSAGE)
        util.debug.add_to_log({"github: fork result": result})

        url = (
            result["ssh_url"]
            if base_remote_url.startswith("git@")
            else result["clone_url"]
        )
        self.window.run_command("gs_remote_add", {
            "url": url,
            "set_as_push_default": True
        })
