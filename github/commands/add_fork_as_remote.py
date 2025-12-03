from itertools import chain

from ...core.ui_mixins.quick_panel import show_paginated_panel
from .. import github
from .. import git_mixins
from GitSavvy.core.base_commands import GsWindowCommand
from GitSavvy.core.runtime import on_worker


__all__ = (
    "gs_github_add_fork_as_remote",
)


class gs_github_add_fork_as_remote(git_mixins.GithubRemotesMixin, GsWindowCommand):

    """
    Get list of repos on GitHub associated with the active repo.  Display, and when
    selected, add selection as git remote.
    """

    @on_worker
    def run(self):
        base_remote = github.parse_remote(self.get_integrated_remote_url())
        base_repo_data = github.get_repo_data(base_remote)
        parent = None

        forks = []
        if "parent" in base_repo_data:
            parent = base_repo_data["parent"]
            forks.append(parent)

        if "source" in base_repo_data:
            source = base_repo_data["source"]
            if parent and parent["clone_url"] != source["clone_url"]:
                forks.append(source)

        forks_ = chain(forks, github.get_forks(base_remote))
        show_paginated_panel(
            forks_,
            self.on_select_fork,
            limit=self.savvy_settings.get("github_per_page_max", 100),
            format_item=lambda fork: (fork["full_name"], fork),
            status_message="Getting forks...")

    def on_select_fork(self, fork):
        if not fork:
            return
        self.fork = fork
        self.window.show_quick_panel([fork["clone_url"], fork["ssh_url"]], self.on_select_url)

    def on_select_url(self, index):
        if index < 0:
            return
        elif index == 0:
            url = self.fork["clone_url"]
        elif index == 1:
            url = self.fork["ssh_url"]

        self.window.run_command("gs_remote_add", {"url": url, "ignore_tags": True})
