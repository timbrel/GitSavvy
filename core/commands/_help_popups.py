from textwrap import dedent
import re

from ...common import util
from GitSavvy.core.base_commands import GsTextCommand


__all__ = (
    "gs_blame_help_tooltip",
    "gs_inline_diff_help_tooltip",
    "gs_diff_help_tooltip",
    "gs_show_commit_help_tooltip",
    "gs_show_file_at_commit_help_tooltip",
    "gs_log_graph_help_tooltip",
    "gs_line_history_help_tooltip",
    "gs_stash_help_tooltip",
)

CSS = """\
body {
  margin: 0em;
  padding: 1em;
}
h2 {
  margin: 0;
  margin-top: -1em;
  margin-bottom: -0.2em;
  font-weight: normal;
}
h3 {
  margin: 0;
  margin-top: 0.9em;
  margin-bottom: 0.1em;
  font-weight: normal;
}
.shortcut-key {
    color: var(--bluish);
}
"""

HELP_TEMPLATE = """
<html>
<style> {css} </style>
<body> {content} </body>
</html>
"""


class GsAbstractHelpPopup(GsTextCommand):
    key_bindings = "<MUST IMPLEMENT `key_bindings` in subclass>"

    def run(self, edit):
        view = self.view

        def prepare_content(content):
            for line in content.splitlines():
                if not line:
                    yield "<br/>"
                elif line.startswith("## "):
                    yield "<h3>{}</h3>".format(line.strip("# "))
                elif line.startswith("### "):
                    yield "<div><code>{}</code></div>".format(line.strip("# "))
                else:
                    line = line.replace("<", "&lt;").replace(">", "&gt;")
                    line = re.sub(r"(\[.+?\])", r'<span class="shortcut-key">\1</span>', line)
                    line = re.sub(r"`(.+?)`", r'<span class="shortcut-key">\1</span>', line)
                    line = re.sub(r"(\s(?=\s))", r"&nbsp;", line)
                    yield "<div><code>{}</code></div>".format(line)

        content = "\n".join(prepare_content(
            self.help_text().format(cr=util.super_key)))
        html = HELP_TEMPLATE.format(css=CSS, content=content, super_key=util.super_key)
        visible_region = view.visible_region()
        viewport_extent = view.viewport_extent()
        max_width = viewport_extent[0] - 20
        max_height = 900
        view.show_popup(html, 0, visible_region.begin(), max_width, max_height)

    def help_text(self):
        return dedent("""\
        Keyboard Shortcuts:

        {}""").format(self.key_bindings)


class gs_blame_help_tooltip(GsAbstractHelpPopup):
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


class gs_inline_diff_help_tooltip(GsAbstractHelpPopup):
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


class gs_diff_help_tooltip(GsAbstractHelpPopup):
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


class gs_show_commit_help_tooltip(GsAbstractHelpPopup):
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


class gs_show_file_at_commit_help_tooltip(GsAbstractHelpPopup):
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


class gs_log_graph_help_tooltip(GsAbstractHelpPopup):
    key_bindings = dedent("""\
    [enter]        open main menu with additional commands
    [o]            open commit in a new view; on `#issues`, open a browser
    [m]            toggle commit details panel on the bottom
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


class gs_line_history_help_tooltip(GsAbstractHelpPopup):
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


class gs_stash_help_tooltip(GsAbstractHelpPopup):
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
