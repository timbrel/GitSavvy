"""
Easy access to GitHub issues.
"""

import sublime
from sublime_plugin import WindowCommand
from datetime import datetime
import webbrowser

from ...common import util
from ...core.git_command import GitCommand
from .. import github
from .. import git_mixins

NO_ISSUES_FOUND = "No issues were found."


class GsOpenGithubIssue(WindowCommand, GitCommand, git_mixins.GithubRemotesMixin):

    """
    Display a quick panel showing issues from Github.  Selection of an
    issue will open that issue in the web browser.
    """

    def run(self):
        sublime.set_timeout_async(lambda: self.run_async())

    # for now this is only the first set of pages issues
    def fetch_all_issues(self):
        remote = github.parse_remote(self.get_integrated_remote_url())
        return github.get_issues(remote)

    def run_async(self):
        issues = self.fetch_all_issues()

        if not issues:
            sublime.message_dialog(NO_ISSUES_FOUND)
            return

        entries = []
        for issue in issues:
            title = issue["title"]
            time = datetime.strptime(issue["created_at"], '%Y-%m-%dT%H:%M:%SZ')
            details = "#{} opened {} by {}".format(
                issue["number"],
                util.dates.fuzzy(time),
                issue["user"]["login"]
            )
            entries.append([title, details])
        self._urls = [issue["html_url"] for issue in issues]
        self.window.show_quick_panel(
            entries,
            self.on_selection,
            flags=sublime.MONOSPACE_FONT
        )

    def on_selection(self, selection_id):
        if selection_id != -1:
            url = self._urls[selection_id]
            webbrowser.open(url)

