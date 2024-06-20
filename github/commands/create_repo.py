from functools import partial
import os

from GitSavvy.common import util
from GitSavvy.core.base_commands import GsWindowCommand
from GitSavvy.core.ui_mixins.input_panel import show_single_line_input_panel
from GitSavvy.core.utils import show_panel
from GitSavvy.core.runtime import on_worker
from GitSavvy.github import github

from GitSavvy.core.base_commands import Args, GsCommand, Kont


__all__ = (
    "gs_github_create_repo",
)


def ask_for_repo_name(cmd: GsCommand, args: Args, done: Kont) -> None:
    suggestion = (
        os.path.basename(folders[0])
        if (folders := cmd.window.folders())
        else ""
    )

    def on_done(name: str) -> None:
        if name:
            done(name)

    show_single_line_input_panel("New Repo Name:", suggestion, on_done)


def get_github_user_token(cmd: GsCommand, args: Args, done: Kont) -> None:
    fqdn = "github.com"
    token = cmd.savvy_settings.get("api_tokens", {}).get(fqdn)
    if not token:
        cmd.window.status_message(f"Abort, no API token found in the settings for {fqdn}.")
        return
    done(token)


class gs_github_create_repo(GsWindowCommand):
    defaults = {
        "token": get_github_user_token,
        "name": ask_for_repo_name
    }

    @on_worker
    def run(self, token: str, name: str) -> None:
        payload = github.create_user_repo(token, name)
        self.window.status_message("The repo was created successfully.")
        urls = [payload["clone_url"], payload["ssh_url"]]

        def on_remote_name(name: str) -> None:
            show_panel(self.window, urls, partial(on_url, name))

        def on_url(name: str, idx: int) -> None:
            url = urls[idx]
            self.git("remote", "add", name, url)
            self.window.status_message("The new remote was added successfully.")
            util.view.refresh_gitsavvy_interfaces(self.window)

        show_single_line_input_panel("Add repo as", "origin", on_remote_name)
