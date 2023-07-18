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

        issue_str = view.substr(view.extract_scope(point))
        url = self.url_for_issue(issue_str)

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

    def url_for_issue(self, issue_str: str) -> str:
        remotes = self.get_remotes()
        base_remote_name = self.get_integrated_remote_name(remotes)
        base_remote_url = remotes[base_remote_name]
        base_remote = github.parse_remote(base_remote_url)

        prefix, issue_nr = issue_str.split('#')
        url = f"{base_remote.url}/issues/{issue_nr}"
        if prefix:
            return url.replace(
                f"/{base_remote.owner}/{base_remote.repo}/",
                f"/{prefix}/"
            )
        return url


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
