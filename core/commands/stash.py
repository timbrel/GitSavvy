from sublime_plugin import WindowCommand

from ..git_command import GitCommand
from ..ui_mixins.quick_panel import PanelCommandMixin
from ..ui_mixins.quick_panel import show_stash_panel
from ..ui_mixins.input_panel import show_single_line_input_panel
from ...common import util


MYPY = False
if MYPY:
    from typing import Optional, Union
    StashId = Union[int, str]


class SelectStashIdMixin(WindowCommand):
    def run(self, stash_id=None):
        # type: (Optional[StashId]) -> None
        if stash_id is not None:
            self.do(stash_id)
            return

        view = self.window.active_view()
        if view:
            stash_id = view.settings().get("git_savvy.stash_view.stash_id", None)
            if stash_id is not None:
                self.do(stash_id)
                return

        show_stash_panel(self.on_done)

    def on_done(self, stash_id):
        # type: (Optional[StashId]) -> None
        if stash_id is not None:
            self.do(stash_id)

    def do(self, stash_id):
        # type: (StashId) -> None
        return NotImplemented


class GsStashApplyCommand(SelectStashIdMixin, GitCommand):

    """
    Apply the selected stash.
    """

    def do(self, stash_id):
        # type: (StashId) -> None
        self.apply_stash(stash_id)
        util.view.refresh_gitsavvy(self.window.active_view())


class GsStashPopCommand(SelectStashIdMixin, GitCommand):

    """
    Pop the selected stash.
    """

    def do(self, stash_id):
        # type: (StashId) -> None
        self.pop_stash(stash_id)
        util.view.refresh_gitsavvy(self.window.active_view())


class GsStashDropCommand(SelectStashIdMixin, GitCommand):

    """
    Drop the selected stash.
    """

    @util.actions.destructive(description="drop a stash")
    def do(self, stash_id):
        # type: (StashId) -> None
        self.drop_stash(stash_id)
        util.view.refresh_gitsavvy(self.window.active_view())


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
        stash_view.run_command("gs_replace_view_text", {"text": self.show_stash(stash_id), "nuke_cursors": True})

    def create_stash_view(self, stash_id):
        window = self.window
        repo_path = self.repo_path
        stash_view = util.view.get_scratch_view(self, "stash", read_only=True)
        title = "stash@{{{}}}".format(stash_id)
        stash_view.set_name(title)
        stash_view.set_syntax_file("Packages/GitSavvy/syntax/diff.sublime-syntax")
        stash_view.settings().set("git_savvy.repo_path", repo_path)
        stash_view.settings().set("git_savvy.stash_view.stash_id", stash_id)
        window.focus_view(stash_view)

        return stash_view


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
