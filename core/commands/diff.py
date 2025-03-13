"""
Implements a special view to visualize and stage pieces of a project's
current diff.
"""

from collections import defaultdict
from contextlib import contextmanager
from functools import partial
from itertools import chain, count, groupby, takewhile
import os

import sublime
from sublime_plugin import WindowCommand, TextCommand, EventListener

from . import inline_diff
from . import intra_line_colorizer
from . import stage_hunk
from .navigate import GsNavigate
from ..fns import head, filter_, flatten, unique
from ..parse_diff import SplittedDiff
from ..git_command import GitCommand
from ..runtime import ensure_on_ui, enqueue_on_worker, throttled
from ..ui_mixins.quick_panel import LogHelperMixin
from ..utils import flash, focus_view, hprint, line_indentation, show_panel
from ..view import (
    capture_cur_position, clamp, replace_view_content, scroll_to_pt,
    place_view, place_cursor_and_show, y_offset, Position)
from ...common import util


__all__ = (
    "gs_diff",
    "gs_diff_refresh",
    "gs_diff_intent_to_add",
    "gs_diff_toggle_setting",
    "gs_diff_toggle_cached_mode",
    "gs_diff_switch_files",
    "gs_diff_grab_quick_panel_view",
    "gs_diff_zoom",
    "gs_diff_stage_or_reset_hunk",
    "gs_initiate_fixup_commit",
    "gs_diff_open_file_at_hunk",
    "gs_diff_navigate",
    "gs_diff_undo",
    "GsDiffFocusEventListener",
)


from typing import (
    Callable, Dict, Iterable, Iterator, Literal, List, NamedTuple, Optional, Set,
    Tuple, TypeVar
)
from ..parse_diff import FileHeader, Hunk, HunkLine, TextRange
from ..types import LineNo, ColNo
from ..git_mixins.history import LogEntry
T = TypeVar('T')
Point = int
LineCol = Tuple[LineNo, ColNo]
_FileName = str
Position_ = Tuple[Position, _FileName]


class HunkLineWithB(NamedTuple):
    line: HunkLine
    b: LineNo


DIFF_TITLE = "DIFF: {}"
DIFF_CACHED_TITLE = "DIFF: {}, STAGE"
DECODE_ERROR_MESSAGE = """
-- Can't decode diff output. --

You may have checked in binary data git doesn't detect, or not UTF-8
encoded files. In the latter case use the "fallback_encoding" setting,
in the former you may want to edit the `.gitattributes` file.

Note, however, that diffs with *mixed* encodings are not supported.
"""


# Clickable lines:
# (A)  common/commands/view_manipulation.py  |   1 +
# (B) --- a/common/commands/view_manipulation.py
# (C) +++ b/common/commands/view_manipulation.py
# (D) diff --git a/common/commands/view_manipulation.py b/common/commands/view_manipulation.py
FILE_RE = (
    r"^(?:\s(?=.*\s+\|\s+\d+\s)|--- a\/|\+{3} b\/|diff .+b\/)"
    #     ^^^^^^^^^^^^^^^^^^^^^ (A)
    #     ^ one space, and then somewhere later on the line the pattern `  |  23 `
    #                           ^^^^^^^ (B)
    #                                   ^^^^^^^^ (C)
    #                                            ^^^^^^^^^^^ (D)
    r"(\S[^|]*?)"
    #         ^ ! lazy to not match the trailing spaces, see below

    r"(?:\s+\||$)"
    #          ^ (B), (C), (D)
    #    ^^^^^ (A) We must match the spaces here bc Sublime will not rstrip() the
    #    filename for us.
)

# Clickable line:
# @@ -69,6 +69,7 @@ class GsHandleVintageousCommand(TextCommand):
#           ^^ we want the second (current) line offset of the diff
LINE_RE = r"^@@ [^+]*\+(\d+)"
active_on_activated = True


def compute_identifier_for_view(view):
    # type: (sublime.View) -> Optional[Tuple]
    settings = view.settings()
    return (
        settings.get('git_savvy.repo_path'),
        settings.get('git_savvy.file_path'),
        settings.get('git_savvy.diff_view.base_commit'),
        settings.get('git_savvy.diff_view.target_commit')
    ) if settings.get('git_savvy.diff_view') else None


def is_diff_view(view):
    # type: (sublime.View) -> bool
    return view.settings().get('git_savvy.diff_view')


@contextmanager
def disabled_on_activated():
    global active_on_activated
    active_on_activated = False
    try:
        yield
    finally:
        active_on_activated = True


