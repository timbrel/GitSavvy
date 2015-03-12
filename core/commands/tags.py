import os

import sublime
from sublime_plugin import WindowCommand, TextCommand, EventListener

from ..git_command import GitCommand
from ...common import util

TAG_DELETE_MESSAGE = "Tag deleted."

VIEW_TITLE = "TAGS: {}"

LOCAL_TEMPLATE = """
  LOCAL:
{}
"""

REMOTE_TEMPLATE = """
  REMOTE ({}):
{}
"""

VIEW_HEADER_TEMPLATE = """
  BRANCH:  {branch_status}
  ROOT:    {repo_root}
  HEAD:    {current_head}
"""

NO_TAGS_MESSAGE = """
  Your repository has no tags.
"""

LOADING_TAGS_MESSAGE = """
  Please stand by while fetching tags from remote(s).
"""

KEY_BINDINGS_MENU = """
  #############
  ## ACTIONS ##
  #############

  [c] create (NYI)
  [d] delete
  [p] push to remote(s) (NYI)
  [P] push all tags to remote(s) (NYI)
  [l] view commit

  ###########
  ## OTHER ##
  ###########

  [r] refresh status

-
"""

view_section_ranges = {}


class GsShowTagsCommand(WindowCommand, GitCommand):

    """
    Open a tags view for the active git repository.
    """

    def run(self):
        repo_path = self.repo_path
        title = VIEW_TITLE.format(os.path.basename(repo_path))
        tags_view = util.view.get_read_only_view(self, "tags")
        util.view.disable_other_plugins(tags_view)
        tags_view.set_name(title)
        tags_view.set_syntax_file("Packages/GitSavvy/syntax/tags.tmLanguage")
        tags_view.settings().set("git_savvy.repo_path", repo_path)
        tags_view.settings().set("word_wrap", False)
        self.window.focus_view(tags_view)
        tags_view.sel().clear()

        tags_view.run_command("gs_tags_refresh")


class GsTagsRefreshCommand(TextCommand, GitCommand):

    """
    Get the current state of the git repo and display tags and command
    menu to the user.
    """

    def run(self, edit, **kwargs):
        sublime.set_timeout_async(lambda: self.run_async(**kwargs))

    def run_async(self):
        view_contents = self.get_contents(loading=True)
        self.view.run_command("gs_replace_view_text", {"text": view_contents})
        sublime.set_timeout_async(lambda: self.append_tags())

    def get_contents(self, loading=False):
        """
        Build string to use as contents of tags view. Includes repository
        information in the header, per-tag information, and a key-bindings
        menu at the bottom.
        """
        header = VIEW_HEADER_TEMPLATE.format(
            branch_status=self.get_branch_status(),
            repo_root=self.repo_path,
            current_head=self.get_latest_commit_msg_for_head()
        )

        if loading:
            return header + LOADING_TAGS_MESSAGE + KEY_BINDINGS_MENU
        else:
            view_text = ""

            cursor = len(header)
            local, remotes = self.sort_tag_entries(self.get_tags())
            local_region, remote_region = (sublime.Region(0, 0), ) * 2

            def get_region(new_text):
                nonlocal cursor
                start = cursor
                cursor += len(new_text)
                end = cursor
                return sublime.Region(start, end)


            if local:
                local_lines = "\n".join(
                    "    {} {}".format(t.sha[:7], t.tag)
                    for t in local
                    )
                local_text = LOCAL_TEMPLATE.format(local_lines)
                local_region = get_region(local_text)
                view_text += local_text
            if remotes:
                for group in remotes:
                    remote_lines = "\n".join(
                        "    {} {}".format(t.sha[:7], t.tag)
                        for t in group.entries
                        )
                    remote_text = REMOTE_TEMPLATE.format(group.remote, remote_lines)
                    remote_region = get_region(remote_text)
                    view_text += remote_text

            view_text = view_text or NO_TAGS_MESSAGE

            contents = header + view_text + KEY_BINDINGS_MENU

            return contents, (local_region, remote_region)

    def append_tags(self):
        view_contents, ranges = self.get_contents()
        view_section_ranges[self.view.id()] = ranges
        self.view.run_command("gs_replace_view_text", {"text": view_contents})

    @staticmethod
    def sort_tag_entries(tag_list):
        """
        Take entries from `get_tags` and sort them into groups.
        """
        local, remotes = [], []

        for item in tag_list:
            if hasattr(item, "remote"):
                # TODO: remove entries that exist locally
                remotes.append(item)
            else:
                local.append(item)

        return local, remotes


class GsTagsFocusEventListener(EventListener):

    """
    If the current view is a tags view, refresh the view with
    the repository's tags when the view regains focus.
    """

    def on_activated(self, view):
        if view.settings().get("git_savvy.tags_view") == True:
            view.run_command("gs_tags_refresh")


class GsTagDeleteCommand(TextCommand, GitCommand):

    """
    Delete tag(s) in selection.
    """

    def run(self, edit):
        valid_ranges = view_section_ranges[self.view.id()][:3]

        lines = util.view.get_lines_from_regions(
            self.view,
            self.view.sel(),
            valid_ranges=valid_ranges
            )

        items = tuple(line[4:].strip().split() for line in lines if line)

        if items:
            for item in items:
                self.git("tag", "-d", item[1])
            util.view.refresh_gitsavvy(self.view)
            sublime.status_message(TAG_DELETE_MESSAGE)


class GsTagViewLogCommand(TextCommand, GitCommand):

    """
    Display a panel containing the commit log for the selected tag's hash.
    """

    def run(self, edit):
        valid_ranges = view_section_ranges[self.view.id()][:3]

        lines = util.view.get_lines_from_regions(
            self.view,
            self.view.sel(),
            valid_ranges=valid_ranges
            )

        items = tuple(line[4:].strip().split() for line in lines if line)

        if items:
            for item in items:
                self.git("log", "-1", "--pretty=medium", item[0], show_panel=True)
                break
