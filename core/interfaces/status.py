import os
import tempfile
import subprocess
import shlex
from shutil import rmtree
from functools import partial

import sublime
from sublime_plugin import WindowCommand, TextCommand

from ...common import ui
from ..git_command import GitCommand
from ...common import util
from ..constants import MERGE_CONFLICT_PORCELAIN_STATUSES


class GsShowStatusCommand(WindowCommand, GitCommand):

    """
    Open a status view for the active git repository.
    """

    def run(self):
        StatusInterface(repo_path=self.repo_path)


class StatusInterface(ui.Interface, GitCommand):

    """
    Status dashboard.
    """

    interface_type = "status"
    read_only = True
    syntax_file = "Packages/GitSavvy/syntax/status.tmLanguage"
    word_wrap = False

    template = """\

      BRANCH:  {branch_status}
      ROOT:    {git_root}
      HEAD:    {head}

    {< staged_files}
    {< unstaged_files}
    {< untracked_files}
    {< merge_conflicts}
    {< no_status_message}
    {< stashes}
      ###################                   ###############
      ## SELECTED FILE ##                   ## ALL FILES ##
      ###################                   ###############

      [o] open file                         [a] stage all unstaged files
      [s] stage file                        [A] stage all unstaged and untracked files
      [u] unstage file                      [U] unstage all staged files
      [d] discard changes to file           [D] discard all unstaged changes
      [h] open file on remote
      [M] launch external merge tool for conflict

      [l] diff file inline                  [f] diff all files
      [e] diff file                         [F] diff all cached files

      #############                         #############
      ## ACTIONS ##                         ## STASHES ##
      #############                         #############

      [c] commit                            [t][a] apply stash
      [C] commit, including unstaged        [t][p] pop stash
      [m] amend previous commit             [t][s] show stash
                                            [t][c] create stash
      [i] ignore file                       [t][u] create stash including untracked files
      [I] ignore pattern                    [t][d] discard stash

      ###########
      ## OTHER ##
      ###########

      [r] refresh status

    -
    """

    template_staged = """
      STAGED:
    {}
    """

    template_unstaged = """
      UNSTAGED:
    {}
    """

    template_untracked = """
      UNTRACKED:
    {}
    """

    template_merge_conflicts = """
      MERGE CONFLICTS:
    {}
    """

    template_stashes = """
      STASHES:
    {}
    """

    def title(self):
        return "STATUS: {}".format(os.path.basename(self.repo_path))

    def pre_render(self):
        (self.staged_entries,
         self.unstaged_entries,
         self.untracked_entries,
         self.conflict_entries) = self.sort_status_entries(self.get_status())

    @staticmethod
    def sort_status_entries(file_status_list):
        """
        Take entries from `git status` and sort them into groups.
        """
        staged, unstaged, untracked, conflicts = [], [], [], []

        for f in file_status_list:
            if (f.index_status, f.working_status) in MERGE_CONFLICT_PORCELAIN_STATUSES:
                conflicts.append(f)
                continue
            if f.index_status == "?":
                untracked.append(f)
                continue
            elif f.working_status in ("M", "D"):
                unstaged.append(f)
            if f.index_status != " ":
                staged.append(f)

        return staged, unstaged, untracked, conflicts

    @ui.partial("branch_status")
    def render_branch_status(self):
        return self.get_branch_status(delim="\n           ")

    @ui.partial("git_root")
    def render_git_root(self):
        return self.short_repo_path

    @ui.partial("head")
    def render_head(self):
        return self.get_latest_commit_msg_for_head()

    @ui.partial("staged_files")
    def render_staged_files(self):
        if not self.staged_entries:
            return ""
        return self.template_staged.format("\n".join(
            "  {} {}".format("-" if f.index_status == "D" else " ", f.path)
            for f in self.staged_entries
            ))

    @ui.partial("unstaged_files")
    def render_unstaged_files(self):
        if not self.unstaged_entries:
            return ""
        return self.template_unstaged.format("\n".join(
            "  {} {}".format("-" if f.working_status == "D" else " ", f.path)
            for f in self.unstaged_entries
            ))

    @ui.partial("untracked_files")
    def render_untracked_files(self):
        if not self.untracked_entries:
            return ""
        return self.template_untracked.format(
            "\n".join("    " + f.path for f in self.untracked_entries))

    @ui.partial("merge_conflicts")
    def render_merge_conflicts(self):
        if not self.conflict_entries:
            return ""
        return self.template_merge_conflicts.format(
            "\n".join("    " + f.path for f in self.conflict_entries))

    @ui.partial("no_status_message")
    def render_no_status_message(self):
        return ("\n    Your working directory is clean.\n"
                if not (self.staged_entries or
                        self.unstaged_entries or
                        self.untracked_entries or
                        self.conflict_entries)
                else "")

    @ui.partial("stashes")
    def render_stashes(self):
        stash_list = self.get_stashes()
        if not stash_list:
            return ""

        return self.template_stashes.format("\n".join(
            "    ({}) {}".format(stash.id, stash.description) for stash in stash_list))


