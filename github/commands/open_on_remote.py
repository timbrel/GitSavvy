import sublime
from sublime_plugin import TextCommand

from ...core.git_command import GitCommand
from ..github import open_file_in_browser  # , open_repo, open_issues
from ..github import open_repo
from ..github import open_issues

from .. import git_mixins


class GsOpenFileOnRemoteCommand(TextCommand, GitCommand, git_mixins.GithubRemotesMixin):

    """
    Open a new browser window to the web-version of the currently opened
    (or specified) file. If `preselect` is `True`, include the selected
    lines in the request. If the active tracked remote is the same as the
    integrated remote, open browser directly, if not, display a list to remotes
    to choose from.

    At present, this only supports github.com and GitHub enterprise.
    """

    def run(self, edit, remote=None, preselect=False, fpath=None):
        sublime.set_timeout_async(
            lambda: self.run_async(remote, preselect, fpath))

    def run_async(self, remote, preselect, fpath):
        self.fpath = fpath or self.get_rel_path()
        self.preselect = preselect

        self.remotes = self.get_remotes()
        self.remote_keys = list(self.remotes.keys())

        if not remote:
            remote = self.guess_github_remote()

        if remote:
            self.open_file_on_remote(remote)
        else:
            try:
                pre_selected_index = self.remote_keys.index(self.last_remote_used)
            except ValueError:
                pre_selected_index = 0

            self.view.window().show_quick_panel(
                self.remote_keys,
                self.on_select_remote,
                flags=sublime.MONOSPACE_FONT,
                selected_index=pre_selected_index
            )

    def on_select_remote(self, index):
        if index == -1:
            return

        remote = self.remote_keys[index]
        self.last_remote_used = remote
        self.open_file_on_remote(remote)

    def open_file_on_remote(self, remote):
        fpath = self.fpath
        if isinstance(fpath, str):
            fpath = [fpath]
        remote_url = self.remotes[remote]
        commit_hash = self.get_commit_hash_for_head()
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

        for p in fpath:
            open_file_in_browser(
                p,
                remote_url,
                commit_hash,
                start_line=start_line,
                end_line=end_line
            )


class GsOpenGithubRepoCommand(TextCommand, GitCommand, git_mixins.GithubRemotesMixin):

    """
    Open a new browser window to the GitHub remote repository.
    """

    def run(self, edit, remote=None):
        sublime.set_timeout_async(lambda: self.run_async(remote))

    def run_async(self, remote):
        self.remotes = self.get_remotes()
        self.remote_keys = list(self.remotes.keys())

        if not remote:
            remote = self.guess_github_remote()

        if remote:
            open_repo(self.remotes[remote])
        else:
            try:
                pre_selected_index = self.remote_keys.index(self.last_remote_used)
            except ValueError:
                pre_selected_index = 0

            self.view.window().show_quick_panel(
                self.remote_keys,
                self.on_select_remote,
                flags=sublime.MONOSPACE_FONT,
                selected_index=pre_selected_index
            )

    def on_select_remote(self, index):
        if index == -1:
            return
        remote = self.remote_keys[index]
        self.last_remote_used = remote
        open_repo(self.remotes[remote])


class GsOpenGithubIssuesCommand(TextCommand, GitCommand, git_mixins.GithubRemotesMixin):

    """
    Open a new browser window to the GitHub remote repository's issues page.
    """

    def run(self, edit):
        open_issues(self.get_integrated_remote_url())
