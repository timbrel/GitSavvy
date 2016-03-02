import os

import sublime
from sublime_plugin import WindowCommand, TextCommand

from ...common import ui
from ..git_command import GitCommand
from ...common import util


def filter_quick_panel(fn):
    return lambda idx: fn(idx) if idx != -1 else None


def move_cursor(view, line_change):
    sels = view.sel()
    new_sels = []
    for sel in sels:
        a_row, a_col = view.rowcol(sel.a)
        b_row, b_col = view.rowcol(sel.b)
        new_a_pt = view.text_point(a_row + line_change, a_col)
        new_b_pt = view.text_point(b_row + line_change, b_col)
        new_sels.append(sublime.Region(new_a_pt, new_b_pt))
    sels.clear()
    sels.add_all(new_sels)


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
    syntax_file = "Packages/GitSavvy/syntax/rebase.sublime-syntax"
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

        ** All actions take immediate effect, but can be undone. **

      ########################                  ############
      ## MANIPULATE COMMITS ##                  ## REBASE ##
      ########################                  ############

      [q] squash commit with previous           [f] define base ref for dashboard
      [Q] squash all commits                    [r] rebase branch on top of...
      [e] edit commit message                   [c] continue rebase
      [p] drop commit                           [k] skip commit during rebase
      [d] move commit down (after next)         [A] abort rebase
      [u] move commit up (before previous)
      [w] show commit

      [tab]       transition to next dashboard
      [SHIFT-tab] transition to previous dashboard

      [{super_key}-Z] undo previous action
      [{super_key}-Y] redo action
    {conflicts_bindings}
    -
    """

    conflicts_keybindings = """
    ###############
    ## CONFLICTS ##
    ###############

    [o] open file
    [s] stage file in current state
    [y] use version from your commit
    [b] use version from new base
    [M] launch external merge tool
    """

    separator = "\n    ┃\n"
    commit = "  {caret} {status}  {commit_hash}  {commit_summary}{conflicts}"
    conflict = "    ┃           conflict: {path}"

    _base_commit = None
    _active_conflicts = None

    def __init__(self, *args, **kwargs):
        self.conflicts_keybindings = \
            "\n".join(line[2:] for line in self.conflicts_keybindings.split("\n"))
        super().__init__(*args, **kwargs)

    def title(self):
        return "REBASE: {}".format(os.path.basename(self.repo_path))

    def pre_render(self):
        self._in_rebase = self.in_rebase()
        self.view.settings().set("git_savvy.in_rebase", self._in_rebase)
        cached_pre_rebase_state = self.view.settings().get("git_savvy.rebase_in_progress")
        if cached_pre_rebase_state:
            branch_state, target_branch = cached_pre_rebase_state
            self.complete_action(
                branch_state,
                True,
                "rebased on top of {}".format(target_branch)
                )
            self.view.settings().set("git_savvy.rebase_in_progress", None)

    @ui.partial("active_branch")
    def render_active_branch(self):
        return (self.rebase_branch_name()
                if self._in_rebase else
                self.get_current_branch_name())

    @ui.partial("base_ref")
    def render_base_ref(self):
        return self.base_ref()

    @ui.partial("base_commit")
    def render_base_commit(self):
        return self.base_commit()[:7]

    @ui.partial("status")
    def render_status(self):
        if self._in_rebase:
            return "Rebase halted due to CONFLICT."

        log = self.view.settings().get("git_savvy.rebase_log") or []
        log_len = len(log)
        saved_cursor = self.view.settings().get("git_savvy.rebase_log_cursor")
        cursor = saved_cursor if saved_cursor is not None else log_len - 1

        if cursor < 0 and log_len > 0:
            return "Redo available."

        try:
            cursor_entry = log[cursor]
        except IndexError:
            return "Ready."

        if cursor == log_len - 1:
            return "Successfully {}. Undo available.".format(cursor_entry["description"])

        return "Successfully {}. Undo/redo available.".format(cursor_entry["description"])

    @ui.partial("diverged_commits")
    def render_diverged_commits(self):
        commits_info = self.get_diverged_commits_info(
            start=self.base_commit(),
            end=self.rebase_orig_head() if self._in_rebase else "HEAD"
            )
        return self.separator.join(self.commit.format(**commit_info) for commit_info in commits_info)

    @ui.partial("super_key")
    def render_super_key(self):
        return util.super_key

    def get_diverged_commits_info(self, start, end):
        self.entries = self.log(start_end=(start, end), reverse=True)
        return (self._get_diverged_in_rebase()
                if self._in_rebase else
                self._get_diverged_outside_rebase())

    def _get_diverged_in_rebase(self):
        self._active_conflicts = None
        conflict_commit = self.rebase_conflict_at()
        rewritten = dict(self.rebase_rewritten())
        commits_info = []

        for entry in self.entries:
            was_rewritten = entry.long_hash in rewritten
            new_hash = rewritten[entry.long_hash][:7] if was_rewritten else None
            is_conflict = entry.long_hash == conflict_commit

            if is_conflict:
                self._active_conflicts = self._get_conflicts_in_rebase()
                conflicts = (
                    "" if not self._active_conflicts else
                    "\n" + "\n".join("    ┃           ! {}".format(conflict.path)
                                     for conflict in self._active_conflicts)
                    )

            commits_info.append({
                "caret": self.CARET if is_conflict else " ",
                "status": (self.SUCCESS if was_rewritten else
                           self.CONFLICT if is_conflict else
                           self.UNKNOWN),
                "commit_hash": new_hash if was_rewritten else entry.short_hash,
                "commit_summary": ("(was {}) {}".format(entry.short_hash, entry.summary)
                                   if was_rewritten else
                                   entry.summary),
                "conflicts": conflicts if is_conflict else ""
            })

        return commits_info

    def _get_conflicts_in_rebase(self):
        """
        Look for unmerged conflicts in status, which are one of:
           DD    unmerged, both deleted
           AU    unmerged, added by us
           UD    unmerged, deleted by them
           UA    unmerged, added by them
           DU    unmerged, deleted by us
           AA    unmerged, both added
           UU    unmerged, both modified
        """
        return [
            entry
            for entry in self.get_status()
            if (
                (entry.index_status == "D" and entry.working_status == "D") or
                (entry.index_status == "A" and entry.working_status == "U") or
                (entry.index_status == "U" and entry.working_status == "D") or
                (entry.index_status == "U" and entry.working_status == "A") or
                (entry.index_status == "D" and entry.working_status == "U") or
                (entry.index_status == "A" and entry.working_status == "A") or
                (entry.index_status == "U" and entry.working_status == "U")
            )
        ]

    def _get_diverged_outside_rebase(self):
        return [{"caret": " ",
                 "status": self.UNKNOWN,
                 "commit_hash": entry.short_hash,
                 "commit_summary": entry.summary,
                 "conflicts": ""}
                for entry in self.entries]

    @ui.partial("conflicts_bindings")
    def render_conflicts_bindings(self):
        return self.conflicts_keybindings if self._in_rebase else ""

    def base_ref(self):
        base_ref = self.view.settings().get("git_savvy.rebase.base_ref")

        if not base_ref:
            project_data = sublime.active_window().project_data() or {}
            project_settings = project_data.get('settings', {})
            base_ref = project_settings.get("rebase_default_base_ref", "master")
            branches = list(self.get_branches())

            # Check that the base_ref we return is a valid branch
            if base_ref not in [branch.name_with_remote for branch in branches]:
                # base_ref isn't a valid branch, so we'll try to pick a sensible alternative
                local_branches = [branch for branch in branches if not branch.remote]
                inactive_local_branches = [branch for branch in local_branches if not branch.active]

                if inactive_local_branches:
                    base_ref = inactive_local_branches[0].name_with_remote
                elif local_branches:
                    base_ref = local_branches[0].name_with_remote
                else:
                    base_ref = "HEAD"

            self.view.settings().set("git_savvy.rebase.base_ref", base_ref)

        return base_ref

    def base_commit(self):
        if self._in_rebase:
            return self.rebase_onto_commit()

        base_ref = self.base_ref()
        self._base_commit = self.git("merge-base", "HEAD", base_ref).strip()
        return self._base_commit

    def get_branch_ref(self, branch_name):
        stdout = self.git("show-ref", "refs/heads/" + branch_name)
        return stdout.strip().split(" ")[0]

    def get_branch_state(self):
        branch_name = self.get_current_branch_name()
        ref = self.get_branch_ref(branch_name)
        return branch_name, ref

    def complete_action(self, branch_state, success, description):
        log = self.view.settings().get("git_savvy.rebase_log") or []
        cursor = self.view.settings().get("git_savvy.rebase_log_cursor") or (len(log) - 1)
        log = log[:cursor+1]

        branch_name, ref_before = branch_state
        log.append({
            "description": description,
            "branch_name": branch_name,
            "ref_before": ref_before,
            "ref_after": self.get_branch_ref(branch_name),
            "success": success
            })

        cursor = len(log) - 1

        self.set_log(log, cursor)

    def get_log(self):
        settings = self.view.settings()
        return settings.get("git_savvy.rebase_log"), settings.get("git_savvy.rebase_log_cursor")

    def set_log(self, log, cursor):
        self.view.settings().set("git_savvy.rebase_log", log)
        self.view.settings().set("git_savvy.rebase_log_cursor", cursor)


class GsRebaseUndoCommand(TextCommand, GitCommand):

    """
    Revert branch HEAD to point to commit prior to previous action.
    """

    def run(self, edit):
        self.interface = ui.get_interface(self.view.id())
        sublime.set_timeout_async(self.run_async, 0)

    def run_async(self):
        log, cursor = self.interface.get_log()
        if log is None or cursor is None or cursor == -1:
            return

        branch_name, ref = self.interface.get_branch_state()

        current = log[cursor]
        if current["branch_name"] != branch_name:
            sublime.error_message("Current branch does not match expected. Cannot undo.")
            return

        try:
            self.checkout_ref(current["ref_before"])
            self.git("branch", "-f", branch_name, "HEAD")
            cursor -= 1

        except Exception as e:
            sublime.error_message("Error encountered. Cannot undo.")
            raise e

        finally:
            self.checkout_ref(branch_name)
            self.interface.set_log(log, cursor)
            util.view.refresh_gitsavvy(self.view)


class GsRebaseRedoCommand(TextCommand, GitCommand):

    """
    If an undo action was taken, set branch HEAD to point to commit of
    un-done action.
    """

    def run(self, edit):
        self.interface = ui.get_interface(self.view.id())
        sublime.set_timeout_async(self.run_async, 0)

    def run_async(self):
        log, cursor = self.interface.get_log()
        if log is None or cursor is None or cursor == len(log) - 1:
            return

        branch_name, ref = self.interface.get_branch_state()

        undone_action = log[cursor+1]
        if undone_action["branch_name"] != branch_name:
            sublime.error_message("Current branch does not match expected. Cannot redo.")
            return

        try:
            self.checkout_ref(undone_action["ref_after"])
            self.git("branch", "-f", branch_name, "HEAD")
            cursor += 1

        except Exception as e:
            sublime.error_message("Error encountered. Cannot redo.")
            raise e

        finally:
            self.checkout_ref(branch_name)
            self.interface.set_log(log, cursor)
            util.view.refresh_gitsavvy(self.view)


ui.register_listeners(RebaseInterface)


class RewriteBase(TextCommand, GitCommand):

    """
    Base class for all commit manipulation actions.
    """

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

    def get_idx_entry_and_prev(self, short_hash):
        for idx, entry in enumerate(self.interface.entries):
            if entry.short_hash == short_hash:
                selected_idx, selected_entry = idx, entry
                break
            entry_before_selected = entry

        return selected_idx, selected_entry, entry_before_selected

    def make_changes(self, commit_chain, description, base_commit=None):
        base_commit = base_commit or self.interface.base_commit()
        branch_state = self.interface.get_branch_state()
        success = True

        try:
            self.rewrite_active_branch(
                base_commit=base_commit,
                commit_chain=commit_chain
                )

        except Exception as e:
            success = False
            raise e

        finally:
            self.interface.complete_action(branch_state, success, description)

        util.view.refresh_gitsavvy(self.view)


class GsRebaseSquashCommand(RewriteBase):

    def run_async(self):
        short_hash = self.get_selected_short_hash()
        if not short_hash:
            return

        # Cannot squash first commit.
        if self.interface.entries[0].short_hash == short_hash:
            sublime.status_message("Unable to squash first commit.")
            return

        squash_idx, to_squash, before_squash = self.get_idx_entry_and_prev(short_hash)
        _, _, two_entries_before_squash = self.get_idx_entry_and_prev(before_squash.short_hash)

        # Generate identical change templates with author/date metadata in tact.
        commit_chain = [
            self.ChangeTemplate(orig_hash=entry.long_hash,
                                do_commit=True,
                                msg=entry.raw_body,
                                datetime=entry.datetime,
                                author="{} <{}>".format(entry.author, entry.email))
            for entry in self.interface.entries[squash_idx-1:]
        ]

        # The first commit (the one immediately previous to the selected commit) will
        # not be commited again.  However, the second commit (the selected) must include
        # the diff from the first, and all the meta-data for the squashed commit must
        # match the first.
        commit_chain[0].do_commit = False
        commit_chain[1].msg = commit_chain[0].msg + "\n\n" + commit_chain[1].msg
        commit_chain[1].datetime = commit_chain[0].datetime
        commit_chain[1].author = commit_chain[0].author

        self.make_changes(commit_chain, "squashed " + short_hash, two_entries_before_squash.long_hash)
        move_cursor(self.view, -2)


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

        self.make_changes(commit_chain, "squashed all commits")


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
        short_hash = entry_to_edit.short_hash
        edit_idx, to_edit, entry_before_edit = self.get_idx_entry_and_prev(short_hash)

        # Generate identical change templates with author/date metadata
        # in tact.  For the edited entry, replace the message with
        # the content from the temporary edit view.
        commit_chain = [
            self.ChangeTemplate(orig_hash=entry.long_hash,
                                do_commit=True,
                                msg=commit_msg if entry == entry_to_edit else entry.raw_body,
                                datetime=entry.datetime,
                                author="{} <{}>".format(entry.author, entry.email))
            for entry in self.interface.entries[edit_idx:]
        ]

        self.make_changes(
            commit_chain,
            "edited " + entry_to_edit.short_hash,
            entry_before_edit.long_hash
        )


class GsRebaseDropCommand(RewriteBase):

    def run_async(self):
        short_hash = self.get_selected_short_hash()
        if not short_hash:
            return
        drop_idx, to_drop, entry_before_drop = self.get_idx_entry_and_prev(short_hash)

        # Generate identical change templates with author/date metadata in tact.
        commit_chain = [
            self.ChangeTemplate(orig_hash=entry.long_hash,
                                do_commit=True,
                                msg=entry.raw_body,
                                datetime=entry.datetime,
                                author="{} <{}>".format(entry.author, entry.email))
            for entry in self.interface.entries[drop_idx+1:]
        ]

        self.make_changes(commit_chain, "dropped " + short_hash, entry_before_drop.long_hash)


class GsRebaseMoveUpCommand(RewriteBase):

    def run_async(self):
        short_hash = self.get_selected_short_hash()
        if not short_hash:
            return
        if self.interface.entries[0].short_hash == short_hash:
            sublime.status_message("Unable to move first commit up.")
            return

        move_idx, to_move, entry_before_move = self.get_idx_entry_and_prev(short_hash)

        # Find the base commit - this is tricky because you have to use the two
        # commits previous to the selected commit as the base commit.  If
        # the selected commit is the second visible commit, we'll fall back
        # to the default base commit hash.
        if self.interface.entries[1].short_hash == short_hash:
            base_commit_hash = None
        else:
            _, _, two_entries_before_move = self.get_idx_entry_and_prev(entry_before_move.short_hash)
            base_commit_hash = two_entries_before_move.long_hash

        commit_chain = [
            self.ChangeTemplate(orig_hash=entry.long_hash,
                                do_commit=True,
                                msg=entry.raw_body,
                                datetime=entry.datetime,
                                author="{} <{}>".format(entry.author, entry.email))
            # Start at the commit prior to the selected commit.
            for entry in self.interface.entries[move_idx-1:]
        ]

        # Take the change to move and swap it with the one before.
        commit_chain[0], commit_chain[1] = commit_chain[1], commit_chain[0]

        try:
            self.make_changes(commit_chain, "moved " + short_hash + " up", base_commit_hash)
            move_cursor(self.view, -2)
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

        move_idx, to_move, entry_before_move = self.get_idx_entry_and_prev(short_hash)

        commit_chain = [
            self.ChangeTemplate(orig_hash=entry.long_hash,
                                move=entry.short_hash == short_hash,
                                do_commit=True,
                                msg=entry.raw_body,
                                datetime=entry.datetime,
                                author="{} <{}>".format(entry.author, entry.email))
            for entry in self.interface.entries[move_idx:]
        ]

        # Take the change to move and swap it with the one following.
        commit_chain[0], commit_chain[1] = commit_chain[1], commit_chain[0]

        try:
            self.make_changes(commit_chain, "moved " + short_hash + " down", entry_before_move.long_hash)
            move_cursor(self.view, 2)
        except:
            sublime.message_dialog("Unable to move commit, most likely due to a conflict.")


class GsRebaseShowCommitCommand(RewriteBase):

    def run_async(self):
        short_hash = self.get_selected_short_hash()
        if not short_hash:
            return

        long_hash = None
        for entry in self.interface.entries:
            if entry.short_hash == short_hash:
                long_hash = entry.long_hash
        if not long_hash:
            return

        self.view.window().run_command("gs_show_commit", {"commit_hash": long_hash})


class GsRebaseOpenFileCommand(TextCommand, GitCommand):

    def run(self, edit):
        sublime.set_timeout_async(self.run_async, 0)

    def run_async(self):
        sels = self.view.sel()
        line_regions = [self.view.line(sel) for sel in sels]
        abs_paths = [os.path.join(self.repo_path, line[18:])
                     for reg in line_regions
                     for line in self.view.substr(reg).split("\n") if line]
        for path in abs_paths:
            self.view.window().open_file(path)


class GsRebaseStageFileCommand(TextCommand, GitCommand):

    def run(self, edit):
        sublime.set_timeout_async(self.run_async, 0)

    def run_async(self):
        sels = self.view.sel()
        line_regions = [self.view.line(sel) for sel in sels]
        paths = (line[18:]
                 for reg in line_regions
                 for line in self.view.substr(reg).split("\n") if line)
        for path in paths:
            self.stage_file(path)
        util.view.refresh_gitsavvy(self.view)


class GsRebaseUseCommitVersionCommand(TextCommand, GitCommand):

    def run(self, edit):
        sublime.set_timeout_async(self.run_async, 0)

    def run_async(self):
        interface = ui.get_interface(self.view.id())
        conflicts = interface._active_conflicts

        sels = self.view.sel()
        line_regions = [self.view.line(sel) for sel in sels]
        paths = (line[18:]
                 for reg in line_regions
                 for line in self.view.substr(reg).split("\n") if line)
        for path in paths:
            if self.is_commit_version_deleted(path, conflicts):
                self.git("rm", "--", path)
            else:
                self.git("checkout", "--theirs", "--", path)
                self.stage_file(path)
        util.view.refresh_gitsavvy(self.view)

    def is_commit_version_deleted(self, path, conflicts):
        for conflict in conflicts:
            if conflict.path == path:
                return conflict.working_status == "D"
        return False

class GsRebaseUseBaseVersionCommand(TextCommand, GitCommand):

    def run(self, edit):
        sublime.set_timeout_async(self.run_async, 0)

    def run_async(self):
        interface = ui.get_interface(self.view.id())
        conflicts = interface._active_conflicts

        sels = self.view.sel()
        line_regions = [self.view.line(sel) for sel in sels]
        paths = (line[18:]
                 for reg in line_regions
                 for line in self.view.substr(reg).split("\n") if line)
        for path in paths:
            if self.is_base_version_deleted(path, conflicts):
                self.git("rm", "--", path)
            else:
                self.git("checkout", "--ours", "--", path)
                self.stage_file(path)
        util.view.refresh_gitsavvy(self.view)

    def is_base_version_deleted(self, path, conflicts):
        for conflict in conflicts:
            if conflict.path == path:
                return conflict.index_status == "D"
        return False


class GsRebaseLaunchMergeToolCommand(TextCommand, GitCommand):

    def run(self, edit):
        sublime.set_timeout_async(self.run_async, 0)

    def run_async(self):
        interface = ui.get_interface(self.view.id())
        conflicts = interface._active_conflicts

        sels = self.view.sel()
        line_regions = [self.view.line(sel) for sel in sels]
        paths = [line[18:]
                 for reg in line_regions
                 for line in self.view.substr(reg).split("\n") if line]
        if len(paths) > 1:
            sublime.error_message("You can only launch merge tool for a single file at a time.")
            return

        path = paths[0]

        if self.is_either_version_deleted(path, conflicts):
            sublime.error_message("Cannot open merge tool for file that has been deleted.")
            return

        self.launch_tool_for_file(os.path.join(self.repo_path, path))

    def is_either_version_deleted(self, path, conflicts):
        for conflict in conflicts:
            if conflict.path == path:
                return conflict.index_status == "D" or conflict.working_status == "D"
        return False


class GsRebaseDefineBaseRefCommand(TextCommand, GitCommand):

    base_types = [
        "Use branch as base.",
        "Use ref as base."
    ]

    def run(self, edit):
        sublime.set_timeout_async(self.run_async, 0)

    def run_async(self):
        self.view.window().show_quick_panel(
            self.base_types,
            filter_quick_panel(self.on_type_select)
        )

    def on_type_select(self, type_idx):
        if type_idx == 0:
            branches = [branch.name_with_remote
                             for branch in self.get_branches()
                             if not branch.active]
            self.view.window().show_quick_panel(
                branches,
                filter_quick_panel(lambda idx: self.set_base_ref(branches[idx]))
            )
        elif type_idx == 1:
            self.view.window().show_input_panel(
                "Enter ref to use for base:",
                "",
                lambda entry: self.set_base_ref(entry) if entry else None,
                None,
                None
            )

    def set_base_ref(self, ref):
        self.view.settings().set("git_savvy.rebase.base_ref", ref)
        util.view.refresh_gitsavvy(self.view)


class GsRebaseOnTopOfCommand(TextCommand, GitCommand):

    base_types = [
        "Rebase on top of branch.",
        "Rebase on top of ref."
    ]

    def run(self, edit):
        sublime.set_timeout_async(self.run_async, 0)

    def run_async(self):
        self.view.window().show_quick_panel(
            self.base_types,
            filter_quick_panel(self.on_type_select)
        )

    def on_type_select(self, type_idx):
        if type_idx == 0:
            entries = [branch.name_with_remote
                            for branch in self.get_branches()
                            if not branch.active]
            self.view.window().show_quick_panel(
                entries,
                filter_quick_panel(lambda idx: self.set_base_ref(entries[idx]))
            )
        elif type_idx == 1:
            self.view.window().show_input_panel(
                "Enter commit or other ref to use for rebase:",
                "",
                lambda entry: self.set_base_ref(entry) if entry else None,
                None,
                None
            )

    def set_base_ref(self, selection):
        interface = ui.get_interface(self.view.id())
        branch_state = interface.get_branch_state()
        self.view.settings().set("git_savvy.rebase_in_progress", (branch_state, selection))

        self.view.settings().set("git_savvy.rebase.base_ref", selection)
        self.git("rebase", selection)
        util.view.refresh_gitsavvy(self.view)


class GsRebaseAbortCommand(TextCommand, GitCommand):

    def run(self, edit):
        sublime.set_timeout_async(self.run_async, 0)

    def run_async(self):
        try:
            self.git("rebase", "--abort")
        finally:
            util.view.refresh_gitsavvy(self.view)


class GsRebaseContinueCommand(TextCommand, GitCommand):

    def run(self, edit):
        sublime.set_timeout_async(self.run_async, 0)

    def run_async(self):
        try:
            self.git("rebase", "--continue")
        finally:
            util.view.refresh_gitsavvy(self.view)


class GsRebaseSkipCommand(TextCommand, GitCommand):

    def run(self, edit):
        sublime.set_timeout_async(self.run_async, 0)

    def run_async(self):
        try:
            self.git("rebase", "--skip")
        finally:
            util.view.refresh_gitsavvy(self.view)
