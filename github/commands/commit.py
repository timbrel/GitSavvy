"""
GitHub extensions to the new-commit view.
"""

import re

import sublime
from sublime_plugin import TextCommand

from ...core.git_command import GitCommand
from .. import github


class GsShowGithubIssuesCommand(TextCommand, GitCommand):

    """
    Display a panel of GitHub issues to either:

        1) the remote repo, if default_repo is True, or
        2) another repo on the same remote, if default_repo
           is False.

    After the user makes their selection, insert the issue
    number at the current cursor position.
    """

    def run(self, edit, default_repo=True):
        if not default_repo:
            first_cursor = self.view.sel()[0].begin()
            text_before_cursor = self.view.substr(sublime.Region(0, first_cursor))
            nondefault_repo = re.search(r"([a-zA-Z\-_0-9\.]+)/([a-zA-Z\-_0-9\.]+)$", text_before_cursor).groups()
        else:
            nondefault_repo = None

        sublime.set_timeout_async(lambda: self.run_async(nondefault_repo))

    def run_async(self, nondefault_repo):
        default_remote_name, default_remote = self.get_remotes().popitem(last=False)
        remote = github.parse_remote(default_remote)

        if nondefault_repo:
            owner, repo_name = nondefault_repo
            remote = github.GitHubRepo(
                url="",
                fqdn=remote.fqdn,
                owner=owner,
                repo=repo_name,
                token=remote.token
            )

        issues = github.get_issues(remote)

        if not issues:
            return

        self.menu_items = ["{} - {}".format(issue["number"], issue["title"]) for issue in issues]
        self.view.show_popup_menu(self.menu_items, self.on_done)

    def on_done(self, selection_id):
        if selection_id != -1:
            selection = self.menu_items[selection_id]
            number = selection.split(" ")[0]
            self.view.run_command("gs_insert_text_at_cursor", {"text": number})


class GsShowGithubContributorsCommand(TextCommand, GitCommand):

    """
    Query github for a list of people that have contributed to the GitHub project
    setup as a remote for the current Git project, and display that list the the
    user.  When a selection is made, insert that selection at the current cursor
    position.
    """

    def run(self, edit):
        sublime.set_timeout_async(lambda: self.run_async())

    def run_async(self):
        default_remote_name, default_remote = self.get_remotes().popitem(last=False)
        remote = github.parse_remote(default_remote)

        contributors = github.get_contributors(remote)

        if not contributors:
            return

        self.menu_items = [contributor["login"] for contributor in contributors]
        self.view.show_popup_menu(self.menu_items, self.on_done)

    def on_done(self, selection_id):
        if selection_id != -1:
            selection = self.menu_items[selection_id]
            self.view.run_command("gs_insert_text_at_cursor", {"text": selection})