ui.register_listeners(StatusInterface)


class GsStatusOpenFileCommand(TextCommand, GitCommand):

    """
    For every file that is selected or under a cursor, open a that
    file in a new view.
    """

    def run(self, edit):
        lines = util.view.get_lines_from_regions(self.view, self.view.sel())
        file_paths = (line.strip() for line in lines if line[:4] == "    ")
        abs_paths = (os.path.join(self.repo_path, file_path) for file_path in file_paths)
        for path in abs_paths:
            self.view.window().open_file(path)


class GsStatusDiffInlineCommand(TextCommand, GitCommand):

    """
    For every file selected or under a cursor, open a new inline-diff view for
    that file.  If the file is staged, open the inline-diff in cached mode.
    """

    def run(self, edit):
        interface = ui.get_interface(self.view.id())

        non_cached_sections = (interface.get_view_regions("unstaged_files") +
                               interface.get_view_regions("merge_conflicts"))
        non_cached_lines = util.view.get_lines_from_regions(
            self.view,
            self.view.sel(),
            valid_ranges=non_cached_sections
        )
        non_cached_files = (
            os.path.join(self.repo_path, line.strip())
            for line in non_cached_lines
            if line[:4] == "    ")

        cached_sections = interface.get_view_regions("staged_files")
        cached_lines = util.view.get_lines_from_regions(
            self.view,
            self.view.sel(),
            valid_ranges=cached_sections
        )
        cached_files = (
            os.path.join(self.repo_path, line.strip())
            for line in cached_lines
            if line[:4] == "    ")

        sublime.set_timeout_async(
            lambda: self.load_inline_diff_windows(non_cached_files, cached_files), 0)

    def load_inline_diff_windows(self, non_cached_files, cached_files):
        for fpath in non_cached_files:
            syntax = util.file.get_syntax_for_file(fpath)
            settings = {
                "git_savvy.file_path": fpath,
                "git_savvy.repo_path": self.repo_path,
                "syntax": syntax
            }
            self.view.window().run_command("gs_inline_diff", {"settings": settings})

        for fpath in cached_files:
            syntax = util.file.get_syntax_for_file(fpath)
            settings = {
                "git_savvy.file_path": fpath,
                "git_savvy.repo_path": self.repo_path,
                "syntax": syntax
            }
            self.view.window().run_command("gs_inline_diff", {
                "settings": settings,
                "cached": True
            })


