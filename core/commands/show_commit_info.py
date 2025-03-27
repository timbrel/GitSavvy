from collections import defaultdict
from contextlib import contextmanager

import sublime
from sublime_plugin import WindowCommand

from . import diff
from . import intra_line_colorizer
from ..git_command import GitCommand
from ..runtime import enqueue_on_worker, ensure_on_ui, throttled
from ..view import replace_view_content


__all__ = (
    "gs_show_commit_info",
)

from typing import DefaultDict, Dict, Iterator, List, Tuple
ViewportPosition = Tuple[float, float]
Selection = List[sublime.Region]


storage = defaultdict(dict)  # type: DefaultDict[sublime.View, Dict[str, Tuple[ViewportPosition, Selection]]]
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


def show_panel(window: sublime.Window, name: str = PANEL_NAME) -> None:
    window.run_command("show_panel", {"panel": "output.{}".format(PANEL_NAME)})


def panel_is_visible(window, name=PANEL_NAME):
    # type: (sublime.Window, str) -> bool
    return window.active_panel() == "output.{}".format(name)


def panel_belongs_to_graph(panel):
    # type: (sublime.View) -> bool
    return panel.settings().get("git_savvy.show_commit_view.belongs_to_a_graph", False)


def ensure_panel_is_visible(window, name=PANEL_NAME):
    # type: (sublime.Window, str) -> None
    if not panel_is_visible(window, name):
        window.run_command("show_panel", {"panel": "output.{}".format(name)})


class gs_show_commit_info(WindowCommand, GitCommand):
    def run(self, commit_hash, file_path=None, from_log_graph=False):
        commit_hash = self.get_short_hash(commit_hash)

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
            self.run_impl(commit_hash, file_path, from_log_graph)
        else:
            enqueue_on_worker(throttled(self.run_impl, commit_hash, file_path, from_log_graph))

    def run_impl(self, commit_hash, file_path=None, from_log_graph=False):
        output_view = ensure_panel(self.window)
        settings = output_view.settings()
        settings.set("git_savvy.repo_path", self.repo_path)
        settings.set("git_savvy.show_commit_view.belongs_to_a_graph", from_log_graph)

        settings.set("result_file_regex", diff.FILE_RE)
        settings.set("result_line_regex", diff.LINE_RE)
        settings.set("result_base_dir", self.repo_path)

        if commit_hash:
            show_patch = self.savvy_settings.get("show_full_commit_info")
            show_diffstat = self.savvy_settings.get("show_diffstat")
            text = self.read_commit(commit_hash, file_path, show_diffstat, show_patch)
        else:
            text = ''

        ensure_on_ui(_draw, self.window, output_view, text, commit_hash, from_log_graph)


def _draw(window, view, text, commit, from_log_graph):
    # type: (sublime.Window, sublime.View, str, str, bool) -> None
    with restore_viewport_position(view, commit):
        replace_view_content(view, text)

    intra_line_colorizer.annotate_intra_line_differences(view)

    # In case we reuse a hidden panel, show the panel *after* updating
    # the content to reduce visual flicker.  This is only ever useful
    # if `show_commit_info` is used to enhance a quick panel.  For the
    # graph view `_draw` effectively runs as a by-product of calling
    # `show_panel`, thus `ensure_panel_is_visible` is not needed in
    # that case.
    if not from_log_graph:
        ensure_panel_is_visible(window)


@contextmanager
def restore_viewport_position(view, next_commit):
    # type: (sublime.View, str) -> Iterator
    remember_view_state(view)
    view.settings().set("git_savvy.show_commit_view.commit", next_commit)
    yield
    restore_view_state(view, next_commit)


def remember_view_state(view):
    # type: (sublime.View) -> None
    prev_commit = view.settings().get("git_savvy.show_commit_view.commit")
    if prev_commit:
        storage[view][prev_commit] = (view.viewport_position(), [r for r in view.sel()])


def restore_view_state(view, next_commit):
    # type: (sublime.View, str) -> None
    prev_position, prev_sel = storage[view].get(next_commit, ((0, 0), [sublime.Region(0)]))
    view.set_viewport_position(prev_position, animate=False)
    view.sel().clear()
    view.sel().add_all(prev_sel)