class gs_diff(WindowCommand, GitCommand):

    """
    Create a new view to display the difference of `target_commit`
    against `base_commit`. If `target_commit` is None, compare
    working directory with `base_commit`.  If `in_cached_mode` is set,
    display a diff of the Git index. Set `disable_stage` to True to
    disable Ctrl-Enter in the diff view.
    """

    def run(
        self,
        repo_path=None,
        file_path=None,
        in_cached_mode=None,  # type: Optional[bool]
        current_file=False,
        base_commit=None,
        target_commit=None,
        disable_stage=False,
        title=None,
        ignore_whitespace=False,
        context_lines=3
    ):
        # type: (...) -> None
        if repo_path is None:
            repo_path = self.repo_path
        assert repo_path
        if current_file:
            file_path = self.file_path or file_path

        active_view = self.window.active_view()
        assert active_view

        this_id = (
            repo_path,
            file_path,
            base_commit,
            target_commit
        )
        active_views_id = compute_identifier_for_view(active_view) or ()
        if (
            active_views_id[:2] == this_id[:2]
            if base_commit is None and target_commit is None
            else active_views_id == this_id
        ) and (
            in_cached_mode is None
            or active_view.settings().get('git_savvy.diff_view.in_cached_mode') == in_cached_mode
        ):
            active_view.close()
            return

        cur_pos = None
        if active_view.settings().get("git_savvy.inline_diff_view"):
            if base_commit is None and target_commit is None:
                base_commit = active_view.settings().get("git_savvy.inline_diff_view.base_commit")
                target_commit = active_view.settings().get("git_savvy.inline_diff_view.target_commit")
                disable_stage = base_commit or target_commit
            if in_cached_mode is None:
                in_cached_mode = active_view.settings().get("git_savvy.inline_diff_view.in_cached_mode")
            if _cur_pos := capture_cur_position(active_view):
                rel_file_path = self.get_rel_path(file_path)
                row, col, offset = _cur_pos
                line_no, col_no = inline_diff.translate_pos_from_diff_view_to_file(active_view, row + 1, col + 1)
                cur_pos = Position(line_no - 1, col_no - 1, offset), rel_file_path

        elif active_view.settings().get("git_savvy.show_file_at_commit_view"):
            if base_commit is None and target_commit is None:
                target_commit = active_view.settings().get("git_savvy.show_file_at_commit_view.commit")
                base_commit = self.previous_commit(target_commit, file_path)
                disable_stage = True
            if _cur_pos := capture_cur_position(active_view):
                rel_file_path = self.get_rel_path(file_path)
                cur_pos = _cur_pos, rel_file_path

        elif av_fname := active_view.file_name():
            if _cur_pos := capture_cur_position(active_view):
                rel_file_path = self.get_rel_path(av_fname)
                if in_cached_mode:
                    row, col, offset = _cur_pos
                    new_row = self.find_matching_lineno(None, None, row + 1, file_path) - 1
                    cur_pos = Position(new_row, col, offset), rel_file_path
                else:
                    cur_pos = _cur_pos, rel_file_path

        for view in self.window.views():
            if compute_identifier_for_view(view) == this_id:
                diff_view = view
                if in_cached_mode is not None:
                    diff_view.settings().set("git_savvy.diff_view.in_cached_mode", in_cached_mode)

                with disabled_on_activated():
                    focus_view(diff_view)
                place_view(self.window, diff_view, after=active_view)
                break

        else:
            if not title:
                title = (DIFF_CACHED_TITLE if in_cached_mode else DIFF_TITLE).format(
                    os.path.basename(file_path) if file_path else os.path.basename(repo_path)
                )
            show_diffstat = self.savvy_settings.get("show_diffstat", True)
            diff_view = util.view.create_scratch_view(self.window, "diff", {
                "title": title,
                "syntax": "Packages/GitSavvy/syntax/diff_view.sublime-syntax",
                "git_savvy.repo_path": repo_path,
                "git_savvy.file_path": file_path,
                "git_savvy.diff_view.in_cached_mode": bool(in_cached_mode),
                "git_savvy.diff_view.ignore_whitespace": ignore_whitespace,
                "git_savvy.diff_view.context_lines": context_lines,
                "git_savvy.diff_view.base_commit": base_commit,
                "git_savvy.diff_view.target_commit": target_commit,
                "git_savvy.diff_view.show_diffstat": show_diffstat,
                "git_savvy.diff_view.disable_stage": disable_stage,
                "git_savvy.diff_view.history": [],
                "git_savvy.diff_view.just_hunked": "",
                "result_file_regex": FILE_RE,
                "result_line_regex": LINE_RE,
                "result_base_dir": repo_path,
            })
            diff_view.run_command("gs_handle_vintageous")

        # Assume diffing a single file is very fast and do it
        # sync because it looks better.
        diff_view.run_command("gs_diff_refresh", {
            "sync": bool(file_path),
            "match_position": cur_pos
        })


class gs_diff_refresh(TextCommand, GitCommand):
    """Refresh the diff view with the latest repo state."""

    def run(self, edit, sync=True, match_position=None):
        if sync:
            self.run_impl(sync, match_position)
        else:
            enqueue_on_worker(self.run_impl, sync, match_position)

    def run_impl(self, runs_on_ui_thread, match_position):
        view = self.view
        if not runs_on_ui_thread and not view.is_valid():
            return

        settings = view.settings()
        repo_path = settings.get("git_savvy.repo_path")
        file_path = settings.get("git_savvy.file_path")
        in_cached_mode = settings.get("git_savvy.diff_view.in_cached_mode")
        ignore_whitespace = settings.get("git_savvy.diff_view.ignore_whitespace")
        base_commit = settings.get("git_savvy.diff_view.base_commit")
        target_commit = settings.get("git_savvy.diff_view.target_commit")
        show_diffstat = settings.get("git_savvy.diff_view.show_diffstat")
        disable_stage = settings.get("git_savvy.diff_view.disable_stage")
        context_lines = settings.get('git_savvy.diff_view.context_lines')

        def run_diff() -> bytes:
            return self.git(
                "diff",
                "--ignore-all-space" if ignore_whitespace else None,
                "--unified={}".format(context_lines) if context_lines is not None else None,
                "--stat" if show_diffstat else None,
                "--patch",
                "--no-color",
                "--cached" if in_cached_mode else None,
                base_commit,
                target_commit,
                "--",
                file_path,
                decode=False
            )

        raw_diff = run_diff()
        untracked_file = (
            not raw_diff
            and file_path
            # Only check the cached value in `store` to not get expensive
            # for the normal case of just checking a clean file.
            and self.is_probably_untracked_file(file_path)
        )
        if untracked_file:
            self.intent_to_add(file_path)
            try:
                raw_diff = run_diff()
            finally:
                self.undo_intent_to_add(file_path)

        try:
            diff = self.strict_decode(raw_diff)
        except UnicodeDecodeError:
            diff = DECODE_ERROR_MESSAGE
            diff += "\n-- Partially decoded output follows; � denotes decoding errors --\n\n"
            diff += raw_diff.decode("utf-8", "replace")

        if not diff and settings.get("git_savvy.diff_view.just_hunked"):
            history = self.view.settings().get("git_savvy.diff_view.history") or [[[]]]
            if history[-1][0][1:3] != ["-R", None]:  # not when discarding
                view.run_command("gs_diff_toggle_cached_mode")
                return

        if settings.get("git_savvy.just_committed"):
            if diff:
                settings.set("git_savvy.just_committed", False)
            else:
                if in_cached_mode:
                    settings.set("git_savvy.diff_view.in_cached_mode", False)
                    view.run_command("gs_diff_refresh")
                else:
                    view.close()
                return

        prelude = "\n"
        title = (DIFF_CACHED_TITLE if in_cached_mode else DIFF_TITLE).format(
            os.path.basename(file_path) if file_path else os.path.basename(repo_path)
        )

        if file_path:
            rel_file_path = os.path.relpath(file_path, repo_path)
            prelude += "  FILE: {}{}\n".format(rel_file_path, "  (UNTRACKED)" if untracked_file else "")

        if disable_stage:
            if in_cached_mode:
                prelude += "  {}..INDEX\n".format(base_commit or target_commit)
                title += ", {}..INDEX".format(base_commit or target_commit)
            else:
                if base_commit and target_commit:
                    prelude += "  {}..{}\n".format(base_commit, target_commit)
                    title += ", {}..{}".format(base_commit, target_commit)
                elif base_commit and "..." in base_commit:
                    prelude += "  {}\n".format(base_commit)
                    title += ", {}".format(base_commit)
                else:
                    prelude += "  {}..WORKING DIR\n".format(base_commit or target_commit)
                    title += ", {}..WORKING DIR".format(base_commit or target_commit)
        else:
            if untracked_file:
                ...
            elif in_cached_mode:
                prelude += "  STAGED CHANGES (Will commit)\n"
            else:
                prelude += "  UNSTAGED CHANGES\n"

        if ignore_whitespace:
            prelude += "  IGNORING WHITESPACE\n"

        prelude += "\n--\n"

        ensure_on_ui(_draw, view, title, prelude, diff, match_position)


