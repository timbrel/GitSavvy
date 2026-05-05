from __future__ import annotations
from functools import lru_cache
import os
import re

import sublime
from sublime_plugin import TextCommand, ViewEventListener

from ..base_commands import GsTextCommand, GsWindowCommand
from ..fns import chain, filter_
from ..runtime import (
    enqueue_on_ui, enqueue_on_worker, on_worker,
    run_as_text_command, text_command, throttled
)
from ..ui_mixins.quick_panel import show_log_panel
from ..utils import flash, focus_view
from ..view import apply_position, capture_cur_position, replace_view_content, Position
from ...common import util
from GitSavvy.core.git_command import GitCommand, GitSavvyError
from GitSavvy.core.git_mixins.history import CommitInfo

from .log import LogMixin


__all__ = (
    "gs_show_file_at_commit",
    "gs_show_file_at_commit_refresh",
    "gs_show_file_at_commit_just_refresh_reference_document",
    "gs_show_current_file",
    "gs_show_file_at_commit_open_log",
    "gs_show_file_at_commit_open_previous_commit",
    "gs_show_file_at_commit_open_next_commit",
    "gs_show_file_at_commit_open_commit",
    "gs_show_file_at_commit_open_file_on_working_dir",
    "gs_show_file_at_commit_open_graph_context",
    "gs_show_file_at_commit_open_info_popup",
    "RenewReferenceDocument",
)


from typing import Dict, NamedTuple, Optional, Set, Tuple


SHOW_COMMIT_TITLE = "FILE: {}, {}"
views_with_reference_document: Set[sublime.View] = set()


class NextCommit(NamedTuple):
    commit_hash: Optional[str]
    error_message: Optional[str] = None


# Reapply the reference document as Sublime forgets these on reload.
class RenewReferenceDocument(ViewEventListener):
    @classmethod
    def is_applicable(cls, settings):
        return settings.get("git_savvy.show_file_at_commit_view")

    @classmethod
    def applies_to_primary_view_only(cls):
        return False

    def on_activated(self):
        if self.view not in views_with_reference_document:
            self.view.run_command("gs_show_file_at_commit_just_refresh_reference_document")

    def on_close(self):
        views_with_reference_document.discard(self.view)


def compute_identifier_for_view(view: sublime.View) -> Optional[Tuple]:
    settings = view.settings()
    return (
        settings.get('git_savvy.repo_path'),
        settings.get('git_savvy.file_path'),
        settings.get('git_savvy.show_file_at_commit_view.commit')
    ) if settings.get('git_savvy.show_file_at_commit_view') else None


class gs_show_file_at_commit(GsWindowCommand):

    def run(self, commit_hash: str = None, filepath: str = None,
            position: Optional[Position] = None, lang: Optional[str] = None) -> None:
        fix_position = False
        if not filepath:
            view = self._current_view()
            if not view:
                raise RuntimeError("can't grab an active view.")

            filepath = view.file_name()
            if not filepath:
                self.window.status_message("Not available for unsaved/unnamed files.")
                return

            if position is None:
                position = capture_cur_position(view)
                fix_position = position is not None

            if lang is None:
                lang = view.settings().get('syntax')

        if commit_hash:
            commit_hash = self.get_short_hash(commit_hash)
            # Callers may pass a historical path from a diff hunk.  Keep the
            # view anchored to the working-dir path and resolve historical
            # names only when loading content for a specific commit.
            filepath = self.filename_at_head(filepath, commit_hash)
        else:
            commit_hash = self.recent_commit("HEAD", filepath)
            if not commit_hash:
                self.window.status_message("No older revision of this file found.")
                return

            if fix_position:
                assert position
                row, col, offset = position
                line = self.reverse_find_matching_lineno_between_files(
                    (commit_hash, self.filename_at_commit(filepath, commit_hash)),
                    (None, filepath),
                    row + 1
                )
                position = Position(line - 1, col, offset)

        this_id = (
            self.repo_path,
            filepath,
            commit_hash
        )
        for view in self.window.views():
            if compute_identifier_for_view(view) == this_id:
                focus_view(view)
                if position:
                    run_as_text_command(apply_position, view, *position)
                break
        else:
            self.create_view(commit_hash, filepath, position, lang)

    def create_view(self, commit_hash: str, file_path: str,
                    position: Optional[Position], syntax: Optional[str]) -> None:
        active_view = self.window.active_view()
        title = SHOW_COMMIT_TITLE.format(
            os.path.basename(file_path),
            commit_hash,
        )
        view = util.view.create_scratch_view(self.window, "show_file_at_commit", {
            "title": title,
            "syntax": syntax or util.file.guess_syntax_for_file(self.window, file_path),
            "git_savvy.repo_path": self.repo_path,
            "git_savvy.file_path": file_path,
            "git_savvy.show_file_at_commit_view.commit": commit_hash,
            "auto_indent": False,
            "detect_indentation": False,
            "translate_tabs_to_spaces": False,
        })
        pass_next_commits_info_along(active_view, to=view)

        view.run_command("gs_show_file_at_commit_refresh", {
            "position": position
        })


