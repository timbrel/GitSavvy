import sublime
from sublime_plugin import WindowCommand, TextCommand
import re
from ..git_command import GitCommand
from .log import GsLogActionCommand, GsLogCommand
from .navigate import GsNavigate
from ...common import util
from ..ui_mixins.quick_panel import show_branch_panel


COMMIT_NODE_CHAR = "●"
COMMIT_NODE_CHAR_OPTIONS = "●*"
GRAPH_CHAR_OPTIONS = " /_\|\-."
COMMIT_LINE = re.compile(
    "^[{graph_chars}]*[{node_chars}][{graph_chars}]* (?P<commit_hash>[a-f0-9]{{5,40}})".format(
        graph_chars=GRAPH_CHAR_OPTIONS, node_chars=COMMIT_NODE_CHAR_OPTIONS))


class LogGraphMixin(object):

    """
    Open a new window displaying an ASCII-graphic representation
    of the repo's branch relationships.
    """

    def run(self, file_path=None, title=None):
        self._file_path = file_path
        self.title = title or "GRAPH"
        sublime.set_timeout_async(self.run_async)

    def run_async(self):
        # need to get repo_path before the new view is created.
        repo_path = self.repo_path
        view = util.view.get_scratch_view(self, "log_graph", read_only=True)
        view.settings().set("git_savvy.repo_path", repo_path)
        view.settings().set("git_savvy.file_path", self._file_path)
        view.settings().set("git_savvy.git_graph_args", self.get_graph_args())
        view.settings().set("word_wrap", False)
        view.set_syntax_file("Packages/GitSavvy/syntax/graph.sublime-syntax")
        view.set_name(self.title)
        view.sel().clear()
        view.run_command("gs_log_graph_refresh")
        view.run_command("gs_log_graph_navigate")

    def get_graph_args(self):
        savvy_settings = sublime.load_settings("GitSavvy.sublime-settings")
        args = savvy_settings.get("git_graph_args")
        if self._file_path:
            file_path = self.get_rel_path(self._file_path)
            args = args + ["--", file_path]
        return args


class GsLogGraphRefreshCommand(TextCommand, GitCommand):

    """
    Refresh the current graph view with the latest commits.
    """

    def run(self, edit):
        file_path = self.file_path
        if file_path:
            graph_content = "File: {}\n\n".format(file_path)
        else:
            graph_content = ""

        args = self.view.settings().get("git_savvy.git_graph_args")
        graph_content += self.git(*args)
        graph_content = re.sub('(^[{}]*)\*'.format(GRAPH_CHAR_OPTIONS),
            r'\1'+COMMIT_NODE_CHAR, graph_content, flags=re.MULTILINE)

        self.view.run_command("gs_replace_view_text", {"text": graph_content, "nuke_cursors": True})
        self.view.run_command("gs_log_graph_more_info")

        self.view.run_command("gs_handle_vintageous")
        self.view.run_command("gs_handle_arrow_keys")


class GsLogGraphCurrentBranch(LogGraphMixin, WindowCommand, GitCommand):
    pass


class GsLogGraphAllBranches(LogGraphMixin, WindowCommand, GitCommand):

    def get_graph_args(self):
        args = super().get_graph_args()
        args.append("--all")
        return args


class GsLogGraphByAuthorCommand(LogGraphMixin, WindowCommand, GitCommand):

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


class GsLogGraphByBranchCommand(LogGraphMixin, WindowCommand, GitCommand):

    def run_async(self):
        show_branch_panel(self.on_branch_selection)

    def on_branch_selection(self, branch):
        if branch:
            self._selected_branch = branch
            super().run_async()

    def get_graph_args(self):
        args = super().get_graph_args()
        args.append(self._selected_branch)
        return args


class GsLogGraphCommand(GsLogCommand):
    default_actions = [
        ["gs_log_graph_current_branch", "For current branch"],
        ["gs_log_graph_all_branches", "For all branches"],
        ["gs_log_graph_by_author", "Filtered by author"],
        ["gs_log_graph_by_branch", "Filtered by branch"],
    ]


class GsLogGraphActionCommand(GsLogActionCommand):

    """
    Checkout the commit at the selected line. It is also used by compare_commit_view.
    """
    default_actions = [
        ["show_commit", "Show commit"],
        ["checkout_commit", "Checkout commit"],
        ["compare_against", "Compare commit against ..."],
        ["copy_sha", "Copy the full SHA"]
    ]

    def update_actions(self):
        super().update_actions()
        view = self.window.active_view()
        if view.settings().get("git_savvy.compare_commit_view.target_commit") == "HEAD":
            self.actions.append(["cherry_pick", "Cherry-pick commit"])

        if view.settings().get("git_savvy.log_graph_view"):
            self.actions.extend([
                ["diff_commit", "Diff commit"],
                ["diff_commit_cache", "Diff commit (cached)"],
            ])

    def run(self):
        view = self.window.active_view()

        self.selections = view.sel()

        lines = util.view.get_lines_from_regions(view, self.selections)
        line = lines[0]

        m = COMMIT_LINE.search(line)
        self._commit_hash = m.groupdict()['commit_hash'] if m else ""
        self._file_path = self.file_path

        if not len(self.selections) == 1:
            sublime.status_message("You can only do actions on one commit at a time.")
            return

        super().run(commit_hash=self._commit_hash, file_path=self._file_path)

    def cherry_pick(self):
        self.git("cherry-pick", self._commit_hash)

    def show_file_at_commit(self):
        self.window.run_command(
            "gs_show_file_at_commit",
            {"commit_hash": self._commit_hash, "filepath": self._file_path})


class GsLogGraphNavigateCommand(GsNavigate):

    """
    Travel between commits. It is also used by compare_commit_view.
    """
    offset = 0

    def run(self, edit, **kwargs):
        super().run(edit, **kwargs)
        self.view.window().run_command("gs_log_graph_more_info")

    def get_available_regions(self):
        return self.view.find_by_selector("constant.numeric.graph.commit-hash.git-savvy")


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
        commit_hash = m.groupdict()['commit_hash'] if m else ""

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
