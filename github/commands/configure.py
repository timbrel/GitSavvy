from __future__ import annotations
from itertools import chain

import sublime
from sublime_plugin import WindowCommand

from ..git_mixins import GithubRemotesMixin
from GitSavvy.core.git_command import GitCommand
from GitSavvy.core.git_mixins.branches import Branch
from GitSavvy.core.ui_mixins.quick_panel import show_remote_panel
from GitSavvy.core.utils import hprint, show_busy_panel, show_noop_panel, show_panel, AnimatedText


__all__ = (
    "gs_github_configure_remote",
)


class gs_github_configure_remote(WindowCommand, GithubRemotesMixin, GitCommand):
    def run(self):
        remotes = self.get_remotes()
        if len(remotes) == 0:
            self.persist_integration(None, None)
            show_noop_panel(self.window, "No remotes are defined")
            return

        config = self.read_gitsavvy_config()
        currently_set_remote = config.get("ghremote")
        currently_set_branch = config.get("ghbranch")

        def on_done(remote_name):
            self.ask_for_default_branch(
                remote_name,
                (
                    # if the remote_name changes, a possible set branch is invalid
                    None if remote_name != currently_set_remote
                    else currently_set_branch
                ),
                len(remotes) == 1
            )

        show_remote_panel(on_done, remotes=remotes, allow_direct=True)

    def ask_for_default_branch(
        self,
        remote_name: str,
        currently_set_branch: str | None,
        only_one_remote_defined: bool,
        _hide_overlay: bool = False
    ):
        """Determine the default branch of the given remote."""
        default_branch = self.guess_default_branch(remote_name)
        branches = [b for b in self.get_branches() if b.remote == remote_name]

        a = (i for i, b in enumerate(branches) if b.name == currently_set_branch)
        b = (i for i, b in enumerate(branches) if b.canonical_name == default_branch)
        selected = next(chain(a, b, [-1]))

        items = [
            sublime.QuickPanelItem(
                b.canonical_name,
                annotation="(default)" if b.canonical_name == default_branch else "")
            for b in branches
        ] + [f"Refresh `{remote_name}`"]

        def on_done(idx):
            if idx == len(items) - 1:
                show_busy_panel(
                    self.window,
                    AnimatedText(
                        f"Refreshing `{remote_name}`...",
                        f"Refreshing `{remote_name}`.. ",
                        tick=0.3
                    ),
                    task=lambda: self.fetch(remote_name),
                    kont=lambda: self.ask_for_default_branch(
                        remote_name, currently_set_branch, only_one_remote_defined
                    )
                )
                return

            branch = branches[idx]
            if branch.canonical_name == default_branch:
                hprint(
                    f"Skip forcing the integration branch to {branch.canonical_name} "
                    f"as that's the default.")
                if only_one_remote_defined:
                    self.persist_integration(None, None)
                    self.window.status_message("Unset all integration settings.")
                else:
                    self.persist_integration(remote_name, None)
                    self.window.status_message(
                        f"Configured {remote_name} to be the integration remote."
                    )
            else:
                self.persist_integration(remote_name, branch)
                self.window.status_message(
                    f"Configured {branch.canonical_name} to be the integration branch."
                )

        show_panel(self.window, items, on_done, selected_index=selected)

    def persist_integration(self, remote_name: str | None, branch: Branch | None) -> None:
        self.git("config", "--local", "--unset-all", "GitSavvy.ghRemote", throw_on_error=False)
        self.git("config", "--local", "--unset-all", "GitSavvy.ghBranch", throw_on_error=False)
        if remote_name:
            self.git("config", "--local", "--add", "GitSavvy.ghRemote", remote_name)
        if branch:
            self.git("config", "--local", "--add", "GitSavvy.ghBranch", branch.name)

    def guess_default_branch(self, remote_name) -> str | None:
        if contents := self._read_git_file("refs", "remotes", remote_name, "HEAD"):
            # ref: refs/remotes/origin/master
            for line in contents.splitlines():
                line = line.strip()
                if line.startswith("ref: refs/remotes/"):
                    return line[18:]

        try:
            output = self.git(
                "ls-remote", "--symref", remote_name, "HEAD",
                timeout=2.0,
                throw_on_error=False,
                show_panel_on_error=False,
            )
        except Exception:
            return None
        else:
            for line in output.splitlines():
                # ref: refs/heads/master  HEAD
                line = line.strip()
                if line.startswith("ref: refs/heads/"):
                    return remote_name + line[16:].split()[0]
        return None