class GsStatusDiffCommand(TextCommand, GitCommand):

    """
    For every file selected or under a cursor, open a new diff view for
    that file.  If the file is staged, open the diff in cached mode.
    """

    def run(self, edit):
        interface = ui.get_interface(self.view.id())

        non_cached_sections = (interface.get_view_regions("unstaged_files") +
                               interface.get_view_regions("untracked_files") +
                               interface.get_view_regions("merge_conflicts"))
        non_cached_lines = util.view.get_lines_from_regions(
            self.view,
            self.view.sel(),
            valid_ranges=non_cached_sections
        )
        non_cached_files = (
            os.path.join(self.repo_path, line.strip())
            for line in non_cached_lines
            if line[:4] == "    "
        )

        cached_sections = interface.get_view_regions("staged_files")
        cached_lines = util.view.get_lines_from_regions(
            self.view,
            self.view.sel(),
            valid_ranges=cached_sections
        )
        cached_files = (
            os.path.join(self.repo_path, line.strip())
            for line in cached_lines
            if line[:4] == "    "
        )

        sublime.set_timeout_async(
            lambda: self.load_diff_windows(non_cached_files, cached_files), 0)

    def load_diff_windows(self, non_cached_files, cached_files):
        for fpath in non_cached_files:
            self.view.window().run_command("gs_diff", {
                "file_path": fpath,
                "current_file": True
            })

        for fpath in cached_files:
            self.view.window().run_command("gs_diff", {
                "file_path": fpath,
                "in_cached_mode": True,
                "current_file": True
            })


class GsStatusStageFileCommand(TextCommand, GitCommand):

    """
    For every file that is selected or under a cursor, if that file is
    unstaged, stage it.
    """

    def run(self, edit):
        interface = ui.get_interface(self.view.id())
        valid_ranges = (interface.get_view_regions("unstaged_files") +
                        interface.get_view_regions("untracked_files") +
                        interface.get_view_regions("merge_conflicts"))

        lines = util.view.get_lines_from_regions(
            self.view,
            self.view.sel(),
            valid_ranges=valid_ranges
        )
        # Remove the leading spaces and hyphen-character for deleted files.
        file_paths = tuple(line[4:].strip() for line in lines if line)

        if file_paths:
            for fpath in file_paths:
                self.stage_file(fpath)
            sublime.status_message("Staged files successfully.")
            util.view.refresh_gitsavvy(self.view)


class GsStatusUnstageFileCommand(TextCommand, GitCommand):

    """
    For every file that is selected or under a cursor, if that file is
    staged, unstage it.
    """

    def run(self, edit):
        interface = ui.get_interface(self.view.id())
        valid_ranges = interface.get_view_regions("staged_files")
        lines = util.view.get_lines_from_regions(
            self.view,
            self.view.sel(),
            valid_ranges=valid_ranges
        )
        # Remove the leading spaces and hyphen-character for deleted files.
        file_paths = tuple(line[4:].strip() for line in lines if line)

        if file_paths:
            for fpath in file_paths:
                self.unstage_file(fpath)
            sublime.status_message("Unstaged files successfully.")
            util.view.refresh_gitsavvy(self.view)


class GsStatusDiscardChangesToFileCommand(TextCommand, GitCommand):

    """
    For every file that is selected or under a cursor, if that file is
    unstaged, reset the file to HEAD.  If it is untracked, delete it.
    """

    @util.actions.destructive(description="discard one or more files")
    def run(self, edit):
        interface = ui.get_interface(self.view.id())
        self.discard_untracked(interface)
        self.discard_unstaged(interface)
        util.view.refresh_gitsavvy(self.view)
        sublime.status_message("Successfully discarded changes.")

    def discard_untracked(self, interface):
        valid_ranges = interface.get_view_regions("untracked_files")
        lines = util.view.get_lines_from_regions(
            self.view,
            self.view.sel(),
            valid_ranges=valid_ranges
        )
        file_paths = tuple(line[4:].strip() for line in lines if line)

        if file_paths:
            for fpath in file_paths:
                self.discard_untracked_file(fpath)

    def discard_unstaged(self, interface):
        valid_ranges = (interface.get_view_regions("unstaged_files") +
                        interface.get_view_regions("merge_conflicts"))
        lines = util.view.get_lines_from_regions(
            self.view,
            self.view.sel(),
            valid_ranges=valid_ranges
        )
        file_paths = tuple(line[4:].strip() for line in lines if line)

        if file_paths:
            for fpath in file_paths:
                self.checkout_file(fpath)


