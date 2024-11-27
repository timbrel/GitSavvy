import sublime

from ..gitlab import open_file_in_browser, open_repo, open_issues

from ..git_mixins import GitLabRemotesMixin
from ...core.ui_mixins.quick_panel import show_remote_panel
from GitSavvy.core.base_commands import GsTextCommand
from GitSavvy.core.runtime import on_worker


__all__ = (
    "gs_gitlab_open_file_in_browser",
    "gs_gitlab_open_repo",
    "gs_gitlab_open_issues",
)

EARLIER_COMMIT_PROMPT = ("The remote chosen may not contain the commit. "
                         "Open the file {} before?")


class gs_gitlab_open_file_in_browser(GsTextCommand, GitLabRemotesMixin):

    """
    Open a new browser window to the web-version of the currently opened
    (or specified) file. If `preselect` is `True`, include the selected
    lines in the request. If the active tracked remote is the same as the
    integrated remote, open browser directly, if not, display a list to remotes
    to choose from.

    At present, this only supports gitlab.com and hosted servers.
    """

    @on_worker
    def run(self, edit, remote=None, preselect=False, fpath=None):
        self.fpath = fpath or self.get_rel_path()
        self.preselect = preselect

        self.remotes = remotes = self.get_remotes()

        if not remote:
            remote = self.guess_gitlab_remote(remotes)

        if remote:
            self.open_file_on_remote(remote)
        else:
            show_remote_panel(self.open_file_on_remote, allow_direct=True)

    def open_file_on_remote(self, remote):
        fpath = self.fpath
        if isinstance(fpath, str):
            fpath = [fpath]
        remote_url = self.remotes[remote]

        if self.view.settings().get("git_savvy.show_file_at_commit_view"):
            # if it is a show_file_at_commit_view, get the hash from settings
            commit_hash = self.view.settings().get("git_savvy.show_file_at_commit_view.commit")
        else:
            commit_hash = self.get_commit_hash_for_head()

        base_hash = commit_hash

        # check if the remote contains the commit hash
        if remote not in self.remotes_containing_commit(commit_hash):
            upstream = self.get_upstream_for_active_branch()
            if upstream:
                merge_base = self.git("merge-base", commit_hash, upstream.canonical_name).strip()
                if merge_base and remote in self.remotes_containing_commit(merge_base):
                    count = self.git(
                        "rev-list", "--count", "{}..{}".format(merge_base, commit_hash)).strip()
                    if not sublime.ok_cancel_dialog(EARLIER_COMMIT_PROMPT.format(
                            count + (" commit" if count == "1" else " commits"))):
                        return

                    commit_hash = merge_base

        start_line = None
        end_line = None

        if self.preselect and len(fpath) == 1:
            selections = self.view.sel()
            if len(selections) >= 1:
                first_selection = selections[0]
                last_selection = selections[-1]
                # Git lines are 1-indexed; Sublime rows are 0-indexed.
                start_line = self.view.rowcol(first_selection.begin())[0] + 1
                end_line = self.view.rowcol(last_selection.end())[0] + 1

                # forward line number if the opening commit is the merge base
                if base_hash != commit_hash:
                    diff = self.no_context_diff(base_hash, commit_hash, fpath[0])
                    start_line = self.adjust_line_according_to_diff(diff, start_line)
                    end_line = self.adjust_line_according_to_diff(diff, end_line)

        for p in fpath:
            open_file_in_browser(
                p,
                remote_url,
                commit_hash,
                start_line=start_line,
                end_line=end_line
            )


class gs_gitlab_open_repo(GsTextCommand, GitLabRemotesMixin):

    """
    Open a new browser window to the GitLab remote repository.
    """

    @on_worker
    def run(self, edit, remote=None):
        self.remotes = remotes = self.get_remotes()

        if not remote:
            remote = self.guess_gitlab_remote(remotes)

        if remote:
            open_repo(self.remotes[remote])
        else:
            show_remote_panel(self.on_remote_selection, allow_direct=True)

    def on_remote_selection(self, remote):
        open_repo(self.remotes[remote])


class gs_gitlab_open_issues(GsTextCommand, GitLabRemotesMixin):

    """
    Open a new browser window to the GitLab remote repository's issues page.
    """

    def run(self, edit):
        open_issues(self.get_integrated_remote_url())
