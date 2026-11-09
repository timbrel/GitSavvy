from __future__ import annotations

import sublime
from sublime_plugin import TextCommand, EventListener

from ..git_command import GitCommand, find_repo_path_for_view
from ..runtime import run_when_worker_is_idle, throttled
from ..settings import app_settings
from GitSavvy.core import store


STATUSBAR_KEY = "gitsavvy-repo-status"


class GsStatusBarEventListener(EventListener):
    def on_activated(self, view):
        view.run_command("gs_draw_status_bar")

    def on_post_save(self, view):
        view.run_command("gs_update_status")


class gs_update_status(TextCommand, GitCommand):
    def run(self, edit):
        run_when_worker_is_idle(throttled(self.run_impl, self.view))

    def run_impl(self, view):
        repo_path = self.find_repo_path()
        if repo_path:
            try:
                self.update_working_dir_status()
            except RuntimeError:
                # Although with `if repo_path` we have enough to make the
                # status call to git safe, the processing of the status
                # asks `self.repo_path` multiple times.
                # A user might have closed the view in between so we MUST
                # catch potential `RuntimeError`s.
                pass


class gs_draw_status_bar(TextCommand, GitCommand):

    """
    Update the short Git status and GitHub CI status in the Sublime status bar.
    """

    def run(self, edit) -> None:
        view = self.view
        repo_path = self.find_repo_path()
        text = compose_status(store.current_state(repo_path)) if repo_path else ""
        if text:
            view.set_status(STATUSBAR_KEY, text)
        else:
            view.erase_status(STATUSBAR_KEY)


def compose_status(state: store.RepoStore) -> str:
    """
    Join the independently enabled Git and GitHub CI components.
    Draws only from the store; never runs git or computes timings.
    """
    git_status = (
        state.get("short_status", "")
        if app_settings.get("git_status_in_status_bar")
        else ""
    )
    ci_status = (
        select_ci_status(state)
        if app_settings.get("github_ci_status_in_status_bar", True)
        else ""
    )
    return " ".join(filter(None, (git_status, ci_status)))


def select_ci_status(state: store.RepoStore) -> str:
    """
    Select the published CI text for the current HEAD: by branch and
    commit when attached, by commit alone when detached.
    """
    head = state.get("head")
    cache = state.get("github_ci_status")
    if not head or not head.commit_hash or not cache:
        return ""

    if not head.detached:
        return cache.get((head.branch, head.commit_hash), "")

    # Prefer the most recently published observation of that commit.
    for (_, commit_hash), text in reversed(cache.items()):
        if commit_hash == head.commit_hash:
            return text
    return ""


def on_status_update(repo_path: str, state: store.RepoStore) -> None:
    view = sublime.active_window().active_view()
    if view and find_repo_path_for_view(view) == repo_path:
        view.run_command("gs_draw_status_bar")


store.subscribe("*", {"short_status", "head", "github_ci_status"}, on_status_update)
