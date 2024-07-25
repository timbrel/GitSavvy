import re

import sublime
from sublime_plugin import EventListener, TextCommand, WindowCommand

from ..runtime import enqueue_on_worker
from ..git_command import GitCommand
from ..ui_mixins.quick_panel import PanelCommandMixin
from ..ui_mixins.quick_panel import show_stash_panel
from ..ui_mixins.input_panel import show_single_line_input_panel
from ..utils import flash, hprint, uprint
from ..view import replace_view_content
from ...common import util


from typing import Optional, Union
StashId = Union[int, str]


class RevalidateStashView(EventListener):
    def on_activated(self, view):
        # type: (sublime.View) -> None
        # Subtle runtime nuance: this "on_activated" runs sync when
        # the view is created in `create_stash_view` t.i. even before
        # we change its settings.  Thus we don't revalidate initially.
        stash_id = view.settings().get("git_savvy.stash_view.stash_id", None)  # type: Optional[StashId]
        if stash_id is None:
            return

        view.settings().set("git_savvy.stash_view.status", "revalidate")
        enqueue_on_worker(revalidate, view, stash_id)  # <== on worker


def revalidate(view, stash_id):
    # type: (sublime.View, StashId) -> None
    view_text = view.substr(sublime.Region(0, view.size()))

    try:
        git = GitCommand()
        git.view = view  # type: ignore
        stash_patch = git.show_stash(stash_id)
    except Exception:
        stash_patch = ":-("

    if stash_patch == view_text:
        view.settings().set("git_savvy.stash_view.status", "valid")
    else:
        view.settings().set("git_savvy.stash_view.status", "invalid")


class SelectStashIdMixin(TextCommand):
    def run(self, edit, stash_id=None):
        # type: (sublime.Edit, Optional[StashId]) -> None
        if stash_id is not None:
            self.do(stash_id)
            return

        _stash_id = self.view.settings().get("git_savvy.stash_view.stash_id", None)  # type: Optional[StashId]
        if _stash_id is not None:
            view_status = self.view.settings().get("git_savvy.stash_view.status")
            if view_status == "revalidate":
                # Enqueue as worker task to ensure that we run after
                # on_activated's `revalidate` has finished which runs
                # on the worker as well.
                enqueue_on_worker(self.view.run_command, self.name())
            elif view_status == "invalid":
                flash(
                    self.view,
                    "Nothing done, the stash you're looking at is outdated. "
                )
                hprint(
                    "GitSavvy: The stash you're looking at is outdated. "
                    "Maybe its number ({}) changed because you modified the "
                    "stash list since opening this view. ".format(_stash_id)
                )
            elif view_status == "valid":
                self.do(_stash_id)
            else:
                raise RuntimeError("stash view in invalid state '{}'".format(view_status))
            return

        show_stash_panel(self.on_done)

    def on_done(self, stash_id):
        # type: (Optional[StashId]) -> None
        if stash_id is not None:
            self.do(stash_id)

    def do(self, stash_id):
        # type: (StashId) -> None
        raise NotImplementedError


class GsStashApplyCommand(SelectStashIdMixin, GitCommand):

    """
    Apply the selected stash.
    """

    def do(self, stash_id):
        # type: (StashId) -> None
        try:
            self.apply_stash(stash_id)
            flash(self.view, "Successfully applied stash ({}).".format(stash_id))
        finally:
            util.view.refresh_gitsavvy(self.view)


class GsStashPopCommand(SelectStashIdMixin, GitCommand):

    """
    Pop the selected stash.
    """

    def do(self, stash_id):
        # type: (StashId) -> None
        try:
            self.pop_stash(stash_id)
            flash(self.view, "Successfully popped stash ({}).".format(stash_id))
            if self.view.settings().get("git_savvy.stash_view.stash_id", None) == stash_id:
                self.view.close()
        finally:
            util.view.refresh_gitsavvy(self.view)


