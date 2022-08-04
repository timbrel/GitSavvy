import os
from webbrowser import open as open_in_browser

import sublime
from sublime_plugin import WindowCommand, TextCommand

from . import diff
from . import intra_line_colorizer
from ..git_command import GitCommand
from ..utils import flash, focus_view
from ..runtime import enqueue_on_worker
from ..view import replace_view_content, Position
from GitSavvy.github import github
from GitSavvy.github.git_mixins import GithubRemotesMixin
from GitSavvy.common import interwebs
SUBLIME_SUPPORTS_REGION_ANNOTATIONS = int(sublime.version()) >= 4050


__all__ = (
    "gs_show_commit",
    "gs_show_commit_refresh",
    "gs_show_commit_open_on_github",
    "gs_show_commit_toggle_setting",
    "gs_show_commit_open_file_at_hunk",
    "gs_show_commit_show_hunk_on_working_dir",
    "gs_show_commit_open_graph_context",
)

MYPY = False
if MYPY:
    from typing import Optional, Tuple
    from ..types import LineNo, ColNo

SHOW_COMMIT_TITLE = "SHOW-COMMIT: {}"


def compute_identifier_for_view(view):
    # type: (sublime.View) -> Optional[Tuple]
    settings = view.settings()
    return (
        settings.get('git_savvy.repo_path'),
        settings.get('git_savvy.show_commit_view.commit')
    ) if settings.get('git_savvy.show_commit_view') else None


class gs_show_commit(WindowCommand, GitCommand):

    def run(self, commit_hash):
        # need to get repo_path before the new view is created.
        repo_path = self.repo_path
        if commit_hash in {"", "HEAD"}:
            commit_hash = self.git("rev-parse", "--short", "HEAD").strip()

        this_id = (
            self.repo_path,
            commit_hash
        )
        for view in self.window.views():
            if compute_identifier_for_view(view) == this_id:
                focus_view(view)
                break
        else:
            view = self.window.new_file()
            settings = view.settings()
            settings.set("git_savvy.show_commit_view", True)
            settings.set("git_savvy.show_commit_view.commit", commit_hash)
            settings.set("git_savvy.repo_path", repo_path)
            settings.set("git_savvy.show_commit_view.ignore_whitespace", False)
            settings.set("git_savvy.show_commit_view.show_diffstat", self.savvy_settings.get("show_diffstat", True))

            settings.set("result_file_regex", diff.FILE_RE)
            settings.set("result_line_regex", diff.LINE_RE)
            settings.set("result_base_dir", repo_path)

            view.set_syntax_file("Packages/GitSavvy/syntax/show_commit.sublime-syntax")
            view.set_name(SHOW_COMMIT_TITLE.format(self.get_short_hash(commit_hash)))
            view.set_scratch(True)
            view.set_read_only(True)
            view.run_command("gs_show_commit_refresh")
            view.run_command("gs_handle_vintageous")


class gs_show_commit_refresh(TextCommand, GithubRemotesMixin, GitCommand):

    def run(self, edit):
        settings = self.view.settings()
        commit_hash = settings.get("git_savvy.show_commit_view.commit")
        ignore_whitespace = settings.get("git_savvy.show_commit_view.ignore_whitespace")
        show_diffstat = settings.get("git_savvy.show_commit_view.show_diffstat")
        content = self.read_commit(
            commit_hash,
            show_diffstat=show_diffstat,
            ignore_whitespace=ignore_whitespace
        )
        replace_view_content(self.view, content)
        intra_line_colorizer.annotate_intra_line_differences(self.view)
        if SUBLIME_SUPPORTS_REGION_ANNOTATIONS:
            enqueue_on_worker(self.annotate_with_github_link, commit_hash)

    def annotate_with_github_link(self, commit):
        # type: (str) -> None
        try:
            remote_url = self.get_integrated_remote_url()
        except ValueError:
            return
        github_repo = github.parse_remote(remote_url)
        auth = (github_repo.token, "x-oauth-basic") if github_repo.token else None
        url = "{}/commit/{}".format(github_repo.url, commit)
        try:
            response = interwebs.request_url("HEAD", url, auth=auth)
        except Exception:
            return

        if 200 <= response.status < 300:
            self.view.add_regions(
                "link_to_github",
                [sublime.Region(0)],
                annotations=[
                    '<a href="{}">Open on GitHub</a>'
                    .format(url)
                ],
                annotation_color="#aaa0"
            )


class gs_show_commit_open_on_github(TextCommand, GithubRemotesMixin, GitCommand):
    def run(self, edit):
        settings = self.view.settings()
        commit = settings.get("git_savvy.show_commit_view.commit")
        try:
            remote_url = self.get_integrated_remote_url()
        except ValueError as exc:
            flash(self.view, str(exc))
            return

        github_repo = github.parse_remote(remote_url)
        auth = (github_repo.token, "x-oauth-basic") if github_repo.token else None
        url = "{}/commit/{}".format(github_repo.url, commit)
        try:
            response = interwebs.request_url("HEAD", url, auth=auth)
        except Exception as exc:
            flash(self.view, str(exc))
            return

        if 200 <= response.status < 300:
            open_in_browser(url)
        else:
            flash(self.view, "commit not found on {}".format(github_repo.url))


class gs_show_commit_toggle_setting(TextCommand):

    """
    Toggle view settings: `ignore_whitespace`.
    """

    def run(self, edit, setting):
        setting_str = "git_savvy.show_commit_view.{}".format(setting)
        settings = self.view.settings()
        settings.set(setting_str, not settings.get(setting_str))
        flash(self.view, "{} is now {}".format(setting, settings.get(setting_str)))
        self.view.run_command("gs_show_commit_refresh")


class gs_show_commit_open_file_at_hunk(diff.gs_diff_open_file_at_hunk):

    """
    For each cursor in the view, identify the hunk in which the cursor lies,
    and open the file at that hunk in a separate view.
    """

    def load_file_at_line(self, commit_hash, filename, line, col):
        # type: (Optional[str], str, LineNo, ColNo) -> None
        """
        Show file at target commit if `git_savvy.diff_view.target_commit` is non-empty.
        Otherwise, open the file directly.
        """
        if not commit_hash:
            print("Could not parse commit for its commit hash")
            return
        window = self.view.window()
        if not window:
            return

        full_path = os.path.join(self.repo_path, filename)
        window.run_command("gs_show_file_at_commit", {
            "commit_hash": commit_hash,
            "filepath": full_path,
            "position": Position(line - 1, col - 1, None)
        })


class gs_show_commit_show_hunk_on_working_dir(diff.gs_diff_open_file_at_hunk):
    def load_file_at_line(self, commit_hash, filename, line, col):
        # type: (Optional[str], str, LineNo, ColNo) -> None
        if not commit_hash:
            print("Could not parse commit for its commit hash")
            return
        window = self.view.window()
        if not window:
            return

        full_path = os.path.join(self.repo_path, filename)
        line = self.find_matching_lineno(commit_hash, None, line, full_path)
        window.open_file(
            "{file}:{line}:{col}".format(file=full_path, line=line, col=col),
            sublime.ENCODED_POSITION
        )


class gs_show_commit_open_graph_context(TextCommand, GitCommand):
    def run(self, edit):
        # type: (...) -> None
        window = self.view.window()
        if not window:
            return

        settings = self.view.settings()
        commit_hash = settings.get("git_savvy.show_commit_view.commit")

        window.run_command("gs_graph", {
            "all": True,
            "follow": self.get_short_hash(commit_hash)
        })