class _gs_show_file_at_commit_refresh_mixin(GsTextCommand):
    def update_reference_document(self, commit_hash: str, file_path: str) -> None:
        self.view.set_reference_document(self.previous_file_version(commit_hash, file_path))
        views_with_reference_document.add(self.view)

    def previous_file_version(self, current_commit: str, file_path: str) -> str:
        previous_commit = get_previous_commit(self, self.view, current_commit, file_path)
        if previous_commit:
            file_path_at_commit = self.filename_at_commit(file_path, previous_commit)
            return self.get_file_content_at_commit(file_path_at_commit, previous_commit)
        else:
            # For initial revisions of a file, everything is new/added, and we
            # just compare with the empty "".
            return ""


class gs_show_file_at_commit_just_refresh_reference_document(_gs_show_file_at_commit_refresh_mixin):
    def run(self, edit: sublime.Edit, position: Position = None, sync: bool = True) -> None:
        view = self.view
        settings = view.settings()
        file_path = settings.get("git_savvy.file_path")
        commit_hash = settings.get("git_savvy.show_file_at_commit_view.commit")
        view.reset_reference_document()
        enqueue_on_worker(self.update_reference_document, commit_hash, file_path)


class gs_show_file_at_commit_refresh(_gs_show_file_at_commit_refresh_mixin):
    def run(self, edit: sublime.Edit, position: Position = None, sync: bool = True) -> None:
        view = self.view
        settings = view.settings()
        file_path = settings.get("git_savvy.file_path")
        commit_hash = settings.get("git_savvy.show_file_at_commit_view.commit")

        def program():
            file_path_at_commit = self.filename_at_commit(file_path, commit_hash)
            text = self.get_file_content_at_commit(file_path_at_commit, commit_hash)
            render(view, text, position)
            view.reset_reference_document()
            commit_details = self.commit_subject_and_date(commit_hash)
            self.update_title(commit_details, file_path)
            self.update_status_bar(commit_details)
            enqueue_on_worker(self.update_reference_document, commit_hash, file_path)

        if sync:
            program()
        else:
            enqueue_on_worker(program)

    def update_status_bar(self, commit_details: CommitInfo) -> None:
        view = self.view
        settings = view.settings()
        window = view.window()
        if not window:
            return
        message = "On commit {}{}{}".format(
            commit_details.short_hash,
            f": {commit_details.subject}" if commit_details.subject else "",
            f" ({commit_details.date})" if commit_details.date else "")

        # Status messages are only temporary shown and in this case
        # the roundabout 4 seconds just aren't enough. Loop here to
        # extend Sublime Text's hardcoded duration.
        def sink(n=0):
            if (
                view != window.active_view()
                or commit_details.short_hash != settings.get("git_savvy.show_file_at_commit_view.commit")
            ):
                return

            flash(self.view, message)
            if n < 4:
                sublime.set_timeout_async(lambda: sink(n + 1), 3000)

        sink()

    def update_title(self, commit_details: CommitInfo, file_path: str) -> None:
        details = ", ".join(filter_((commit_details.subject, commit_details.date)))
        message = "{}{}".format(
            commit_details.short_hash,
            f" {details}" if details else ""
        )
        title = SHOW_COMMIT_TITLE.format(
            os.path.basename(file_path),
            message
        )
        self.view.set_name(title)


@text_command
def render(view: sublime.View, text: str, position: Optional[Position]) -> None:
    replace_view_content(view, text)
    if position:
        apply_position(view, *position)


