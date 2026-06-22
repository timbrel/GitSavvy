from __future__ import annotations

import sublime
import sublime_plugin

import os

from GitSavvy.core.git_command import GitCommand


__all__ = (
    "gs_open_repo_config",
    "gs_open_repo_exclude",
)


class gs_open_repo_config(sublime_plugin.WindowCommand, GitCommand):
    """
    Open the repository config file.

    If `highlight` is provided, open the file at the first line whose
    stripped text exactly matches it.  For example, callers can pass
    `highlight='[remote "origin"]'` to jump to a remote section.
    """

    def run(self, highlight: str | None = None) -> None:
        try:
            repo_path = self.get_repo_path()
        except ValueError:
            self.window.status_message("No .git repo found")
            return

        wanted_file = self.repo_config_path
        if not os.path.exists(wanted_file):
            self.window.status_message("No config file found for {}".format(repo_path))
            return

        if highlight:
            line = self.find_config_line(wanted_file, highlight)
            if line:
                self.window.open_file(
                    "{}:{}:1".format(wanted_file, line),
                    sublime.ENCODED_POSITION
                )
                return

        self.window.open_file(wanted_file)

    def find_config_line(self, config_file: str, highlight: str) -> int | None:
        try:
            with open(config_file, encoding="utf-8") as f:
                for line_no, line in enumerate(f, start=1):
                    if line.strip() == highlight:
                        return line_no
        except OSError:
            pass
        return None


class gs_open_repo_exclude(sublime_plugin.WindowCommand, GitCommand):
    def run(self) -> None:
        try:
            repo_path = self.get_repo_path()
        except ValueError:
            self.window.status_message("No .git repo found")
            return

        wanted_file = os.path.join(self.git_common_dir, 'info', 'exclude')
        if not os.path.exists(wanted_file):
            self.window.status_message("No exclude file found for {}".format(repo_path))
            return

        self.window.open_file(wanted_file)
