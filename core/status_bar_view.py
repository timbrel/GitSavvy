from functools import partial

import sublime
import sublime_plugin

from .git_command import repo_path_for_view_if_cached
from .settings import GitSavvySettings
from .state import current_state, subscribe

if False:
    from typing import Callable, Iterable, Optional, TypeVar
    from .state import RepoPath, RepoStatus
    T = TypeVar('T')


filter_ = partial(filter, None)  # type: (Callable[[Iterable[Optional[T]]], Iterable[T]])


class GsStatusBarEventListener(sublime_plugin.EventListener):
    # Note: We listen for 'on_activated' bc we must draw on the main
    # thread for the happy path to avoid visual yank on the status bar.
    # We also only draw if the repo path is cached bc we must avoid
    # doing expensive work on the main thread.
    def on_activated(self, view):
        repo_path = repo_path_for_view_if_cached(view)
        if repo_path:
            status = current_state(repo_path)
            render(view, status)


def render_status(repo_path, status):
    # type: (RepoPath, RepoStatus) -> None
    for v in active_views():
        if repo_path_for_view_if_cached(v) == repo_path:
            render(v, status)


def active_views():
    # type: () -> Iterable[sublime.View]
    return filter_(w.active_view() for w in sublime.windows())


def render(view, status):
    # type: (sublime.View, RepoStatus) -> None
    if not GitSavvySettings().get("git_status_in_status_bar"):
        return

    short_status = status.get('short_status')
    if short_status:
        view.set_status("gitsavvy-repo-status", short_status)
    else:
        view.erase_status("gitsavvy-repo-status")


subscribe('status_bar_updater', render_status)
