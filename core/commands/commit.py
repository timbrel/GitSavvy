from itertools import chain, takewhile
import os

import sublime
from sublime_plugin import WindowCommand, TextCommand
from sublime_plugin import EventListener, ViewEventListener

from .diff import DECODE_ERROR_MESSAGE
from . import intra_line_colorizer
from . import multi_selector
from ..git_command import GitCommand, GitSavvyError
from ..fns import flatten, head
from ..runtime import enqueue_on_worker, run_on_new_thread, text_command
from ..settings import GitSavvySettings, SettingsMixin
from ..ui_mixins.quick_panel import LogHelperMixin
from ..utils import focus_view
from ..view import replace_view_content
from ...common import util
from ...common.theme_generator import ThemeGenerator


__all__ = (
    "gs_commit",
    "gs_prepare_commit_refresh_diff",
    "gs_commit_view_unstage_in_all_mode",
    "gs_commit_view_do_commit",
    "gs_commit_view_sign",
    "gs_commit_view_close",
    "gs_commit_log_helper",
    "GsPrepareCommitFocusEventListener",
    "GsPedanticEnforceEventListener",
)


from typing import Dict, List, Optional, Tuple, Union
from ..git_mixins.history import LogEntry


COMMIT_HELP_TEXT_EXTRA = """##
## "<tab>"       at the very first char to see the recent log
## "fixup<tab>"  to create a fixup subject  (short: "fix<tab>")
## "squash<tab>  to create a squash subject (short: "sq<tab>")
## "#<tab>"      to reference a GitHub issue (or: "owner/repo#<tab>")
"""

HELP_WHEN_PATCH_IS_VISIBLE = """\
## In the diff below, [o] will open the file under the cursor.
## [ctrl+r]      to navigate between files
"""
HELP_WHEN_UNSTAGING_IS_POSSIBLE = """\
## [u]/[U]       to unstage
"""
HELP_WHEN_DISCARDING_IS_POSSIBLE = """\
## [d]/[D]       to discard changes
"""
HELP_WHEN_UNDOING_IS_POSSIBLE = """\
## [{key}+z]{space}     to undo
""".format(key=util.super_key, space=" " * (5 - len(util.super_key)))

COMMIT_HELP_TEXT_ALT = """\
## To make a commit, type your commit message and close the window.
## To cancel the commit, delete the commit message and close the window.
## To sign off on the commit, press {key}-S.
""".format(key=util.super_key) + COMMIT_HELP_TEXT_EXTRA


COMMIT_HELP_TEXT = """\
## To make a commit, type your commit message and press {key}-ENTER.
## To cancel the commit, close the window. To sign off on the commit,
## press {key}-S.
""".format(key=util.super_key) + COMMIT_HELP_TEXT_EXTRA

COMMIT_SIGN_TEXT = """

Signed-off-by: {name} <{email}>
"""

COMMIT_TITLE = "COMMIT: {}"
THE_EMPTY_SHA = ""

CONFIRM_ABORT = "Confirm to abort commit?"


def compute_identifier_for_view(view):
    # type: (sublime.View) -> Optional[Tuple]
    settings = view.settings()
    return (
        settings.get('git_savvy.repo_path'),
    ) if settings.get('git_savvy.commit_view') else None


def view_has_simple_cursor(view):
    # type: (sublime.View) -> bool
    return len(view.sel()) == 1 and view.sel()[0].empty()