class GsStatusOpenFileOnRemoteCommand(TextCommand, GitCommand):

    """
    For every file that is selected or under a cursor, open a new browser
    window to that file on GitHub.
    """

    def run(self, edit):
        interface = ui.get_interface(self.view.id())
        valid_ranges = (interface.get_view_regions("unstaged_files") +
                        interface.get_view_regions("untracked_files") +
                        interface.get_view_regions("merge_conflicts") +
                        interface.get_view_regions("staged_files"))

        lines = util.view.get_lines_from_regions(
            self.view,
            self.view.sel(),
            valid_ranges=valid_ranges
        )
        file_paths = tuple(line[4:].strip() for line in lines if line)

        if file_paths:
            file_paths = list(file_paths)
            for fpath in file_paths:
                self.view.run_command("gs_open_file_on_remote", {"fpath": fpath})


class GsStatusStageAllFilesCommand(TextCommand, GitCommand):

    """
    Stage all unstaged files.
    """

    def run(self, edit):
        self.add_all_tracked_files()
        util.view.refresh_gitsavvy(self.view)


class GsStatusStageAllFilesWithUntrackedCommand(TextCommand, GitCommand):

    """
    Stage all unstaged files, including new files.
    """

    def run(self, edit):
        self.add_all_files()
        util.view.refresh_gitsavvy(self.view)


class GsStatusUnstageAllFilesCommand(TextCommand, GitCommand):

    """
    Unstage all staged changes.
    """

    def run(self, edit):
        self.unstage_all_files()
        util.view.refresh_gitsavvy(self.view)


class GsStatusDiscardAllChangesCommand(TextCommand, GitCommand):

    """
    Reset all unstaged files to HEAD.
    """

    @util.actions.destructive(description="discard all unstaged changes")
    def run(self, edit):
        self.discard_all_unstaged()
        util.view.refresh_gitsavvy(self.view)


class GsStatusCommitCommand(TextCommand, GitCommand):

    """
    Open a commit window.
    """

    def run(self, edit):
        self.view.window().run_command("gs_commit", {"repo_path": self.repo_path})


class GsStatusCommitUnstagedCommand(TextCommand, GitCommand):

    """
    Open a commit window.  When the commit message is provided, stage all unstaged
    changes and then do the commit.
    """

    def run(self, edit):
        self.view.window().run_command(
            "gs_commit",
            {"repo_path": self.repo_path, "include_unstaged": True}
        )


class GsStatusAmendCommand(TextCommand, GitCommand):

    """
    Open a commit window to amend the previous commit.
    """

    def run(self, edit):
        self.view.window().run_command(
            "gs_commit",
            {"repo_path": self.repo_path, "amend": True}
        )


class GsStatusIgnoreFileCommand(TextCommand, GitCommand):

    """
    For each file that is selected or under a cursor, add an
    entry to the git root's `.gitignore` file.
    """

    def run(self, edit):
        interface = ui.get_interface(self.view.id())
        valid_ranges = (interface.get_view_regions("unstaged_files") +
                        interface.get_view_regions("untracked_files") +
                        interface.get_view_regions("merge_conflicts") +
                        interface.get_view_regions("staged_files"))
        lines = util.view.get_lines_from_regions(
            self.view,
            self.view.sel(),
            valid_ranges=valid_ranges
        )
        file_paths = tuple(line[4:].strip() for line in lines if line)

        if file_paths:
            for fpath in file_paths:
                self.add_ignore(os.path.join("/", fpath))
            sublime.status_message("Successfully ignored files.")
            util.view.refresh_gitsavvy(self.view)


class GsStatusIgnorePatternCommand(TextCommand, GitCommand):

    """
    For the first file that is selected or under a cursor (other
    selections/cursors will be ignored), prompt the user for
    a new pattern to `.gitignore`, prefilled with the filename.
    """

    def run(self, edit):
        interface = ui.get_interface(self.view.id())
        valid_ranges = (interface.get_view_regions("unstaged_files") +
                        interface.get_view_regions("untracked_files") +
                        interface.get_view_regions("merge_conflicts") +
                        interface.get_view_regions("staged_files"))
        lines = util.view.get_lines_from_regions(
            self.view,
            self.view.sel(),
            valid_ranges=valid_ranges
        )
        file_paths = tuple(line[4:].strip() for line in lines if line)

        if file_paths:
            self.view.window().run_command("gs_ignore_pattern", {"pre_filled": file_paths[0]})


