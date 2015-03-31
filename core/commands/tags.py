import os

import sublime
from sublime_plugin import WindowCommand, TextCommand, EventListener

from ..git_command import GitCommand
from ...common import util

TAG_DELETE_MESSAGE = "Tag deleted."
TAG_CREATE_MESSAGE = "Tag \"{}\" created."
TAG_CREATE_PROMPT = "Enter tag:"
TAG_CREATE_MESSAGE_PROMPT = "Enter message:"
START_PUSH_MESSAGE = "Pushing tag..."
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
LOADING_TAGS_MESSAGE = "    Loading tags from remote..."

KEY_BINDINGS_MENU = """
  #############                   ###########
  ## ACTIONS ##                   ## OTHER ##
  #############                   ###########

  [c] create                      [r] refresh status
  [d] delete
  [p] push to remote
  [P] push all tags to remote
  [l] view commit

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

    def run(self, edit, section=None):
        self.section = section
        sublime.set_timeout_async(self.run_async)

    def run_async(self):
        if not self.section:
            view_contents, ranges = self.get_contents()
            view_section_ranges[self.view.id()] = ranges
            self.view.run_command("gs_replace_view_text", {"text": view_contents})

        if not self.section or self.section == 1:
            sublime.set_timeout_async(self.append_local)

        if not self.section or self.section == 2:
            sublime.set_timeout_async(self.append_all_remotes)
        elif isinstance(self.section, str):
            sublime.set_timeout_async(lambda: self.append_remote(self.section))

    def get_contents(self):
        """
        Build a string to use as a base for the contents in the tags view.
        It includes repository information in the header, sections for tags'
        locations, and a key-bindings menu at the bottom.
        """
        header = VIEW_HEADER_TEMPLATE.format(
            branch_status=self.get_branch_status(delim="\n           "),
            repo_root=self.short_repo_path,
            current_head=self.get_latest_commit_msg_for_head()
        )

        cursor = len(header)
        regions = []

        def get_region(new_text):
            nonlocal cursor
            start = cursor
            cursor += len(new_text)
            end = cursor
            return sublime.Region(start, end)

        view_text = LOCAL_TEMPLATE.format(LOADING_TAGS_MESSAGE)
        regions.append(get_region(view_text))

        self.remotes = list(self.get_remotes().keys())
        if self.remotes:
            for remote in self.remotes:
                remote_text = REMOTE_TEMPLATE.format(remote, LOADING_TAGS_MESSAGE)
                regions.append(get_region(remote_text))
                view_text += remote_text

        contents = header + view_text + KEY_BINDINGS_MENU

        return contents, tuple(regions)

    def append_local(self):
        """
        Build a string containing tags available in the local repository, then
        append it to the section, finally updating the stored sections for the view.
        """
        tags = self.get_tags(reverse=True)
        lines = "\n".join("    {} {}".format(t.sha[:7], t.tag) for t in tags)
        text = LOCAL_TEMPLATE.format(lines or NO_LOCAL_TAGS_MESSAGE)

        section = view_section_ranges[self.view.id()][0]
        self.view.run_command("gs_replace_region", {
            "text": text,
            "begin": section.begin(),
            "end": section.end()
            })

        # Fix the section sizes
        section.b = section.a + len(text)
        self.update_sections(0)

    def append_remote(self, remote):
        """
        Build a string containing tags available in a remote repository, then
        append it to the section, finally updating the stored sections for the view.
        """
        tags = self.get_tags(remote, reverse=True)
        lines = "\n".join("    {} {}".format(t.sha[:7], t.tag) for t in tags if t.tag[-3:] != "^{}")
        text = REMOTE_TEMPLATE.format(remote, lines or NO_REMOTE_TAGS_MESSAGE)

        index = self.remotes.index(remote)
        section = view_section_ranges[self.view.id()][index + 1]
        self.view.run_command("gs_replace_region", {
            "text": text,
            "begin": section.begin(),
            "end": section.end()
            })

        # Fix the section sizes
        section.b = section.a + len(text)
        self.update_sections(index + 1)

    def append_all_remotes(self):
        """
        Async "relay" to append all remotes' tags to their relative sections in the view.
        """
        for remote in self.remotes:
            self.append_remote(remote)

    def update_sections(self, index):
        """
        Update the stored sections for the view.
        """
        sections = view_section_ranges[self.view.id()]

        for section in sections[index + 1:]:
            section_size = section.size()
            section.a = sections[index].b
            section.b = section.a + section_size
            index += 1


class GsTagsFocusEventListener(EventListener):

    """
    If the current view is a tags view, refresh the local tags in
    the view when it regains focus.
    """

    def on_activated(self, view):
        if view.settings().get("git_savvy.tags_view"):
            view.run_command("gs_tags_refresh", {"section": 1})


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

            sublime.status_message(TAG_DELETE_MESSAGE)
            if self.view.settings().get("git_savvy.tags_view"):
                self.view.run_command("gs_tags_refresh", {"section": 1})
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
                self.remote = remote
                sublime.set_timeout_async(lambda: self.do_push(
                    "--delete",
                    *("refs/tags/" + t[1] for t in remote_items)
                    ))

    def do_push(self, *args):
        """
        Perform a `git push` operation, then update the relative section in the view.
        """
        self.git("push", self.remote, *args)

        sublime.status_message(TAG_DELETE_MESSAGE)
        if self.view.settings().get("git_savvy.tags_view"):
            self.view.run_command("gs_tags_refresh", {"section": self.remote})


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
        After the user has entered a tag name, validate the tag name, finally
        prompting the user for a tag message.
        """
        if not tag_name:
            return

        stdout = self.git(
            "check-ref-format",
            "--normalize",
            "refs/tags/" + tag_name,
            throw_on_stderr=False
            )

        if not stdout:
            return util.log.panel("\"{}\" is not a valid tag name.".format(tag_name))

        self.tag_name = stdout.strip()[10:]
        self.window.show_input_panel(
            TAG_CREATE_MESSAGE_PROMPT,
            sublime.load_settings("GitSavvy.sublime-settings").get("default_tag_message"),
            self.on_entered_message,
            None,
            None
            )

    def on_entered_message(self, message):
        """
        Create a tag with the previously specified tag name and the provided message.
        """
        if not message:
            return

        message = message.format(tag_name=self.tag_name)

        self.git("tag", self.tag_name, "-F", "-", stdin=message)

        sublime.status_message(TAG_CREATE_MESSAGE.format(self.tag_name))
        view = self.window.active_view()
        if view.settings().get("git_savvy.tags_view"):
            view.run_command("gs_tags_refresh", {"section": 1})


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

        self.selected_remote = self.remotes[remote_index]

        if self.push_all:
            sublime.set_timeout_async(lambda: self.do_push("--tags"))
        elif self.items:
            sublime.set_timeout_async(lambda: self.do_push(
                *("refs/tags/" + t[1] for t in self.items)
                ))

    def do_push(self, *args):
        """
        Perform a `git push` operation, then update the relative section in the view.
        """
        sublime.status_message(START_PUSH_MESSAGE)
        self.git("push", self.selected_remote, *args)
        sublime.status_message(END_PUSH_MESSAGE)

        if self.view.settings().get("git_savvy.tags_view"):
            self.view.run_command("gs_tags_refresh", {"section": self.selected_remote})


class GsTagViewLogCommand(TextCommand, GitCommand):

    """
    Display an output panel containing the commit log for the selected tag's hash.
    """

    def run(self, edit):
        lines = util.view.get_lines_from_regions(
            self.view,
            self.view.sel(),
            valid_ranges=view_section_ranges[self.view.id()]
            )

        items = tuple(line[4:].strip().split() for line in lines if line)

        if items:
            stdout = self.git("show", items[0][0])

            panel_view = self.view.window().create_output_panel("GitSavvy")
            panel_view.set_syntax_file("Packages/GitSavvy/syntax/show_commit.tmLanguage")
            panel_view.settings().set("line_numbers", False)
            panel_view.set_read_only(False)
            panel_view.erase(edit, sublime.Region(0, panel_view.size()))
            panel_view.insert(edit, 0, stdout)
            panel_view.set_read_only(True)
            panel_view.show(0)
            self.view.window().run_command("show_panel", {"panel": "output.{}".format("GitSavvy")})
