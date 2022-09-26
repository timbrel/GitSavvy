"""
GitHub extensions to the new-commit view.
"""

import re

import sublime

from ...core.ui_mixins.quick_panel import show_paginated_panel
from .. import github
from .. import git_mixins
from ...common import util
from GitSavvy.core.base_commands import GsTextCommand
from GitSavvy.core.runtime import enqueue_on_worker, on_worker


__all__ = (
    "gs_github_show_issues",
    "gs_github_show_contributors",
)


class gs_github_show_issues(git_mixins.GithubRemotesMixin, GsTextCommand):

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
            match = re.search(
                r"([a-zA-Z\-_0-9\.]+)/([a-zA-Z\-_0-9\.]+)#$", text_before_cursor)
            if not match:
                raise RuntimeError(
                    "could not extract remote repository path. "
                    "regex inconsistent with key binding context constraint. "
                )
            nondefault_repo = match.groups()
        else:
            nondefault_repo = None

        enqueue_on_worker(self.run_async, nondefault_repo)

    def run_async(self, nondefault_repo):
        remote = github.parse_remote(self.get_integrated_remote_url())

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
        pp = show_paginated_panel(
            issues,
            self.on_done,
            format_item=self.format_item,
            limit=self.savvy_settings.get("github_per_page_max", 100),
            status_message="Getting issues..."
        )
        if pp.is_empty():
            self.window.status_message("No issues found.")

    def format_item(self, issue):
        return (
            [
                "{number}: {title}".format(number=issue["number"], title=issue["title"]),
                "{issue_type} created by {user}, {time_stamp}.".format(
                    issue_type="Pull request" if "pull_request" in issue else "Issue",
                    user=issue["user"]["login"],
                    time_stamp=util.dates.fuzzy(issue["created_at"],
                                                date_format="%Y-%m-%dT%H:%M:%SZ")
                )
            ],
            issue
        )

    def on_done(self, issue):
        if not issue:
            return

        self.view.run_command("insert", {"characters": str(issue["number"])})


class gs_github_show_contributors(git_mixins.GithubRemotesMixin, GsTextCommand):

    """
    Query github for a list of people that have contributed to the GitHub project
    setup as a remote for the current Git project, and display that list the the
    user.  When a selection is made, insert that selection at the current cursor
    position.
    """

    @on_worker
    def run(self, edit):
        remote = github.parse_remote(self.get_integrated_remote_url())
        contributors = github.get_contributors(remote)

        pp = show_paginated_panel(
            contributors,
            self.on_done,
            format_item=self.format_item,
            limit=self.savvy_settings.get("github_per_page_max", 100),
            status_message="Getting contributors..."
        )
        if pp.is_empty():
            self.window.status_message("No contributors found.")

    def format_item(self, contributor):
        return (contributor["login"], contributor)

    def on_done(self, contributor):
        if not contributor:
            return

        self.view.run_command("insert", {"characters": contributor["login"]})