class gs_show_file_at_commit_open_previous_commit(GsTextCommand):
    def run(self, edit) -> None:
        view = self.view

        settings = view.settings()
        file_path = settings.get("git_savvy.file_path")
        commit_hash = settings.get("git_savvy.show_file_at_commit_view.commit")

        previous_commit = get_previous_commit(self, view, commit_hash, file_path)
        if not previous_commit:
            flash(view, "No older commit found.")
            return

        settings.set("git_savvy.show_file_at_commit_view.commit", previous_commit)

        position = capture_cur_position(view)
        if position is not None:
            row, col, offset = position
            line = self.find_matching_lineno_between_files(
                (commit_hash, self.filename_at_commit(file_path, commit_hash)),
                (previous_commit, self.filename_at_commit(file_path, previous_commit)),
                row + 1
            )
            position = Position(line - 1, col, offset)

        popup_was_visible = settings.get("git_savvy.show_file_at_commit.info_popup_visible")
        view.run_command("gs_show_file_at_commit_refresh", {
            "position": position
        })
        if popup_was_visible:
            view.run_command("gs_show_file_at_commit_open_info_popup")


class gs_show_file_at_commit_open_next_commit(GsTextCommand):
    def run(self, edit) -> None:
        view = self.view

        settings = view.settings()
        file_path: str = settings.get("git_savvy.file_path")
        commit_hash: str = settings.get("git_savvy.show_file_at_commit_view.commit")

        next_commit = get_next_commit(self, view, commit_hash, file_path)
        if next_commit.error_message:
            flash(view, next_commit.error_message)
            return

        commit = next_commit.commit_hash
        if not commit:
            branch_hint = recall_branch_hint_for(view)
            if branch_hint is not None:
                flash(view, f"No newer commit found on {friendly_branch_hint(branch_hint)}.")
            else:
                flash(view, "No newer commit found.")
            return

        settings.set("git_savvy.show_file_at_commit_view.commit", commit)
        position = capture_cur_position(view)
        if position is not None:
            row, col, offset = position
            line = self.find_matching_lineno_between_files(
                (commit_hash, self.filename_at_commit(file_path, commit_hash)),
                (commit, self.filename_at_commit(file_path, commit)),
                row + 1
            )
            position = Position(line - 1, col, offset)

        popup_was_visible = settings.get("git_savvy.show_file_at_commit.info_popup_visible")
        view.run_command("gs_show_file_at_commit_refresh", {
            "position": position
        })
        if popup_was_visible:
            view.run_command("gs_show_file_at_commit_open_info_popup")


def get_next_commit(
    cmd: GitCommand,
    view: sublime.View,
    commit_hash: str,
    file_path: str | None = None
) -> NextCommit:
    commit_hash = cmd.get_short_hash(commit_hash)
    if next_commit := recall_next_commit_for(view, commit_hash):
        return NextCommit(next_commit)

    try:
        branch_hint = get_branch_hint_for_view(cmd, view, commit_hash)
    except ValueError:
        return NextCommit(None, "Can't find a newer commit; it looks orphaned.")

    try:
        next_commits = cmd.next_commits(
            commit_hash,
            file_path,
            follow=bool(file_path),
            branch_hint=branch_hint
        )
    except GitSavvyError as e:
        if branch_hint and branch_hint_is_gone(branch_hint, e):
            return NextCommit(None, correlated_branch_is_gone_msg(branch_hint))
        raise
    if next_commits is None:
        return NextCommit(None, commit_is_not_on_branch_msg(commit_hash, branch_hint))
    remember_next_commit_for(view, next_commits)
    return NextCommit(next_commits.get(commit_hash))


def get_previous_commit(
    cmd: GitCommand,
    view: sublime.View,
    commit_hash: str,
    file_path: str | None = None
) -> Optional[str]:
    commit_hash = cmd.get_short_hash(commit_hash)
    if previous := recall_previous_commit_for(view, commit_hash):
        return previous

    if previous := cmd.previous_commit(commit_hash, file_path, follow=bool(file_path)):
        remember_next_commit_for(view, {previous: commit_hash})
    return previous


def get_branch_hint_for_view(cmd: GitCommand, view: sublime.View, commit_hash: str) -> str:
    if (branch_hint := recall_branch_hint_for(view)) is not None:
        return branch_hint

    branch_hint = cmd.get_branch_hint_for_commit(commit_hash)
    remember_branch_hint_for(view, branch_hint)
    return branch_hint


def remember_next_commit_for(view: sublime.View, mapping: Dict[str, str]) -> None:
    settings = view.settings()
    store: Dict[str, str] = settings.get("git_savvy.next_commits", {})
    store.update(mapping)
    settings.set("git_savvy.next_commits", store)


def recall_next_commit_for(view: sublime.View, commit_hash: str) -> Optional[str]:
    settings = view.settings()
    store: Dict[str, str] = settings.get("git_savvy.next_commits", {})
    return store.get(commit_hash)


