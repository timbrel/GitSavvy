"""
Easy access to GitHub issues.
"""

import sublime
from sublime_plugin import WindowCommand
from datetime import datetime
import webbrowser
import re

from ...common import util
from ...core.git_command import GitCommand
from .. import github
from .. import git_mixins

NO_ISSUES_FOUND = "No issues were found."
LAST_PAGE_REGEX = 'page=(\d+)>; rel="last'
PAGING_SUMMARY = "This is page {} of {}."
NEXT_PAGE = ">>> NEXT {} ISSUES >>>"
FIRST_PAGE = "<<< FIRST PAGE OF ISSUES <<<"

class GsOpenGithubIssue(WindowCommand, GitCommand, git_mixins.GithubRemotesMixin):

    """
    Display a quick panel showing issues from Github.  Selection of an
    issue will open that issue in the web browser.
    """

    def run(self, page=1):
        self._page = page
        sublime.set_timeout_async(lambda: self.run_async())

    # for now this is only the first set of pages issues
    def fetch_paged_issues(self, page):
        remote = github.parse_remote(self.get_integrated_remote_url())
        issues = github.paged_get_issues(remote, page=page)

        # rip the last page out of the HTTP response headers
        m = re.search(LAST_PAGE_REGEX, issues.headers["Link"])
        if m and m.group(1):
            self._last_page = int(m.group(1))
        return issues.payload

    def run_async(self):
        issues = self.fetch_paged_issues(page=self._page)

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

        if self._page < self._last_page:
            entries.append([
                NEXT_PAGE.format(len(issues)),
                PAGING_SUMMARY.format(self._page, self._last_page)
                ])
        elif self._page == self._last_page:
            entries.append([
                FIRST_PAGE.format(len(issues)),
                PAGING_SUMMARY.format(self._page, self._last_page)
                ])

        self.window.show_quick_panel(
            entries,
            self.on_selection,
            flags=sublime.MONOSPACE_FONT
        )

    def on_selection(self, index):
        # no selection
        if index == -1:
            return

        # request for next/first page
        change_page_index = len(self._urls)
        if index == change_page_index:
            if self._page < self._last_page:
                self._page += 1
            else:  # if we're already on the last page, return to first
                self._page = 1
            sublime.set_timeout_async(lambda: self.run_async(), 0)
            return

        url = self._urls[index]
        webbrowser.open(url)