class GsStatusApplyStashCommand(TextCommand, GitCommand):

    """
    Apply the selected stash.  The user can only apply one stash at a time.
    """

    def run(self, edit):
        interface = ui.get_interface(self.view.id())
        lines = util.view.get_lines_from_regions(
            self.view,
            self.view.sel(),
            valid_ranges=interface.get_view_regions("stashes")
        )
        ids = tuple(line[line.find("(")+1:line.find(")")] for line in lines if line)

        if len(ids) > 1:
            sublime.status_message("You can only apply one stash at a time.")
            return

        self.apply_stash(ids[0])
        util.view.refresh_gitsavvy(self.view)


class GsStatusPopStashCommand(TextCommand, GitCommand):

    """
    Pop the selected stash.  The user can only pop one stash at a time.
    """

    def run(self, edit):
        interface = ui.get_interface(self.view.id())
        lines = util.view.get_lines_from_regions(
            self.view,
            self.view.sel(),
            valid_ranges=interface.get_view_regions("stashes")
        )
        ids = tuple(line[line.find("(")+1:line.find(")")] for line in lines if line)

        if len(ids) > 1:
            sublime.status_message("You can only pop one stash at a time.")
            return

        self.pop_stash(ids[0])
        util.view.refresh_gitsavvy(self.view)


class GsStatusShowStashCommand(TextCommand, GitCommand):

    """
    For each selected stash, open a new window to display the diff
    for that stash.
    """

    def run(self, edit):
        interface = ui.get_interface(self.view.id())
        lines = util.view.get_lines_from_regions(
            self.view,
            self.view.sel(),
            valid_ranges=interface.get_view_regions("stashes")
        )
        ids = tuple(line[line.find("(")+1:line.find(")")] for line in lines if line)

        for stash_id in ids:
            stash_name = "stash@{{{}}}".format(stash_id)
            stash_text = self.git("stash", "show", "-p", stash_name)
            stash_view = self.get_stash_view(stash_name)
            stash_view.set_read_only(False)
            stash_view.replace(edit, sublime.Region(0, 0), stash_text)
            stash_view.set_read_only(True)
            stash_view.sel().add(sublime.Region(0, 0))

    def get_stash_view(self, title):
        window = self.window if hasattr(self, "window") else self.view.window()
        repo_path = self.repo_path
        stash_view = util.view.get_read_only_view(self, "stash_" + title)
        stash_view.set_name(title)
        stash_view.set_syntax_file("Packages/Diff/Diff.tmLanguage")
        stash_view.settings().set("git_savvy.repo_path", repo_path)
        window.focus_view(stash_view)
        stash_view.sel().clear()

        return stash_view


class GsStatusCreateStashCommand(TextCommand, GitCommand):

    """
    Create a new stash from the user's unstaged changes.
    """

    def run(self, edit):
        self.view.window().show_input_panel("Description:", "", self.on_done, None, None)

    def on_done(self, description):
        self.create_stash(description)
        util.view.refresh_gitsavvy(self.view)


class GsStatusCreateStashWithUntrackedCommand(TextCommand, GitCommand):

    """
    Create a new stash from the user's unstaged changes, including
    new files.
    """

    def run(self, edit):
        self.view.window().show_input_panel("Description:", "", self.on_done, None, None)

    def on_done(self, description):
        self.create_stash(description, include_untracked=True)
        util.view.refresh_gitsavvy(self.view)


class GsStatusDiscardStashCommand(TextCommand, GitCommand):

    """
    Drop the selected stash.  The user can only discard one stash
    at a time.
    """

    def run(self, edit):
        interface = ui.get_interface(self.view.id())
        lines = util.view.get_lines_from_regions(
            self.view,
            self.view.sel(),
            valid_ranges=interface.get_view_regions("stashes")
        )
        ids = tuple(line[line.find("(")+1:line.find(")")] for line in lines if line)

        if len(ids) > 1:
            sublime.status_message("You can only drop one stash at a time.")
            return

        self.drop_stash(ids[0])
        util.view.refresh_gitsavvy(self.view)


