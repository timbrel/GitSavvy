import os

import sublime
from sublime_plugin import WindowCommand, TextCommand

from ...common import ui
from ..git_command import GitCommand
from ...common import util

TAG_DELETE_MESSAGE = "Tag(s) deleted."

NO_REMOTES_MESSAGE = "You have not configured any remotes."

NO_LOCAL_TAGS_MESSAGE = "    Your repository has no tags."
NO_REMOTE_TAGS_MESSAGE = "    Unable to retrieve tags for this remote."
LOADING_TAGS_MESSAGE = "    Loading tags from remote..."

START_PUSH_MESSAGE = "Pushing tag..."
END_PUSH_MESSAGE = "Push complete."


class GsShowTagsCommand(WindowCommand, GitCommand):

    """
    Open a branch dashboard for the active Git repository.
    """

    def run(self):
        TagsInterface(repo_path=self.repo_path)


class TagsInterface(ui.Interface, GitCommand):

    """
    Tags dashboard.
    """

    interface_type = "tags"
    read_only = True
    syntax_file = "Packages/GitSavvy/syntax/tags.sublime-syntax"
    word_wrap = False
    tab_size = 2

    show_remotes = None
    remotes = None

    template = """\

      BRANCH:  {branch_status}
      ROOT:    {repo_root}
      HEAD:    {head}

      LOCAL:
    {local_tags}{remote_tags}
      #############                   ###########
      ## ACTIONS ##                   ## OTHER ##
      #############                   ###########

      [c] create                      [r]         refresh dashboard
      [d] delete                      [e]         toggle display of remote branches
      [p] push to remote              [tab]       transition to next dashboard
      [P] push all tags to remote     [SHIFT-tab] transition to previous dashboard
      [l] view commit

    -
    """

    template_remote = """
      REMOTE ({remote_name}):
    {remote_tags_list}"""

    def title(self):
        return "TAGS: {}".format(os.path.basename(self.repo_path))

    def pre_render(self):
        if self.show_remotes is None:
            savvy_settings = sublime.load_settings("GitSavvy.sublime-settings")
            self.show_remotes = savvy_settings.get("show_remotes_in_tags_dashboard")

        self.local_tags = self.get_tags(reverse=True)
        if not self.remotes and self.show_remotes:
            self.remotes = self.get_remotes()
            for name, uri in self.remotes.items():
                self.remotes[name] = {"uri": uri}

    @ui.partial("branch_status")
    def render_branch_status(self):
        return self.get_branch_status(delim="\n           ")

    @ui.partial("repo_root")
    def render_repo_root(self):
        return self.short_repo_path

    @ui.partial("head")
    def render_head(self):
        return self.get_latest_commit_msg_for_head()

    @ui.partial("local_tags")
    def render_local_tags(self):
        if not self.local_tags:
            return NO_LOCAL_TAGS_MESSAGE

        return "\n".join(
            "    {} {}".format(tag.sha[:7], tag.tag)
            for tag in self.local_tags
            )

    @ui.partial("remote_tags")
    def render_remote_tags(self):
        if not self.show_remotes:
            return self.render_remote_tags_off()

        if self.remotes == []:
            return NO_REMOTES_MESSAGE

        output_tmpl = "\n"
        render_fns = []

        for remote_name, remote in self.remotes.items():
            tmpl_key = "remote_tags_list_" + remote_name
            output_tmpl += "{" + tmpl_key + "}\n"

            @ui.partial(tmpl_key)
            def render_remote(remote=remote, remote_name=remote_name):
                return self.get_remote_tags_list(remote, remote_name)

            render_fns.append(render_remote)

        return output_tmpl, render_fns

    def get_remote_tags_list(self, remote, remote_name):
        if "tags" in remote:
            if remote["tags"]:
                msg = "\n".join(
                    "    {} {}".format(tag.sha[:7], tag.tag)
                    for tag in remote["tags"] if tag.tag[-3:] != "^{}"
                    )
            else:
                msg = NO_REMOTE_TAGS_MESSAGE

        elif "loading" in remote:
            msg = LOADING_TAGS_MESSAGE

        else:
            def do_tags_fetch(remote=remote, remote_name=remote_name):
                remote["tags"] = self.get_tags(remote_name, reverse=True)
                self.render()

            sublime.set_timeout_async(do_tags_fetch, 0)
            remote["loading"] = True
            msg = LOADING_TAGS_MESSAGE

        return self.template_remote.format(
            remote_name=remote_name,
            remote_tags_list=msg
            )

    def render_remote_tags_off(self):
        return "\n\n  ** Press [e] to toggle display of remote branches. **\n"


ui.register_listeners(TagsInterface)


