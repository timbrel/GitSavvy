import os

import sublime
from sublime_plugin import WindowCommand, TextCommand

from ...common import ui
from ..git_command import GitCommand
from ...common import util


class GsShowRebaseCommand(WindowCommand, GitCommand):

    """
    Open a status view for the active git repository.
    """

    def run(self):
        RebaseInterface(repo_path=self.repo_path)


class RebaseInterface(ui.Interface, GitCommand):

    """
    Status dashboard.
    """

    interface_type = "rebase"
    read_only = True
    syntax_file = "Packages/GitSavvy/syntax/rebase.tmLanguage"
    word_wrap = False

    CARET = "▸"
    SUCCESS = "✔"
    CONFLICT = "✕"
    UNKNOWN = "·"

    template = """\

      REBASE:  {active_branch} --> {base_ref} ({base_commit})
      STATUS:  {status}

        ┳ ({base_commit})
        ┃
    {diverged_commits}
        ┃
        ┻

      ########################                  ############
      ## MANIPULATE COMMITS ##                  ## REBASE ##
      ########################                  ############

      [s] squash commit with next               [d] define base ref for dashboard
      [S] squash all commits                    [A] abort rebase
      [e] edit commit message                   [r] rebase onto...
      [d] move commit down (after next)         [C] continue rebase
      [u] move commit up (before previous)      []

      ###############
      ## CONFLICTS ##
      ###############

      [o] open file
      [g] stage file in current state
      [m] use mine
      [t] use theirs
      [M] launch external merge tool

    -
    """

    separator = "\n    ┃\n"
    commit = "  {caret} {status}  {commit_hash}  {commit_summary}{conflicts}"
    conflict = "    ┃           conflict: {path}"

    _base_commit_ref = None
    _base_commit = None

    def title(self):
        return "REBASE: {}".format(os.path.basename(self.repo_path))

    def base_ref(self):
        base_ref = self.view.settings().get("git_savvy.rebase.base_ref")
        if not base_ref:
            base_ref = "master"
            self.view.settings().set("git_savvy.revase.base_ref", "master")
        return base_ref

    def base_commit(self):
        base_ref = self.base_ref()
        if not self._base_commit_ref == base_ref:
            self._base_commit = self.git("merge-base", "HEAD", base_ref).strip()
            self._base_commit_ref = base_ref
        return self._base_commit

    @ui.partial("active_branch")
    def render_active_branch(self):
        return self.get_current_branch_name()

    @ui.partial("base_ref")
    def render_base_ref(self):
        return self.base_ref()

    @ui.partial("base_commit")
    def render_base_commit(self):
        return self.base_commit()[:7]

    @ui.partial("status")
    def render_status(self):
        # Todo
        return "👍"

    @ui.partial("diverged_commits")
    def render_diverged_commits(self):
        self.entries = self.log(start_end=(self.base_ref(), "HEAD"), reverse=True)

        return self.separator.join([
            self.commit.format(
                caret=" ",
                status=self.UNKNOWN,
                commit_hash=entry.short_hash,
                commit_summary=entry.summary,
                conflicts=""
                )
            for entry in self.entries
            ])


ui.register_listeners(RebaseInterface)


class RewriteBase(TextCommand, GitCommand):

    def run(self, edit):
        self.interface = ui.get_interface(self.view.id())
        sublime.set_timeout_async(self.run_async, 0)

    def get_selected_short_hash(self):
        sels = self.view.sel()
        if len(sels) > 1 or not sels or sels[0].a != sels[0].b:
            return

        line = self.view.line(sels[0])
        line_str = self.view.substr(line)
        return line_str[7:14]

    def make_changes(self, commit_chain):
        self.rewrite_active_branch(
            base_commit=self.interface.base_commit(),
            commit_chain=commit_chain
            )

        util.view.refresh_gitsavvy(self.view)


class GsRebaseSquashCommand(RewriteBase):

    def run_async(self):
        short_hash = self.get_selected_short_hash()
        if not short_hash:
            return

        # Cannot squash last commit.
        if self.interface.entries[-1].short_hash == short_hash:
            sublime.status_message("Unable to squash most recent commit.")
            return

        # Generate identical change templates with author/date metadata
        # in tact.  In case of commit-to-squash, indicate that the changes
        # should be rolled over into the next change's commit.
        commit_chain = [
            self.ChangeTemplate(orig_hash=entry.long_hash,
                                do_commit=entry.short_hash != short_hash,
                                msg=entry.raw_body,
                                datetime=entry.datetime,
                                author="{} <{}>".format(entry.author, entry.email))
            for entry in self.interface.entries
        ]

        # Take the commit message from the commit-to-squash and append
        # it to the next commit's message.
        for idx, commit in enumerate(commit_chain):
            if not commit.do_commit:
                commit_chain[idx+1].msg += "\n\n" + commit.msg
                commit.msg = None

        self.make_changes(commit_chain)


