import sublime
from sublime_plugin import WindowCommand, TextCommand
import re
import os
from ..git_command import GitCommand
from .navigate import GsNavigate
from ...common import util

COMMIT_NODE_CHAR = "●"
COMMIT_NODE_CHAR_OPTIONS = "●*"
COMMIT_LINE = re.compile("[%s][ /_\|\-.]*([a-z0-9]{3,})" % COMMIT_NODE_CHAR_OPTIONS)


class GsLogGraphBase(WindowCommand, GitCommand):

    """
    Open a new window displaying an ASCII-graphic representation
    of the repo's branch relationships.
    """

    def run(self, file_path=None, title=None):
        self._file_path = file_path
        self.title = title or "GRAPH"
        sublime.set_timeout_async(self.run_async)

    def run_async(self):
        view = util.view.get_scratch_view(self, "log_graph", read_only=True)
        view.settings().set("git_savvy.git_graph_args", self.get_graph_args())
        view.settings().set("git_savvy.repo_path", self.repo_path)
        view.settings().set("git_savvy.compare_commit_view.file_path", self._file_path)
        view.settings().set("word_wrap", False)
        view.set_syntax_file("Packages/GitSavvy/syntax/graph.sublime-syntax")
        view.set_name(self.title)
        view.sel().clear()
        view.run_command("gs_log_graph_refresh")

    def get_graph_args(self):
        savvy_settings = sublime.load_settings("GitSavvy.sublime-settings")
        args = savvy_settings.get("git_graph_args")
        if self._file_path:
            file_path = self._file_path
            file_path = os.path.realpath(file_path)[len(self.repo_path)+1:]
            args = args + ["--", file_path]
        return args


class GsLogGraphRefreshCommand(TextCommand, GitCommand):

    """
    Refresh the current graph view with the latest commits.
    """

    def run(self, edit):
        args = self.view.settings().get("git_savvy.git_graph_args")
        branch_graph = self.git(*args)
        if COMMIT_NODE_CHAR != "*":
            branch_graph = branch_graph.replace("*", COMMIT_NODE_CHAR)
        self.view.run_command("gs_replace_view_text", {"text": branch_graph, "nuke_cursors": True})
        self.view.run_command("gs_log_graph_more_info")

        self.view.run_command("gs_handle_vintageous")


class GsLogGraphCurrentBranch(GsLogGraphBase):
    pass


class GsLogGraphByAuthorCommand(GsLogGraphBase):

    """
    Open a quick panel containing all committers for the active
    repository, ordered by most commits, Git name, and email.
    Once selected, display a quick panel with all commits made
    by the specified author.
    """

    def run_async(self):
        email = self.git("config", "user.email").strip()
        self._entries = []

        commiter_str = self.git("shortlog", "-sne", "HEAD")
        for line in commiter_str.split('\n'):
            m = re.search('\s*(\d*)\s*(.*)\s<(.*)>', line)
            if m is None:
                continue
            commit_count, author_name, author_email = m.groups()
            author_text = "{} <{}>".format(author_name, author_email)
            self._entries.append((commit_count, author_name, author_email, author_text))

        self.window.show_quick_panel(
            [entry[3] for entry in self._entries],
            self.on_author_selection,
            flags=sublime.MONOSPACE_FONT,
            selected_index=(list(line[2] for line in self._entries)).index(email)
        )

    def on_author_selection(self, index):
        if index == -1:
            return
        self._selected_author = self._entries[index][3]
        super().run_async()

    def get_graph_args(self):
        args = super().get_graph_args()
        args.insert(1, "--author={}".format(self._selected_author))
        return args


class GsLogGraphByBranchCommand(GsLogGraphBase):

    def run_async(self):
        self.all_branches = [b.name_with_remote for b in self.get_branches()]

        if hasattr(self, '_selected_branch') and self._selected_branch in self.all_branches:
            pre_selected_index = self.all_branches.index(self._selected_branch)
        else:
            pre_selected_index = self.all_branches.index(self.get_current_branch_name())

        self.window.show_quick_panel(
            self.all_branches,
            self.on_branch_selection,
            flags=sublime.MONOSPACE_FONT,
            selected_index=pre_selected_index
        )

    def on_branch_selection(self, index):
        if index < 0:
            return
        self._selected_branch = self.all_branches[index]
        super().run_async()

    def get_graph_args(self):
        args = super().get_graph_args()
        args.append(self._selected_branch)
        return args