class gs_commit(WindowCommand, GitCommand):

    """
    Display a transient window to capture the user's desired commit message.
    If the user is amending the previous commit, pre-populate the commit
    message area with the previous commit message.
    """

    def run(self, repo_path=None, include_unstaged=False, amend=False, initial_text=""):
        repo_path = repo_path or self.repo_path

        this_id = (
            repo_path,
        )
        for view in self.window.views():
            if compute_identifier_for_view(view) == this_id:
                settings = view.settings()
                settings.set("git_savvy.commit_view.include_unstaged", include_unstaged)
                settings.set("git_savvy.commit_view.amend", amend)
                focus_view(view)
                break
        else:
            view = self.window.new_file()
            settings = view.settings()
            settings.set("git_savvy.repo_path", repo_path)
            settings.set("git_savvy.commit_view", True)
            settings.set("git_savvy.commit_view.include_unstaged", include_unstaged)
            settings.set("git_savvy.commit_view.automatically_switched_to_all", False)
            settings.set("git_savvy.diff_view.in_cached_mode", not include_unstaged)
            settings.set("git_savvy.commit_view.amend", amend)
            commit_on_close = self.savvy_settings.get("commit_on_close")
            settings.set("git_savvy.commit_on_close", commit_on_close)
            prompt_on_abort_commit = self.savvy_settings.get("prompt_on_abort_commit")
            settings.set("git_savvy.prompt_on_abort_commit", prompt_on_abort_commit)
            util.view.mark_as_lintable(view)

            view.set_syntax_file("Packages/GitSavvy/syntax/make_commit.sublime-syntax")
            run_on_new_thread(augment_color_scheme, view)
            view.run_command("gs_handle_vintageous")

            title = COMMIT_TITLE.format(os.path.basename(repo_path))
            view.set_name(title)
            view.set_scratch(True)  # ignore dirty on actual commit
            self.initialize_view(view, amend)

        initial_text_ = initial_text.rstrip()
        if initial_text_:
            if extract_commit_subject(view).strip():
                initial_text_ += "\n\n"
            replace_view_content(view, initial_text_, sublime.Region(0))
            if view_has_simple_cursor(view):
                view.sel().clear()
                view.sel().add(len(initial_text_))

    def initialize_view(self, view, amend):
        # type: (sublime.View, bool) -> None
        merge_msg_path = os.path.join(self.git_dir, "MERGE_MSG")
        initial_text = ""
        if amend:
            last_commit_message = self.git("log", "-1", "--pretty=%B").strip()
            initial_text += last_commit_message
        elif os.path.exists(merge_msg_path):
            with util.file.safe_open(merge_msg_path, "r") as f:
                initial_text += f.read()

        initial_text += "\n\n" + generate_help_text(view)

        commit_help_extra_file = self.savvy_settings.get("commit_help_extra_file") or ".commit_help"
        commit_help_extra_path = os.path.join(self.repo_path, commit_help_extra_file)
        if os.path.exists(commit_help_extra_path):
            with util.file.safe_open(commit_help_extra_path, "r", encoding="utf-8") as f:
                initial_text += f.read()

        replace_view_content(view, initial_text)
        view.run_command("gs_prepare_commit_refresh_diff")


def augment_color_scheme(view):
    # type: (sublime.View) -> None
    settings = GitSavvySettings()
    colors = settings.get('colors').get('commit')
    if not colors:
        return

    themeGenerator = ThemeGenerator.for_view(view)
    themeGenerator.add_scoped_style(
        "GitSavvy Multiselect Marker",
        multi_selector.MULTISELECT_SCOPE,
        background=colors['multiselect_foreground'],
        foreground=colors['multiselect_background'],
    )
    themeGenerator.apply_new_theme("commit_view", view)


def generate_help_text(view, with_patch_commands=False):
    # type: (sublime.View, bool) -> str
    settings = view.settings()
    commit_on_close = settings.get("git_savvy.commit_on_close")
    help_text = (
        COMMIT_HELP_TEXT_ALT
        if commit_on_close
        else COMMIT_HELP_TEXT
    )
    if with_patch_commands:
        help_text += HELP_WHEN_PATCH_IS_VISIBLE
        help_text += HELP_WHEN_UNSTAGING_IS_POSSIBLE
        if not settings.get("git_savvy.diff_view.in_cached_mode"):
            help_text += HELP_WHEN_DISCARDING_IS_POSSIBLE
        if settings.get("git_savvy.diff_view.history"):
            help_text += HELP_WHEN_UNDOING_IS_POSSIBLE
    return help_text


