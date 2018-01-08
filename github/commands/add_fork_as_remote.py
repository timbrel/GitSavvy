import sublime
from sublime_plugin import WindowCommand
from itertools import chain

from ...core.git_command import GitCommand
from ...core.ui_mixins.quick_panel import show_paginated_panel
from .. import github
from .. import git_mixins


class GsAddForkAsRemoteCommand(WindowCommand, GitCommand, git_mixins.GithubRemotesMixin):

    """
    Get list of repos on GitHub associated with the active repo.  Display, and when
    selected, add selection as git remote.
    """

    def run(self):
        sublime.set_timeout_async(self.run_async, 0)

    def run_async(self):
        savvy_settings = sublime.load_settings("GitSavvy.sublime-settings")
        base_remote = github.parse_remote(self.get_integrated_remote_url())
        base_repo_data = github.get_repo_data(base_remote)
        parent = None

        forks = []
        if "parent" in base_repo_data:
            parent = base_repo_data["parent"]
            forks.append(parent)

        if "source" in base_repo_data:
            source = base_repo_data["source"]
            if not parent["clone_url"] == source["clone_url"]:
                forks.append(source)

        forks = chain(forks, github.get_forks(base_remote))

        show_paginated_panel(
            forks,
            self.on_select_fork,
            limit=savvy_settings.get("github_per_page_max", 100),
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

        self.window.run_command("gs_remote_add", {"url": url})
