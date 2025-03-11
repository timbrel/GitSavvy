import sublime_plugin

import os

from GitSavvy.core.git_command import GitCommand


__all__ = (
    "gs_open_repo_config",
    "gs_open_repo_exclude",
)


class gs_open_repo_config(sublime_plugin.WindowCommand, GitCommand):
    def run(self) -> None:
        try:
            repo_path = self.get_repo_path()
        except ValueError:
            self.window.status_message("No .git repo found")
            return

        wanted_file = os.path.join(repo_path, '.git', 'config')
        if not os.path.exists(wanted_file):
            self.window.status_message("No config file found for {}".format(repo_path))
            return

        self.window.open_file(wanted_file)


class gs_open_repo_exclude(sublime_plugin.WindowCommand, GitCommand):
    def run(self) -> None:
        try:
            repo_path = self.get_repo_path()
        except ValueError:
            self.window.status_message("No .git repo found")
            return

        wanted_file = os.path.join(repo_path, '.git', 'info', 'exclude')
        if not os.path.exists(wanted_file):
            self.window.status_message("No exclude file found for {}".format(repo_path))
            return

        self.window.open_file(wanted_file)
