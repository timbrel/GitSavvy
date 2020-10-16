from functools import lru_cache
import os

import sublime
from sublime_plugin import WindowCommand, TextCommand
from sublime_plugin import EventListener, ViewEventListener

from . import intra_line_colorizer
from ..git_command import GitCommand
from ..runtime import enqueue_on_worker
from ..utils import focus_view
from ..view import replace_view_content
from ...common import util
from ...core.settings import SettingsMixin
from GitSavvy.core.fns import filter_
from GitSavvy.core.ui_mixins.quick_panel import short_ref


__all__ = (
    "gs_commit",
    "gs_prepare_commit_refresh_diff",
    "gs_commit_view_do_commit",
    "gs_commit_view_sign",
    "gs_commit_view_close",
    "gs_commit_log_helper",
    "GsPrepareCommitFocusEventListener",
    "GsPedanticEnforceEventListener",
)


MYPY = False
if MYPY:
    from typing import Optional, Tuple


COMMIT_HELP_TEXT_EXTRA = """##
## "<tab>"       at the very first char to see the recent log
## "fixup<tab>"  to create a fixup subject  (short: "fix<tab>")
## "squash<tab>  to create a squash subject (short: "sq<tab>")
## "#<tab>"      to reference a GitHub issue (or: "owner/repo#<tab>")
## In the diff below, [o] will open the file under the cursor.
"""

COMMIT_HELP_TEXT_ALT = """

## To make a commit, type your commit message and close the window.
## To cancel the commit, delete the commit message and close the window.
## To sign off on the commit, press {key}-S.
""".format(key=util.super_key) + COMMIT_HELP_TEXT_EXTRA


COMMIT_HELP_TEXT = """

## To make a commit, type your commit message and press {key}-ENTER.
## To cancel the commit, close the window. To sign off on the commit,
## press {key}-S.
""".format(key=util.super_key) + COMMIT_HELP_TEXT_EXTRA

COMMIT_SIGN_TEXT = """

Signed-off-by: {name} <{email}>
"""

COMMIT_TITLE = "COMMIT: {}"

CONFIRM_ABORT = "Confirm to abort commit?"


def compute_identifier_for_view(view):
    # type: (sublime.View) -> Optional[Tuple]
    settings = view.settings()
    return (
        settings.get('git_savvy.repo_path'),
    ) if settings.get('git_savvy.commit_view') else None


class gs_commit(WindowCommand, GitCommand):

    """
    Display a transient window to capture the user's desired commit message.
    If the user is amending the previous commit, pre-populate the commit
    message area with the previous commit message.
    """

    def run(self, repo_path=None, include_unstaged=False, amend=False):
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
            settings.set("git_savvy.commit_view.amend", amend)
            commit_on_close = self.savvy_settings.get("commit_on_close")
            settings.set("git_savvy.commit_on_close", commit_on_close)
            prompt_on_abort_commit = self.savvy_settings.get("prompt_on_abort_commit")
            settings.set("git_savvy.prompt_on_abort_commit", prompt_on_abort_commit)

            view.set_syntax_file("Packages/GitSavvy/syntax/make_commit.sublime-syntax")
            view.run_command("gs_handle_vintageous")

            title = COMMIT_TITLE.format(os.path.basename(repo_path))
            view.set_name(title)
            view.set_scratch(True)  # ignore dirty on actual commit
            self.initialize_view(view, include_unstaged, amend)

    def initialize_view(self, view, include_unstaged, amend):
        # type: (sublime.View, bool, bool) -> None
        merge_msg_path = os.path.join(self.repo_path, ".git", "MERGE_MSG")

        help_text = (
            COMMIT_HELP_TEXT_ALT
            if self.savvy_settings.get("commit_on_close")
            else COMMIT_HELP_TEXT
        )

        if amend:
            last_commit_message = self.git("log", "-1", "--pretty=%B").strip()
            initial_text = last_commit_message + help_text
        elif os.path.exists(merge_msg_path):
            with util.file.safe_open(merge_msg_path, "r") as f:
                initial_text = f.read() + help_text
        else:
            initial_text = help_text

        commit_help_extra_file = self.savvy_settings.get("commit_help_extra_file") or ".commit_help"
        commit_help_extra_path = os.path.join(self.repo_path, commit_help_extra_file)
        if os.path.exists(commit_help_extra_path):
            with util.file.safe_open(commit_help_extra_path, "r", encoding="utf-8") as f:
                initial_text += f.read()

        replace_view_content(view, initial_text)
        view.run_command("gs_prepare_commit_refresh_diff")


class gs_prepare_commit_refresh_diff(TextCommand, GitCommand):
    def run(self, edit, sync=True):
        # type: (sublime.Edit, bool) -> None
        if sync:
            self.run_impl()
        else:
            enqueue_on_worker(self.run_impl)

    def run_impl(self):
        # type: () -> None
        view = self.view
        settings = view.settings()
        include_unstaged = settings.get("git_savvy.commit_view.include_unstaged")
        amend = settings.get("git_savvy.commit_view.amend")
        show_commit_diff = self.savvy_settings.get("show_commit_diff")
        # for backward compatibility, check also if show_commit_diff is True
        shows_diff = show_commit_diff is True or show_commit_diff == "full"
        shows_stat = (
            show_commit_diff == "stat"
            or (show_commit_diff == "full" and self.savvy_settings.get("show_diffstat"))
        )
        diff_text = self.git(
            "diff",
            "--no-color",
            "--patch" if shows_diff else None,
            "--stat" if shows_stat else None,
            "--cached" if not include_unstaged else None,
            "HEAD^" if amend
            else "HEAD" if include_unstaged
            else None
        )
        if diff_text:
            final_text = ("\n" + diff_text) if shows_diff or shows_stat else ""
        else:
            final_text = "\nNothing to commit.\n"

        try:
            region = view.find_by_selector("git-savvy.diff")[0]
        except IndexError:
            region = sublime.Region(view.size())

        if view.substr(region) == final_text:
            return

        replace_view_content(view, final_text, region)
        if shows_diff:
            intra_line_colorizer.annotate_intra_line_differences(view, final_text, region.begin())


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