class gs_prepare_commit_refresh_diff(TextCommand, GitCommand):
    def run(self, edit, sync=True, just_switched=False):
        # type: (sublime.Edit, bool, bool) -> None
        if sync:
            self.run_impl(sync, just_switched)
        else:
            enqueue_on_worker(self.run_impl, sync, just_switched)

    def run_impl(self, sync, just_switched):
        # type: (bool, bool) -> None
        view = self.view
        settings = view.settings()
        include_unstaged = settings.get("git_savvy.commit_view.include_unstaged")
        automatically_switched_to_all = settings.get(
            "git_savvy.commit_view.automatically_switched_to_all")
        amend = settings.get("git_savvy.commit_view.amend")
        show_commit_diff = self.savvy_settings.get("show_commit_diff")
        # for backward compatibility, check also if show_commit_diff is True
        show_patch = show_commit_diff is True or show_commit_diff == "full"
        show_stat = (
            show_commit_diff == "stat"
            or (show_commit_diff == "full" and self.savvy_settings.get("show_diffstat"))
        )

        try:
            raw_diff_text = self.git_throwing_silently(
                "diff",
                "--no-color",
                "--patch" if show_patch else None,
                "--stat" if show_stat else None,
                "--cached" if not include_unstaged else None,
                "HEAD^" if amend
                else "HEAD" if include_unstaged
                else None,
                decode=False
            )
        except GitSavvyError as e:
            if (amend or include_unstaged) and "ambiguous argument 'HEAD" in e.stderr:
                raw_diff_text = self.git(
                    "diff",
                    "--no-color",
                    "--patch" if show_patch else None,
                    "--stat" if show_stat else None,
                    "--cached" if not include_unstaged else None,
                    self.the_empty_sha(),
                    decode=False
                )
            else:
                e.show_error_panel()
                raise

        if not raw_diff_text and (show_patch or show_stat) and not include_unstaged:
            settings.set("git_savvy.commit_view.include_unstaged", True)
            settings.set("git_savvy.commit_view.automatically_switched_to_all", True)
            settings.set("git_savvy.diff_view.in_cached_mode", False)
            view.run_command("gs_prepare_commit_refresh_diff", {"sync": sync, "just_switched": True})
            return

        try:
            diff_text = self.strict_decode(raw_diff_text)
        except UnicodeDecodeError:
            diff_text = DECODE_ERROR_MESSAGE
            diff_text += "\n-- Partially decoded output follows; � denotes decoding errors --\n\n"""
            diff_text += raw_diff_text.decode("utf-8", "replace")

        final_text = generate_help_text(view, with_patch_commands=show_patch and bool(diff_text))
        if diff_text:
            final_text += ("\n" + diff_text) if show_patch or show_stat else ""
        else:
            final_text += "\nNothing to commit.\n"

        try:
            region = view.find_by_selector("meta.dropped.git.commit")[0]
        except IndexError:
            region = sublime.Region(view.size())

        if view.substr(region) != final_text:
            replace_view_content(view, final_text, region)
            if show_patch:
                intra_line_colorizer.annotate_intra_line_differences(view, final_text, region.begin())

        if include_unstaged and automatically_switched_to_all and not just_switched:
            enqueue_on_worker(self.maybe_switch_back)

    def maybe_switch_back(self) -> None:
        view = self.view
        settings = view.settings()
        try:
            self.git_throwing_silently("diff", "--cached", "--quiet")
        except GitSavvyError:
            settings.set("git_savvy.commit_view.include_unstaged", False)
            settings.set("git_savvy.diff_view.in_cached_mode", True)
            view.run_command("gs_prepare_commit_refresh_diff", {"sync": False, "just_switched": True})

    def the_empty_sha(self):
        # type: () -> str
        global THE_EMPTY_SHA
        if not THE_EMPTY_SHA:
            THE_EMPTY_SHA = self.git('mktree', stdin='').strip()
        return THE_EMPTY_SHA


