import sublime
import threading
from sublime_plugin import WindowCommand

from ..git_command import GitCommand
from ...common import util
from ..ui_mixins.input_panel import show_single_line_input_panel


class CustomCommandThread(threading.Thread):
    def __init__(self, func, *args, custom_environ=None, **kwargs):
        self.custom_environ = custom_environ
        super(CustomCommandThread, self).__init__(**kwargs)
        self.cmd_args = args
        self.cmd_func = func
        self.daemon = True

    def run(self):
        return self.cmd_func(*self.cmd_args,
                             custom_environ=self.custom_environ)


class GsCustomCommand(WindowCommand, GitCommand):

    """
    Run the specified custom command asynchronously.
    """

    def run(self, **kwargs):
        if not kwargs.get('args'):
            sublime.error_message("Custom command must provide args.")
            return

        # prompt for custom command argument
        if '{PROMPT_ARG}' in kwargs.get('args'):
            prompt_msg = kwargs.pop("prompt_msg", "Command argument: ")
            return show_single_line_input_panel(
                prompt_msg,
                "",
                lambda arg: sublime.set_timeout_async(
                    lambda: self.run_async(custom_argument=arg, **kwargs), 0
                )
            )

        sublime.set_timeout_async(lambda: self.run_async(**kwargs), 0)

    def run_async(self,
                  output_to_panel=False,
                  output_to_buffer=False,
                  args=None,
                  start_msg="Starting custom command...",
                  complete_msg="Completed custom command.",
                  syntax=None,
                  run_in_thread=False,
                  custom_argument=None,
                  custom_environ=None):

        for idx, arg in enumerate(args):
            if arg == "{REPO_PATH}":
                args[idx] = self.repo_path
            elif arg == "{FILE_PATH}":
                args[idx] = self.file_path
            elif arg == "{PROMPT_ARG}":
                args[idx] = custom_argument

        self.window.status_message(start_msg)
        if run_in_thread:
            stdout = ''
            cmd_thread = CustomCommandThread(self.git, *args, custom_environ=custom_environ)
            cmd_thread.start()
        else:
            stdout = self.git(*args, custom_environ=custom_environ)
        self.window.status_message(complete_msg)

        if output_to_panel:
            util.log.panel(stdout.replace("\r", "\n"))
        if output_to_buffer:
            view = self.window.new_file()
            view.set_scratch(True)
            view.run_command("gs_replace_view_text", {"text": stdout.replace("\r", "\n"), "nuke_cursors": True})
            if syntax:
                view.set_syntax_file(syntax)

        util.view.refresh_gitsavvy(self.window.active_view())