class GsPedanticEnforceEventListener(EventListener, SettingsMixin):
    """
    Set regions to warn for pedantic commits
    """

    def on_selection_modified(self, view):
        if 'make_commit' not in view.settings().get('syntax', ''):
            return

        if not self.savvy_settings.get('pedantic_commit'):
            return

        self.view = view
        self.first_line_limit = self.savvy_settings.get('pedantic_commit_first_line_length')
        self.body_line_limit = self.savvy_settings.get('pedantic_commit_message_line_length')
        self.warning_length = self.savvy_settings.get('pedantic_commit_warning_length')

        self.comment_start_region = self.view.find_by_selector("meta.dropped.git.commit")
        self.first_comment_line = None
        if self.comment_start_region:
            self.first_comment_line = self.view.rowcol(self.comment_start_region[0].begin())[0]

        if self.savvy_settings.get('pedantic_commit_ruler'):
            self.view.settings().set("rulers", self.find_rulers())

        warning, illegal = self.find_too_long_lines()
        self.view.add_regions(
            'make_commit_warning', warning,
            scope='invalid.deprecated.line-too-long.git-commit', flags=sublime.DRAW_NO_FILL)
        self.view.add_regions(
            'make_commit_illegal', illegal,
            scope='invalid.deprecated.line-too-long.git-commit')

    def find_rulers(self):
        on_first_line = False
        on_message_body = False

        subject_near_limit = len(self.view.substr(self.view.line(sublime.Region(0))).rstrip()) >= 40

        for region in self.view.sel():
            first_line = self.view.rowcol(region.begin())[0]
            last_line = self.view.rowcol(region.end())[0]

            if first_line == 0 and subject_near_limit:
                on_first_line = True

            if self.first_comment_line:
                if first_line in range(2, self.first_comment_line) or last_line in range(2, self.first_comment_line):
                    on_message_body = True
            else:
                if first_line >= 2 or last_line >= 2:
                    on_message_body = True

        new_rulers = []
        if on_first_line:
            new_rulers.append(self.first_line_limit)

        if on_message_body:
            new_rulers.append(self.body_line_limit)

        return new_rulers

    def find_too_long_lines(self):
        warning_lines = []
        illegal_lines = []

        first_line = self.view.line(sublime.Region(0, 0))
        length = len(self.view.substr(first_line).rstrip())
        if length > self.first_line_limit:
            warning_lines.append(sublime.Region(
                first_line.a + self.first_line_limit,
                min(first_line.a + self.first_line_limit + self.warning_length, first_line.b)))

        if length > self.first_line_limit + self.warning_length:
            illegal_lines.append(
                sublime.Region(first_line.a + self.first_line_limit + self.warning_length, first_line.b))

        # Add second line to illegal
        if self.first_comment_line is None or self.first_comment_line > 1:
            illegal_lines.append(sublime.Region(self.view.text_point(1, 0), self.view.text_point(2, 0) - 1))

        if self.first_comment_line:
            body_region = sublime.Region(self.view.text_point(2, 0), self.comment_start_region[0].begin())
        else:
            body_region = sublime.Region(self.view.text_point(2, 0), self.view.size())

        for line in self.view.lines(body_region):
            length = line.b - line.a
            if length > self.body_line_limit:
                warning_lines.append(sublime.Region(
                    line.a + self.body_line_limit,
                    min(line.a + self.body_line_limit + self.warning_length, line.b)))

            if self.body_line_limit + self.warning_length < length:
                illegal_lines.append(sublime.Region(line.a + self.body_line_limit + self.warning_length, line.b))

        return [warning_lines, illegal_lines]


def extract_commit_message(view):
    # type: (sublime.View) -> str
    try:
        region = view.find_by_selector("meta.commit.message")[0]
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
        window.status_message("Commiting...")
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

        self.view.close()
        util.view.refresh_gitsavvy_interfaces(window)


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


class gs_commit_log_helper(TextCommand, GitCommand):
    def run(self, edit, prefix="fixup! ", move_to_eol=True):
        view = self.view
        window = view.window()
        assert window

        cursor = view.sel()[0].begin()
        items = self.log(limit=100)

        def on_done(idx):
            window.run_command("hide_panel", {"panel": "output.show_commit_info"})  # type: ignore[union-attr]
            if idx == -1:
                return
            entry = items[idx]
            text = "{}{}".format(prefix, entry.summary)
            replace_view_content(view, text, region=view.line(cursor))
            if move_to_eol:
                view.sel().clear()
                view.sel().add(len(text))

        # `on_highlight` also gets called `on_done`, and then
        # our "show|hide_panel" side-effects get muddled.  We
        # reduce the side-effect here using `lru_cache`.
        @lru_cache(1)
        def on_highlight(idx):
            entry = items[idx]
            window.run_command("gs_show_commit_info", {  # type: ignore[union-attr]  # mypy bug
                "commit_hash": entry.short_hash
            })

        format_item = lambda entry: "  ".join(filter_(
            (
                entry.short_hash,
                short_ref(entry.ref) if not entry.ref.startswith("HEAD ->") else "",
                entry.summary
            )
        ))
        window.show_quick_panel(
            list(map(format_item, items)),
            on_done,
            flags=sublime.MONOSPACE_FONT | sublime.KEEP_OPEN_ON_FOCUS_LOST,
            on_highlight=on_highlight
        )
