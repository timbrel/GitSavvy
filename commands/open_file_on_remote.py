import os

from sublime_plugin import WindowCommand

from .base_command import BaseCommand


class GgOpenFileOnRemoteCommand(WindowCommand, BaseCommand):

    def run(self):
        fpath = os.path.relpath(self.file_path, self.repo_path)
        self.open_file_on_remote(fpath)
