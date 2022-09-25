from webbrowser import open as open_in_browser

import sublime
from sublime_plugin import EventListener, TextCommand

from GitSavvy.core.utils import flash
from GitSavvy.core.git_command import GitCommand
from .. import github, git_mixins


__all__ = (
    "gs_github_open_issue_at_cursor",
    "gs_github_hover_on_issues_controller",
)


ISSUE_SCOPES = "meta.git-savvy.issue-reference"


class gs_github_open_issue_at_cursor(TextCommand, git_mixins.GithubRemotesMixin, GitCommand):
    def run(self, edit, point=None, open_popup=False):
        view = self.view
        if point is None:
            point = view.sel()[0].begin()

        if not view.match_selector(point, ISSUE_SCOPES):
            flash(view, "Not on an issue or pr name.")
            return

        def on_navigate(href: str):
            open_in_browser(href)

        complete_str = view.substr(view.extract_scope(point))
        if complete_str[0] != "#":
            flash(view, "Only implemented for simple references.  (E.g. '#23')")
            return

        issue_nr = complete_str[1:]
        url = self.url_for_issue(issue_nr)

        if open_popup:
            view.show_popup(
                '<a href="{url}">{url}</a>'.format(url=url),
                flags=sublime.HIDE_ON_MOUSE_MOVE_AWAY,
                location=point,
                max_width=1000,
                on_navigate=on_navigate
            )
        else:
            open_in_browser(url)

    def url_for_issue(self, issue_nr: str) -> str:
        remotes = self.get_remotes()
        base_remote_name = self.get_integrated_remote_name(remotes)
        base_remote_url = remotes[base_remote_name]
        base_remote = github.parse_remote(base_remote_url)
        return "{}/issues/{}".format(base_remote.url, issue_nr)


class gs_github_hover_on_issues_controller(EventListener):
    def on_hover(self, view, point, hover_zone):
        # type: (sublime.View, int, int) -> None
        if (
            hover_zone == sublime.HOVER_TEXT
            and view.match_selector(point, ISSUE_SCOPES)
        ):
            view.run_command("gs_github_open_issue_at_cursor", {
                "point": point,
                "open_popup": True
            })
