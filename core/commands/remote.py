import sublime

from . import init
from ...common import util
from ..ui_mixins.quick_panel import show_remote_panel
from ..ui_mixins.input_panel import show_single_line_input_panel
from GitSavvy.core.runtime import run_on_new_thread
from GitSavvy.core.base_commands import GsWindowCommand


__all__ = (
    "gs_remote_add",
    "gs_remote_remove",
    "gs_remote_rename",
)


class gs_remote_add(GsWindowCommand):
    """
    Add remotes
    """

    def run(self, url=None, set_as_push_default=False, ignore_tags=False):
        self.ignore_tags = ignore_tags
        self.set_as_push_default = set_as_push_default
        if url:
            self.on_enter_remote(url)
        else:
            clip_content = sublime.get_clipboard(256).strip()
            show_single_line_input_panel(
                "Remote URL",
                init.parse_url_from_clipboard(clip_content),
                self.on_enter_remote)

    def on_enter_remote(self, input_url):
        self.url = input_url
        owner = self.username_from_url(input_url)

        show_single_line_input_panel("Remote name", owner, self.on_enter_name)

    def on_enter_name(self, remote_name):
        self.git("remote", "add", remote_name, self.url)
        if self.ignore_tags:
            self.git("config", f"remote.{remote_name}.push", "+refs/heads/*:refs/heads/*")
            self.git("config", f"remote.{remote_name}.tagOpt", "--no-tags")
        if self.set_as_push_default:
            run_on_new_thread(self.git, "config", "--local", "gitsavvy.pushdefault", remote_name)
            self.update_store({"last_remote_used_for_push": remote_name})

        if self.savvy_settings.get("fetch_new_remotes", True) or sublime.ok_cancel_dialog(
            "Your remote was added successfully.  "
            "Would you like to fetch from this remote?"
        ):
            self.window.run_command("gs_fetch", {"remote": remote_name})


class gs_remote_remove(GsWindowCommand):
    """
    Remove remotes
    """

    def run(self):
        show_remote_panel(self.on_remote_selection, show_url=True)

    @util.actions.destructive(description="remove a remote")
    def on_remote_selection(self, remote):
        self.git("remote", "remove", remote)
        util.view.refresh_gitsavvy_interfaces(self.window, refresh_status_bar=False)


class gs_remote_rename(GsWindowCommand):
    """
    Reame remotes
    """

    def run(self):
        show_remote_panel(self.on_remote_selection, show_url=True)

    def on_remote_selection(self, remote):
        self.remote = remote
        show_single_line_input_panel("New name", remote, self.on_enter_name)

    def on_enter_name(self, new_name):
        if not new_name:
            return
        if new_name == self.remote:
            return
        self.git("remote", "rename", self.remote, new_name)
        self.window.status_message("remote {} was renamed as {}.".format(self.remote, new_name))
        util.view.refresh_gitsavvy_interfaces(self.window, refresh_status_bar=False)