class GsLogGraphCommand(WindowCommand, GitCommand):
    def run(self, file_path=None, current_file=False):
        self._file_path = self.file_path if current_file else file_path
        options_array = [
            "For current branch",
            "Filtered by author",
            "Filtered by branch",
        ]
        self.window.show_quick_panel(
            options_array,
            self.on_option_selection,
            flags=sublime.MONOSPACE_FONT
        )

    def on_option_selection(self, index):
        if index < 0:
            return

        if index == 0:
            self.window.run_command("gs_log_graph_current_branch", {"file_path": self._file_path})
        elif index == 1:
            self.window.run_command("gs_log_graph_by_author", {"file_path": self._file_path})
        elif index == 2:
            self.window.run_command("gs_log_graph_by_branch", {"file_path": self._file_path})


class GsLogGraphActionCommand(TextCommand, GitCommand):

    """
    Checkout the commit at the selected line. It is also used by compare_commit_view.
    """

    def run(self, edit):
        self.actions = [
            "Show commit",
            "Checkout commit"
         ]
        if self.view.settings().get("git_savvy.compare_commit_view"):
            self.actions.append("Cherry-pick commit")

        self.selections = self.view.sel()

        lines = util.view.get_lines_from_regions(self.view, self.selections)
        line = lines[0]

        m = COMMIT_LINE.search(line)
        self.commit_hash = m.group(1) if m else ""

        if not len(self.selections) == 1:
            sublime.status_message("You can only do actions on one commit at a time.")
            return

        self.view.window().show_quick_panel(
            self.actions,
            self.on_action_selection,
            selected_index=self.quick_panel_branch_diff_history_idx,
            flags=sublime.MONOSPACE_FONT
        )

    def on_action_selection(self, index):
        if index == -1:
            return
        self.quick_panel_branch_diff_history_idx = index

        # Show commit
        if index == 0:
            self.view.window().run_command("gs_show_commit", {"commit_hash": self.commit_hash})

        # Checkout commit
        if index == 1:
            self.checkout_ref(self.commit_hash)
            util.view.refresh_gitsavvy(self.view)

        # Cherry-pick  commit
        if index == 2:
            self.view.window().run_command("gs_cherry_pick", {"target_hash": self.commit_hash})


class GsLogGraphNavigateCommand(GsNavigate):

    """
    Travel between commits. It is also used by compare_commit_view.
    """

    def run(self, edit, **kwargs):
        super().run(edit, **kwargs)
        self.view.run_command("show_at_center")
        self.view.window().run_command("gs_log_graph_more_info")

    def get_available_regions(self):
        return [self.view.line(region) for region in
                self.view.find_by_selector("constant.numeric.graph.commit-hash.git-savvy")]


class GsLogGraphMoreInfoCommand(TextCommand, GitCommand):

    """
    Show all info about a commit in a quick panel. It is also used by compare_commit_view.
    """

    def run(self, edit):
        savvy_settings = sublime.load_settings("GitSavvy.sublime-settings")
        show_more = savvy_settings.get("graph_show_more_commit_info")
        if not show_more:
            return

        selections = self.view.sel()
        if len(selections) != 1:
            return

        lines = util.view.get_lines_from_regions(self.view, selections)
        if not lines:
            return
        line = lines[0]

        m = COMMIT_LINE.search(line)
        commit_hash = m.group(1) if m else ""

        if len(commit_hash) <= 3:
            return

        self.view.window().run_command("gs_show_commit_info", {"commit_hash": commit_hash})


class GsLogGraphToggleMoreInfoCommand(TextCommand, WindowCommand, GitCommand):

    """
    Toggle `graph_show_more_commit_info` setting. It is also used by compare_commit_view.
    """

    def run(self, edit):
        savvy_settings = sublime.load_settings("GitSavvy.sublime-settings")
        show_more = not savvy_settings.get("graph_show_more_commit_info")
        savvy_settings.set("graph_show_more_commit_info", show_more)
        if not show_more:
            self.view.window().run_command("hide_panel")

        self.view.run_command("gs_log_graph_more_info")
