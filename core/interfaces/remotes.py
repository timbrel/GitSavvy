from __future__ import annotations
from contextlib import contextmanager
from itertools import chain
import os
from typing import TypedDict

from sublime_plugin import WindowCommand

from ...common import ui
from ..commands import GsNavigate
from ..git_command import GitCommand
from ..git_mixins.active_branch import Commit
from ..git_mixins.remotes import ConfigEntry, RemoteInfo, RemoteInfoBlob
from ..runtime import enqueue_on_worker
from GitSavvy.core.utils import flash


__all__ = (
    "gs_show_remotes",
    "gs_remotes_add",
    "gs_remotes_delete",
    "gs_remotes_rename",
    "gs_remotes_open_config",
    "gs_remotes_refresh",
    "gs_remotes_navigate_remote",
)


class RemotesViewState(TypedDict, total=False):
    git_root: str
    long_status: str
    recent_commits: list[Commit]
    remote_info: RemoteInfoBlob
    show_help: bool


class gs_show_remotes(WindowCommand, GitCommand):
    """
    Open a remotes dashboard for the active Git repository.
    """

    def run(self):
        ui.show_interface(self.window, self.repo_path, "remotes")


class RemotesInterface(ui.ReactiveInterface):
    """
    Remotes dashboard.
    """

    interface_type = "remotes"
    syntax_file = "Packages/GitSavvy/syntax/remotes.sublime-syntax"

    template = """\

      ROOT:    {git_root}

      BRANCH:  {branch_status}
      HEAD:    {head}

      REMOTE:
    {remote_list}
    {< help}
    """

    template_help = """
      #############
      ## ACTIONS ##
      #############

      [a] add remote
      [D] delete selected remote
      [R] rename selected remote
      [o] open config file

      [tab]       transition to next dashboard
      [shift-tab] transition to previous dashboard
      [r]         refresh
      [?]         toggle this help menu

    -
    """

    subscribe_to = {"long_status", "recent_commits", "remote_info"}
    state: RemotesViewState

    def title(self) -> str:
        return "REMOTES: {}".format(os.path.basename(self.repo_path))

    def refresh_view_state(self) -> None:
        enqueue_on_worker(self.get_latest_commits)
        enqueue_on_worker(self.get_remote_info)
        self.view.run_command("gs_update_status")

        self.update_state({
            "git_root": self.short_repo_path,
            "show_help": not self.view.settings().get("git_savvy.help_hidden"),
        })

    @contextmanager
    def keep_cursor_on_something(self):
        on_a_remote = lambda: self.cursor_is_on_something("meta.git-savvy.remotes.remote")
        was_on_a_remote = on_a_remote()
        yield
        if was_on_a_remote and not on_a_remote() or not on_a_remote():
            self.view.run_command("gs_remotes_navigate_remote")

    @ui.section("branch_status")
    def render_branch_status(self, long_status: str) -> str:
        return long_status

    @ui.section("git_root")
    def render_git_root(self, git_root: str) -> str:
        return git_root

    @ui.section("head")
    def render_head(self, recent_commits: list[Commit]) -> str:
        if not recent_commits:
            return "No commits yet."

        return "{0.hash} {0.message}".format(recent_commits[0])

    @ui.section("remote_list")
    def render_remote_list(self, remote_info: RemoteInfoBlob) -> str:
        remote_infos = remote_info.remotes
        if not remote_infos:
            return "\n  ** No remotes configured. **"

        show_markers = len(remote_infos) > 1
        name_width = max(len(remote.name) for remote in remote_infos)

        return "\n".join(
            f"\n{rendered}\n" if "\n" in rendered else rendered
            for remote in remote_infos
            if (rendered := self.render_remote(
                remote,
                show_markers,
                remote_info.push_remote,
                remote_info.integration_remote,
                name_width
            ))
        ).lstrip("\n")

    @ui.section("help")
    def render_help(self, show_help: bool) -> str:
        if not show_help:
            return ""
        return self.template_help

    def render_remote(
        self,
        remote: RemoteInfo,
        show_markers: bool,
        push_remote: str | None,
        integration_remote: str | None,
        name_width: int
    ) -> str:
        marker = self.marker_for_remote(remote.name, show_markers, push_remote, integration_remote)
        main_line = "  {} {:<{}}  {}".format(
            marker,
            remote.name,
            name_width,
            remote.url
        )
        config_lines = (
            "      {} = {}".format(line.key, line.value)
            for line in self.visible_remote_config(remote)
        )
        return "\n".join(chain([main_line], config_lines))

    def visible_remote_config(self, remote: RemoteInfo) -> list[ConfigEntry]:
        return [
            config
            for config in remote.config
            if self.should_show_remote_config(remote.name, config.key, config.value)
        ]

    def should_show_remote_config(self, remote_name: str, key: str, value: str) -> bool:
        return not (
            key == "url"
            or key == "fetch" and value == "+refs/heads/*:refs/remotes/{}/*".format(remote_name)
        )

    def marker_for_remote(
        self,
        remote_name: str,
        show_markers: bool,
        push_remote: str | None,
        integration_remote: str | None
    ) -> str:
        if not show_markers:
            return " "
        if remote_name == integration_remote:
            return "*"
        if remote_name == push_remote:
            return "▸"
        return " "


