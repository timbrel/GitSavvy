import sublime
from sublime_plugin import EventListener, WindowCommand

from . import util
from ..core.settings import SettingsMixin


IGNORE_NEXT_ACTIVATE = False


class GsInterfaceFocusEventListener(EventListener):

    """
    Trigger handlers for view life-cycle events.
    """

    def on_activated(self, view):
        global IGNORE_NEXT_ACTIVATE

        # When the user just opened e.g. the goto or command palette overlay
        # prevent a refresh signal on closing that panel.
        # Whitelist "Terminus" which reports itself as a widget as well.
        if view.settings().get('is_widget') and not view.settings().get("terminus_view"):
            IGNORE_NEXT_ACTIVATE = True
        elif IGNORE_NEXT_ACTIVATE:
            IGNORE_NEXT_ACTIVATE = False
        else:
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


class GitCommandFromTerminal(EventListener, SettingsMixin):
    def on_load(self, view):
        if view.file_name():
            name = view.file_name().split("/")[-1]
            if name in git_view_syntax.keys():
                syntax_file = git_view_syntax[name]
                if "COMMIT_EDITMSG" == name and self.savvy_settings.get("use_syntax_for_commit_editmsg"):
                    syntax_file = util.file.get_syntax_for_file("COMMIT_EDITMSG")

                view.set_syntax_file(syntax_file)
                view.settings().set("git_savvy.{}_view".format(name), True)
                view.set_scratch(True)

    def on_pre_close(self, view):
        if view.file_name():
            name = view.file_name().split("/")[-1]
            if name in git_view_syntax.keys():
                view.run_command("save")


PROJECT_MSG = """
<body>
<p>Add the key <code>"GitSavvy"</code> as follows</p>
{<br>
&nbsp;&nbsp;"settings": {<br>
&nbsp;&nbsp;&nbsp;&nbsp;"GitSavvy": {<br>
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;// GitSavvy settings go here<br>
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;<br>
&nbsp;&nbsp;&nbsp;&nbsp;}<br>
}
</body>
"""


class KeyboardSettingsListener(EventListener):
    def on_post_window_command(self, window, command, args):
        if command == "edit_settings":
            base = args.get("base_file", "")
            if base.endswith("sublime-keymap") and "/GitSavvy/Default" in base:
                w = sublime.active_window()
                w.focus_group(0)
                w.run_command("open_file", {"file": "${packages}/GitSavvy/Default.sublime-keymap"})
                w.focus_group(1)
            elif base.endswith("GitSavvy.sublime-settings"):
                w = sublime.active_window()
                view = w.active_view()
                sublime.set_timeout(
                    lambda: view.show_popup(PROJECT_MSG), 1000)


class GsEditSettingsCommand(WindowCommand):
    """
    For some reasons, the command palette doesn't trigger `on_post_window_command` for
    dev version of Sublime Text. The command palette would call `gs_edit_settings` and
    subsequently trigger `on_post_window_command`.
    """
    def run(self, **kwargs):
        self.window.run_command("edit_settings", kwargs)


class GsEditProjectSettingsCommand(WindowCommand):
    """
    For some reasons, the command palette doesn't trigger `on_post_window_command` for
    dev version of Sublime Text. The command palette would call `gs_edit_settings` and
    subsequently trigger `on_post_window_command`.
    """
    def run(self):
        project_file_name = self.window.project_file_name()
        project_data = self.window.project_data()
        if not project_file_name or project_data is None:
            sublime.error_message("No project data found.")
            return

        sublime.set_timeout(lambda: self.window.run_command("edit_settings", {
            "user_file": project_file_name,
            "base_file": "${packages}/GitSavvy/GitSavvy.sublime-settings"
        }), 100)
