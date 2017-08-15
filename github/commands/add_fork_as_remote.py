import sublime
from sublime_plugin import TextCommand
from itertools import chain

from ...core.git_command import GitCommand
from ...core.ui_mixins.quick_panel import show_paginated_panel
from .. import github
from .. import git_mixins


class GsAddForkAsRemoteCommand(TextCommand, GitCommand, git_mixins.GithubRemotesMixin):

    """
    Get list of repos on GitHub associated with the active repo.  Display, and when
    selected, add selection as git remote.
    """

    def run(self, edit):
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
            self.on_select,
            limit=savvy_settings.get("github_per_page_max", 100),
            format_item=self.format_item,
            status_message="Getting forks...")

    def format_item(self, fork):
        return (fork["full_name"], fork)

    def on_select(self, fork):
        if not fork:
            return

        url = fork["clone_url"]
        owner = fork["owner"]["login"]
        self.git("remote", "add", owner, url)
        sublime.status_message("Added remote '{}'.".format(owner))