class GsRebaseSquashAllCommand(RewriteBase):

    def run_async(self):

        # Generate identical change templates with author/date metadata
        # in tact.  However, set do_commit to false for all but the last change,
        # in order for diffs to be rolled into that final commit.
        last_commit_idx = len(self.interface.entries) - 1
        commit_chain = [
            self.ChangeTemplate(orig_hash=entry.long_hash,
                                do_commit=idx == last_commit_idx,
                                msg=entry.raw_body,
                                datetime=entry.datetime,
                                author="{} <{}>".format(entry.author, entry.email))
            for idx, entry in enumerate(self.interface.entries)
        ]

        # Take the commit message from the commit-to-squash and append
        # it to the next commit's message.
        for idx, commit in enumerate(commit_chain):
            if not commit.do_commit:
                commit_chain[idx+1].msg += "\n\n" + commit.msg
                commit.msg = None

        self.make_changes(commit_chain)


class GsRebaseEditCommand(RewriteBase):

    def run(self, edit):
        self.interface = ui.get_interface(self.view.id())
        short_hash = self.get_selected_short_hash()

        for entry in self.interface.entries:
            if entry.short_hash == short_hash:
                break
        else:
            return

        ui.EditView(content=entry.raw_body,
                    repo_path=self.repo_path,
                    window=self.view.window(),
                    on_done=lambda commit_msg: self.do_edit(entry, commit_msg))

    def do_edit(self, entry_to_edit, commit_msg):
        # Generate identical change templates with author/date metadata
        # in tact.  For the edited entry, replace the message with
        # the content from the temporary edit view.

        commit_chain = [
            self.ChangeTemplate(orig_hash=entry.long_hash,
                                do_commit=True,
                                msg=commit_msg if entry == entry_to_edit else entry.raw_body,
                                datetime=entry.datetime,
                                author="{} <{}>".format(entry.author, entry.email))
            for entry in self.interface.entries
        ]
        self.make_changes(commit_chain)


class GsRebaseMoveUpCommand(RewriteBase):

    def run_async(self):
        short_hash = self.get_selected_short_hash()
        if not short_hash:
            return
        if self.interface.entries[0].short_hash == short_hash:
            sublime.status_message("Unable to move first commit up.")

        commit_chain = [
            self.ChangeTemplate(orig_hash=entry.long_hash,
                                move=entry.short_hash == short_hash,
                                do_commit=True,
                                msg=entry.raw_body,
                                datetime=entry.datetime,
                                author="{} <{}>".format(entry.author, entry.email))
            for entry in self.interface.entries
        ]

        # Take the change to move and swap it with the one before.
        for idx, commit in enumerate(commit_chain):
            if commit.move:
                commit_chain[idx], commit_chain[idx-1] = commit_chain[idx-1], commit_chain[idx]
                break

        try:
            self.make_changes(commit_chain)
        except:
            sublime.message_dialog("Unable to move commit, most likely due to a conflict.")


class GsRebaseMoveDownCommand(RewriteBase):

    def run_async(self):
        short_hash = self.get_selected_short_hash()
        if not short_hash:
            return
        if self.interface.entries[-1].short_hash == short_hash:
            sublime.status_message("Unable to move last commit down.")
            return

        commit_chain = [
            self.ChangeTemplate(orig_hash=entry.long_hash,
                                move=entry.short_hash == short_hash,
                                do_commit=True,
                                msg=entry.raw_body,
                                datetime=entry.datetime,
                                author="{} <{}>".format(entry.author, entry.email))
            for entry in self.interface.entries
        ]

        # Take the change to move and swap it with the one following.
        for idx, commit in enumerate(commit_chain):
            if commit.move:
                commit_chain[idx], commit_chain[idx+1] = commit_chain[idx+1], commit_chain[idx]
                break

        try:
            self.make_changes(commit_chain)
        except:
            sublime.message_dialog("Unable to move commit, most likely due to a conflict.")
