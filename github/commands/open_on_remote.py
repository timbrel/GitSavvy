from __future__ import annotations

from difflib import SequenceMatcher
from webbrowser import open as open_in_browser

import sublime

from ..github import construct_github_file_url, open_issues, open_repo

from .. import git_mixins
from ...core.ui_mixins.quick_panel import show_remote_panel
from ...core.utils import flash, hprint
from GitSavvy.core.base_commands import GsTextCommand
from GitSavvy.core.runtime import on_worker


__all__ = (
    "gs_github_open_file_in_browser",
    "gs_github_copy_file_url",
    "gs_github_open_repo",
    "gs_github_open_issues",
)


class GsGithubFileUrlMixin(GsTextCommand, git_mixins.GithubRemotesMixin):

    """
    Open a new browser window to the web-version of the currently opened
    (or specified) file. If `preselect` is `True`, include the selected
    lines in the request. If the active tracked remote is the same as the
    integrated remote, open browser directly, if not, display a list to remotes
    to choose from.

    At present, this only supports github.com and GitHub enterprise.
    """

    @on_worker
    def run(self, edit, remote=None, preselect=False, fpath=None):
        if fpath is None:
            file_path = self.file_path
            if not file_path:
                flash(self.view, "Not available for unsaved files.")
                return
            fpath = self.to_short_path(file_path)
        self.fpath = fpath
        self.preselect = preselect

        self.remotes = remotes = self.get_remotes()

        if not remote:
            remote = self.guess_github_remote(remotes)

        if remote:
            self.open_file_on_remote(remote)
        else:
            show_remote_panel(self.open_file_on_remote, allow_direct=True)

    def open_file_on_remote(self, remote: str) -> None:
        paths = [self.fpath] if isinstance(self.fpath, str) else self.fpath
        if not paths:
            return
        settings = self.view.settings()
        historical = settings.get("git_savvy.show_file_at_commit_view", False)
        if historical:
            revision = commit = settings.get("git_savvy.show_file_at_commit_view.commit")
            if not commit or remote not in self.remotes_containing_commit(commit):
                flash(self.view, f"This revision has not been pushed to {remote} yet.")
                return
        else:
            target = self.published_branch(remote)
            if target is None:
                return
            revision, commit = target

        published_paths = self.git(
            "ls-tree", "-r", "-z", "--name-only", commit, "--", *paths
        ).split("\0")
        for path in paths:
            if path not in published_paths:
                flash(self.view, f"{path} does not exist at {revision} on {remote}.")
                return

        start_line = end_line = None
        if self.preselect and len(paths) == 1 and self.view.sel():
            selection = sublime.Region(self.view.sel()[0].begin(), self.view.sel()[-1].end())
            start_line = self.view.rowcol(selection.begin())[0] + 1
            end_line = self.view.rowcol(max(selection.begin(), selection.end() - 1))[0] + 1
            if not historical:
                content = self.view.substr(sublime.Region(0, self.view.size()))
                published_content = self.get_file_content_at_commit(paths[0], commit)
                lines = matching_lines(content, published_content, start_line, end_line)
                if lines is None:
                    flash(self.view, f"The selected lines are not published on {remote}/{revision}.")
                    return
                start_line, end_line = lines

        self.computed_urls([
            construct_github_file_url(
                path, self.remotes[remote], revision,
                start_line=start_line, end_line=end_line
            )
            for path in paths
        ])

    def computed_urls(self, urls: list[str]) -> None:
        raise NotImplementedError

    def published_branch(self, remote: str) -> tuple[str, str] | None:
        """Choose a browser branch name and its locally known snapshot."""
        upstream = self.get_upstream_for_active_branch()
        if upstream and upstream.remote == remote and upstream.status != "gone":
            branch = upstream.branch
        else:
            default = self.guess_default_branch(remote)
            if not default:
                flash(self.view, f"Could not determine the default branch of {remote}.")
                return None
            prefix = remote + "/"
            branch = default[len(prefix):] if default.startswith(prefix) else default

        commit = self.git(
            "rev-parse", "--verify", "--quiet", f"refs/remotes/{remote}/{branch}^{{commit}}",
            throw_on_error=False
        ).strip()
        if not commit:
            flash(self.view, f"{remote}/{branch} is not available locally. Fetch {remote} first.")
            return None
        return branch, commit


class gs_github_open_file_in_browser(GsGithubFileUrlMixin):
    def computed_urls(self, urls: list[str]) -> None:
        for url in urls:
            open_in_browser(url)


class gs_github_copy_file_url(GsGithubFileUrlMixin):
    def computed_urls(self, urls: list[str]) -> None:
        if len(urls) > 1:
            hprint("Multiple files selected, only copying the first URL")

        for url in urls:
            sublime.set_clipboard(url)
            flash(self.view, f"Copied '{url}' to the clipboard")
            break


class gs_github_open_repo(GsTextCommand, git_mixins.GithubRemotesMixin):

    """
    Open a new browser window to the GitHub remote repository.
    """

    @on_worker
    def run(self, edit, remote=None):
        self.remotes = remotes = self.get_remotes()

        if not remote:
            remote = self.guess_github_remote(remotes)

        if remote:
            open_repo(self.remotes[remote])
        else:
            show_remote_panel(self.on_remote_selection, allow_direct=True)

    def on_remote_selection(self, remote):
        open_repo(self.remotes[remote])


class gs_github_open_issues(GsTextCommand, git_mixins.GithubRemotesMixin):

    """
    Open a new browser window to the GitHub remote repository's issues page.
    """

    def run(self, edit):
        open_issues(self.get_integrated_remote_url())


def matching_lines(
    content: str, published_content: str, start: int, end: int
) -> tuple[int, int] | None:
    """Map a 1-based line range only when all its lines are unchanged."""
    matcher = SequenceMatcher(None, content.splitlines(), published_content.splitlines(), autojunk=False)
    for local_start, remote_start, length in matcher.get_matching_blocks():
        if local_start <= start - 1 and end <= local_start + length:
            offset = remote_start - local_start
            return start + offset, end + offset
    return None
