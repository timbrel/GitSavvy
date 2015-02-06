import os

import sublime

from sublime_plugin import WindowCommand

from .base_command import BaseCommand


class GsOpenFileOnRemoteCommand(WindowCommand, BaseCommand):

    def run(self, preselect=False):
        fpath = os.path.relpath(self.file_path, self.repo_path)
        start_line = None
        end_line = None

        if preselect:
            view = sublime.active_window().active_view()
            selections = view.sel()
            if len(selections) >= 1:
                first_selection = selections[0]
                last_selection = selections[-1]
                # Git lines are 1-indexed; Sublime rows are 0-indexed.
                start_line = view.rowcol(first_selection.begin())[0] + 1
                end_line = view.rowcol(last_selection.end())[0] + 1

        self.open_file_on_remote(fpath, start_line, end_line)
