from __future__ import annotations
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
    Add a git remote.

    If `url` is omitted, prompt for a URL and then a remote name.
    `ignore_tags` configures the remote for branch-only fetch/push use,
    `follow_their_head` controls whether Git should track the remote HEAD,
    and `set_as_push_default` stores the remote as GitSavvy's push target.
    """

    def run(
        self,
        url: str | None = None,
        set_as_push_default: bool = False,
        ignore_tags: bool = False,
        follow_their_head: bool = True
    ) -> None:
        self.follow_their_head = follow_their_head
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

    def on_enter_remote(self, input_url: str) -> None:
        self.url = input_url
        owner = self.username_from_url(input_url)

        show_single_line_input_panel("Remote name", owner, self.on_enter_name)

    def on_enter_name(self, remote_name: str) -> None:
        self.git("remote", "add", remote_name, self.url)
        if self.ignore_tags:
            self.git("config", f"remote.{remote_name}.push", "+refs/heads/*:refs/heads/*")
            self.git("config", f"remote.{remote_name}.tagOpt", "--no-tags")
        if not self.follow_their_head:
            self.git("config", f"remote.{remote_name}.followRemoteHEAD", "never")
            if default_branch := self.guess_default_branch(remote_name):
                self.git("config", "--add", f"remote.{remote_name}.fetch", f"^refs/heads/{default_branch}")
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
    Remove a git remote.

    If `remote` is omitted, prompt for one.  Also remove remaining
    local remote-tracking refs for the deleted remote.
    """

    def run(self, remote: str | None = None) -> None:
        if remote:
            self.on_remote_selection(remote)
        else:
            show_remote_panel(self.on_remote_selection, show_url=True)

    @util.actions.destructive(description="remove a remote")
    def on_remote_selection(self, remote: str) -> None:
        self.git("remote", "remove", remote)
        self.remove_remote_tracking_refs(remote)
        util.view.refresh_gitsavvy_interfaces(self.window, refresh_status_bar=False)

    def remove_remote_tracking_refs(self, remote: str) -> None:
        prefix = f"refs/remotes/{remote}/"
        refs = self.git("for-each-ref", "--format=%(refname)", prefix).splitlines()
        for ref in refs:
            if ref.startswith(prefix):
                self.git("update-ref", "--no-deref", "-d", ref)


class gs_remote_rename(GsWindowCommand):
    """
    Rename a git remote.

    If `remote` is omitted, prompt for one.  Then prompt for the new name.
    """

    def run(self, remote: str | None = None) -> None:
        if remote:
            self.on_remote_selection(remote)
        else:
            show_remote_panel(self.on_remote_selection, show_url=True)

    def on_remote_selection(self, remote: str) -> None:
        self.remote = remote
        show_single_line_input_panel("New name", remote, self.on_enter_name)

    def on_enter_name(self, new_name: str) -> None:
        if not new_name:
            return
        if new_name == self.remote:
            return
        self.git("remote", "rename", self.remote, new_name)
        self.window.status_message("remote {} was renamed as {}.".format(self.remote, new_name))
        util.view.refresh_gitsavvy_interfaces(self.window, refresh_status_bar=False)
