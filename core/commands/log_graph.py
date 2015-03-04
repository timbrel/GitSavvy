from sublime_plugin import WindowCommand, TextCommand

from ..git_command import GitCommand

LOG_GRAPH_TITLE = "GRAPH"


class GsLogGraphCommand(WindowCommand, GitCommand):

    """
    Open a new window displaying an ASCII-graphic representation
    of the repo's branch relationships.
    """

    def run(self):
        repo_path = self.repo_path
        view = self.window.new_file()
        view.settings().set("git_savvy.log_graph_view", True)
        view.settings().set("git_savvy.repo_path", repo_path)
        view.set_name(LOG_GRAPH_TITLE)
        view.set_scratch(True)
        view.set_read_only(True)
        view.run_command("gs_log_graph_initialize")


class GsLogGraphInitializeCommand(TextCommand, GitCommand):

    def run(self, edit):
        branch_graph = self.git("log", "--oneline", "--graph", "--decorate")
        self.view.run_command("gs_replace_view_text", {"text": branch_graph})