DROP_UNDO_MESSAGE = """\
GitSavvy: Dropped stash ({}), in case you want to undo, run:
  $ git branch tmp {}
"""
EXTRACT_COMMIT = re.compile(r"\((.*)\)$")


class GsStashDropCommand(SelectStashIdMixin, GitCommand):

    """
    Drop the selected stash.
    """

    @util.actions.destructive(description="drop a stash")
    def do(self, stash_id):
        # type: (StashId) -> None
        rv = self.drop_stash(stash_id)
        match = EXTRACT_COMMIT.search(rv.strip())
        if match:
            commit = match.group(1)
            uprint(DROP_UNDO_MESSAGE.format(stash_id, commit))
        flash(
            self.view,
            "Successfully dropped stash ({}). "
            "Open Sublime console for undo instructions.".format(stash_id)
        )

        if self.view.settings().get("git_savvy.stash_view.stash_id", None) == stash_id:
            self.view.close()
        else:
            util.view.refresh_gitsavvy(self.view)


class GsStashCommand(PanelCommandMixin, WindowCommand, GitCommand):
    default_actions = [
        ["gs_stash_apply", "Apply stash"],
        ["gs_stash_pop", "Pop stash"],
        ["gs_stash_drop", "Drop stash"],
    ]


class GsStashShowCommand(WindowCommand, GitCommand):

    """
    For each selected stash, open a new window to display the diff
    for that stash.
    """

    def run(self, stash_ids=[]):
        if len(stash_ids) == 0:
            show_stash_panel(self.do_show)
        else:
            for stash_id in stash_ids:
                self.do_show(stash_id)

    def do_show(self, stash_id):
        if stash_id is None:
            return

        stash_view = self.create_stash_view(stash_id)
        content = self.show_stash(stash_id)
        replace_view_content(stash_view, content)

    def create_stash_view(self, stash_id):
        title = "stash@{{{}}}".format(stash_id)
        description = self.description_of_stash(str(stash_id))
        if description:
            title += " - " + description
        stash_view = util.view.create_scratch_view(self.window, "stash", {
            "title": title,
            "syntax": "Packages/GitSavvy/syntax/diff.sublime-syntax",
            "git_savvy.repo_path": self.repo_path,
            "git_savvy.stash_view.stash_id": stash_id,
            "git_savvy.stash_view.status": "valid",
        })
        return stash_view

    def description_of_stash(self, stash_id):
        # type: (str) -> str
        for stash in self.current_state().get("stashes", []):
            if stash.id == stash_id:
                return stash.description
        return ""


class GsStashSaveCommand(WindowCommand, GitCommand):

    """
    Create a new stash from the user's unstaged changes.
    """

    def run(self, include_untracked=False, stash_of_indexed=False):
        self.include_untracked = include_untracked
        self.stash_of_indexed = stash_of_indexed
        show_single_line_input_panel("Description:", "", self.on_done)

    def on_done(self, description):
        if not self.stash_of_indexed:
            self.create_stash(description, include_untracked=self.include_untracked)
        else:
            # Create a temporary stash of everything, including staged files.
            self.git("stash", "--keep-index")
            # Stash only the indexed files, since they're the only thing left in the working directory.
            self.create_stash(description)
            # Clean out the working directory.
            self.git("reset", "--hard")
            try:
                # Pop the original stash, taking us back to the original working state.
                self.apply_stash(1)
                # Get the diff from the originally staged files, and remove them from the working dir.
                stash_text = self.git("stash", "show", "--no-color", "-p")
                self.git("apply", "-R", stdin=stash_text)
                # Delete the temporary stash.
                self.drop_stash(1)
                # Remove all changes from the staging area.
                self.git("reset")
            except Exception as e:
                # Restore the original working state.
                self.pop_stash(1)
                raise e
        util.view.refresh_gitsavvy(self.window.active_view())
