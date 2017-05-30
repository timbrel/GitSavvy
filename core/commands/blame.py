import re
from collections import namedtuple, defaultdict
import unicodedata

import sublime
from sublime_plugin import WindowCommand, TextCommand

from ..git_command import GitCommand
from ...common import util
from ..ui_mixins.quick_panel import PanelActionMixin, PaginatedPanel, show_log_panel


BlamedLine = namedtuple("BlamedLine", ("contents", "commit_hash", "orig_lineno", "final_lineno"))

NOT_COMMITED_HASH = "0000000000000000000000000000000000000000"
BLAME_TITLE = "BLAME: {}{}"


class GsBlameCommand(PanelActionMixin, WindowCommand, GitCommand):
    selected_index = 0
    default_actions = [
        ["blame", "Default", (False, )],
        ["blame", "Ignore whitespace", (True, )],
        ["blame", "Detect moved or copied lines within same file", (), {'option': "-M"}],
        ["blame", "Detect moved or copied lines within same commit", (), {'option': "-C"}],
        ["blame", "Detect moved or copied lines across all commits", (), {'option': "-CCC"}],
    ]

    @util.view.single_cursor_coords
    def run(self, coords, file_path=None, repo_path=None, commit_hash=None):
        self.coords = coords
        self._file_path = file_path or self.file_path
        self.__repo_path = repo_path or self.repo_path
        self._commit_hash = commit_hash
        super().run()

    def update_actions(self):
        super().update_actions()
        if self._commit_hash is None:
            self.actions.insert(6,
                ["pick_commit", "Pick a commit"])

    def blame(self, ignore_whitespace=True, option=None):
        view = self.window.new_file()
        view.set_syntax_file("Packages/GitSavvy/syntax/blame.sublime-syntax")
        view.settings().set("git_savvy.blame_view", True)
        view.settings().set("git_savvy.repo_path", self.__repo_path)
        view.settings().set("git_savvy.file_path", self._file_path)
        view.settings().set("git_savvy.lineno", self.coords[0] + 1)
        view.settings().set("git_savvy.commit_hash", self._commit_hash)
        view.settings().set("git_savvy.ignore_whitespace", ignore_whitespace)
        view.settings().set("git_savvy.detect_move_or_copy", option)

        view.settings().set("word_wrap", False)
        view.settings().set("line_numbers", False)
        view.settings().set('indent_guide_options', [])
        view.set_scratch(True)

        view.run_command("gs_blame_initialize_view")

    def pick_commit(self):
        self._branch = None
        show_log_panel(self.commit_generator(), self.picked_commit)

    def picked_commit(self, commit_hash):
        self._commit_hash = commit_hash
        super().run()



