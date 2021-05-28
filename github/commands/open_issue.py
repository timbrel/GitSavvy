from webbrowser import open as open_in_browser

from sublime_plugin import TextCommand

from GitSavvy.core.utils import flash
from GitSavvy.core.git_command import GitCommand
from .. import github, git_mixins


__all__ = (
    "gs_github_open_issue_at_cursor",
)


ISSUE_SCOPES = "constant.other.issue-ref.git-savvy, string.other.issue.git-savvy"


class gs_github_open_issue_at_cursor(TextCommand, git_mixins.GithubRemotesMixin, GitCommand):
    def run(self, edit):
        view = self.view
        cursor = view.sel()[0].begin()
        if not view.match_selector(cursor, ISSUE_SCOPES):
            flash(view, "Not on an issue or pr name.")
            return

        issue_nr = view.substr(view.extract_scope(cursor))[1:]

        remotes = self.get_remotes()
        base_remote_name = self.get_integrated_remote_name(remotes)
        base_remote_url = remotes[base_remote_name]
        base_remote = github.parse_remote(base_remote_url)
        url = "{}/issues/{}".format(base_remote.url, issue_nr)
        open_in_browser(url)