class gs_commit_view_unstage_in_all_mode(TextCommand, GitCommand):
    def run(self, edit, whole_file=False):
        # type: (sublime.Edit, bool) -> None
        view = self.view
        self.add_all_tracked_files()
        settings = view.settings()
        settings.set("git_savvy.commit_view.include_unstaged", False)
        settings.set("git_savvy.diff_view.in_cached_mode", True)
        view.run_command("gs_diff_stage_or_reset_hunk")

        history = settings.get("git_savvy.diff_view.history") or []
        if history:
            args, patch, cursor_pts, in_cached_mode = history.pop()
            history.append((["add", "-u"], patch, cursor_pts, in_cached_mode))
            settings.set("git_savvy.diff_view.history", history)


class GsPrepareCommitFocusEventListener(ViewEventListener):
    @classmethod
    def is_applicable(cls, settings):
        return settings.get("git_savvy.commit_view")

    @classmethod
    def applies_to_primary_view_only(cls):
        return False

    def on_activated(self):
        self.view.run_command("gs_prepare_commit_refresh_diff", {"sync": False})

    # Must be "async", see: https://github.com/timbrel/GitSavvy/pull/1382
    def on_selection_modified_async(self) -> None:
        view = self.view
        in_dropped_content = any(
            r.contains(s) or r.intersects(s)
            for r in view.find_by_selector("git-savvy.make-commit meta.dropped.git.commit")
            for s in view.sel()
        )
        view.set_read_only(in_dropped_content)

    def on_text_command(self, command_name, args):
        # type: (str, Dict) -> Union[None, str]
        if command_name != "select_all":
            return None

        view = self.view
        cursor = cursor_position(view)
        if cursor is None:
            return None

        if not view.match_selector(cursor, "meta.commit.message"):
            return None

        r = view.find_by_selector("meta.commit.message")[0]
        set_selection(
            view,
            # Do not select the trailing space because that's where the
            # read-only section begins!  We want "select_all" followed by
            # "delete" to leave one line intact.
            [sublime.Region(r.begin(), r.end() - 1)]
        )
        return "noop"


def cursor_position(view):
    # type: (sublime.View) -> Optional[sublime.Point]
    sel = view.sel()
    frozen_sel = [r for r in sel]
    if len(frozen_sel) == 1 and frozen_sel[0].empty():
        return frozen_sel[0].b
    return None


@text_command
def set_selection(view, regions):
    # type: (sublime.View, List[sublime.Region]) -> None
    sel = view.sel()
    sel.clear()
    sel.add_all(regions)


