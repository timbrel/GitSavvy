"""
GitHub extensions to the new-commit view.
"""

import re

import sublime
from sublime_plugin import TextCommand

from ...core.git_command import GitCommand
from .. import github
from .. import git_mixins


class GsShowGithubIssuesCommand(TextCommand, GitCommand, git_mixins.GithubRemotesMixin):

    """
    Display a panel of GitHub issues to either:

        1) the remote repo, if default_repo is True, or
        2) another repo on the same remote, if default_repo
           is False.

    After the user makes their selection, insert the issue
    number at the current cursor position.
    """

    def run(self, edit):
        sublime.set_timeout_async(self.run_async)

    def run_async(self):
        remote = github.parse_remote(self.get_integrated_remote_url())

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
