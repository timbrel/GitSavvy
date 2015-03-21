import sublime
from sublime_plugin import WindowCommand

from ..git_command import GitCommand

class GsGuiCommand(WindowCommand, GitCommand):

    def run(self):
        sublime.set_timeout_async(self.run_async, 0)

    def run_async(self):
        self.git("gui")
