import sublime
from sublime_plugin import TextCommand

from ...core.git_command import GitCommand
from ..github import open_file_in_browser  # , open_repo, open_issues
from ..github import open_repo
from ..github import open_issues

from .. import git_mixins
from ...core.ui_mixins.quick_panel import show_remote_panel
from ...core.commands.push import GsPushToBranchNameCommand


PUSH_PROMPT = ("The remote chosen may not contain the latest commits.  "
               "Would you like to push to remote?")


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
            show_remote_panel(self.open_file_on_remote)

    def open_file_on_remote(self, remote):
        if not remote:
            return

        fpath = self.fpath
        if isinstance(fpath, str):
            fpath = [fpath]
        remote_url = self.remotes[remote]

        if self.view.settings().get("git_savvy.show_file_at_commit_view"):
            # if it is a show_file_at_commit_view, get the hash from settings
            commit_hash = self.view.settings().get("git_savvy.show_file_at_commit_view.commit")
        else:
            commit_hash = self.get_commit_hash_for_head()

        valid_remotes = set([
                branch.split("/")[0]
                for branch in self.branches_contain_commit(commit_hash, remote_only=True)
            ])

        # check if the remote contains the commit hash
        if remote not in valid_remotes:
            if sublime.ok_cancel_dialog(PUSH_PROMPT):
                self.view.window().run_command(
                    "gs_push_and_open_file_on_remote",
                    {"remote": remote, "set_upstream": True}
                )
        else:
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


class GsPushAndOpenFileOnRemoteCommand(GsPushToBranchNameCommand):

    def do_push(self, *args, **kwargs):
        super().do_push(*args, **kwargs)
        self.window.active_view().run_command(
            "gs_open_file_on_remote",
            {"remote": self.selected_remote})


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
            show_remote_panel(self.on_remote_selection)

    def on_remote_selection(self, remote):
        if not remote:
            return
        open_repo(self.remotes[remote])


class GsOpenGithubIssuesCommand(TextCommand, GitCommand, git_mixins.GithubRemotesMixin):

    """
    Open a new browser window to the GitHub remote repository's issues page.
    """

    def run(self, edit):
        open_issues(self.get_integrated_remote_url())