def recall_previous_commit_for(view: sublime.View, commit_hash: str) -> Optional[str]:
    settings = view.settings()
    store: Dict[str, str] = settings.get("git_savvy.next_commits", {})
    try:
        return next(previous for previous, next_commit in store.items() if next_commit == commit_hash)
    except StopIteration:
        return None


def remember_branch_hint_for(view: sublime.View, branch_hint: str) -> None:
    view.settings().set("git_savvy.history.branch_hint", branch_hint)


def recall_branch_hint_for(view: sublime.View) -> Optional[str]:
    return view.settings().get("git_savvy.history.branch_hint")


def friendly_branch_hint(branch_hint: str) -> str:
    if not branch_hint:
        return "HEAD"
    return drop_prefix(branch_hint, ("refs/heads/", "refs/remotes/"))


def drop_prefix(s: str, prefixes: str | tuple[str, ...]) -> str:
    if isinstance(prefixes, str):
        prefixes = (prefixes,)
    try:
        prefix = next(prefix for prefix in prefixes if s.startswith(prefix))
    except StopIteration:
        return s
    return s[len(prefix):]


def branch_hint_is_gone(branch_hint: str, error: GitSavvyError) -> bool:
    return f"ambiguous argument '{branch_hint}" in error.stderr


def correlated_branch_is_gone_msg(branch_hint: str) -> str:
    return f"The correlated branch {friendly_branch_hint(branch_hint)} is gone."


def commit_is_not_on_branch_msg(commit_hash: str, branch_hint: str) -> str:
    return f"Commit {commit_hash} is no longer on {friendly_branch_hint(branch_hint)}."


def pass_next_commits_info_along(view: Optional[sublime.View], to: sublime.View) -> None:
    if not view:
        return
    from_settings, to_settings = view.settings(), to.settings()
    if from_settings.get("git_savvy.file_path") != to_settings.get("git_savvy.file_path"):
        return
    store: Dict[str, str] = from_settings.get("git_savvy.next_commits", {})
    if store:
        to_settings.set("git_savvy.next_commits", store)
    branch_hint = from_settings.get("git_savvy.history.branch_hint")
    if branch_hint is not None:
        to_settings.set("git_savvy.history.branch_hint", branch_hint)


class gs_show_current_file(LogMixin, GsTextCommand):
    """
    Show a panel of commits of current file on current branch and
    then open the file at the selected commit.
    """

    def run(self, edit: sublime.Edit) -> None:  # type: ignore[override]
        if not self.file_path:
            if not self.view.is_read_only() and not self.view.file_name():
                flash(self.view, "Not for unsaved/unnamed files.")
            else:
                flash(self.view, "The view does not refer any file name.")
            return
        super().run(file_path=self.file_path)

    def do_action(self, commit_hash, **kwargs):
        view = self.view
        position = capture_cur_position(view)
        if position is not None:
            assert self.file_path
            row, col, offset = position
            line = self.reverse_find_matching_lineno_between_files(
                (commit_hash, self.filename_at_commit(self.file_path, commit_hash)),
                (None, self.file_path),
                row + 1
            )
            position = Position(line - 1, col, offset)

        self.window.run_command("gs_show_file_at_commit", {
            "commit_hash": commit_hash,
            "filepath": self.file_path,
            "position": position,
            "lang": view.settings().get('syntax')
        })


