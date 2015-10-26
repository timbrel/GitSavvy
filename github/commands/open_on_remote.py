from sublime_plugin import TextCommand

from ...core.git_command import GitCommand
from ..github import open_file_in_browser#, open_repo, open_issues
from ..github import open_repo
from ..github import open_issues

from .. import git_mixins


class GsOpenFileOnRemoteCommand(TextCommand, GitCommand, git_mixins.GithubRemotesMixin):

    """
    Open a new browser window to the web-version of the currently opened
    (or specified) file. If `preselect` is `True`, include the selected
    lines in the request.

    At present, this only supports github.com and GitHub enterprise.
    """

    def run(self, edit, preselect=False, fpath=None):
        fpath = fpath or self.get_rel_path()
        start_line = None
        end_line = None

        if preselect:
            selections = self.view.sel()
            if len(selections) >= 1:
                first_selection = selections[0]
                last_selection = selections[-1]
                # Git lines are 1-indexed; Sublime rows are 0-indexed.
                start_line = self.view.rowcol(first_selection.begin())[0] + 1
                end_line = self.view.rowcol(last_selection.end())[0] + 1

        open_file_in_browser(
            fpath,
            self.get_integrated_remote_url(),
            self.get_commit_hash_for_head(),
            start_line=start_line,
            end_line=end_line
        )


class GsOpenGithubRepoCommand(TextCommand, GitCommand, git_mixins.GithubRemotesMixin):

    """
    Open a new browser window to the GitHub remote repository.
    """

    def run(self, edit):
        open_repo(self.get_integrated_remote_url())


class GsOpenGithubIssuesCommand(TextCommand, GitCommand, git_mixins.GithubRemotesMixin):

    """
    Open a new browser window to the GitHub remote repository's issues page.
    """

    def run(self, edit):
        open_issues(self.get_integrated_remote_url())