class GsBlameInitializeViewCommand(TextCommand, GitCommand):

    def run(self, edit):
        settings = self.view.settings()
        commit_hash = settings.get("git_savvy.commit_hash", None)
        self.view.set_name(
            BLAME_TITLE.format(
                self.get_rel_path(self.file_path) if self.file_path else "unknown",
                " at {}".format(commit_hash) if commit_hash else ""
            )
        )
        content = self.get_content(
            ignore_whitespace=settings.get("git_savvy.ignore_whitespace", False),
            detect_move_or_copy=settings.get("git_savvy.detect_move_or_copy", None),
            commit_hash=commit_hash
        )

        self.view.run_command("gs_new_content_and_regions", {
            "content": content,
            "regions": {},
            "nuke_cursors": False
        })

        if settings.get("git_savvy.lineno", None) is not None:
            self.scroll_to(settings.get("git_savvy.lineno"))
            # Only scroll the first time
            settings.erase("git_savvy.lineno")

    def get_content(self, ignore_whitespace=False, detect_move_or_copy=None, commit_hash=None):
        blame_porcelain = self.git(
            "blame", "-p", '-w' if ignore_whitespace else None, detect_move_or_copy, commit_hash, "--", self.file_path
        )
        blame_porcelain = unicodedata.normalize('NFC', blame_porcelain)
        blamed_lines, commits = self.parse_blame(blame_porcelain.splitlines())

        commit_infos = {
            commit_hash: self.short_commit_info(commit)
            for commit_hash, commit in commits.items()
        }

        partitions = tuple(self.partition(blamed_lines))

        longest_commit_line = max(
            (line
             for commit_info in commit_infos.values()
             for line in commit_info),
            key=len)

        longest_code_line = max(
            (line.contents for partition in partitions for line in partition),
            key=len
        )

        partitions_with_commits_iter = self.couple_partitions_and_commits(
            partitions=partitions,
            commit_infos=commit_infos,
            left_pad=len(longest_commit_line)
        )

        spacer = (
            "-" * len(longest_commit_line) +
            " | " +
            "-" * (5 + len(longest_code_line)) +
            "\n"
        )

        return spacer.join(partitions_with_commits_iter)

    def parse_blame(self, blame_porcelain):
        lines_iter = iter(blame_porcelain)

        blamed_lines = []
        commits = defaultdict(lambda: defaultdict(str))

        for line in lines_iter:
            commit_hash, orig_lineno, final_lineno, _ = \
                re.match(r"([0-9a-f]{40}) (\d+) (\d+)( \d+)?", line).groups()
            commits[commit_hash]["short_hash"] = commit_hash[:12]
            commits[commit_hash]["long_hash"] = commit_hash

            next_line = next(lines_iter)
            while not next_line.startswith("\t"):
                # Iterate through header keys and values.
                try:
                    k, v = re.match(r"([^ ]+) (.+)", next_line).groups()
                except AttributeError as e:
                    # Sometimes git-blame includes keys without values;
                    # since we don't care about these, simply discard.
                    print("Skipping blame line: " + repr(next_line))
                commits[commit_hash][k] = v
                next_line = next(lines_iter)

            # If `next_lines` starts with a tab (and breaks out of the above
            # while loop), it is an actual line of code.  The line following
            # that will be a new header or the end of the file.
            blamed_lines.append(BlamedLine(
                # Strip tab character.
                contents=next_line[1:],
                commit_hash=commit_hash,
                orig_lineno=orig_lineno,
                final_lineno=final_lineno))

        return blamed_lines, commits

    @staticmethod
    def partition(blamed_lines):
        prev_line = None
        current_hunk = []
        for line in blamed_lines:
            if prev_line and line.commit_hash != prev_line.commit_hash:
                yield current_hunk
                current_hunk = []

            prev_line = line
            current_hunk.append(line)
        yield current_hunk

    @staticmethod
    def short_commit_info(commit):
        if commit["long_hash"] == NOT_COMMITED_HASH:
            return ("Not committed yet.", )

        summary = commit["summary"]
        if len(summary) > 40:
            summary = summary[:36] + " ..."
        author_info = commit["author"] + " " + commit["author-mail"]
        time_stamp = util.dates.fuzzy(commit["author-time"]) if commit["author-time"] else ""

        return (summary, commit["short_hash"], author_info, time_stamp)

    @staticmethod
    def couple_partitions_and_commits(partitions, commit_infos, left_pad):
        left_fallback = " " * left_pad
        right_fallback = ""

        for partition in partitions:
            output = ""
            commit_info = commit_infos[partition[0].commit_hash]
            left_len = len(commit_info)
            right_len = len(partition)
            total_lines = len(max((commit_info, partition), key=len))

            for i in range(total_lines):
                left = commit_info[i] if i < left_len else left_fallback
                right = partition[i].contents if i < right_len else right_fallback
                lineno = partition[i].final_lineno if i < right_len else right_fallback

                output += "{left: <{left_pad}} | {lineno: >4} {right}\n".format(
                    left=left,
                    left_pad=left_pad,
                    lineno=lineno,
                    right=right)
                output = output.strip() + "\n"

            yield output

    def scroll_to(self, lineno):
        pattern = r".{{40}} \| {lineno: >4} ".format(lineno=lineno)
        corresponding_region = self.view.find(pattern, 0)
        blame_view_pt = corresponding_region.b

        self.view.sel().add(sublime.Region(blame_view_pt, blame_view_pt))
        sublime.set_timeout_async(lambda: self.view.show_at_center(blame_view_pt), 0)


