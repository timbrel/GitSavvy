import os

import sublime
from sublime_plugin import WindowCommand, TextCommand, EventListener

from ..git_command import GitCommand
from ...common import util

TAG_DELETE_MESSAGE = "Tag deleted."
TAG_CREATE_PROMPT = "Enter tag:"
TAG_CREATE_MESSAGE_PROMPT = "Enter message:"
START_PUSH_MESSAGE = "Starting push..."
END_PUSH_MESSAGE = "Push complete."
NO_REMOTES_MESSAGE = "You have not configured any remotes."

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

NO_LOCAL_TAGS_MESSAGE = "    Your repository has no tags."
NO_REMOTE_TAGS_MESSAGE = "    This remote has no tags."
LOADING_TAGS_MESSAGE = "    Loading tags from remote.."

KEY_BINDINGS_MENU = """
  #############
  ## ACTIONS ##
  #############

  [c] create
  [d] delete
  [p] push to remote
  [P] push all tags to remote
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

    def run(self, edit):
        sublime.set_timeout_async(self.run_async)

    def run_async(self):
        view_contents, ranges = self.get_contents()
        view_section_ranges[self.view.id()] = ranges
        self.view.run_command("gs_replace_view_text", {"text": view_contents})
        sublime.set_timeout_async(self.append_tags)

    def get_contents(self):
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

        cursor = len(header)
        tags = self.get_tags()
        regions = []

        def get_region(new_text):
            nonlocal cursor
            start = cursor
            cursor += len(new_text)
            end = cursor
            return sublime.Region(start, end)

        lines = "\n".join("    {} {}".format(t.sha[:7], t.tag) for t in tags)
        view_text = LOCAL_TEMPLATE.format(lines or NO_LOCAL_TAGS_MESSAGE)
        regions.append(get_region(view_text))

        self.remotes = list(self.get_remotes().keys())
        if self.remotes:
            for remote in self.remotes:
                remote_text = REMOTE_TEMPLATE.format(remote, LOADING_TAGS_MESSAGE)
                regions.append(get_region(remote_text))
                view_text += remote_text

        contents = header + view_text + KEY_BINDINGS_MENU

        return contents, tuple(regions)

    def append_tags(self):
        """
        Fetch, format and append remote tags to the view.
        """
        remotes_length = len(self.remotes)
        if remotes_length:
            sections = view_section_ranges[self.view.id()]

            for remote in self.remotes:
                remote_text = self.get_remote_text(remote)
                section_index = self.remotes.index(remote) + 1
                section = sections[section_index]
                self.view.run_command("gs_replace_region", {
                    "text": remote_text,
                    "begin": section.begin(),
                    "end": section.end()
                    })

                # Fix the section size
                section.b = section.a + len(remote_text)

                # Fix the next section size
                if section_index < remotes_length:
                    next_section = sections[section_index + 1]
                    next_section_size = next_section.size()
                    next_section.a = section.b
                    next_section.b = next_section.a + next_section_size

    def get_remote_text(self, remote):
        """
        Build string to use as contents of a remote's section in the tag view.
        """
        tags = self.get_tags(remote)
        lines = "\n".join("    {} {}".format(t.sha[:7], t.tag) for t in tags if t.tag[-3:] != "^{}")
        lines_text = REMOTE_TEMPLATE.format(remote, lines or NO_REMOTE_TAGS_MESSAGE)

        return lines_text


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
        sections = view_section_ranges[self.view.id()]

        # Local
        local_sections = sections[:1]
        local_lines = util.view.get_lines_from_regions(
            self.view,
            self.view.sel(),
            valid_ranges=local_sections
            )

        local_items = tuple(line[4:].strip().split() for line in local_lines if line)
        if local_items:
            for item in local_items:
                self.git("tag", "-d", item[1])

            util.view.refresh_gitsavvy(self.view)
            sublime.status_message(TAG_DELETE_MESSAGE)
            return

        # Remote
        remotes = list(self.get_remotes().keys())
        for remote in remotes:
            remote_index = remotes.index(remote)
            remote_sections = (sections[remote_index + 1], )
            remote_lines = util.view.get_lines_from_regions(
                self.view,
                self.view.sel(),
                valid_ranges=remote_sections
                )

            remote_items = tuple(line[4:].strip().split() for line in remote_lines if line)
            if remote_items:
                self.git(
                    "push",
                    remote,
                    "--delete",
                    *("refs/tags/" + t[1] for t in remote_items)
                    )

                util.view.refresh_gitsavvy(self.view)
                sublime.status_message(TAG_DELETE_MESSAGE)


class GsTagCreateCommand(WindowCommand, GitCommand):

    """
    Through a series of panels, allow the user to add a tag and message.
    """

    def run(self):
        sublime.set_timeout_async(self.run_async)

    def run_async(self):
        """
        Prompt the user for a tag name.
        """
        self.window.show_input_panel(
            TAG_CREATE_PROMPT,
            "",
            self.on_entered_tag,
            None,
            None
            )

    def on_entered_tag(self, tag_name):
        """
        After the user has entered a tag name, prompt the user for a
        tag message. If the message is empty, use the pre-defined one.
        """
        if not tag_name:
            return

        # TODO: do some validation

        self.tag_name = tag_name
        self.window.show_input_panel(
            TAG_CREATE_MESSAGE_PROMPT,
            sublime.load_settings("GitSavvy.sublime-settings").get("default_tag_message"),
            self.on_entered_message,
            None,
            None
            )

    def on_entered_message(self, message):
        """
        Perform `git tag tag_name -F -`
        """
        if not message:
            return

        message = message.format(tag_name=self.tag_name)

        self.git("tag", self.tag_name, "-F", "-", stdin=message)


class GsTagPushCommand(TextCommand, GitCommand):

    """
    Displays a panel of all remotes defined for the repository, then push
    selected or all tag(s) to the selected remote.
    """

    def run(self, edit, push_all=False):
        if not push_all:
            # Valid sections are in the Local section
            valid_ranges = view_section_ranges[self.view.id()][:1]

            lines = util.view.get_lines_from_regions(
                self.view,
                self.view.sel(),
                valid_ranges=valid_ranges
                )

            self.items = tuple(line[4:].strip().split() for line in lines if line)

        self.push_all = push_all
        sublime.set_timeout_async(self.run_async)

    def run_async(self):
        """
        Display a panel of all remotes defined for the repo, then proceed to
        `on_select_remote`. If no remotes are defined, notify the user and
        proceed no further.
        """
        self.remotes = list(self.get_remotes().keys())
        if not self.remotes:
            self.view.window().show_quick_panel([NO_REMOTES_MESSAGE], None)
        else:
            self.view.window().show_quick_panel(
                self.remotes,
                self.on_select_remote,
                flags=sublime.MONOSPACE_FONT
                )

    def on_select_remote(self, remote_index):
        """
        Push tag(s) to the remote that was previously selected
        """

        # If the user pressed `esc` or otherwise cancelled
        if remote_index == -1:
            return

        selected_remote = self.remotes[remote_index]

        sublime.status_message(START_PUSH_MESSAGE)
        if self.push_all:
            self.git("push", selected_remote, "--tags")
        elif self.items:
            self.git(
                "push",
                selected_remote,
                *("refs/tags/" + t[1] for t in self.items)
                )

        sublime.status_message(END_PUSH_MESSAGE)


class GsTagViewLogCommand(TextCommand, GitCommand):

    """
    Display a panel containing the commit log for the selected tag's hash.
    """

    def run(self, edit):
        lines = util.view.get_lines_from_regions(
            self.view,
            self.view.sel(),
            valid_ranges=view_section_ranges[self.view.id()]
            )

        items = tuple(line[4:].strip().split() for line in lines if line)

        if items:
            self.git("log", "-1", "--pretty=medium", items[0][0], show_panel=True)
