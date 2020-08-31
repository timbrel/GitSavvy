import os

import sublime
from sublime_plugin import EventListener, WindowCommand

from . import util
from ..core.settings import SettingsMixin


IGNORE_NEXT_ACTIVATE = False


class GsInterfaceFocusEventListener(EventListener):

    """
    Trigger handlers for view life-cycle events.
    """

    # When the user just opened e.g. the goto or command palette overlay
    # prevent a refresh signal on closing that panel.
    # Whitelist "Terminus" which reports itself as a widget.
    def on_deactivated(self, view):
        global IGNORE_NEXT_ACTIVATE
        settings = view.settings()
        IGNORE_NEXT_ACTIVATE = (
            settings.get("is_widget")
            and not settings.get("terminus_view")
        )

    def on_activated(self, view):
        global IGNORE_NEXT_ACTIVATE
        if IGNORE_NEXT_ACTIVATE:
            return

        if view.settings().get("is_widget"):
            return

        # status bar is handled by GsStatusBarEventListener
        util.view.refresh_gitsavvy(view, refresh_status_bar=False)

    def on_close(self, view):
        util.view.handle_closed_view(view)


NATIVE_GIT_EDITOR_FILES = {
    'MERGE_MSG',
    'COMMIT_EDITMSG',
    'PULLREQ_EDITMSG',
    'git-rebase-todo',
}


class GitCommandFromTerminal(EventListener, SettingsMixin):
    def on_load(self, view):
        # type: (sublime.View) -> None
        file_path = view.file_name()
        if file_path and os.path.basename(file_path) in NATIVE_GIT_EDITOR_FILES:
            view.set_scratch(True)

    def on_pre_close(self, view):
        # type: (sublime.View) -> None
        file_path = view.file_name()
        if file_path and os.path.basename(file_path) in NATIVE_GIT_EDITOR_FILES:
            view.run_command("save")


PROJECT_MSG = """
<body>
<p>Add the key <code>"GitSavvy"</code> as follows</p>
<code>
{<br>
  "settings": {<br>
    "GitSavvy": {<br>
        // GitSavvy settings go here<br>
    }<br>
  }<br>
}<br>
</code>
</body>
""".replace(" ", "&nbsp;")


class KeyboardSettingsListener(EventListener):
    def on_post_window_command(self, window, command, args):
        if command == "edit_settings":
            base = args.get("base_file", "")
            if base.endswith("sublime-keymap") and "/GitSavvy/Default" in base:
                w = sublime.active_window()
                w.focus_group(0)
                w.run_command("open_file", {"file": "${packages}/GitSavvy/Default.sublime-keymap"})
                w.focus_group(1)
            elif args.get("user_file", "").endswith(".sublime-project"):
                w = sublime.active_window()
                view = w.active_view()
                data = window.project_data()
                if view and "GitSavvy" not in data.get("settings", {}):
                    sublime.set_timeout_async(
                        lambda: view.show_popup(PROJECT_MSG, max_width=550)  # type: ignore
                    )
            else:
                w = sublime.active_window()
                w.focus_group(1)
                right_view = w.active_view()
                if not right_view:
                    return
                filename = os.path.basename(right_view.file_name() or "")
                if not filename:
                    return

                w.focus_group(0)
                for r in sublime.find_resources(filename):
                    if r.startswith("Packages/") and "/GitSavvy/syntax/" in r:
                        w.run_command("open_file", {"file": "${packages}/" + r[9:]})
                w.focus_group(1)


class GsEditSettingsCommand(WindowCommand):
    """
    For some reasons, the command palette doesn't trigger `on_post_window_command` for
    dev version of Sublime Text. The command palette would call `gs_edit_settings` and
    subsequently trigger `on_post_window_command`.
    Ref: https://github.com/sublimehq/sublime_text/issues/2234
    """
    def run(self, **kwargs):
        self.window.run_command("edit_settings", kwargs)


class GsEditProjectSettingsCommand(WindowCommand):
    """
    For some reasons, the command palette doesn't trigger `on_post_window_command` for
    dev version of Sublime Text. The command palette would call `gs_edit_settings` and
    subsequently trigger `on_post_window_command`.
    Ref: https://github.com/sublimehq/sublime_text/issues/2234
    """
    def run(self):
        project_file_name = self.window.project_file_name()
        project_data = self.window.project_data()
        if not project_file_name or project_data is None:
            sublime.error_message("No project data found.")
            return

        self.window.run_command("edit_settings", {
            "user_file": project_file_name,
            "base_file": "${packages}/GitSavvy/GitSavvy.sublime-settings"
        })