class GsStatusLaunchMergeToolCommand(TextCommand, GitCommand):

    """
    Launch external merge tool for selected file.
    """

    def run(self, edit):
        interface = ui.get_interface(self.view.id())
        valid_ranges = (interface.get_view_regions("unstaged_files") +
                        interface.get_view_regions("untracked_files") +
                        interface.get_view_regions("merge_conflicts") +
                        interface.get_view_regions("staged_files"))
        lines = util.view.get_lines_from_regions(
            self.view,
            self.view.sel(),
            valid_ranges=valid_ranges
        )
        file_paths = tuple(line[4:].strip() for line in lines if line)

        if len(file_paths) > 1:
            sublime.error_message("You can only launch merge tool for a single file at a time.")
            return

        sublime.set_timeout_async(lambda: self.launch_tool_for_file(file_paths[0]), 0)

    def launch_tool_for_file(self, fpath):
        """
        Given the relative path to a tracked file with a merge conflict,
        launch the configured merge tool against the four versions of that
        file (our version, their version, common ancestor, merged version).
        """
        merge_cmd_tmpl = self.get_merge_cmd_tmpl()
        base_content, ours_content, theirs_content = self.get_versioned_content(fpath)

        temp_dir = tempfile.mkdtemp()
        base_path = os.path.join(temp_dir, "base")
        ours_path = os.path.join(temp_dir, "ours")
        theirs_path = os.path.join(temp_dir, "theirs")
        merge_path = os.path.join(self.repo_path, fpath)

        merge_cmd = merge_cmd_tmpl.replace("$REMOTE", theirs_path)
        merge_cmd = merge_cmd.replace("$BASE", base_path)
        merge_cmd = merge_cmd.replace("$LOCAL", ours_path)
        merge_cmd = merge_cmd.replace("$MERGED", merge_path)
        merge_cmd_args = shlex.split(merge_cmd)

        try:
            with open(base_path, "w") as base:
                base.write(base_content)
            with open(ours_path, "w") as ours:
                ours.write(ours_content)
            with open(theirs_path, "w") as theirs:
                theirs.write(theirs_content)

            startupinfo = None
            if os.name == "nt":
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW

            p = subprocess.Popen(
                merge_cmd_args,
                cwd=self.repo_path,
                env=os.environ,
                startupinfo=startupinfo
                )
            p.wait()

        except Exception as e:
            rmtree(temp_dir)
            raise e

    def get_merge_cmd_tmpl(self):
        """
        Query Git for the command to invoke the external merge tool.
        """
        tool = self.git("config", "merge.tool").strip()
        if not tool:
            sublime.error_message("You have not configured a merge tool for Git.")
            return
        return self.git("config", "mergetool.{}.cmd".format(tool))

    def get_versioned_content(self, fpath):
        """
        Given the path to a tracked file with a merge conflict, return
        the contents of the base version (common ancestor), the local version
        (ours), and the remote version (theirs).
        """
        entries = self.git("ls-files", "-u", "-s", "-z", "--", fpath).split("\x00")
        entries = tuple(entry for entry in entries if entry)

        if not len(entries) == 3:
            sublime.error_message("Unable to merge selected file.")
            return

        # 100644 ffba696331701a1007320c5df88c50f4b0cf0ab9 1   example.js
        # 100644 913b897df13331fec0c959be5a994be48c7dc395 2   example.js
        # 100644 0c11353d13f667542c5b4eeddb7af620ff0055a0 3   example.js
        #        ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
        base_hash, ours_hash, theirs_hash = (entry.split(" ")[1] for entry in entries)

        base_content = self.git("show", base_hash)
        ours_content = self.git("show", ours_hash)
        theirs_content = self.git("show", theirs_hash)

        return base_content, ours_content, theirs_content