def _draw(view, title, prelude, diff_text, match_position):
    # type: (sublime.View, str, str, str, Optional[Position_]) -> None
    was_empty = not view.find_by_selector("git-savvy.diff_view git-savvy.diff")
    navigated = False
    text = prelude + diff_text

    view.set_name(title)
    replace_view_content(view, text)

    if match_position:
        cur_pos, wanted_filename = match_position
        diff = SplittedDiff.from_view(view)
        if header := find_header_for_filename(diff.headers, wanted_filename):
            row, col, row_offset = cur_pos
            lineno = row + 1
            if hunk := find_hunk_for_line(diff.hunks_for_head(header), lineno):
                for line, b in recount_lines_for_jump_to_file(hunk):
                    # We're switching from a real file to the diff.  `lineno`
                    # comes from the `b` ("to") side.  We must filter using
                    # `not is_from_line()` as `recount_lines_for_jump_to_file`
                    # yields all lines in the hunk.
                    if not line.is_from_line() and b == lineno:
                        pt = line.a + col + line.mode_len
                        # Do not scroll if the cursor fits on the first "page",
                        # always show the prelude if possible.
                        _, cy = view.text_to_layout(pt)
                        _, vh = view.viewport_extent()
                        should_scroll = cy >= vh
                        place_cursor_and_show(
                            view, pt, row_offset if should_scroll else None, no_overscroll=True)
                        navigated = True
                        break

    if was_empty and not navigated:
        view.run_command("gs_diff_navigate")

    intra_line_colorizer.annotate_intra_line_differences(view, diff_text, len(prelude))


def find_header_for_filename(headers, filename):
    # type: (Iterable[FileHeader], str) -> Optional[FileHeader]
    for header in headers:
        if header.to_filename() == filename:
            return header
    else:
        return None


def find_hunk_for_line(hunks, row):
    # type: (Iterable[Hunk], int) -> Optional[Hunk]
    for hunk in hunks:
        start, length = hunk.header().safely_parse_metadata()[-1]
        if start <= row <= start + length:
            return hunk
    else:
        return None


class gs_diff_intent_to_add(TextCommand, GitCommand):
    def run(self, edit):
        settings = self.view.settings()
        file_path = settings.get("git_savvy.file_path")
        untracked_file = self.git("ls-files", "--", file_path).strip() == ""
        if not untracked_file:
            flash(self.view, "The file is already tracked.")
            return

        self.intent_to_add(file_path)

        history = settings.get("git_savvy.diff_view.history") or []
        frozen_sel = [s for s in self.view.sel()]
        patch = ""
        pts = [s.a for s in frozen_sel]
        in_cached_mode = settings.get("git_savvy.diff_view.in_cached_mode")
        history.append((["add", "--intent-to-add", file_path], patch, pts, in_cached_mode))
        settings.set("git_savvy.diff_view.history", history)
        settings.set("git_savvy.diff_view.just_hunked", patch)

        flash(self.view, "set --intent-to-add")
        self.view.run_command("gs_diff_refresh")


class gs_diff_toggle_setting(TextCommand):

    """
    Toggle view settings: `ignore_whitespace`.
    """

    def run(self, edit, setting):
        settings = self.view.settings()

        setting_str = "git_savvy.diff_view.{}".format(setting)
        current_mode = settings.get(setting_str)
        next_mode = not current_mode
        settings.set(setting_str, next_mode)
        flash(self.view, "{} is now {}".format(setting, next_mode))

        self.view.run_command("gs_diff_refresh")


class gs_diff_toggle_cached_mode(TextCommand):

    """
    Toggle `in_cached_mode` or flip `base` with `target`.
    """

    # NOTE: Blocking because `set_and_show_cursor` must run within a `TextCommand`
    def run(self, edit):
        settings = self.view.settings()

        base_commit = settings.get("git_savvy.diff_view.base_commit")
        target_commit = settings.get("git_savvy.diff_view.target_commit")
        if base_commit and target_commit:
            settings.set("git_savvy.diff_view.base_commit", target_commit)
            settings.set("git_savvy.diff_view.target_commit", base_commit)
            self.view.run_command("gs_diff_refresh")
            return

        if base_commit and "..." in base_commit:
            a, b = base_commit.split("...")
            settings.set("git_savvy.diff_view.base_commit", "{}...{}".format(b, a))
            self.view.run_command("gs_diff_refresh")
            return

        last_cursors = settings.get('git_savvy.diff_view.last_cursors') or []
        settings.set('git_savvy.diff_view.last_cursors', pickle_sel(self.view.sel()))

        setting_str = "git_savvy.diff_view.{}".format('in_cached_mode')
        current_mode = settings.get(setting_str)
        next_mode = not current_mode
        settings.set(setting_str, next_mode)
        flash(self.view, "Showing {} changes".format("staged" if next_mode else "unstaged"))

        # `gs_diff_refresh` may call us (`gs_diff_toggle_cached_mode`) if
        # `just_hunked` is set read and clear first.
        just_hunked = self.view.settings().get("git_savvy.diff_view.just_hunked")
        self.view.settings().set("git_savvy.diff_view.just_hunked", "")
        self.view.run_command("gs_diff_refresh")

        # Check for `last_cursors` as well bc it is only falsy on the *first*
        # switch. T.i. if the user hunked and then switches to see what will be
        # actually committed, the view starts at the top. Later, the view will
        # show the last added hunk.
        if just_hunked:
            if last_cursors:
                region = find_hunk_in_view(self.view, just_hunked)
                if region:
                    set_and_show_cursor(self.view, region.a)
                    return
            else:
                set_and_show_cursor(self.view, 0)
                self.view.run_command("gs_diff_navigate")
                return

        if last_cursors:
            # The 'flipping' between the two states should be as fast as possible and
            # without visual clutter.
            with no_animations():
                set_and_show_cursor(self.view, unpickle_sel(last_cursors))


