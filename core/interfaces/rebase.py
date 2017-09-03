import os
import re

import sublime
from sublime_plugin import WindowCommand, TextCommand

from ...common import ui, util
from ..commands import GsNavigate
from ..constants import MERGE_CONFLICT_PORCELAIN_STATUSES
from ..exceptions import GitSavvyError
from ..git_command import GitCommand
from ..git_mixins.rebase import NearestBranchMixin
from ..ui_mixins.quick_panel import PanelActionMixin, show_log_panel


COMMIT_NODE_CHAR = "●"
COMMIT_NODE_CHAR_OPTIONS = "●*"
COMMIT_LINE = re.compile("\s*[%s]\s*([a-z0-9]{3,})" % COMMIT_NODE_CHAR_OPTIONS)
NEAREST_NODE_PATTERN = re.compile(r'.*\*.*\[(.*?)(?:(?:[\^\~]+[\d]*){1})\]')  # http://regexr.com/3gm03


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


class RebaseInterface(ui.Interface, NearestBranchMixin, GitCommand):

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
      [Q] squash commit with ...                [r] rebase branch on top of...
      [S] squash all commits                    [m] toggle preserve merges mode
      [p] drop commit                           [c] continue rebase
      [e] edit commit message                   [{skip_commit_key}] skip commit during rebase
      [d] move commit down (after next)         [A] abort rebase
      [D] move commit after ...
      [u] move commit up (before previous)
      [U] move commit before ...
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
        return self.get_short_hash(self.base_commit())

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
            return "Not rebased." if self.is_not_rebased() else "Ready."

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
            new_hash = rewritten[entry.long_hash] if was_rewritten else None
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
                "commit_hash": self.get_short_hash(new_hash) if was_rewritten else entry.short_hash,
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

    def base_ref(self, reset_ref=False):
        base_ref = self.view.settings().get("git_savvy.rebase.base_ref")

        if not base_ref or reset_ref:
            project_data = sublime.active_window().project_data() or {}
            project_settings = project_data.get('settings', {})
            base_ref = project_settings.get("rebase_default_base_ref")

            if not base_ref:
                # use remote tracking branch as a sane default
                remote_branch = self.get_upstream_for_active_branch()
                base_ref = self.nearest_branch(self.get_current_branch_name(),
                                               default=remote_branch or "master")
                util.debug.add_to_log('Found base ref {}'.format(base_ref))

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

    def is_not_rebased(self):
        return self.base_commit() != self.git("rev-parse", self.base_ref()).strip()

    def contain_merges(self, base_commit=None):
        if not base_commit:
            base_commit = self.base_commit()
        count = self.git("rev-list", "--count", "{}..HEAD".format(base_commit)).strip()
        return int(count) > len(self.entries)

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
        # all commit maniplication actions are all or nothing, we don't have
        # to worry about partially success for now.
        if success:
            log = log[:cursor+1]

            log.append({
                "description": description,
                "branch_name": branch_name,
                "ref_before": ref_before,
                "ref_after": self.get_branch_ref(branch_name)
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
            util.view.refresh_gitsavvy(self.view, refresh_sidebar=True)


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
            util.view.refresh_gitsavvy(self.view, refresh_sidebar=True)


ui.register_listeners(RebaseInterface)


class RewriteBase(TextCommand, GitCommand):

    """
    Base class for all commit manipulation actions.
    """

    def run(self, edit):
        self.interface = ui.get_interface(self.view.id())

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
        m = COMMIT_LINE.match(line_str)
        if m:
            return m.group(1)

    def get_idx_entry_and_prev(self, short_hash):
        entry_before_selected = None

        for idx, entry in enumerate(self.interface.entries):
            if entry.short_hash == short_hash:
                selected_idx, selected_entry = idx, entry
                break
            entry_before_selected = entry

        if not entry_before_selected:
            entry_before_selected = self.log1(self.interface.base_commit())

        return selected_idx, selected_entry, entry_before_selected

    def make_changes(self, commit_chain, description, base_commit=None):
        base_commit = base_commit or self.interface.base_commit()

        if not self.interface.preserve_merges() and self.interface.contain_merges(base_commit):

            sublime.message_dialog(
                "Unable to manipulate merge commits. Either "
                "1) use preserve merges mode,"
                "2) rebase first."
            )
            return

        branch_name, ref, changed_files = self.interface.get_branch_state()
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

        util.view.refresh_gitsavvy(self.view, refresh_sidebar=True)


class GsRebaseSquashCommand(RewriteBase):

    def run(self, edit, step=None):
        self.step = step
        super().run(edit)

    def run_async(self):
        short_hash = self.get_selected_short_hash()
        if not short_hash:
            return

        # Cannot squash first commit.
        if self.interface.entries[0].short_hash == short_hash:
            sublime.message_dialog("Unable to squash first commit.")
            return

        squash_idx, squash_entry, _ = self.get_idx_entry_and_prev(short_hash)

        if self.commit_is_merge(squash_entry.long_hash):
            sublime.message_dialog("Unable to squash a merge.")
            return

        self.squash_idx = squash_idx
        self.squash_entry = squash_entry

        if self.step:
            self.do_action(self.interface.entries[squash_idx-1].long_hash)
        else:
            reversed_logs = list(reversed(self.interface.entries[0:squash_idx]))
            show_log_panel(reversed_logs, self.do_action)

    def do_action(self, target_commit):

        squash_idx, squash_entry, _ = self.get_idx_entry_and_prev(self.squash_entry.short_hash)
        target_idx, target_entry, before_target = \
            self.get_idx_entry_and_prev(self.get_short_hash(target_commit))

        if self.commit_is_merge(target_entry.long_hash):
            sublime.message_dialog("Unable to squash a merge.")
            return

        # Generate identical change templates with author/date metadata in tact.
        commit_chain = self.perpare_rewrites(self.interface.entries[target_idx:])
        commit_chain.insert(1, commit_chain.pop(squash_idx - target_idx))

        # The first commit (the one immediately previous to the selected commit) will
        # not be commited again.  However, the second commit (the selected) must include
        # the diff from the first, and all the meta-data for the squashed commit must
        # match the first.
        commit_chain[0].do_commit = False
        commit_chain[1].msg = commit_chain[0].msg + "\n\n" + commit_chain[1].msg
        commit_chain[1].datetime = commit_chain[0].datetime
        commit_chain[1].author = commit_chain[0].author

        self.make_changes(
            commit_chain,
            "squashed " + squash_entry.short_hash,
            before_target.long_hash
        )


class GsRebaseSquashAllCommand(RewriteBase):

    def run_async(self):
        for entry in self.interface.entries:
            if self.commit_is_merge(entry.long_hash):
                sublime.message_dialog("Unable to squash a merge.")
                return

        # Generate identical change templates with author/date metadata
        # in tact.  However, set do_commit to false for all but the last change,
        # in order for diffs to be rolled into that final commit.
        last_commit_idx = len(self.interface.entries) - 1
        commit_chain = self.perpare_rewrites(self.interface.entries)

        # Take the commit message from the commit-to-squash and append
        # it to the next commit's message.
        for idx, commit in enumerate(commit_chain):
            commit.modified = True
            if idx < last_commit_idx:
                commit.do_commit = False
                commit_chain[idx+1].msg = commit.msg + "\n\n" + commit_chain[idx+1].msg
                commit.msg = None
            else:
                commit.squashed = True

        self.make_changes(commit_chain, "squashed all commits")


class GsRebaseEditCommand(RewriteBase):

    def run_async(self):
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
        commit_chain[0].modified = True

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
        commit_chain = self.perpare_rewrites(self.interface.entries[drop_idx+1:])

        self.make_changes(
            commit_chain,
            "dropped " + short_hash,
            entry_before_drop.long_hash
        )


class GsRebaseMoveUpCommand(RewriteBase):

    def run(self, edit, step=None):
        self.step = step
        super().run(edit)

    def run_async(self):
        short_hash = self.get_selected_short_hash()
        if not short_hash:
            return

        if self.interface.entries[0].short_hash == short_hash:
            sublime.message_dialog("Unable to move first commit up.")
            return

        move_idx, move_entry, _ = self.get_idx_entry_and_prev(short_hash)
        self.move_idx = move_idx
        self.move_entry = move_entry

        if self.step:
            self.do_action(self.interface.entries[move_idx-1].long_hash)
        else:
            logs = list(reversed(self.interface.entries[:move_idx]))
            show_log_panel(logs, self.do_action)

    def do_action(self, target_commit):

        move_idx, move_entry, _ = self.get_idx_entry_and_prev(self.move_entry.short_hash)
        target_idx, target_entry, before_target = \
            self.get_idx_entry_and_prev(self.get_short_hash(target_commit))
        idx = move_idx - target_idx

        commit_chain = self.perpare_rewrites(self.interface.entries[target_idx:])
        _, _, entry_before_target = self.get_idx_entry_and_prev(target_entry.short_hash)
        commit_chain.insert(0, commit_chain.pop(idx))

        try:
            self.make_changes(
                commit_chain,
                "move " + move_entry.short_hash + "up",
                entry_before_target.long_hash
            )
            move_cursor(self.view, -2 * idx)
        except Exception as e:
            GitSavvyError("Unable to move commit, most likely due to a conflict. \n\n{}".format(e))


class GsRebaseMoveDownCommand(RewriteBase):

    def run(self, edit, step=None):
        self.step = step
        super().run(edit)

    def run_async(self):
        short_hash = self.get_selected_short_hash()
        if not short_hash:
            return

        if self.interface.entries[-1].short_hash == short_hash:
            sublime.message_dialog("Unable to move last commit down.")
            return

        move_idx, move_entry, _ = self.get_idx_entry_and_prev(short_hash)
        self.move_idx = move_idx
        self.move_entry = move_entry

        if self.step:
            self.do_action(self.interface.entries[move_idx+1].long_hash)
        else:
            logs = self.interface.entries[move_idx+1:]
            show_log_panel(logs, self.do_action)

    def do_action(self, target_commit):

        move_idx, move_entry, before_move = self.get_idx_entry_and_prev(self.move_entry.short_hash)
        target_idx, target_entry, _ = \
            self.get_idx_entry_and_prev(self.get_short_hash(target_commit))
        idx = target_idx - move_idx

        commit_chain = self.perpare_rewrites(self.interface.entries[move_idx:])
        _, _, entry_before_target = self.get_idx_entry_and_prev(target_entry.short_hash)
        commit_chain.insert(idx, commit_chain.pop(0))

        try:
            self.make_changes(
                commit_chain,
                "move " + move_entry.short_hash + "down",
                before_move.long_hash
            )
            move_cursor(self.view, 2 * idx)
        except Exception as e:
            GitSavvyError("Unable to move commit, most likely due to a conflict. \n\n{}".format(e))


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

    def run(self, *args):
        self.interface = ui.get_interface(self.view.id())
        super().run(*args)

    def _get_branches(self):
        branches = [branch.name_with_remote
                    for branch in self.get_branches()
                    if not branch.active]
        self.select_branch(branches)

    def select_branch(self, branches=None):
        if branches is None:
            sublime.set_timeout_async(self._get_branches, 0)
        else:
            base_ref = self.interface.base_ref()
            self.view.window().show_quick_panel(
                branches,
                filter_quick_panel(lambda idx: self.set_base_ref(branches[idx])),
                selected_index=branches.index(base_ref) if base_ref in branches else None
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
        util.view.refresh_gitsavvy(self.view, refresh_sidebar=True)


class GsRebaseAbortCommand(TextCommand, GitCommand):

    def run(self, edit):
        sublime.set_timeout_async(self.run_async, 0)

    def run_async(self):
        try:
            self.git("rebase", "--abort")
        finally:
            util.view.refresh_gitsavvy(self.view, refresh_sidebar=True)


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
            util.view.refresh_gitsavvy(self.view, refresh_sidebar=True)


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

    offset = 0

    def get_available_regions(self):
        commit_selector = "meta.git-savvy.rebase-graph.entry support.type.git-savvy.rebase.commit_hash"
        conflict_selector = "meta.git-savvy.rebase-graph.conflict keyword.other.name.git-savvy.rebase-conflict"

        regions = self.view.find_by_selector(conflict_selector)
        if len(regions) == 0:
            regions = self.view.find_by_selector(commit_selector)

        return regions
