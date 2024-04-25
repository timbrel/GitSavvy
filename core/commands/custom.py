import sublime
from sublime_plugin import WindowCommand

from ..git_command import GitCommand
from ..runtime import enqueue_on_worker
from ..ui_mixins.input_panel import show_single_line_input_panel
from ..view import replace_view_content
from ...common import util
from GitSavvy.core.runtime import run_new_daemon_thread


__all__ = (
    "gs_custom",
)


class gs_custom(WindowCommand, GitCommand):

    """
    Run the specified custom command asynchronously.
    """

    def run(self, **kwargs):
        args = kwargs.get('args')
        if not args:
            sublime.error_message("Custom command must provide args.")
            return

        # prompt for custom command argument
        if '{PROMPT_ARG}' in args:
            prompt_msg = kwargs.pop("prompt_msg", "Command argument: ")
            return show_single_line_input_panel(
                prompt_msg,
                "",
                lambda arg: self.run_impl(custom_argument=arg, **kwargs)
            )

        self.run_impl(**kwargs)

    def run_impl(
        self,
        output_to_panel=False,
        output_to_buffer=False,
        args=None,
        start_msg="Starting custom command...",
        complete_msg="Completed custom command.",
        syntax=None,
        run_in_thread=False,
        custom_argument=None,
        custom_environ=None,
    ):

        for idx, arg in enumerate(args):
            if arg == "{REPO_PATH}":
                args[idx] = self.repo_path
            elif arg == "{FILE_PATH}":
                args[idx] = self.file_path
            elif arg == "{PROMPT_ARG}":
                args[idx] = custom_argument

        def program():
            self.window.status_message(start_msg)
            stdout = self.git(*args, custom_environ=custom_environ)
            self.window.status_message(complete_msg)

            if output_to_panel:
                util.log.display_panel(self.window, stdout)
            if output_to_buffer:
                view = self.window.new_file()
                view.set_scratch(True)
                if syntax:
                    view.set_syntax_file(syntax)
                replace_view_content(view, stdout.replace("\r", "\n"))

            util.view.refresh_gitsavvy_interfaces(self.window)

        if run_in_thread:
            run_new_daemon_thread(program)
        else:
            enqueue_on_worker(program)
