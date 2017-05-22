from sublime_plugin import EventListener

from . import util


class GsInterfaceFocusEventListener(EventListener):

    """
    Trigger handlers for view life-cycle events.
    """

    def on_activated(self, view):
        util.view.refresh_gitsavvy(view)

    def on_close(self, view):
        util.view.handle_closed_view(view)


git_view_syntax = {
    'MERGE_MSG': 'Packages/GitSavvy/syntax/make_commit.sublime-syntax',
    'COMMIT_EDITMSG': 'Packages/GitSavvy/syntax/make_commit.sublime-syntax',
    'PULLREQ_EDITMSG': 'Packages/GitSavvy/syntax/make_commit.sublime-syntax',
    'git-rebase-todo': 'Packages/GitSavvy/syntax/rebase_interactive.sublime-syntax',
}


class GitCommandFromTerminal(EventListener):
    def on_load(self, view):
        if view.file_name():
            name = view.file_name().split("/")[-1]
            if name in git_view_syntax.keys():
                view.set_syntax_file(git_view_syntax[name])
                view.settings().set("git_savvy.{}_view".format(name), True)
                view.set_scratch(True)

    def on_pre_close(self, view):
        if view.file_name():
            name = view.file_name().split("/")[-1]
            if name in git_view_syntax.keys():
                view.run_command("save")
