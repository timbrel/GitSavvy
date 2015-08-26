import sublime
from sublime_plugin import WindowCommand

from ..git_command import GitCommand
from ...common import util


ALL_REMOTES = "All remotes."


class GsCustomCommand(WindowCommand, GitCommand):

    """
    Run the specified custom command asynchronously.
    """

    def run(self, **kwargs):
        sublime.set_timeout_async(lambda: self.run_async(**kwargs), 0)

    def run_async(self,
                  output_to_panel=False,
                  args=None,
                  start_msg="Starting custom command...",
                  complete_msg="Completed custom command."):

        if not args:
            sublime.error_message("Custom command must provide args.")

        for idx, arg in enumerate(args):
            if arg == "{REPO_PATH}":
                args[idx] = self.repo_path
            elif arg == "{FILE_PATH}":
                args[idx] = self.file_path

        sublime.status_message(start_msg)
        stdout = self.git(*args)
        sublime.status_message(complete_msg)

        if output_to_panel:
            util.log.panel(stdout)
