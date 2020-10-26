from sublime_plugin import TextCommand
from ...core.git_command import GitCommand


__all__ = (
    "gs_handle_vintageous",
    "gs_handle_arrow_keys"
)


class gs_handle_vintageous(TextCommand, GitCommand):

    """
    Set the vintageous_friendly view setting if needed.
    Enter insert mode if vintageous_enter_insert_mode option is enabled.
    """

    def run(self, edit):
        if self.savvy_settings.get("vintageous_friendly"):
            self.view.settings().set("git_savvy.vintageous_friendly", True)
            if self.savvy_settings.get("vintageous_enter_insert_mode"):
                self.view.settings().set("vintageous_reset_mode_when_switching_tabs", False)
                # NeoVintageous renamed the command starting with v1.22.0.
                # We call both commands for backwards compatibility.
                self.view.run_command("_enter_insert_mode")
                self.view.run_command("nv_enter_insert_mode")  # since NeoVintageous 1.22.0


class gs_handle_arrow_keys(TextCommand, GitCommand):

    """
    Set the arrow_keys_navigation view setting if needed.
    It allows navigation by using arrow keys.
    """

    def run(self, edit):
        if self.savvy_settings.get("arrow_keys_navigation"):
            self.view.settings().set("git_savvy.arrow_keys_navigation", True)