class GsTagsToggleRemotesCommand(TextCommand, GitCommand):

    """
    Toggle display of the remote tags.
    """

    def run(self, edit, show=None):
        interface = ui.get_interface(self.view.id())
        interface.remotes = None
        if show is None:
            interface.show_remotes = not interface.show_remotes
        else:
            interface.show_remotes = show
        interface.render()


class GsTagsRefreshCommand(TextCommand, GitCommand):

    """
    Refresh the tags dashboard.
    """

    def run(self, edit, reset_remotes=False):
        interface = ui.get_interface(self.view.id())
        if reset_remotes:
            interface.remotes = None

        util.view.refresh_gitsavvy(self.view)


class GsTagDeleteCommand(TextCommand, GitCommand):

    """
    Delete selected tag(s).
    """

    def run(self, edit):
        sublime.set_timeout_async(self.run_async, 0)

    def run_async(self):
        interface = ui.get_interface(self.view.id())
        self.delete_local(interface)
        self.delete_remote(interface)
        util.view.refresh_gitsavvy(self.view)

    def delete_local(self, interface):
        lines = interface.get_selection_lines_in_region("local_tags")
        tags_to_delete = tuple(line[12:].strip() for line in lines if line)

        if not tags_to_delete:
            return

        for tag in tags_to_delete:
            self.git("tag", "-d", tag)

        sublime.status_message(TAG_DELETE_MESSAGE)
        util.view.refresh_gitsavvy(self.view)

    def delete_remote(self, interface):
        if not interface.remotes:
            return

        for remote_name, remote in interface.remotes.items():
            lines = interface.get_selection_lines_in_region("remote_tags_list_" + remote_name)
            tags_to_delete = tuple(line[12:].strip() for line in lines if line[:4] == "    ")

            if tags_to_delete:
                self.git(
                    "push",
                    remote_name,
                    "--delete",
                    *("refs/tags/" + tag for tag in tags_to_delete)
                    )

        sublime.status_message(TAG_DELETE_MESSAGE)
        interface.remotes = None
        util.view.refresh_gitsavvy(self.view)


class GsTagPushCommand(TextCommand, GitCommand):

    """
    Displays a panel of all remotes defined for the repository, then push
    selected or all tag(s) to the selected remote.
    """

    def run(self, edit, push_all=False):
        sublime.set_timeout_async(lambda: self.run_async(push_all=push_all), 0)

    def run_async(self, push_all):
        self.remotes = tuple(self.get_remotes().keys())
        if not self.remotes:
            self.view.window().show_quick_panel([NO_REMOTES_MESSAGE], None)
            return

        self.view.window().show_quick_panel(
            self.remotes,
            self.push_all if push_all else self.push_selected,
            flags=sublime.MONOSPACE_FONT
            )

    def push_selected(self, remote_idx):
        # The user pressed `esc` or otherwise cancelled.
        if remote_idx == -1:
            return
        remote = self.remotes[remote_idx]

        interface = ui.get_interface(self.view.id())
        lines = interface.get_selection_lines_in_region("local_tags")
        tags_to_push = tuple(line[12:].strip() for line in lines if line)

        sublime.status_message(START_PUSH_MESSAGE)
        self.git("push", remote, *("refs/tags/" + tag for tag in tags_to_push))
        sublime.status_message(END_PUSH_MESSAGE)

        interface.remotes = None
        util.view.refresh_gitsavvy(self.view)

    def push_all(self, remote_idx):
        # The user pressed `esc` or otherwise cancelled.
        if remote_idx == -1:
            return
        remote = self.remotes[remote_idx]
        sublime.status_message(START_PUSH_MESSAGE)
        self.git("push", remote, "--tags")
        sublime.status_message(END_PUSH_MESSAGE)

        interface = ui.get_interface(self.view.id())
        interface.remotes = None
        util.view.refresh_gitsavvy(self.view)


class GsTagViewLogCommand(TextCommand, GitCommand):

    """
    Display the commit for the selected tag's hash.
    """

    def run(self, edit):
        sublime.set_timeout_async(self.run_async, 0)

    def run_async(self):
        interface = ui.get_interface(self.view.id())
        local_lines = interface.get_selection_lines_in_region("local_tags")
        commit_hashes = [line[4:11] for line in local_lines if line]

        if interface.remotes:
            for remote_name, remote in interface.remotes.items():
                lines = interface.get_selection_lines_in_region("remote_tags_list_" + remote_name)
                commit_hashes.extend(line[4:11] for line in lines if line[:4] == "    ")

        window = self.view.window()
        for commit_hash in commit_hashes:
            window.run_command("gs_show_commit", {"commit_hash": commit_hash})
