from __future__ import annotations

import sublime
from sublime_plugin import EventListener

from .. import command_log
from ..base_commands import GsWindowCommand


__all__ = (
    "gs_show_command_log",
    "GsCommandLogEventListener",
)


class gs_show_command_log(GsWindowCommand):
    def run(self) -> None:
        command_log.show(self.window)


class GsCommandLogEventListener(EventListener):
    def on_post_window_command(
        self,
        window: sublime.Window,
        command_name: str,
        args: dict | None
    ) -> None:
        if (
            command_name == "show_panel"
            and args
            and args.get("panel") == "output.{}".format(command_log.PANEL_NAME)
        ):
            command_log.panel_opened()

    def on_pre_close_window(self, window: sublime.Window) -> None:
        command_log.window_closed(window.id())
