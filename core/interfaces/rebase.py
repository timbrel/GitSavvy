import os

import sublime
from sublime_plugin import WindowCommand, TextCommand

from ...common import ui, util
from ..commands import GsNavigate
from ..constants import MERGE_CONFLICT_PORCELAIN_STATUSES
from ..git_command import GitCommand
from ..ui_mixins.quick_panel import PanelActionMixin
from ..exceptions import GitSavvyError


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
    CONFLICT = "✘"
    UNKNOWN = "●"

    template = """\

      REBASE:  {active_branch} --> {base_ref} ({base_commit}){preserve_merges}
      STATUS:  {status}

        ┬ ({base_commit})
        │
    {diverged_commits}
        │
        ┴

        ** All actions take immediate effect, but can be undone. **

    {< help}
    """

    template_help = """
      ########################                  ############
      ## MANIPULATE COMMITS ##                  ## REBASE ##
      ########################                  ############

      [q] squash commit with previous           [f] define base ref for dashboard
      [Q] squash all commits                    [r] rebase branch on top of...
      [e] edit commit message                   [m] toggle preserve merge mode
      [p] drop commit                           [c] continue rebase
      [d] move commit down (after next)         [{skip_commit_key}] skip commit during rebase
      [u] move commit up (before previous)      [A] abort rebase
      [w] show commit

      [?]         toggle this help menu
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

    separator = "\n    │\n"
    commit = "  {caret} {status}  {commit_hash}  {commit_summary}{conflicts}"

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
            (branch_name, ref, changed_files), target_branch = cached_pre_rebase_state
            self.complete_action(
                branch_name,
                ref,
                True,
                "rebased on top of {}".format(target_branch)
                )
            self.view.settings().set("git_savvy.rebase_in_progress", None)

    def on_new_dashboard(self):
        self.view.run_command("gs_rebase_navigate_commits")

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
            return "Not yet rebased." if self.not_yet_rebased() else "Ready."

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

    @ui.partial("preserve_merges")
    def render_preserve_merge(self):
        if self.preserve_merges():
            return " (Preserving merges)"
        else:
            return ""

    @ui.partial("help")
    def render_help(self):
        help_hidden = self.view.settings().get("git_savvy.help_hidden")
        vintageous_friendly = self.view.settings().get("git_savvy.vintageous_friendly", False)
        if help_hidden:
            return ""
        else:
            return self.template_help.format(
                super_key=util.super_key,
                conflicts_bindings=self.render_conflicts_bindings(),
                skip_commit_key='k' if not vintageous_friendly else 'K')

    def preserve_merges(self):
        savvy_settings = sublime.load_settings("GitSavvy.sublime-settings")
        default = savvy_settings.get("rebase_preserve_merges")
        return not self.in_rebase_apply() and \
            (self.in_rebase_merge() or
             self.view.settings().get("git_savvy.rebase.preserve_merges", default))

    def get_diverged_commits_info(self, start, end):
        preserve = self.preserve_merges()
        self.entries = self.log_rebase(start, end, preserve)
        return (self._get_diverged_in_rebase()
                if self._in_rebase else
                self._get_diverged_outside_rebase())

    def _get_diverged_in_rebase(self):
        self._active_conflicts = None
        conflict_commit = self.rebase_conflict_at()
        rewritten = self.rebase_rewritten()
        commits_info = []

        for entry in self.entries:
            conflicts = ""
            was_rewritten = entry.long_hash in rewritten
            new_hash = rewritten[entry.long_hash][:7] if was_rewritten else None
            is_merge = self.commit_is_merge(entry.long_hash)
            if self.in_rebase_merge() and is_merge:
                is_conflict = conflict_commit in self.commits_of_merge(entry.long_hash)
                if is_conflict:
                    conflict_logs = self.log_merge(entry.long_hash)
                    for conflict_idx, c in enumerate(conflict_logs):
                        conflicts = conflicts + "\n    │    {}  {}  {}".format(
                            self.SUCCESS if c.long_hash in rewritten else
                            self.CONFLICT if c.long_hash == conflict_commit else
                            self.UNKNOWN,
                            c.short_hash,
                            c.summary)
                        if c.long_hash == conflict_commit:
                            self._active_conflicts = self._get_conflicts_in_rebase()
                            if self._active_conflicts:
                                conflicts = conflicts + "\n" + "\n".join(
                                    "    │           ! {}".format(conflict.path)
                                    for conflict in self._active_conflicts)
            else:
                is_conflict = entry.long_hash == conflict_commit
                if is_conflict:
                    self._active_conflicts = self._get_conflicts_in_rebase()
                    if self._active_conflicts:
                        conflicts = conflicts + "\n" + "\n".join(
                            "    │           ! {}".format(conflict.path)
                            for conflict in self._active_conflicts)

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
        Look for unmerged conflicts in status
        """
        return [
            entry
            for entry in self.get_status()
            if (entry.index_status, entry.working_status) in MERGE_CONFLICT_PORCELAIN_STATUSES
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

    def not_yet_rebased(self):
        return self.git("merge-base", self.base_commit(), "HEAD").strip() != \
                self.git("rev-parse", self.base_ref()).strip()

    def get_branch_ref(self, branch_name):
        stdout = self.git("show-ref", "refs/heads/" + branch_name)
        return stdout.strip().split(" ")[0]

    def get_branch_state(self):
        branch_name = self.get_current_branch_name()
        ref = self.get_branch_ref(branch_name)
        index_status = self.get_status()
        return branch_name, ref, index_status

    def complete_action(self, branch_name, ref_before, success, description):
        log = self.view.settings().get("git_savvy.rebase_log") or []
        cursor = self.view.settings().get("git_savvy.rebase_log_cursor") or (len(log) - 1)
        log = log[:cursor+1]

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

        branch_name, ref, _ = self.interface.get_branch_state()

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

        branch_name, ref, _ = self.interface.get_branch_state()

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

    # use to remap parents of merges
    commit_parent_map = {}

    def run(self, edit):
        self.interface = ui.get_interface(self.view.id())

        if self.interface.not_yet_rebased():
            sublime.message_dialog(
                "Unable to manipulate commits, rebase first."
            )
            return

        _, _, changed_files = self.interface.get_branch_state()

        if len(changed_files):
            sublime.message_dialog(
                "Unable to manipulate commits while repo is in unclean state."
            )
            return

        sublime.set_timeout_async(self.run_async, 0)

    def get_selected_short_hash(self):
        sels = self.view.sel()
        if len(sels) > 1 or not sels or sels[0].a != sels[0].b:
            return

        line = self.view.line(sels[0])
        line_str = self.view.substr(line)
        return line_str[7:14]

    def get_idx_entry_and_prev(self, short_hash):
        entry_before_selected = None

        for idx, entry in enumerate(self.interface.entries):
            if entry.short_hash == short_hash:
                selected_idx, selected_entry = idx, entry
                break
            entry_before_selected = entry

        return selected_idx, selected_entry, entry_before_selected

    def make_changes(self, commit_chain, description, base_commit=None):
        branch_name, ref, changed_files = self.interface.get_branch_state()
        base_commit = base_commit or self.interface.base_commit()
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
            self.interface.complete_action(branch_name, ref, success, description)

        util.view.refresh_gitsavvy(self.view)

    def commit_parents(self, commit):
        return [self.commit_parent_map[p] if p in self.commit_parent_map else p
                for p in super().commit_parents(commit)]


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

        if self.commit_is_merge(to_squash.long_hash) or \
                self.commit_is_merge(before_squash.long_hash):
            sublime.status_message("Unable to squash merges.")
            return

        # Generate identical change templates with author/date metadata in tact.
        commit_chain = self.perpare_rewrites(self.interface.entries[squash_idx-1:])

        # The first commit (the one immediately previous to the selected commit) will
        # not be commited again.  However, the second commit (the selected) must include
        # the diff from the first, and all the meta-data for the squashed commit must
        # match the first.
        commit_chain[0].do_commit = False
        commit_chain[1].msg = commit_chain[0].msg + "\n\n" + commit_chain[1].msg
        commit_chain[1].datetime = commit_chain[0].datetime
        commit_chain[1].author = commit_chain[0].author
        self.commit_parent_map = {commit_chain[1].orig_hash: commit_chain[0].orig_hash}

        self.make_changes(
            commit_chain,
            "squashed " + short_hash,
            two_entries_before_squash.long_hash if two_entries_before_squash else None
        )
        move_cursor(self.view, -2)


class GsRebaseSquashAllCommand(RewriteBase):

    def run_async(self):

        # Generate identical change templates with author/date metadata
        # in tact.  However, set do_commit to false for all but the last change,
        # in order for diffs to be rolled into that final commit.
        last_commit_idx = len(self.interface.entries) - 1
        commit_chain = self.perpare_rewrites(self.interface.entries)

        # Take the commit message from the commit-to-squash and append
        # it to the next commit's message.
        for idx, commit in enumerate(commit_chain):
            if idx < last_commit_idx:
                commit.do_commit = False
            if not commit.do_commit:
                commit_chain[idx+1].msg = commit.msg + "\n\n" + commit_chain[idx+1].msg
                commit.msg = None

        self.make_changes(
            commit_chain,
            "squashed all commits")


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
        commit_chain = self.perpare_rewrites(self.interface.entries[edit_idx:])
        commit_chain[0].msg = commit_msg

        self.make_changes(
            commit_chain,
            "edited " + entry_to_edit.short_hash,
            entry_before_edit.long_hash if entry_before_edit else None
        )


class GsRebaseDropCommand(RewriteBase):

    def run_async(self):
        short_hash = self.get_selected_short_hash()
        if not short_hash:
            return
        drop_idx, to_drop, entry_before_drop = self.get_idx_entry_and_prev(short_hash)

        # Generate identical change templates with author/date metadata in tact.
        commit_chain = self.perpare_rewrites(self.interface.entries[drop_idx+1:])
        self.commit_parent_map = {
            to_drop.long_hash: entry_before_drop.long_hash
            if entry_before_drop else self.interface.base_commit()
        }

        self.make_changes(
            commit_chain,
            "dropped " + short_hash,
            entry_before_drop.long_hash if entry_before_drop else None
        )


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
            base_commit_hash = self.interface.base_commit()
        else:
            _, _, two_entries_before_move = self.get_idx_entry_and_prev(entry_before_move.short_hash)
            base_commit_hash = two_entries_before_move.long_hash

        commit_chain = self.perpare_rewrites(self.interface.entries[move_idx-1:])

        if self.commit_is_merge(to_move.long_hash):
            self.commit_parent_map = {
                entry_before_move.long_hash: base_commit_hash
            }
        elif self.commit_is_merge(entry_before_move.long_hash):
            self.commit_parent_map = {
                base_commit_hash: to_move.long_hash
            }
        else:
            self.commit_parent_map = {
                commit_chain[1].orig_hash: commit_chain[0].orig_hash
            }

        # Take the change to move and swap it with the one before.
        commit_chain[0], commit_chain[1] = commit_chain[1], commit_chain[0]

        try:
            self.make_changes(
                commit_chain,
                "moved " + short_hash + " up",
                base_commit_hash)
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
        base_commit_hash = entry_before_move.long_hash \
            if entry_before_move else self.interface.base_commit()

        commit_chain = self.perpare_rewrites(self.interface.entries[move_idx:])

        if self.commit_is_merge(to_move.long_hash):
            self.commit_parent_map = {
                base_commit_hash: commit_chain[1].orig_hash
            }
        elif self.commit_is_merge(commit_chain[1].orig_hash):
            self.commit_parent_map = {
                to_move.long_hash: base_commit_hash
            }
        else:
            self.commit_parent_map = {
                commit_chain[1].orig_hash: commit_chain[0].orig_hash
            }
        # Take the change to move and swap it with the one following.
        commit_chain[0], commit_chain[1] = commit_chain[1], commit_chain[0]

        try:
            self.make_changes(
                commit_chain,
                "moved " + short_hash + " down",
                base_commit_hash)
            move_cursor(self.view, 2)
        except:
            sublime.message_dialog("Unable to move commit, most likely due to a conflict.")


class GsRebaseShowCommitCommand(RewriteBase):

    def run(self, edit):
        self.interface = ui.get_interface(self.view.id())
        sublime.set_timeout_async(self.run_async)

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


class GsRebaseDefineBaseRefCommand(PanelActionMixin, TextCommand, GitCommand):

    default_actions = [
        ["select_branch", "Use branch as base."],
        ["select_ref", "Use ref as base."],
    ]

    def _get_branches(self):
        branches = [branch.name_with_remote
                    for branch in self.get_branches()
                    if not branch.active]
        self.select_branch(branches)

    def select_branch(self, branches=None):
        if branches is None:
            sublime.set_timeout_async(self._get_branches, 0)
        else:
            self.view.window().show_quick_panel(
                branches,
                filter_quick_panel(lambda idx: self.set_base_ref(branches[idx]))
            )

    def select_ref(self):
        self.view.window().show_input_panel(
            "Enter commit or other ref to use for rebase:",
            "",
            lambda entry: self.set_base_ref(entry) if entry else None,
            None, None
        )

    def set_base_ref(self, ref):
        self.view.settings().set("git_savvy.rebase.base_ref", ref)
        util.view.refresh_gitsavvy(self.view)


class GsRebaseOnTopOfCommand(GsRebaseDefineBaseRefCommand):

    default_actions = [
        ["select_branch", "Rebase on top of branch."],
        ["select_ref", "Rebase on top of ref."],
    ]

    def set_base_ref(self, selection):
        interface = ui.get_interface(self.view.id())
        branch_state = interface.get_branch_state()
        self.view.settings().set("git_savvy.rebase_in_progress", (branch_state, selection))

        self.view.settings().set("git_savvy.rebase.base_ref", selection)
        self.git(
            "rebase",
            "-p" if interface.preserve_merges() else None,
            selection)
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
            if self.in_rebase_merge():
                (staged_entries,
                 unstaged_entries,
                 untracked_entries,
                 conflict_entries) = self.sort_status_entries(self.get_status())
                if len(unstaged_entries) + len(untracked_entries) + len(conflict_entries) == 0 and \
                        len(staged_entries) > 0:
                    self.git("commit", "--no-edit")

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


class GsRebaseTogglePreserveModeCommand(TextCommand, GitCommand):

    def run(self, edit):
        preserve = self.view.settings().get("git_savvy.rebase.preserve_merges", False)
        self.view.settings().set("git_savvy.rebase.preserve_merges", not preserve)
        savvy_settings = sublime.load_settings("GitSavvy.sublime-settings")
        savvy_settings.set("rebase_preserve_merges", not preserve)
        util.view.refresh_gitsavvy(self.view)


class GsRebaseNavigateCommitsCommand(GsNavigate):

    """
    Move cursor to the next (or previous) selectable commit in the dashboard.

    If a commit has conflicts, navigate to the next (or previous) file.
    """

    offset_map = {
        "meta.git-savvy.rebase-graph.entry": 7,
        "meta.git-savvy.rebase-graph.conflict": 16,
    }

    def run(self, edit, forward=True):
        sel = self.view.sel()
        if not sel:
            return

        current_position = sel[0].a

        available_regions = self.get_available_regions()

        new_position = (self.forward(current_position, available_regions)
                        if forward
                        else self.backward(current_position, available_regions))

        if new_position is None:
            return

        offset = 7  # default to commit offset, conflict is always after
        next_context = self.view.scope_name(new_position)
        for scope in next_context.split():
            if scope in self.offset_map:
                offset = self.offset_map[scope]

        # Position the cursor at next/previous commit or conflict filename
        sel.clear()
        new_position += offset
        sel.add(sublime.Region(new_position, new_position))

    def get_available_regions(self):
        regions = [region for selector in self.offset_map.keys()
                   for region in self.view.find_by_selector(selector)]
        return sorted([
            line for region in regions for line in self.view.lines(region)])