class gs_diff_switch_files(TextCommand, GitCommand):
    def run(self, edit, recursed=False, auto_close=False, forward=None):
        # type: (sublime.Edit, bool, bool, Optional[bool]) -> None
        view = self.view
        window = view.window()
        if not window:
            return
        if view.element() == "quick_panel:input":
            if av := window.active_view():
                av.settings().set("gs_diff.intentional_hide", True)
                window.run_command("hide_overlay")
                av.run_command("gs_diff_switch_files", {"recursed": True, "auto_close": auto_close, "forward": forward})
            return

        AUTO_CLOSE_AFTER = 1000  # [ms]
        SEP = "                      ———— UNTRACKED FILES ————"
        settings = view.settings()
        auto_close_state: Literal["MUST_INSTALL", "ACTIVE", "DEAD"]
        auto_close_state = "MUST_INSTALL" if auto_close else "DEAD"

        if base_commit := settings.get("git_savvy.diff_view.base_commit"):
            target_commit = settings.get("git_savvy.diff_view.target_commit")
            available = self.list_touched_filenames(base_commit, target_commit)
        else:
            status = self.current_state().get("status")
            in_cached_mode = settings.get("git_savvy.diff_view.in_cached_mode")
            if status:
                if in_cached_mode:
                    available = [f.path for f in status.staged_files]
                else:
                    available = [f.path for f in status.unstaged_files]
                    if status.untracked_files:
                        available += [SEP] + [f.path for f in status.untracked_files]
            else:
                available = self.list_touched_filenames(None, None, cached=in_cached_mode)

        file_path = settings.get("git_savvy.file_path")
        if not available:
            selected_index = 0
        elif file_path:
            normalized_relative_path = self.get_rel_path(file_path)
            try:
                idx = available.index(normalized_relative_path)
            except ValueError:
                selected_index = 0
            else:
                delta = 1 if forward is True else -1 if forward is False else 0
                idx = (idx + delta) % len(available)
                if available[idx] == SEP:
                    idx = (idx + delta) % len(available)
                selected_index = idx + 1  # skip the "--all" entry
        else:
            selected_index = 1 if forward is True else len(available) if forward is False else 0

        items = ["--all"] + available
        if not recursed:
            original_view_state = (file_path, view.viewport_position(), [(s.a, s.b) for s in view.sel()], )
            settings.set("git_savvy.original_view_state", original_view_state)

        def auto_close_panel():
            nonlocal auto_close_state
            if auto_close_state == "ACTIVE":
                settings.set("gs_diff.intentional_hide", True)
                window.run_command("hide_overlay")

        def on_done(idx):
            nonlocal auto_close_state
            auto_close_state = "DEAD"
            settings.erase("git_savvy.original_view_state")
            ...  # already everything done in `on_highlight`

        def on_highlight(idx):
            nonlocal auto_close_state
            if auto_close_state == "MUST_INSTALL":
                auto_close_state = "ACTIVE"
                sublime.set_timeout_async(auto_close_panel, AUTO_CLOSE_AFTER)
            elif auto_close_state == "ACTIVE":
                auto_close_state = "DEAD"

            item = items[idx]
            if item == SEP:
                return
            enqueue_on_worker(throttled(on_highlight_, item))

        def on_highlight_(item: str) -> None:
            if item == "--all":
                if not settings.get("git_savvy.file_path"):
                    return
                settings.erase("git_savvy.file_path")
            else:
                next_file_path = os.path.normpath(os.path.join(self.repo_path, item))
                if next_file_path == settings.get("git_savvy.file_path"):
                    return
                settings.set("git_savvy.file_path", next_file_path)
            view.run_command("gs_diff_refresh", {"sync": True})
            view.set_viewport_position((0, 0))
            view.sel().clear()
            view.sel().add(sublime.Region(0))
            view.run_command("gs_diff_navigate")

        def on_cancel():
            nonlocal auto_close_state
            auto_close_state = "DEAD"
            if settings.get("gs_diff.intentional_hide"):
                settings.erase("gs_diff.intentional_hide")
                return
            original_view_state = settings.get("git_savvy.original_view_state")
            settings.erase("git_savvy.original_view_state")
            if original_view_state:
                file_path, viewport_position, sel = original_view_state
                if file_path:
                    if file_path == settings.get("git_savvy.file_path"):
                        return
                    settings.set("git_savvy.file_path", file_path)
                else:
                    if not settings.get("git_savvy.file_path"):
                        return
                    settings.erase("git_savvy.file_path")
                view.run_command("gs_diff_refresh", {"sync": True})
                view.set_viewport_position(viewport_position)
                view.sel().clear()
                view.sel().add_all([sublime.Region(*s) for s in sel])

        # Skip the `on_activated` event e.g. when the quick panel closes, because
        # we update and refresh the underlying view manually in the callbacks.
        settings.set("git_savvy.ignore_next_activated_event", True)
        show_panel(
            window,
            items,
            on_done,
            on_cancel=on_cancel,
            on_highlight=on_highlight,
            selected_index=selected_index,
            flags=sublime.MONOSPACE_FONT
        )
        window.run_command("gs_diff_grab_quick_panel_view")