class gs_show_file_at_commit_open_log(GsTextCommand):
    """
    Show a panel of commits of this historical file and live-preview
    highlighted commits in the current show-file-at-commit view.
    """
    selected_index: int = 0

    @on_worker
    def run(self, edit: sublime.Edit) -> None:
        shown_commit = self.initial_commit = \
            self.view.settings().get("git_savvy.show_file_at_commit_view.commit")
        self.initial_position = capture_cur_position(self.view)
        file_path = self.file_path

        try:
            branch_hint = get_branch_hint_for_view(self, self.view, self.initial_commit)
        except ValueError:
            branch_hint = self.initial_commit

        entries = self.log_generator(
            file_path=file_path,
            branch=branch_hint,
            follow=True,
            topo_order=True,
            show_panel_on_error=False
        )

        leading = []
        try:
            for idx, entry in enumerate(entries):
                leading.append(entry)
                if entry.long_hash.startswith(shown_commit):
                    self.selected_index = idx
                    break
        except GitSavvyError as e:
            if branch_hint and branch_hint_is_gone(branch_hint, e):
                flash(self.view, correlated_branch_is_gone_msg(branch_hint))
            else:
                e.show_error_panel()
                raise

        enqueue_on_ui(
            show_log_panel,
            chain(leading, entries),
            self.on_done,
            selected_index=self.selected_index,
            on_highlight=self.on_highlight
        )

    def on_done(self, commit):
        if commit:
            return  # nothing further to do as we already updated `on_highlight`

        # Revert
        view = self.view
        view.settings().set("git_savvy.show_file_at_commit_view.commit", self.initial_commit)
        position = self.initial_position
        view.run_command("gs_show_file_at_commit_refresh", {
            "position": position
        })

    @lru_cache(1)
    def on_highlight(self, commit):
        if commit:
            sublime.set_timeout_async(throttled(self._on_highlight, commit), 10)

    def _on_highlight(self, commit):
        view = self.view
        commit = self.get_short_hash(commit)
        previous_commit = view.settings().get("git_savvy.show_file_at_commit_view.commit")
        view.settings().set("git_savvy.show_file_at_commit_view.commit", commit)
        position = capture_cur_position(view)
        if position is not None:
            row, col, offset = position
            file_path = view.settings().get("git_savvy.file_path")
            line = self.find_matching_lineno_between_files(
                (previous_commit, self.filename_at_commit(file_path, previous_commit)),
                (commit, self.filename_at_commit(file_path, commit)),
                row + 1
            )
            position = Position(line - 1, col, offset)

        view.run_command("gs_show_file_at_commit_refresh", {
            "position": position,
            "sync": False,
        })


class gs_show_file_at_commit_open_commit(TextCommand):
    def run(self, edit) -> None:
        window = self.view.window()
        if not window:
            return

        settings = self.view.settings()
        commit_hash = settings.get("git_savvy.show_file_at_commit_view.commit")
        assert commit_hash

        window.run_command("gs_show_commit", {"commit_hash": commit_hash})


class gs_show_file_at_commit_open_file_on_working_dir(GsTextCommand):
    def run(self, edit) -> None:
        window = self.view.window()
        if not window:
            return

        settings = self.view.settings()
        commit_hash = settings.get("git_savvy.show_file_at_commit_view.commit")
        file_path = settings.get("git_savvy.file_path")
        assert commit_hash
        assert file_path

        row, col = self.view.rowcol(self.view.sel()[0].begin())
        line = self.find_matching_lineno_between_files(
            (commit_hash, self.filename_at_commit(file_path, commit_hash)),
            (None, file_path),
            row + 1
        )
        window.open_file(
            "{file}:{line}:{col}".format(file=file_path, line=line, col=col + 1),
            sublime.ENCODED_POSITION
        )


class gs_show_file_at_commit_open_graph_context(GsTextCommand):
    def run(self, edit) -> None:
        window = self.view.window()
        if not window:
            return

        settings = self.view.settings()
        commit_hash = settings.get("git_savvy.show_file_at_commit_view.commit")
        assert commit_hash

        window.run_command("gs_graph", {
            "all": True,
            "follow": self.get_short_hash(commit_hash),
        })


class gs_show_file_at_commit_open_info_popup(GsTextCommand):
    def run(self, edit):
        # type: (...) -> None
        settings = self.view.settings()
        commit_hash = settings.get("git_savvy.show_file_at_commit_view.commit")
        show_patch = self.savvy_settings.get("show_full_commit_info")
        show_diffstat = self.savvy_settings.get("show_diffstat")
        text = self.read_commit(commit_hash, None, show_diffstat, show_patch)

        prelude = re.split(r"^diff", text, 1, re.M)[0].rstrip()
        content = format_as_html(prelude)
        width, _ = self.view.viewport_extent()
        visible_region = self.view.visible_region()

        self.view.show_popup(
            content,
            max_width=width,
            max_height=450,
            location=visible_region.begin(),
            on_hide=lambda: settings.set("git_savvy.show_file_at_commit.info_popup_visible", False)
        )
        settings.set("git_savvy.show_file_at_commit.info_popup_visible", True)


def format_as_html(
    content: str,
    *,
    syntax: str = "show_commit",
    panel_name: str = "gs_format_helper",
) -> str:
    panel = sublime.active_window().create_output_panel(panel_name, unlisted=True)
    if not syntax.endswith(".sublime-syntax"):
        syntax = f"Packages/GitSavvy/syntax/{syntax}.sublime-syntax"
    panel.set_syntax_file(syntax)
    replace_view_content(panel, content)
    return panel.export_to_html(minihtml=True)
