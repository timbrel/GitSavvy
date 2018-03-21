import sublime
from sublime_plugin import EventListener, WindowCommand

from . import util


class GsInterfaceFocusEventListener(EventListener):

    """
    Trigger handlers for view life-cycle events.
    """

    def on_activated(self, view):
        # status bar is handled by GsStatusBarEventListener
        util.view.refresh_gitsavvy(view, refresh_status_bar=False)

    def on_close(self, view):
        util.view.handle_closed_view(view)


git_view_syntax = {
    'MERGE_MSG': 'Packages/GitSavvy/syntax/make_commit.sublime-syntax',
    'COMMIT_EDITMSG': 'Packages/GitSavvy/syntax/make_commit.sublime-syntax',
    'PULLREQ_EDITMSG': 'Packages/GitSavvy/syntax/make_commit.sublime-syntax',
    'git-rebase-todo': 'Packages/GitSavvy/syntax/rebase_interactive.sublime-syntax',
}


class GitCommandFromTerminal(EventListener):
    def on_load(self, view):
        if view.file_name():
            name = view.file_name().split("/")[-1]
            if name in git_view_syntax.keys():
                view.set_syntax_file(git_view_syntax[name])
                view.settings().set("git_savvy.{}_view".format(name), True)
                view.set_scratch(True)

    def on_pre_close(self, view):
        if view.file_name():
            name = view.file_name().split("/")[-1]
            if name in git_view_syntax.keys():
                view.run_command("save")


class KeyboardSettingsListener(EventListener):
    def on_post_window_command(self, window, command, args):
        if command == "edit_settings":
            base = args.get("base_file", "")
            if base.endswith("sublime-keymap") and "/GitSavvy/Default" in base:
                w = sublime.active_window()
                w.focus_group(0)
                w.run_command("open_file", {"file": "${packages}/GitSavvy/Default.sublime-keymap"})
                w.focus_group(1)


class GsEditSettingsCommand(WindowCommand):
    """
    For some reasons, the command palette doesn't trigger `on_post_window_command` for
    dev version of Sublime Text. The command palette would call `gs_edit_settings` and
    subsequently trigger `on_post_window_command`.
    """
    def run(self, **kwargs):
        self.window.run_command("edit_settings", kwargs)