class gs_diff_grab_quick_panel_view(TextCommand):
    def run(self, edit):
        view = self.view
        if view.element() != "quick_panel:input":
            hprint(
                "Can't mark quick_panel for switching files. "
                "[N]/[P] bindings are disabled."
            )
            return
        view.settings().set("gs_diff_files_selector", True)


class gs_diff_zoom(TextCommand):
    """
    Update the number of context lines the diff shows by given `amount`
    and refresh the view.
    """
    def run(self, edit, amount):
        # type: (sublime.Edit, int) -> None
        settings = self.view.settings()
        current = settings.get('git_savvy.diff_view.context_lines')

        MINIMUM, DEFAULT, MIN_STEP_SIZE = 1, 3, 5
        step_size = max(abs(amount), MIN_STEP_SIZE)
        values = chain([MINIMUM, DEFAULT], count(step_size, step_size))
        if amount > 0:
            next_value = next(x for x in values if x > current)
        else:
            try:
                next_value = list(takewhile(lambda x: x < current, values))[-1]
            except IndexError:
                next_value = MINIMUM

        settings.set('git_savvy.diff_view.context_lines', next_value)

        # Getting a meaningful cursor after 'zooming' is the tricky part
        # here. We first extract all hunks under the cursors *verbatim*.
        diff = SplittedDiff.from_view(self.view)
        cur_hunks = []
        for s in self.view.sel():
            hunk = diff.hunk_for_pt(s.a)
            if hunk:
                head_line = diff.head_for_hunk(hunk).first_line()
                for line, line_id in compute_line_ids_for_hunk(hunk):
                    line_region = line.region()
                    # `line_region` spans the *full* line including the
                    # trailing newline char (if any).  Compare excluding
                    # `line_region.b` to not match a cursor at BOL
                    # position on the next line.
                    if line_region.a <= s.a < line_region.b:
                        cur_hunks.append((head_line, line_id, y_offset(self.view, s.a)))
                        break
                else:
                    # If the user is on the very last line of the view, create
                    # a fake line after that.
                    cur_hunks.append((
                        head_line,
                        LineId(line_id.a + 1, line_id.b + 1),
                        y_offset(self.view, s.a)
                    ))

        self.view.run_command("gs_diff_refresh")

        # Now, we fuzzy search the new view content for the old hunks.
        diff = SplittedDiff.from_view(self.view)
        cursors = set()
        scroll_offsets = []
        for head_line, line_id, offset in cur_hunks:
            region = find_line_in_diff(diff, head_line, line_id)
            if region:
                cursors.add(region.a)
                scroll_offsets.append((region.a, offset))

        if not cursors:
            return

        self.view.sel().clear()
        self.view.sel().add_all(list(cursors))

        cursor, offset = min(scroll_offsets, key=lambda cursor_offset: abs(cursor_offset[1]))
        scroll_to_pt(self.view, cursor, offset)


class LineId(NamedTuple):
    a: LineNo
    b: LineNo


HunkLineWithLineNumbers = Tuple[HunkLine, LineId]
HunkLineWithLineId = Tuple[TextRange, LineId]


def compute_line_ids_for_hunk(hunk):
    # type: (Hunk) -> Iterator[HunkLineWithLineId]
    # Use `safely_parse_metadata` to not throw on combined diffs.
    # In that case, the computed line numbers can only be used as identifiers,
    # really counting lines from a combined diff is not implemented here!
    metadata = hunk.header().safely_parse_metadata()
    (a_start, _), (b_start, _) = metadata[0], metadata[-1]
    yield hunk.header(), LineId(a_start - 1, b_start - 1)
    yield from __recount_lines(hunk.content().lines(), a_start, b_start)


def find_line_in_diff(diff, head_line, wanted_line_id):
    # type: (SplittedDiff, str, LineId) -> Optional[sublime.Region]
    header = next((h for h in diff.headers if h.first_line() == head_line), None)
    if header:
        for hunk in diff.hunks_for_head(header):
            for line, line_id in compute_line_ids_for_hunk(hunk):
                if line_id >= wanted_line_id:
                    return line.region()
    return None


class GsDiffFocusEventListener(EventListener):

    """
    If the current view is a diff view, refresh the view with latest tree status
    when the view regains focus.
    """

    def on_activated(self, view):
        settings = view.settings()
        if settings.get("git_savvy.ignore_next_activated_event"):
            settings.set("git_savvy.ignore_next_activated_event", False)
            return

        if active_on_activated and is_diff_view(view):
            view.run_command("gs_diff_refresh", {"sync": False})


