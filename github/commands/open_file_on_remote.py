from sublime_plugin import TextCommand

from ...core.git_command import GitCommand
from ..github import open_file_in_browser


class GsOpenFileOnRemoteCommand(TextCommand, GitCommand):

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

        default_name, default_remote_url = self.get_remotes().popitem(last=False)

        open_file_in_browser(
            fpath,
            default_remote_url,
            self.get_commit_hash_for_head(),
            start_line=start_line,
            end_line=end_line
        )