class RemotesInterfaceCommand(ui.InterfaceCommand):
    interface: RemotesInterface

    def get_selected_remote(self, allow_parent: bool = False, quiet: bool = False) -> str | None:
        remote_names = ui.extract_by_selector(
            self.view,
            "meta.git-savvy.remotes.remote.name"
        )
        if len(remote_names) == 1:
            return remote_names[0]

        if allow_parent and not remote_names:
            remote_name = self.get_parent_remote()
            if remote_name:
                return remote_name

        if not quiet:
            if not remote_names:
                flash(self.view, "No remote selected.")
            else:
                flash(self.view, "Only one remote can be selected.")
        return None

    def get_parent_remote(self) -> str | None:
        if len(self.view.sel()) != 1:
            return None

        line = self.view.line(self.view.sel()[0])
        if not any(
            section.contains(line)
            for section in self.view.get_regions(self.region_name_for("remote_list"))
        ):
            return None

        name_regions = self.view.find_by_selector("meta.git-savvy.remotes.remote.name")
        previous_names = [region for region in name_regions if region.begin() < line.begin()]
        if previous_names:
            return self.view.substr(previous_names[-1])
        return None


class gs_remotes_add(RemotesInterfaceCommand):
    def run(self, edit) -> None:
        window = self.view.window()
        if window:
            window.run_command("gs_remote_add")


class gs_remotes_delete(RemotesInterfaceCommand):
    def run(self, edit) -> None:
        if remote := self.get_selected_remote():
            window = self.view.window()
            if window:
                window.run_command("gs_remote_remove", {"remote": remote})


class gs_remotes_rename(RemotesInterfaceCommand):
    def run(self, edit) -> None:
        if remote := self.get_selected_remote():
            window = self.view.window()
            if window:
                window.run_command("gs_remote_rename", {"remote": remote})


class gs_remotes_open_config(RemotesInterfaceCommand):
    def run(self, edit) -> None:
        window = self.view.window()
        if window:
            remote = self.get_selected_remote(allow_parent=True, quiet=True)
            window.run_command(
                "gs_open_repo_config",
                {"highlight": '[remote "{}"]'.format(remote)} if remote else None
            )


class gs_remotes_refresh(RemotesInterfaceCommand):
    def run(self, edit) -> None:
        self.interface.render()


class gs_remotes_navigate_remote(GsNavigate):

    """
    Move cursor to the next (or previous) selectable remote in the dashboard.
    """
    offset = 0

    def get_available_regions(self):
        return self.view.find_by_selector("meta.git-savvy.remotes.remote.name")
