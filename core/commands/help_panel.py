from __future__ import annotations
from textwrap import dedent

import sublime
import sublime_plugin

from ...common import util
from GitSavvy.core.base_commands import GsTextCommand
from GitSavvy.core.view import replace_view_content


__all__ = (
    "HelpPanelListener",
    "gs_blame_help_panel",
    "gs_inline_diff_help_panel",
    "gs_diff_help_panel",
    "gs_show_commit_help_panel",
    "gs_show_file_at_commit_help_panel",
    "gs_log_graph_help_panel",
    "gs_line_history_help_panel",
    "gs_stash_help_panel",
)


PANEL_NAME = "GitSavvy_Help"
PANEL_SYNTAX = "Packages/GitSavvy/syntax/help.sublime-syntax"
PANEL_TAG = "git_savvy.help_view"


def ensure_panel(
    window: sublime.Window,
    name: str = PANEL_NAME,
    syntax: str = PANEL_SYNTAX,
    tag: str = PANEL_TAG,
    read_only: bool = True,
) -> sublime.View:
    output_view = get_panel(window)
    if output_view:
        return output_view

    output_view = window.create_output_panel(name)
    if read_only:
        output_view.set_read_only(True)
    if syntax:
        output_view.set_syntax_file(syntax)
    if tag:
        output_view.settings().set(tag, True)
    return output_view


def get_panel(window: sublime.Window, name: str = PANEL_NAME) -> sublime.View | None:
    return window.find_output_panel(name)


def show_panel(window: sublime.Window, name: str = PANEL_NAME) -> None:
    window.run_command("show_panel", {"panel": "output.{}".format(PANEL_NAME)})


def hide_panel(window: sublime.Window, name: str = PANEL_NAME) -> None:
    window.run_command("hide_panel", {"panel": "output.{}".format(PANEL_NAME)})


def panel_is_visible(window, name=PANEL_NAME):
    # type: (sublime.Window, str) -> bool
    return window.active_panel() == "output.{}".format(name)


def ensure_panel_is_visible(window, name=PANEL_NAME):
    # type: (sublime.Window, str) -> None
    if not panel_is_visible(window, name):
        window.run_command("show_panel", {"panel": "output.{}".format(name)})


class HelpPanelListener(sublime_plugin.EventListener):
    def on_window_command(self, window: sublime.Window, command_name: str, args: dict | None):
        if (
            command_name == 'hide_panel'
            and panel_is_visible(window)
            and (panel := get_panel(window))
            and (previous_panel := panel.settings().get("git_savvy.previous_panel"))
        ):
            ensure_panel_is_visible(window, previous_panel)
            if next_panel := get_panel(window, previous_panel):
                sublime.set_timeout(
                    lambda: window.focus_view(next_panel)
                )


class GsAbstractOpenHelpPanel(GsTextCommand):
    key_bindings = "<MUST IMPLEMENT `key_bindings` in subclass>"

    def run(self, edit):
        view = self.view
        window = view.window()
        assert window

        if (
            view.element() == "output:output"
            and (settings := view.settings())
            and settings.get("git_savvy.show_commit_view")
        ):
            previous_panel = "show_commit_info"
        else:
            previous_panel = None

        panel = ensure_panel(window)
        content = panel.substr(sublime.Region(0, panel.size()))
        next_content = self.key_bindings.format(cr=util.super_key)
        if panel_is_visible(window) and content == next_content:
            hide_panel(window)
        else:
            show_panel(window)
            replace_view_content(panel, next_content)
            panel.show(0)
        panel.settings().set("git_savvy.previous_panel", previous_panel)


class gs_blame_help_panel(GsAbstractOpenHelpPanel):
    key_bindings = dedent("""\
    ### Actions ###
    [enter]        show all commands
    [o]            open commit under cursor
    [l]            show log of surrounding commits

    [w]            ignore white space
    [f]            detect moved or copied lines within same file
    [c]            detect moved or copied lines within same commit
    [a]            detect moved or copied lines within all commits

    [<]            Blame previous commit
    [>]            Blame next commit
    [alt+<]        Blame a commit before this line's commit
    [alt+>]        Blame next commit

    ### Navigation ###
    [g]            show (commit under cursor) in graph
    [h]            move through the current chunks
    [,]/[.]        go to next/previous chunk (also: [j]/[k] in vintageous mode)

    ### Other ###
    [?]            show this help popup
    [{cr}-,]       Change Settings for current syntax
    """)


