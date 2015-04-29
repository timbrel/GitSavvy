import sublime
from sublime_plugin import TextCommand

from ...core.git_command import GitCommand
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
        base_remote = github.parse_remote(self.get_integrated_remote_url())
        forks = github.get_forks(base_remote)
        base_repo_data = github.get_repo_data(base_remote)
        parent = None

        self.gh_remotes = []

        if "parent" in base_repo_data:
            parent = (base_repo_data["parent"]["full_name"],
                      base_repo_data["parent"]["clone_url"],
                      base_repo_data["parent"]["owner"]["login"])
            self.gh_remotes.append(parent)

        if "source" in base_repo_data:
            source = (base_repo_data["source"]["full_name"],
                      base_repo_data["source"]["clone_url"],
                      base_repo_data["source"]["owner"]["login"])
            if not parent == source:
                self.gh_remotes.append([source, "Source"])

        self.gh_remotes += [
            (fork["full_name"],
             fork["clone_url"],
             fork["owner"]["login"])
            for fork in forks
        ]

        self.view.window().show_quick_panel([remote[0] for remote in self.gh_remotes], self.on_select)

    def on_select(self, idx):
        if idx == -1:
            return
        full_name, url, owner = self.gh_remotes[idx]
        self.git("remote", "add", owner, url)
        sublime.status_message("Added remote '{}'.".format(owner))
