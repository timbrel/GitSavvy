from __future__ import annotations

from difflib import SequenceMatcher
from typing import NamedTuple
from webbrowser import open as open_in_browser

import sublime

from ..github import construct_github_file_url, open_issues, open_repo

from .. import git_mixins
from ...core.ui_mixins.quick_panel import show_remote_panel
from ...core.utils import flash, hprint
from GitSavvy.core.base_commands import GsTextCommand
from GitSavvy.core.git_mixins.branches import Upstream
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

    _branch_snapshot: FileUrlBranches | None = None

    @on_worker
    def run(self, edit, remote=None, preselect=False, fpath=None):
        self._branch_snapshot = None
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
            # The view already loaded this file. Reuse its historical name,
            # including renames, rather than checking the HEAD path again.
            paths = [
                self.to_short_path(self.filename_at_commit(self.to_full_path(path), commit))
                for path in paths
            ]
        else:
            target = self.published_revision(remote)
            if target is None:
                return
            revision, commit = target

            published_paths = self.file_paths_at_commit(commit, tuple(paths))
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
                start_line, end_line = lines if lines is not None else (None, None)

        self.computed_urls([
            construct_github_file_url(
                path, self.remotes[remote], revision,
                start_line=start_line, end_line=end_line
            )
            for path in paths
        ])

    def computed_urls(self, urls: list[str]) -> None:
        raise NotImplementedError

    def published_revision(self, remote: str) -> tuple[str, str] | None:
        """Choose a browser revision and its locally known snapshot."""
        branch: str | None
        upstream = self.get_upstream_for_active_branch()
        if upstream and upstream.remote == remote and upstream.status != "gone":
            branch = upstream.branch
        else:
            default = self.guess_default_branch(remote)
            prefix = remote + "/"
            branch = default[len(prefix):] if default and default.startswith(prefix) else default
            if target := self.published_branch_from_history(remote, branch):
                return target
            if not branch:
                flash(self.view, f"Could not determine the default branch of {remote}.")
                return None

        commit = self.branch_snapshot().remote_tips.get(f"{remote}/{branch}")
        if not commit:
            flash(self.view, f"{remote}/{branch} is not available locally. Fetch {remote} first.")
            return None
        return branch, commit

    def published_branch_from_history(
        self, remote: str, default_branch: str | None
    ) -> tuple[str, str] | None:
        prefix = remote + "/"
        branches = {
            ref[len(prefix):]: commit
            for ref, commit in self.branch_snapshot().remote_tips.items()
            if ref.startswith(prefix)
        }
        if not branches:
            return None

        # Without --octopus, Git compares HEAD with the combined history of
        # the remote tips.  No subprocess per branch or unpublished commit.
        bases = self.git(
            "merge-base", "--all", "HEAD", *dict.fromkeys(branches.values()),
            throw_on_error=False, show_panel_on_error=False
        ).splitlines()
        if not bases:
            return None

        # These cases already identify the preferred containing branch.
        if default_branch and branches.get(default_branch) in bases:
            return default_branch, branches[default_branch]
        if len(branches) == 1:
            return next(iter(branches.items()))

        # The boundary chooses a branch, not the URL's revision. In particular,
        # a feature merged into the default branch should open there, even if
        # its old feature ref still points exactly at the boundary.
        ref_prefix = f"refs/remotes/{remote}/"
        containing = {}
        for line in self.git(
            "for-each-ref", "--format=%(refname) %(objectname) %(symref)",
            *(f"--contains={base}" for base in bases), ref_prefix
        ).splitlines():
            fields = line.split()
            if len(fields) == 2:  # Exclude symbolic aliases such as origin/HEAD.
                ref, commit = fields
                containing[ref[len(ref_prefix):]] = commit
        if not containing:
            return None

        branch = min(containing, key=lambda name: (
            name != default_branch, containing[name] not in bases, name
        ))
        return branch, containing[branch]

    def get_upstream_for_active_branch(self) -> Upstream | None:
        # Remote selection and revision selection share the same snapshot.
        return self.branch_snapshot().upstream

    def branch_snapshot(self) -> FileUrlBranches:
        if self._branch_snapshot is None:
            stored = (
                self.current_state().get("branches")
                if self.view.settings().get("git_savvy.status_view")
                else None
            )
            if stored is not None:
                self._branch_snapshot = FileUrlBranches(
                    next((branch.upstream for branch in stored if branch.active), None),
                    {branch.canonical_name: branch.commit_hash for branch in stored if branch.is_remote}
                )
            else:
                self._branch_snapshot = parse_branch_snapshot(self.git(
                    "for-each-ref",
                    "--format=%(HEAD)%00%(refname)%00%(objectname)%00%(upstream)"
                    "%00%(upstream:remotename)%00%(symref)",
                    "refs/heads", "refs/remotes"
                ))
        return self._branch_snapshot


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
    """Map a 1-based range, landing nearby for edits and dropping unrelated selections."""
    published_lines = published_content.splitlines()
    opcodes = SequenceMatcher(
        None, content.splitlines(), published_lines, autojunk=False
    ).get_opcodes()
    if not any(tag == "equal" for tag, *_ in opcodes):
        return None

    def map_line(line: int) -> int:
        for _, local_start, local_end, remote_start, remote_end in opcodes:
            if local_start <= line - 1 < local_end:
                offset = min(line - 1 - local_start, max(0, remote_end - remote_start - 1))
                return min(remote_start + offset + 1, len(published_lines))
        return len(published_lines)

    return map_line(start), map_line(end)


def parse_branch_snapshot(output: str) -> FileUrlBranches:
    upstream = None
    remote_tips = {}
    for line in output.splitlines():
        active, ref, commit, upstream_ref, remote, symref = line.split("\0")
        if ref.startswith("refs/remotes/") and not symref:
            remote_tips[ref[len("refs/remotes/"):]] = commit
        if active == "*" and upstream_ref:
            canonical = "/".join(upstream_ref.split("/")[2:])
            branch = canonical[len(remote) + 1:] if remote != "." else canonical
            upstream = Upstream(remote, branch, canonical, "")
    if upstream and upstream.remote != "." and upstream.canonical_name not in remote_tips:
        upstream = upstream._replace(status="gone")
    return FileUrlBranches(upstream, remote_tips)


class FileUrlBranches(NamedTuple):
    upstream: Upstream | None
    remote_tips: dict[str, str]
