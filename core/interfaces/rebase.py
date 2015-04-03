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
    UNKNOWN = "?"

    template = """\

      REBASE:  {active_branch} --> {base_ref} ({base_commit})
      STATUS:  {status}

        ┳ ({base_commit})
        ┃
    {diverged_commits}
        ┃
        ┻

      ###############                      #############
      ## CONFLICTS ##                      ## COMMITS ##
      ###############                      #############

      [o] open file                        [s] squash commit with next
      [a] accept file in current state     [S] squash all commits
      [m] use mine                         [u] move commit up (above previous)
      [t] use theirs                       [d] move commit down (below next)
      [M] launch external merge tool       [e] edit commit message

      ####################
      ## REBASE ACTIONS ##
      ####################

      [R] restart rebase
      [A] abort rebase
      [F] finalize rebase

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
