from contextlib import contextmanager

import sublime
from sublime_plugin import WindowCommand

from . import intra_line_colorizer
from ..git_command import GitCommand
from ..runtime import enqueue_on_worker, enqueue_on_ui, throttled
from ..view import replace_view_content


__all__ = (
    "gs_show_commit_info",
)

MYPY = False
if MYPY:
    from typing import Dict, Tuple


storage = {}  # type: Dict[str, Tuple[float, float]]
PANEL_NAME = "show_commit_info"


def ensure_panel(window, name=PANEL_NAME, syntax="Packages/GitSavvy/syntax/show_commit.sublime-syntax"):
    # type: (sublime.Window, str, str) -> sublime.View
    output_view = window.find_output_panel(name)
    if output_view:
        return output_view

    output_view = window.create_output_panel(name)
    output_view.set_read_only(True)
    if syntax:
        output_view.set_syntax_file(syntax)
    output_view.settings().set("git_savvy.show_commit_view", True)
    return output_view


def panel_is_visible(window, name=PANEL_NAME):
    # type: (sublime.Window, str) -> bool
    return window.active_panel() == "output.{}".format(name)


def ensure_panel_is_visible(window, name=PANEL_NAME):
    # type: (sublime.Window, str) -> None
    if not panel_is_visible(window, name):
        window.run_command("show_panel", {"panel": "output.{}".format(name)})


class gs_show_commit_info(WindowCommand, GitCommand):
    def run(self, commit_hash, file_path=None):
        # We're running either blocking or lazy, and currently choose
        # automatically.  Generally, we run blocking to reduce multiple
        # UI changes in short times.  Since this panel is a companion
        # of either the graph view or a log menu panel, we e.g. run
        # *initially*, on first draw, blocking so that Sublime renders the
        # main view and this panel at once.
        # On the other hand: if the panel is open and just need to update,
        # we usually run "on_selection" or "on_highlight" change events,
        # and thus we need to be off the UI thread so the user can navigate
        # faster than we can actually render.
        if (
            not panel_is_visible(self.window) or
            ensure_panel(self.window).size() == 0
        ):
            self.run_impl(commit_hash, file_path)
        else:
            enqueue_on_worker(throttled(self.run_impl, commit_hash, file_path))

    def run_impl(self, commit_hash, file_path=None):
        output_view = ensure_panel(self.window)
        output_view.settings().set("git_savvy.repo_path", self.repo_path)

        if commit_hash:
            show_patch = self.savvy_settings.get("show_full_commit_info")
            show_diffstat = self.savvy_settings.get("show_diffstat")
            text = self.read_commit(commit_hash, file_path, show_diffstat, show_patch)
        else:
            text = ''

        enqueue_on_ui(_draw, self.window, output_view, text, commit_hash)


def _draw(window, view, text, commit):
    # type: (sublime.Window, sublime.View, str, str) -> None
    with restore_viewport_position(view, commit):
        replace_view_content(view, text)

    intra_line_colorizer.annotate_intra_line_differences(view)

    # In case we reuse a hidden panel, show the panel after updating
    # the content to reduce visual flicker.
    ensure_panel_is_visible(window)


@contextmanager
def restore_viewport_position(view, next_commit):
    prev_commit = view.settings().get("git_savvy.show_commit_view.commit")
    if prev_commit:
        storage[prev_commit] = view.viewport_position()

    yield

    view.settings().set("git_savvy.show_commit_view.commit", next_commit)
    prev_position = storage.get(next_commit, (0, 0))
    view.set_viewport_position(prev_position, animate=False)