class gs_diff_stage_or_reset_hunk(TextCommand, GitCommand):

    """
    Depending on whether the user is in cached mode and what action
    the user took, either 1) stage, 2) unstage, or 3) reset the
    hunk under the user's cursor(s).
    """

    # NOTE: The whole command (including the view refresh) must be blocking otherwise
    # the view and the repo state get out of sync and e.g. hitting 'h' very fast will
    # result in errors.

    def run(self, edit, reset=False, whole_file=False):
        # type: (sublime.Edit, bool, bool) -> None
        ignore_whitespace = self.view.settings().get("git_savvy.diff_view.ignore_whitespace")
        if ignore_whitespace:
            sublime.error_message("Staging is not supported while ignoring [w]hitespace is on.")
            return None

        in_cached_mode = self.view.settings().get("git_savvy.diff_view.in_cached_mode")
        if in_cached_mode and reset:
            flash(self.view, "Can't discard staged changes.  Unstage first.")
            return None

        frozen_sel = [s for s in self.view.sel()]
        cursor_pts = [s.a for s in frozen_sel]
        diff = SplittedDiff.from_view(self.view)
        if not diff.headers:
            flash(
                self.view,
                "The {} is clean.".format(
                    "file" if self.view.settings().get("git_savvy.file_path") else "repo"
                )
            )
            return

        if diff.is_combined_diff():
            headers = list(unique(filter_(map(diff.head_for_pt, cursor_pts))))
            files = list(filter_(head.to_filename() for head in headers))
            if not files:
                flash(self.view, "Not within a hunk")
                return
            if self.check_for_conflict_markers(files):
                flash(self.view, "You still have unresolved conflicts.")
            else:
                self.stage_file(*files)
                history = self.view.settings().get("git_savvy.diff_view.history")
                patches = flatten(
                    chain([head], diff.hunks_for_head(head))
                    for head in headers
                )  # type: Iterable[TextRange]
                patch = ''.join(part.text for part in patches)
                history.append((["add", files], patch, cursor_pts, in_cached_mode))
                self.view.settings().set("git_savvy.diff_view.history", history)
                self.view.settings().set("git_savvy.diff_view.just_hunked", patch)
                self.view.run_command("gs_diff_refresh")
            return

        move_fn = None
        if whole_file or all(s.empty() for s in frozen_sel):
            if whole_file:
                headers = (
                    list(unique(filter_(map(diff.head_for_pt, cursor_pts))))
                    or [diff.headers[0]]
                )
                patches = list(flatten(
                    chain([head], diff.hunks_for_head(head))
                    for head in headers
                ))

            else:
                patches = (
                    list(unique(flatten(filter_(diff.head_and_hunk_for_pt(pt) for pt in cursor_pts))))
                    or [diff.headers[0], diff.hunks[0]]
                )

            last_selected_hunk = patches[-1]
            try:
                hunk_to_focus = diff.hunks[diff.hunks.index(last_selected_hunk) + 1]
            except IndexError:
                pass
            else:
                hunk_idx = [hunk for hunk in diff.hunks if hunk not in patches].index(hunk_to_focus)
                move_fn = partial(move_to_hunk, self.view, hunk_idx)

            patch = ''.join(part.text for part in patches)
            zero_diff = self.view.settings().get('git_savvy.diff_view.context_lines') == 0

        else:
            line_starts = selected_line_starts(self.view, frozen_sel)
            patch = compute_patch_for_sel(diff, line_starts, reset or in_cached_mode)
            zero_diff = True

        if patch:
            self.apply_patch(patch, cursor_pts, reset, zero_diff)
            if move_fn:
                move_fn()
            else:
                # just shrink multiple cursors into the first one
                first_cursor = self.view.sel()[0].begin()
                self.view.sel().clear()
                self.view.sel().add(first_cursor)
        else:
            flash(self.view, "Not within a hunk")

    def apply_patch(self, patch, pts, reset, zero_diff):
        # type: (str, List[int], bool, bool) -> None
        in_cached_mode = self.view.settings().get("git_savvy.diff_view.in_cached_mode")

        # ATT: Undo expects always the same args length and order!
        args = ["apply"]  # type: List[Optional[str]]
        if reset:
            args += ["-R", None]        # discard
        elif in_cached_mode:
            args += ["-R", "--cached"]  # unstage
        else:
            args += [None, "--cached"]  # stage

        if zero_diff:
            args += ["--unidiff-zero"]

        args += ["-"]
        self.git(*args, stdin=patch)

        history = self.view.settings().get("git_savvy.diff_view.history") or []
        history.append((args, patch, pts, in_cached_mode))
        self.view.settings().set("git_savvy.diff_view.history", history)
        self.view.settings().set("git_savvy.diff_view.just_hunked", patch)

        if self.view.settings().get("git_savvy.commit_view"):
            self.view.run_command("gs_prepare_commit_refresh_diff")
        else:
            self.view.run_command("gs_diff_refresh")
        # Ideally we would compute the next WorkingDirState but that's not
        # trivial, so we just ask for it:
        self.view.run_command("gs_update_status")


def move_to_hunk(view: sublime.View, hunk_idx: int) -> None:
    diff = SplittedDiff.from_view(view)
    hunk_to_focus = diff.hunks[hunk_idx]
    next_cursor = hunk_to_focus.a
    set_and_show_cursor(view, next_cursor)


def selected_line_starts(view, sel):
    # type: (sublime.View, List[sublime.Region]) -> Set[int]
    selected_lines = flatten(map(view.lines, sel))
    return set(line.a for line in selected_lines)


def chunkby(it, predicate):
    # type: (Iterable[T], Callable[[T], bool]) -> Iterator[List[T]]
    return (list(items) for selected, items in groupby(it, key=predicate) if selected)


def compute_patch_for_sel(diff, line_starts, reverse):
    # type: (SplittedDiff, Set[int], bool) -> str
    hunks = unique(filter_(diff.hunk_for_pt(pt) for pt in sorted(line_starts)))

    def not_context(line_ab):
        line, _ = line_ab
        return not line.is_context()

    def selected(line_ab):
        line, _ = line_ab
        return line.a in line_starts

    patches = defaultdict(list)  # type: Dict[FileHeader, List[stage_hunk.Hunk]]
    for hunk in hunks:
        header = diff.head_for_hunk(hunk)
        for chunk in chunkby(recount_lines(hunk), not_context):
            selected_lines = list(filter(selected, chunk))
            if selected_lines:
                patches[header].append(form_patch(selected_lines))

    whole_patch = "".join(
        stage_hunk.format_patch(header.text, hunks, reverse=reverse)
        for header, hunks in patches.items()
    )
    return whole_patch


def form_patch(lines):
    # type: (List[HunkLineWithLineNumbers]) -> stage_hunk.Hunk
    first_line, (a_start, b_start) = lines[0]
    last_line, _ = lines[-1]
    if first_line.is_from_line() and last_line.is_to_line():
        b_start = next(b for line, (a, b) in lines if line.is_to_line())
    alen = sum(1 for line, _ in lines if not line.is_to_line())
    blen = sum(1 for line, _ in lines if not line.is_from_line())
    content = "".join(line.text for line, _ in lines)
    return stage_hunk.Hunk(a_start, alen, b_start, blen, content)


