from functools import partial
import threading
import uuid

import sublime
from sublime_plugin import TextCommand, EventListener

from ..git_command import GitCommand


def view_is_transient(view: sublime.View) -> bool:
    """Return whether a view can be considered 'transient'.

    For our purpose, transient views are 'detached' views or widgets
    (aka quick or input panels).
    """

    # 'Detached' (already closed) views don't have a window.
    window = view.window()
    if not window:
        return True

    # Widgets are normal views but the typical getters don't list them.
    group, index = window.get_view_index(view)
    if group == -1:
        return True

    return False


if False:
    from typing import Callable, Dict, Optional


current_token = {}  # type: Dict[sublime.ViewId, str]
_lock = threading.Lock()


def maybe_update_status_bar(view):
    # type: (sublime.View) -> None
    if view_is_transient(view):
        return

    vid = view.id()
    with _lock:
        current_token[vid] = token = uuid.uuid4().hex
    sink = partial(update_status_bar, view)
    sublime.set_timeout_async(partial(executor, sink, vid, token))


def update_status_bar(view):
    # type: (sublime.View) -> None
    invalidate_token(view)
    git = make_git(view)
    render(view, fetch_status(git))


def executor(sink, vid, token):
    # type: (Callable[[], None], sublime.ViewId, str) -> None
    if current_token.get(vid) == token:
        sink()


def invalidate_token(view):
    # type: (sublime.View) -> None
    with _lock:
        current_token.pop(view.id(), None)


def make_git(view):
    # type: (sublime.View) -> GitCommand
    git = GitCommand()
    setattr(git, 'view', view)
    return git


def fetch_status(git):
    # type: (GitCommand) -> Optional[str]
    try:
        git.get_repo_path(offer_init=False)  # Yeah, LOL, it's a *getter*
        return git.get_branch_status_short()
    except Exception:
        return None


def render(view, status):
    # type: (sublime.View, Optional[str]) -> None
    if status:
        view.set_status("gitsavvy-repo-status", status)
    else:
        view.erase_status("gitsavvy-repo-status")


class GsStatusBarEventListener(EventListener):
    on_new_async = staticmethod(maybe_update_status_bar)
    on_activated_async = staticmethod(maybe_update_status_bar)
    on_post_save_async = staticmethod(maybe_update_status_bar)


class GsUpdateStatusBarCommand(TextCommand, GitCommand):
    """Record intent to update the status bar."""
    def run(self, edit):
        sublime.set_timeout_async(partial(maybe_update_status_bar, self.view))
