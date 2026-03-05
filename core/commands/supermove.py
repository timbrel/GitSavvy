from __future__ import annotations

import sublime
from sublime_plugin import TextCommand


__all__ = (
    "gs_super_next",
    "gs_super_prev",
)


class gs_super_next(TextCommand):
    """Context-aware "next" navigation across GitSavvy views.

    This is intended as a single command that you can bind once (e.g. to
    ``ctrl+.``) and have it do the "right thing" in inline diffs, diff views,
    commit views, show-commit views, file-at-commit views, and regular
    buffers.
    """

    def is_enabled(self) -> bool:
        view = self.view
        if _is_dired_view(view):
            return False
        return len(view.sel()) > 0

    def run(self, edit: sublime.Edit) -> None:
        delegate(self.view, forward=True)


class gs_super_prev(TextCommand):
    """Context-aware "previous" navigation across GitSavvy views.

    See :class:`gs_super_next` for the resolution order.
    """

    def is_enabled(self) -> bool:
        view = self.view
        if _is_dired_view(view):
            return False
        return len(view.sel()) > 0

    def run(self, edit: sublime.Edit) -> None:
        delegate(self.view, forward=False)


def _is_dired_view(view: sublime.View) -> bool:
    settings = view.settings()
    if settings.get("dired_input_panel"):
        return True
    # mirror your keymap’s selector != "text.dired"
    return view.match_selector(0, "text.dired")


def delegate(view: sublime.View, forward: bool) -> None:
    """Dispatch to the appropriate navigation command for the current view.

    The resolution order mirrors the existing keymap setup:

    1. Inline diff view           -> gs_inline_diff_navigate_hunk
    2. Commit view                -> gs_commit_view_navigate
    3. Line history view          -> gs_line_history_navigate
    4. GitSavvy diff-ish views
       (status/diff, show-commit) -> gs_diff_navigate
    5. File-at-commit view        -> gs_next_hunk / gs_prev_hunk
    6. Plain views / fallback     -> gs_next_hunk / gs_prev_hunk
    """
    settings = view.settings()

    # Inline diff view (interactive diff inside the file buffer)
    if settings.get("git_savvy.inline_diff_view"):
        view.run_command("gs_inline_diff_navigate_hunk", {"forward": forward})
        return

    # Commit view: navigate the embedded patch plus a virtual BOF position so we
    # can always jump back to the commit message.
    if settings.get("git_savvy.commit_view"):
        view.run_command("gs_commit_view_navigate", {"forward": forward})
        return

    # Line history view: navigate between commits and hunks in a very special odd way.
    if settings.get("git_savvy.line_history_view"):
        view.run_command("gs_line_history_navigate", {"forward": forward})
        return

    # Dedicated GitSavvy diff views (status/diff and read-only show-commit views).
    # All of these expose their hunks via SplittedDiff and use gs_diff_navigate as
    # the canonical navigator.
    if (
        settings.get("git_savvy.diff_view")
        or settings.get("git_savvy.show_commit_view")
        or settings.get("git_savvy.stash_view")
    ):
        view.run_command("gs_diff_navigate", {"forward": forward})
        return

    # Single-file-at-commit view. Here we rely on Sublime's reference document
    # machinery, and the existing gs_next_hunk / gs_prev_hunk helpers that
    # jump between modifications (i.e. hunks) for that file.
    if settings.get("git_savvy.show_file_at_commit_view"):
        view.run_command("gs_next_hunk" if forward else "gs_prev_hunk")
        return

    # Fallback: use the generic next/prev hunk navigation which is based on
    # Sublime's next_modification / prev_modification commands.
    view.run_command("gs_next_hunk" if forward else "gs_prev_hunk")