class gs_initiate_fixup_commit(TextCommand, LogHelperMixin):
    def run(self, edit):
        view = self.view
        window = view.window()
        assert window

        def action(entry):
            # type: (LogEntry) -> None
            commit_message = entry.summary
            window.run_command("gs_commit", {
                "initial_text": "fixup! {}".format(commit_message)
            })

        def preselected_commit(items):
            # type: (List[LogEntry]) -> int
            return next(chain(
                head(
                    idx for idx, item in enumerate(items)
                    if (
                        not item.summary.startswith("fixup! ")
                        and not item.summary.startswith("squash! ")
                    )
                ),
                [-1]
            ))

        self.show_log_panel(action, preselected_commit=preselected_commit)


class JumpTo(NamedTuple):
    commit_hash: Optional[str]
    filename: str
    line: LineNo
    col: ColNo


class gs_diff_open_file_at_hunk(TextCommand, GitCommand):

    """
    For each cursor in the view, identify the hunk in which the cursor lies,
    and open the file at that hunk in a separate view.
    """

    def run(self, edit):
        # type: (sublime.Edit) -> None

        def first_per_file(items):
            # type: (Iterable[JumpTo]) -> Iterator[JumpTo]
            seen = set()  # type: Set[str]
            for item in items:
                if item.filename not in seen:
                    seen.add(item.filename)
                    yield item

        diff_regions = self.view.find_by_selector("git-savvy.commit, git-savvy.diff")
        diffs = [
            SplittedDiff.from_string(content, region.a)
            for region in diff_regions
            if (content := self.view.substr(region))
        ]
        if not diffs:
            flash(self.view, "Could not extract a diff.")
            return

        frozen_sel = list(self.view.sel())
        try:
            jump_positions = next(filter_(
                list(first_per_file(filter_(
                    fn(self.view, diff, s.begin())
                    for diff in diffs
                    for s in frozen_sel
                )))
                for fn in (jump_position_to_file, first_jump_position_in_view)
            ))
        except StopIteration:
            flash(self.view, "Not within a hunk")
        else:
            for jp in jump_positions:
                self.load_file_at_line(*jp)

    def load_file_at_line(self, commit_hash, filename, line, col):
        # type: (Optional[str], str, LineNo, ColNo) -> None
        """
        Show file at target commit if `git_savvy.diff_view.target_commit` is non-empty.
        Otherwise, open the file directly.
        """
        full_path = os.path.join(self.repo_path, filename)
        window = self.view.window()
        if not window:
            return

        target_commit = self.view.settings().get("git_savvy.diff_view.target_commit")
        if commit_hash or target_commit:
            window.run_command("gs_show_file_at_commit", {
                "commit_hash": commit_hash or self.resolve_commitish(target_commit),
                "filepath": full_path,
                "position": Position(line - 1, col - 1, None),
            })
        else:
            if self.view.settings().get("git_savvy.diff_view.in_cached_mode"):
                line = self.reverse_find_matching_lineno(None, None, line=line, file_path=full_path)
            window.open_file(
                "{file}:{line}:{col}".format(file=full_path, line=line, col=col),
                sublime.ENCODED_POSITION
            )


def jump_position_to_file(view, diff, pt):
    # type: (sublime.View, SplittedDiff, int) -> Optional[JumpTo]
    hunk = diff.hunk_for_pt(pt)
    if not hunk:
        return None
    return _jump_position_from_hunk(view, diff, hunk, pt)


def first_jump_position_in_view(view, diff, pt):
    # type: (sublime.View, SplittedDiff, int) -> Optional[JumpTo]
    hunk = diff.first_hunk_after_pt(pt)
    if not hunk:
        return None
    return _jump_position_from_hunk(view, diff, hunk, pt)


def _jump_position_from_hunk(view, diff, hunk, pt):
    # type: (sublime.View, SplittedDiff, Hunk, int) -> Optional[JumpTo]
    header = diff.head_for_hunk(hunk)
    line, col = real_linecol_in_hunk(hunk, *row_offset_and_col_in_hunk(view, hunk, pt))
    filename = header.to_filename()
    if not filename:
        return None

    commit_header = diff.commit_for_hunk(hunk)
    commit_hash = commit_header.commit_hash() if commit_header else None
    return JumpTo(commit_hash, filename, line, col)


def row_offset_and_col_in_hunk(view, hunk, pt):
    # type: (sublime.View, Hunk, Point) -> Tuple[int, ColNo]
    """Return row offset of `pt` relative to hunk start and its column

    Note that the column is already 1-based t.i. a `ColNo`
    """
    head_row, _ = view.rowcol(hunk.a)
    pt_row, col = view.rowcol(pt)
    # We want to map columns in a diff output to columns in real files.
    # Strip "line-mode" characters at the start of the line.
    # Since `col` as returned by `rowcol` is 0-based (and we want 1-based
    # line and columns here) account for that as well.
    # Often the user will be on the first char of the line, t.i. within
    # the "line-mode" section. Return `1` in that case.
    return pt_row - head_row, max(col - hunk.mode_len() + 1, 1)


def real_linecol_in_hunk(hunk, row_offset, col):
    # type: (Hunk, int, ColNo) -> LineCol
    """Translate relative to absolute line, col pair"""
    hunk_lines = list(recount_lines_for_jump_to_file(hunk))
    row_offset = clamp(0, len(hunk_lines), row_offset)

    # If the user is on the header line ('@@ ..') pretend to be on the
    # first visible line with some content instead.
    if row_offset == 0:
        row_offset = next(
            (
                index
                for index, (line, _) in enumerate(hunk_lines, 1)
                if not line.is_from_line() and line.content.strip()
            ),
            1
        )
        col = 1

    line, b = hunk_lines[row_offset - 1]

    # Happy path since the user is on a present line
    if not line.is_from_line():
        return b, col

    # The user is on a deleted line ('-') we cannot jump to. If possible,
    # select the next guaranteed to be available line
    for next_line, next_b in hunk_lines[row_offset:]:
        if next_line.is_to_line():
            return next_b, min(col, len(next_line.content) + 1)
        elif next_line.is_context():
            # If we only have a contextual line, choose this or the
            # previous line, pretty arbitrary, depending on the
            # indentation.
            next_lines_indentation = line_indentation(next_line.content)
            if next_lines_indentation == line_indentation(line.content):
                return next_b, next_lines_indentation + 1
            else:
                return max(1, b - 1), 1
    else:
        return b, 1