class GsBlameActionCommand(PanelActionMixin, TextCommand, GitCommand):
    selected_index = 0
    default_actions = [
        ["open_commit", "Open Commit"],
        ["find_line_and_open", "Blame before selected commit"],
        ["open", "Blame on one older commit", (), {'position': "older"}],
        ["open", "Blame on one newer commit", (), {'position': "newer"}],
        ["pick_new_commit", "Pick a new commit to blame"],
        ["show_file_at_commit", "Show file at commit"],
    ]

    @util.view.single_cursor_pt
    def run(self, cursor_pt, edit):
        self.cursor_pt = cursor_pt
        super().run()

    def selected_commit_hash(self):
        hunk_start = util.view.get_instance_before_pt(self.view, self.cursor_pt, r"^\-+ \| \-+")
        if hunk_start is None:
            short_hash_row = 1
        else:
            hunk_start_row, _ = self.view.rowcol(hunk_start)
            short_hash_row = hunk_start_row + 2

        short_hash_pos = self.view.text_point(short_hash_row, 0)
        short_hash = self.view.substr(sublime.Region(short_hash_pos, short_hash_pos + 12))
        return short_hash


    def open_commit(self):
        # Uncommitted blocks.
        if not self.selected_commit_hash().strip():
            return

        self.view.window().run_command("gs_show_commit", {"commit_hash": short_hash})

    def commit_before(self, position, commit_hash):
        # I would like it to be something like this, but I could make it work when
        # i reached the end of the end second last commit
        # previous_commit_hash =  self.git("show", "--format=%H", "--no-patch", "{}^-1".format(commit_hash)).strip()

        log_commits = self.git("log", "--format=%H", "--follow", "--", self.file_path).strip()
        log_commits = log_commits.split("\n")

        commit_hash_len = len(commit_hash)

        for idx, commit in enumerate(log_commits):
            if commit.startswith(commit_hash) :
                if position == "older":
                    if idx < len(log_commits)-1:
                        return log_commits[idx+1]
                    else:
                        # if we are at the end display this the oldest commit
                        return commit
                elif position == "newer":
                    return log_commits[idx-1]

    def newst_commit_for_file(self):
        return self.git("log", "--format=%H", "--follow", "-n", "1", self.file_path).strip()

    def find_line_and_open(self):
        commit_hash = self.selected_commit_hash().strip()
        if not commit_hash:
            self.view.settings().set("git_savvy.commit_hash", self.newst_commit_for_file())
        else:
            self.view.settings().set("git_savvy.commit_hash", self.commit_before("older", commit_hash))

        self.view.run_command("gs_blame_initialize_view")

    def open(self, position):
        settings = self.view.settings()
        commit_hash = settings.get("git_savvy.commit_hash")
        if not commit_hash:
            self.view.settings().set("git_savvy.commit_hash", self.newst_commit_for_file())
            previous_commit_hash = self.commit_before(position, settings.get("git_savvy.commit_hash"))
            settings.set("git_savvy.commit_hash", previous_commit_hash)
        self.view.run_command("gs_blame_initialize_view")

    def show_file_at_commit(self):
        self.view.window().run_command("gs_show_file_at_commit", {
            "commit_hash": self.view.settings().get("git_savvy.commit_hash"),
            "filepath": self.file_path,
            # it is there in the view, with the scope of
            # constant.numeric.line-number.blame.git-savvy
            # Just need to see who to extract it?
            # "lineno":
        })
    def pick_new_commit(self):
        self.view.run_command("gs_blame_pick_commit", {
            "commit_hash": self.view.settings().get("git_savvy.commit_hash"),
        })

class GsBlamePickCommitCommand(TextCommand, GitCommand):

    def run(self, *args, commit_hash=None):
        self._file_path = self.file_path
        self._branch = None
        sublime.set_timeout_async(self.run_async)

    def run_async(self):
        settings = self.view.settings()
        lp = BlameCommitPanel(
            self.commit_generator(),
            self.do_action,
            )
        lp.show()

    def do_action(self, commit_hash):
        settings = self.view.settings()
        settings.set("git_savvy.commit_hash", commit_hash)
        self.view.run_command("gs_blame_initialize_view")


class BlameCommitPanel(PaginatedPanel):

    def format_item(self, entry):
        return ([entry.short_hash + " " + entry.summary,
                 entry.author + ", " + util.dates.fuzzy(entry.datetime)],
                entry.long_hash)