class GsPedanticEnforceEventListener(EventListener, SettingsMixin):
    """
    Set regions to warn for pedantic commits
    """

    def on_selection_modified(self, view: sublime.View):
        if 'make_commit' not in view.settings().get('syntax', ''):
            return

        if not self.savvy_settings.get('pedantic_commit'):
            return

        subject_line_limit = self.savvy_settings.get('pedantic_commit_first_line_length')
        body_line_limit = self.savvy_settings.get('pedantic_commit_message_line_length')
        warning_length = self.savvy_settings.get('pedantic_commit_warning_length')

        if self.savvy_settings.get('pedantic_commit_ruler'):
            rulers = self.find_rulers(view, subject_line_limit, body_line_limit)
            view.settings().set("rulers", rulers)

        warning_lines, illegal_lines = self.find_too_long_lines(
            view, subject_line_limit, body_line_limit, warning_length
        )
        view.add_regions(
            'make_commit_warning',
            warning_lines,
            scope='invalid.deprecated.line-too-long.git-commit',
            flags=sublime.RegionFlags.DRAW_NO_FILL | sublime.RegionFlags.NO_UNDO
        )
        view.add_regions(
            'make_commit_illegal',
            illegal_lines,
            scope='invalid.deprecated.line-too-long.git-commit',
            flags=sublime.RegionFlags.NO_UNDO
        )

    def find_rulers(self, view, subject_line_limit, body_line_limit):
        # type: (sublime.View, int, int) -> List[int]
        ruler_rules = (
            ("meta.commit.message.subject", subject_line_limit, 40),
            ("meta.commit.message.body",    body_line_limit,     0),  # noqa: E241
        )
        return [
            ruler
            for pt in flatten(map(tuple, view.sel()))
            for selector, ruler, min_line_length in ruler_rules
            if view.match_selector(pt, selector)
            if (
                min_line_length == 0
                or len((view.substr(view.line(pt))).rstrip()) >= min_line_length
            )
        ]

    def find_too_long_lines(self, view, subject_line_limit, body_line_limit, warning_length):
        # type: (sublime.View, int, int, int) -> Tuple[List[sublime.Region], List[sublime.Region]]
        warning_lines = []
        illegal_lines = []
        row_rules = {
            0: (subject_line_limit, subject_line_limit + warning_length),
            1: (0,                  0),                                  # noqa: E241
            2: (body_line_limit,    body_line_limit + warning_length),   # noqa: E241
        }

        for line in flatten(map(view.lines, view.find_by_selector("meta.commit.message"))):
            row, _ = view.rowcol(line.a)
            length = len(view.substr(line).rstrip())
            warn_threshold, error_threshold = row_rules[min(row, 2)]
            if length > warn_threshold:
                warning_lines.append(sublime.Region(
                    line.a + warn_threshold,
                    min(line.a + error_threshold, line.b)
                ))
            if length > error_threshold:
                illegal_lines.append(sublime.Region(
                    line.a + error_threshold,
                    line.b
                ))
        return warning_lines, illegal_lines


def extract_commit_message(view):
    # type: (sublime.View) -> str
    return extract_first_region(view, "meta.commit.message")


def extract_commit_subject(view):
    # type: (sublime.View) -> str
    return extract_first_region(view, "meta.commit.message.subject")


def extract_first_region(view, selector):
    # type: (sublime.View, str) -> str
    try:
        region = view.find_by_selector(selector)[0]
    except IndexError:
        return ""

    return view.substr(region)


class gs_commit_view_do_commit(TextCommand, GitCommand):

    """
    Take the text of the current view (minus the help message text) and
    make a commit using the text for the commit message.
    """

    def run(self, edit, message=None):
        enqueue_on_worker(self.run_impl, message)

    def run_impl(self, commit_message=None):
        window = self.view.window()
        if not window:
            return

        settings = self.view.settings()
        if settings.get("git_savvy.commit_view.is_commiting", False):
            return

        if commit_message is None:
            commit_message = extract_commit_message(self.view)

        settings.set("git_savvy.commit_view.is_commiting", True)
        window.status_message("Committing...")
        try:
            self.git(
                "commit",
                "-a" if settings.get("git_savvy.commit_view.include_unstaged") else None,
                "--amend" if settings.get("git_savvy.commit_view.amend") else None,
                "-F",
                "-",
                stdin=commit_message
            )
        finally:
            settings.set("git_savvy.commit_view.is_commiting", False)

        window.status_message("Committed successfully.")

        # We close views on the left side which initiated the fixup
        for v in reversed(adjacent_views_on_the_left(self.view)):
            initiated_message = v.settings().get("initiated_fixup_commit")
            if initiated_message and initiated_message in extract_commit_subject(self.view):
                v.close()
            break

        # We want to refresh and maybe close open diff views.
        diff_views = mark_all_diff_views(window, self.repo_path)
        # Since we're closing the commit view, the next focused view will
        # be the exact next view on the left.  If this is a marked diff view,
        # it receives an `on_activated` event, refreshes, and maybe closes.
        #
        # Everything fine, *but* the user would see this diff view for a moment,
        # before it magically disappears.  So, instead, peek at the left views,
        # maybe close them, and only after that close this commit view all in one
        # synchronous task.
        handled = []
        for v in takewhile(
            lambda v: v in diff_views,
            reversed(adjacent_views_on_the_left(self.view))
        ):
            handled.append(v)
            v.run_command("gs_diff_refresh", {"sync": True})
            # We can break the *sync* loop if refreshing did *not* close the view.
            # That exact view will be the next focused view after closing the commit
            # view and we just probed that it will not get away magically in a split
            # second.
            if v.is_valid():
                v.settings().set("git_savvy.ignore_next_activated_event", True)
                break

        self.view.close()
        for v in diff_views:
            if v not in handled:
                v.run_command("gs_diff_refresh", {"sync": False})
        util.view.refresh_gitsavvy_interfaces(window)


