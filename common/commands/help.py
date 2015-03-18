"""
Interactive help system loader.
"""

import re
import webbrowser

import sublime
from sublime_plugin import WindowCommand, TextCommand

from ...common import util


re_anchor = re.compile(r"^#+ .+", flags=re.MULTILINE)
re_anchor_words = re.compile(r"[a-zA-Z0-9]+")
re_link_scope = re.compile(r"\bmeta\.link\.inline\.markdown\b")


def get_page_and_anchor(view):
    if util.view.get_is_view_of_type(view, "status"):
        return "status.md", None
    if util.view.get_is_view_of_type(view, "tags"):
        return "tag_mgmt.md", "git-tags"
    if util.view.get_is_view_of_type(view, "log_graph"):
        return "history.md", "git-graph"
    if util.view.get_is_view_of_type(view, "branch"):
        return "branch_mgmt.md", "git-branch"
    if util.view.get_is_view_of_type(view, "commit"):
        anchor = ("git-amend-previous-commit"
                  if view.settings().get("git_savvy.commit_view.amend")
                  else None)
        return "commit.md", anchor
    if util.view.get_is_view_of_type(view, "diff"):
        anchor = ("git-diff-cached"
                  if view.settings().get("git_savvy.diff_view.in_cached_mode")
                  else "git-diff")
        return "staging.md", anchor

    if util.view.get_is_view_of_type(view, "inline_diff"):
        anchor = ("git-diff-current-file-inline"
                  if view.settings().set("git_savvy.inline_diff.cached")
                  else "git-diff-current-file-inline-cached")
        return "staging.md", anchor

    return "README.md", None


class GsHelp(WindowCommand):

    """
    Load and display GitSavvy help message.  If in special GitSavvy view,
    load view-specific help information.
    """

    def run(self):
        current_view = self.window.active_view()
        page, anchor = get_page_and_anchor(current_view)

        view = util.view.get_read_only_view(self, "help")
        view.set_name("GITSAVVY HELP")

        syntax_file = util.file.get_syntax_for_file("*.md")
        view.set_syntax_file(syntax_file)

        view.run_command("gs_help_browse", {"page": page, "anchor": anchor})


class GsHelpBrowse(TextCommand):

    """
    Replace the content of the view with the provided text.
    """

    def run(self, edit, page, anchor, add_to_history=True):
        settings = self.view.settings()
        previous_page = settings.get("git_savvy.help.page")

        if not page == previous_page:
            settings.set("git_savvy.help.page", page)
            content = sublime.load_resource("Packages/GitSavvy/docs/" + page)

            is_read_only = self.view.is_read_only()
            self.view.set_read_only(False)
            self.view.replace(edit, sublime.Region(0, self.view.size()), content)
            self.view.set_read_only(is_read_only)

            self.collapse_links()

        else:
            content = self.view.substr(sublime.Region(0, self.view.size()))

        if add_to_history:
            history = settings.get("git_savvy.help.history") or []
            history.append((page, anchor))
            settings.set("git_savvy.help.history", history)

        pt = self.find_anchor(content, anchor)

        sel = self.view.sel()
        sel.clear()
        sel.add(sublime.Region(pt, pt))
        self.view.show(pt)

    @staticmethod
    def find_anchor(content, anchor):
        anchor_line_matches = re_anchor.finditer(content)

        if not anchor:
            return 0

        for line_match in anchor_line_matches:
            line = line_match.group(0)
            santitized_line = "-".join(line.lower() for line in re_anchor_words.findall(line))
            if anchor == santitized_line:
                return line_match.start()

        return 0

    def collapse_links(self):
        self.view.unfold(sublime.Region(0, self.view.size()))
        links = self.view.find_by_selector("markup.underline.link.markdown")
        self.view.fold(links)


class GsHelpGotoLink(TextCommand):

    """
    When the user presses SUPER-Enter over a link in a help document (or CTRL-Enter
    in Windows), browse to the specified link or open a browser if appropriate.
    """

    def run(self, edit):
        sels = self.view.sel()
        if not sels:
            return
        sel = sels[0]
        links = self.view.find_by_selector("markup.underline.link.markdown")

        for link in links:
            if link.b > sel.b:
                dest = self.view.substr(link)
                break
        else:
            return

        if dest.startswith("http://"):
            self.goto_url(dest)
        else:
            self.goto_help_page(dest)

    @staticmethod
    def goto_url(dest):
        webbrowser.open(dest)

    def goto_help_page(self, dest):
        page, anchor = dest.split("#", 1) if "#" in dest else (dest, None)
        self.view.run_command("gs_help_browse", {"page": page, "anchor": anchor})


class GsHelpGotoPrevious(TextCommand):

    """
    Take the user to the previous help page.
    """

    def run(self, edit):
        settings = self.view.settings()

        history = settings.get("git_savvy.help.history") or []
        try:
            history.pop()
            page, anchor = history[-1]
        except IndexError:
            print("sorry, no can do!")
            return

        settings.set("git_savvy.help.history", history)

        self.view.run_command("gs_help_browse", {"page": page, "anchor": anchor, "add_to_history": False})