class gs_inline_diff_help_panel(GsAbstractOpenHelpPanel):
    key_bindings = dedent("""\
    ### Actions ###
    [tab]          switch between staged/unstaged area
    [a]/[b]        show/hide the a and b (red or green) sides of the diff
    [l]            stage line, unstage in cached mode
    [h]            stage hunk, unstage in cached mode
    [L]            reset line
    [H]            reset hunk
    [{cr}-z]       undo last action

    [c]/[C]        commit ([C] to include unstaged)
    [m]            amend previous commit
    [f]            make fixup commit

    ### Navigation ####
    [o]            open file position in working dir
    [O]            open file revision at hunk
    [g]            show context in graph
    [n]/[p]        show next/previous revision of this file
    [,]/[.]        go to next/previous hunk (also: [j]/[k] in vintageous mode)


    ### Other ####
    [?]            show this help popup
    [{cr}-,]       Change Settings for current syntax
    """)


class gs_diff_help_panel(GsAbstractOpenHelpPanel):
    key_bindings = dedent("""\
    ### Actions ###
    [tab]          switch between staged/unstaged area
    [s]/[u]/[d]    stage, unstage, or discard hunk or selection
    [S]/[U]/[D]    stage, unstage, or discard complete file
    [{cr}-z]       undo last action

    [c]/[C]        commit ([C] to include unstaged)
    [m]            amend previous commit
    [f]            make fixup commit
    [a]            set intent-to-add (only if the file is untracked)

    ### Navigation ###
    [o]            open file at hunk
    [,]/[.]        go to next/previous hunk (also: [j]/[k] in vintageous mode)

    ### Other ###
    [w]            ignore white space
    [+]/[-]        show more/less context lines
    [?]            show this help popup
    [{cr}-,]       Change Settings for current syntax
    """)


class gs_show_commit_help_panel(GsAbstractOpenHelpPanel):
    key_bindings = dedent("""\
    ### Navigation ###
    [o]            open file revision at hunk; on `#issues`, open a browser
    [O]            open working dir file
    [n]/[p]        show next/previous commit
    [h]            open commit on GitHub (if available)
    [f]            initiate fixup commit
    [W]            reWord commit message
    [E]            Edit commit
    [g]            show in graph
    [,]/[.]        go to next/previous hunk (also: [j]/[k] in vintageous mode)

    ### Other ###
    [w]            ignore white space
    [?]            show this help popup
    [{cr}-,]       Change Settings for current syntax
    """)


class gs_show_file_at_commit_help_panel(GsAbstractOpenHelpPanel):
    key_bindings = dedent("""\
    ### Actions ###
    [o]            open commit
    [O]            open working dir file
    [g]            show in graph
    [n]/[p]        show next/previous revision of this file
    [l]            choose different revision of this file
    [,]/[.]        go to next/previous hunk

    ### Other ###
    [i]            show commit info popup
    [?]            show this help popup
    """)


class gs_log_graph_help_panel(GsAbstractOpenHelpPanel):
    key_bindings = dedent("""\
    [enter]        open main menu with additional commands
    [o]            open commit in a new view; on `#issues`, open a browser
    [m]/[M]        toggle commit panel on the bottom, [M] to also focus the panel
    [{cr}+C]       copy commit's hash, subject or a combination to the clipboard

    [s]            toggle to overview mode
    [a]            toggle --all / in overview mode: toggle tags
    [f]            edit filters verbatim
    [l]            list paths to add or remove
    [P]/[N]        Show previous tips of the current branch
    [F]            toggle filters

    ### Rebasing ###
    [r]            open rebase menu
    [W]            reWord commit message
    [E]            Edit commit
    [R]            Rebase --interactive from here

    ### Navigation ###
    [{cr}-r]       Goto tags, branches...
    [h]            Goto HEAD commit
    [up]/[down]    go to previous/next commit (also: [,]/[.] or [j]/[k] in vintageous mode)
    Use [alt+up]/[alt+down] for wider jumps

    ### Other ###
    [?]            show this help popup
    [tab]          transition to next dashboard
    [shift-tab]    transition to previous dashboard
    [{cr}-,]       Change Settings for current syntax
    """)


class gs_line_history_help_panel(GsAbstractOpenHelpPanel):
    key_bindings = dedent("""\
    ### Actions ###
    [o]            open commit; on `#issues`, open a browser
    [O]            open file revision at hunk
    [g]            show in graph
    [f]            make fixup commit
    [W]            reWord commit message
    [E]            Edit commit
    [,]/[.]        go to next/previous hunk (also: [j]/[k] in vintageous mode)

    ### Other ###
    [?]            show this help popup
    """)


class gs_stash_help_panel(GsAbstractOpenHelpPanel):
    key_bindings = dedent("""\
    ### Actions ###
    [enter]        Open action panel
    [a]            apply stash
    [p]            pop stash
    [D]            drop stash
    [,]/[.]        go to next/previous hunk (also: [j]/[k] in vintageous mode)

    ### Other ###
    [?]            show this help popup
    [{cr}-,]       Change Settings for current syntax
    """)