def recount_lines_for_jump_to_file(hunk):
    # type: (Hunk) -> Iterator[HunkLineWithB]
    """Recount lines for the jump-to-file feature.

    Only computes b values and handles deletions weird, e.g. for

    ```
        @@ -383,4 +383,3 @@ class gs_diff_zoom(TextCommand):
             step_size = max(abs(amount), MIN_STEP_SIZE)
    -        values = chain([MINIMUM, DEFAULT], count(step_size, step_size))
    -        if amount > 0:
    +        zif amount > 0:
             next_value = next(x for x in values if x > current)

    ```

    yields `(383, 384, 384, 384, 385)`.  That's visually appealing
    though.

    """
    b_start = hunk.header().to_line_start()
    for line in hunk.content().lines():
        yield HunkLineWithB(line, b_start)
        if not line.is_from_line():
            b_start += 1


def recount_lines(hunk):
    # type: (Hunk) -> Iterator[HunkLineWithLineNumbers]
    a_start, _, b_start, _ = hunk.header().parse()
    yield from __recount_lines(hunk.content().lines(), a_start, b_start)


def __recount_lines(hunk_lines, a_start, b_start):
    # type: (Iterable[HunkLine], int, int) -> Iterator[HunkLineWithLineNumbers]
    it = iter(hunk_lines)
    try:
        line = next(it)
    except StopIteration:
        return
    else:
        yield line, LineId(a_start, b_start)

    for line in it:
        if line.is_context():
            a_start += 1
            b_start += 1
        elif line.is_from_line():
            a_start += 1
        elif line.is_to_line():
            b_start += 1
        yield line, LineId(a_start, b_start)


class gs_diff_navigate(GsNavigate):

    """
    Travel between hunks. It is also used by show_commit_view.
    """

    offset = 0
    log_position = True
    shrink_to_cursor = False

    def get_available_regions(self):
        def _gen():
            # type: () -> Iterator[sublime.Region]
            diff = SplittedDiff.from_view(self.view)
            for hunk in diff.hunks:
                yield sublime.Region(hunk.region().a)
                chunks = list(chunkby(hunk.content().lines(), lambda line: not line.is_context()))
                if len(chunks) > 1:
                    for chunk in chunks:
                        yield sublime.Region(chunk[0].region().a, chunk[-1].region().b)

        return sorted(
            list(_gen())
            + self.view.find_by_selector("meta.commit-info.header")
        )


class gs_diff_undo(TextCommand, GitCommand):

    """
    Undo the last action taken in the diff view, if possible.
    """

    # NOTE: Blocking because `set_and_show_cursor` must run within a `TextCommand`
    def run(self, edit):
        settings = self.view.settings()
        history = settings.get("git_savvy.diff_view.history")
        if not history:
            flash(self.view, "Undo stack is empty")
            return

        args, stdin, cursors, in_cached_mode = history.pop()
        if args[0] == "add":
            if args[1] == "-u":
                self.unstage_all_files()
            elif args[1] == "--intent-to-add":
                self.undo_intent_to_add(args[2])
            else:
                self.unstage_file(*args[1])
        else:
            # Toggle the `--reverse` flag.
            args[1] = "-R" if not args[1] else None
            self.git(*args, stdin=stdin)

        settings.set("git_savvy.diff_view.history", history)
        settings.set("git_savvy.diff_view.just_hunked", stdin)

        if settings.get("git_savvy.commit_view"):
            self.view.run_command("gs_prepare_commit_refresh_diff")
        else:
            self.view.run_command("gs_diff_refresh")

        # The cursor is only applicable if we're still in the same cache/stage mode
        if (
            settings.get("git_savvy.diff_view.in_cached_mode") == in_cached_mode
            or settings.get("git_savvy.commit_view")
        ):
            set_and_show_cursor(self.view, cursors)


def find_hunk_in_view(view, patch):
    # type: (sublime.View, str) -> Optional[sublime.Region]
    """Given a patch, search for its first hunk in the view

    Returns the region of the first line of the hunk (the one starting
    with '@@ ...'), if any.
    """
    diff = SplittedDiff.from_string(patch)
    try:
        hunk = diff.hunks[0]
    except IndexError:
        return None

    return (
        view.find(hunk.header().text, 0, sublime.LITERAL)
        or fuzzy_search_hunk_content_in_view(view, hunk.content().text.splitlines())
    )


def fuzzy_search_hunk_content_in_view(view, lines):
    # type: (sublime.View, List[str]) -> Optional[sublime.Region]
    """Fuzzy search the hunk content in the view

    Note that hunk content does not include the starting line, the one
    starting with '@@ ...', anymore.

    The fuzzy strategy here is to search for the hunk or parts of it
    by reducing the contextual lines symmetrically.

    Returns the region of the starting line of the found hunk, if any.
    """
    for hunk_content in shrink_list_sym(lines):
        region = view.find('\n'.join(hunk_content), 0, sublime.LITERAL)
        if region:
            diff = SplittedDiff.from_view(view)
            head_and_hunk = diff.head_and_hunk_for_pt(region.a)
            if head_and_hunk:
                _, hunk = head_and_hunk
                hunk_header = hunk.header()
                return sublime.Region(hunk_header.a, hunk_header.b)
            break
    return None


def shrink_list_sym(list):
    # type: (List[T]) -> Iterator[List[T]]
    while list:
        yield list
        list = list[1:-1]


def pickle_sel(sel):
    return [(s.a, s.b) for s in sel]


def unpickle_sel(pickled_sel):
    return [sublime.Region(a, b) for a, b in pickled_sel]


def set_and_show_cursor(view, cursors):
    sel = view.sel()
    sel.clear()
    try:
        it = iter(cursors)
    except TypeError:
        sel.add(cursors)
    else:
        for c in it:
            sel.add(c)

    view.show(sel)


@contextmanager
def no_animations():
    pref = sublime.load_settings("Preferences.sublime-settings")
    current = pref.get("animation_enabled")
    pref.set("animation_enabled", False)
    try:
        yield
    finally:
        pref.set("animation_enabled", current)