def adjacent_views_on_the_left(view):
    # type: (sublime.View) -> List[sublime.View]
    window = view.window()
    if not window:
        return []
    group, idx = window.get_view_index(view)
    return window.views_in_group(group)[:idx]


def mark_all_diff_views(window, repo_path):
    # type: (sublime.Window, str) -> List[sublime.View]
    open_diff_views = []
    for view in window.views():
        if is_relevant_diff_view(view, repo_path):
            open_diff_views.append(view)
            view.settings().set("git_savvy.just_committed", True)
    return open_diff_views


def is_relevant_diff_view(view, repo_path):
    # type: (sublime.View, str) -> bool
    settings = view.settings()
    return (
        settings.get("git_savvy.diff_view")
        and settings.get("git_savvy.repo_path") == repo_path
    )


class gs_commit_view_sign(TextCommand, GitCommand):

    """
    Sign off on the commit with full name and email.
    """

    def run(self, edit):
        config_name = self.git("config", "user.name").strip()
        config_email = self.git("config", "user.email").strip()
        commit_message = extract_commit_message(self.view)

        sign_text = COMMIT_SIGN_TEXT.format(name=config_name, email=config_email)
        new_commit_message = commit_message.rstrip() + sign_text + "\n"

        view_text = self.view.substr(sublime.Region(0, self.view.size()))
        new_view_text = new_commit_message + view_text[len(commit_message):]
        replace_view_content(self.view, new_view_text)


class gs_commit_view_close(TextCommand, GitCommand):

    """
    Perform commit action on commit view close if `commit_on_close` setting
    is enabled.
    """

    def run(self, edit):
        if self.view.settings().get("git_savvy.commit_on_close"):
            message_txt = extract_commit_message(self.view).strip()
            if message_txt:
                self.view.run_command("gs_commit_view_do_commit", {"message": message_txt})
            else:
                self.view.close()

        elif self.view.settings().get("git_savvy.prompt_on_abort_commit"):
            message_txt = extract_commit_message(self.view).strip()
            if not message_txt or sublime.ok_cancel_dialog(CONFIRM_ABORT):
                self.view.close()

        else:
            self.view.close()


class gs_commit_log_helper(TextCommand, LogHelperMixin):
    def run(self, edit, prefix="fixup! ", move_to_eol=True):
        view = self.view
        subject = extract_commit_subject(view).strip()
        clean_subject = cleanup_subject(subject)
        cursor = view.sel()[0].begin()

        def action(entry):
            # type: (LogEntry) -> None
            text = "{}{}".format(prefix, entry.summary)
            replace_view_content(view, text, region=view.line(cursor))
            if move_to_eol:
                view.sel().clear()
                view.sel().add(len(text))

        def preselected_commit(items):
            # type: (List[LogEntry]) -> int
            return next(chain(
                head(idx for idx, item in enumerate(items) if item.summary == clean_subject),
                head(
                    idx for idx, item in enumerate(items)
                    if (
                        not item.summary.startswith("fixup! ")
                        and not item.summary.startswith("squash! ")
                    )
                ) if prefix else [],
                [-1]
            ))

        self.show_log_panel(action, preselected_commit=preselected_commit)


def cleanup_subject(subject):
    # type: (str) -> str
    if subject.startswith("fixup! "):
        return subject[7:]
    elif subject.startswith("squash! "):
        return subject[8:]
    return subject
