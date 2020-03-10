from collections import namedtuple
from ...common import util


MYPY = False
if MYPY:
    from typing import Optional

LogEntry = namedtuple("LogEntry", (
    "short_hash",
    "long_hash",
    "ref",
    "summary",
    "raw_body",
    "author",
    "email",
    "datetime"
))


RefLogEntry = namedtuple("RefLogEntry", (
    "short_hash",
    "long_hash",
    "summary",
    "reflog_name",
    "reflog_selector",
    "author",
    "datetime"
))


class HistoryMixin():

    def log(self, author=None, branch=None, file_path=None, start_end=None, cherry=None,
            limit=6000, skip=None, reverse=False, all_branches=False, msg_regexp=None,
            diff_regexp=None, first_parent=False, merges=False, no_merges=False, topo_order=False,
            follow=False):

        log_output = self.git(
            "log",
            "--max-count={}".format(limit) if limit else None,
            "--skip={}".format(skip) if skip else None,
            "--reverse" if reverse else None,
            '--format=%h%n%H%n%D%n%s%n%an%n%ae%n%at%x00%B%x00%x00%n',
            "--author={}".format(author) if author else None,
            "--grep={}".format(msg_regexp) if msg_regexp else None,
            "--cherry" if cherry else None,
            "--G" if diff_regexp else None,
            diff_regexp if diff_regexp else None,
            "--first-parent" if first_parent else None,
            "--no-merges" if no_merges else None,
            "--merges" if merges else None,
            "--topo-order" if topo_order else None,
            "--follow" if follow else None,
            "--all" if all_branches else None,
            "{}..{}".format(*start_end) if start_end else None,
            branch if branch else None,
            "--" if file_path else None,
            file_path if file_path else None
        ).strip("\x00")

        entries = []
        for entry in log_output.split("\x00\x00\n"):
            entry = entry.strip()
            if not entry:
                continue
            entry, raw_body = entry.split("\x00")

            short_hash, long_hash, ref, summary, author, email, datetime = entry.split("\n")
            entries.append(LogEntry(short_hash, long_hash, ref, summary, raw_body, author, email, datetime))

        return entries

    def log_generator(self, limit=6000, **kwargs):
        # Generator for show_log_panel
        skip = 0
        while True:
            logs = self.log(limit=limit, skip=skip, **kwargs)
            if not logs:
                break
            for l in logs:
                yield l
            if len(logs) < limit:
                break
            skip = skip + limit

    def reflog(self, limit=6000, skip=None, all_branches=False):
        log_output = self.git(
            "reflog",
            "-{}".format(self._limit),
            "--skip={}".format(skip) if skip else None,
            '--format=%h%n%H%n%s%n%gs%n%gd%n%an%n%at%x00%x00%n',
            "--all" if all_branches else None,
        ).strip("\x00")

        entries = []
        for entry in log_output.split("\x00\x00\n"):
            entry = entry.strip()
            if not entry:
                continue
            short_hash, long_hash, summary, reflog_name, reflog_selector, author, datetime = \
                entry.split("\n")
            entries.append(RefLogEntry(
                short_hash, long_hash, summary, reflog_name, reflog_selector, author, datetime))

        return entries

    def reflog_generator(self, limit=6000, skip=None):
        skip = 0
        while True:
            logs = self.reflog(limit=limit, skip=skip)
            if not logs:
                break
            for l in logs:
                yield (["{} {}".format(l.reflog_selector, l.reflog_name),
                        "{} {}".format(l.short_hash, l.summary),
                        "{}, {}".format(l.author, util.dates.fuzzy(l.datetime))],
                       l.long_hash)
            skip = skip + limit

    def log1(self, commit_hash):
        """
        Return a single LogEntry of a commit.
        """
        return self.log(start_end=("{0}~1".format(commit_hash), commit_hash), limit=1)[0]

    def log_merge(self, merge_hash):
        """
        Return all LogEntry of a merge.
        """
        ret = self.log(
            start_end=("{0}~1".format(merge_hash), merge_hash), topo_order=True, reverse=True)
        return ret[0:(len(ret) - 1)]

    def commits_of_merge(self, merge_hash):
        """
        Return commits of a merge.
        """
        return self.git(
            "rev-list", "--topo-order", "{0}~1..{0}".format(merge_hash)).strip().split("\n")[1:]

    def commit_parents(self, commit_hash):
        """
        Return parents of a commit.
        """
        return self.git("rev-list", "-1", "--parents", commit_hash).strip().split(" ")[1:]

    def commit_is_merge(self, commit_hash):
        sha = self.git("rev-list", "--merges", "-1", "{0}~1..{0}".format(commit_hash)).strip()
        return sha != ""

    def get_short_hash(self, commit_hash):
        # type: (str) -> str
        return self.git("rev-parse", "--short", commit_hash).strip()

    def filename_at_commit(self, filename, commit_hash, follow=False):
        commit_len = len(commit_hash)
        lines = self.git(
            "log",
            "--pretty=oneline",
            "--follow" if follow else None,
            "--name-status",
            "{}..{}".format(commit_hash, "HEAD"),
            "--", filename
        ).split("\n")

        for i in range(0, len(lines), 2):
            if lines[i].split(" ")[0][:commit_len] == commit_hash:
                if lines[i + 1][0] == 'R':
                    return lines[i + 1].split("\t")[2]
                else:
                    return lines[i + 1].split("\t")[1]

        # If the commit hash is not for this file.
        return filename

    def get_file_content_at_commit(self, filename, commit_hash):
        filename = self.get_rel_path(filename)
        filename = filename.replace('\\', '/')
        return self.git("show", commit_hash + ':' + filename)

    def find_matching_lineno(self, base_commit, target_commit, line, file_path=None):
        # type: (Optional[str], str, int, str) -> int
        """
        Return the matching line of the target_commit given the line number of the base_commit.
        """
        if not file_path:
            file_path = self.file_path

        if base_commit:
            base_object = self.get_commit_file_object(base_commit, file_path)
        else:
            base_file_contents = util.file.get_file_contents_binary(self.repo_path, file_path)
            base_object = self.get_object_from_string(base_file_contents)

        target_object = self.get_commit_file_object(target_commit, file_path)

        stdout = self.git(
            "diff", "--no-color", "-U0", base_object, target_object)
        diff = util.parse_diff(stdout)

        if not diff:
            return line

        for hunk in reversed(diff):
            head_start = hunk.head_start if hunk.head_length else hunk.head_start + 1
            saved_start = hunk.saved_start if hunk.saved_length else hunk.saved_start + 1
            head_end = head_start + hunk.head_length
            saved_end = saved_start + hunk.saved_length

            if head_end <= line:
                return saved_end + line - head_end
            elif head_start <= line:
                return saved_start

        # fails to find matching
        return line

    def neighbor_commit(self, commit_hash, position, follow=False):
        """
        Get the commit before or after a specific commit
        """
        if position == "older":
            return self.git(
                "log",
                "--format=%H",
                "--follow" if follow else None,
                "-n", "1",
                "{}~1".format(commit_hash),
                "--", self.file_path
            ).strip()
        elif position == "newer":
            return self.git(
                "log",
                "--format=%H",
                "--follow" if follow else None,
                "--reverse",
                "{}..{}".format(commit_hash, "HEAD"),
                "--", self.file_path
            ).strip().split("\n", 1)[0]

    def newest_commit_for_file(self, file_path, follow=False):
        """
        Get the newest commit for a given file.
        """
        return self.git(
            "log", "--format=%H",
            "--follow" if follow else None, "-n", "1", file_path).strip()

    def get_indexed_file_object(self, file_path):
        """
        Given an absolute path to a file contained in a git repo, return
        git's internal object hash associated with the version of that file
        in the index (if the file is staged) or in the HEAD (if it is not
        staged).
        """
        stdout = self.git("ls-files", "-s", file_path)

        # 100644 c9d70aa928a3670bc2b879b4a596f10d3e81ba7c 0   SomeFile.py
        #        ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
        git_file_entry = stdout.split(" ")
        return git_file_entry[1]

    def get_head_file_object(self, file_path):
        """
        Given an absolute path to a file contained in a git repo, return
        git's internal object hash associated with the version of that
        file in the HEAD.
        """
        stdout = self.git("ls-tree", "HEAD", file_path)

        # 100644 blob 7317069f30eafd4d7674612679322d59f9fb65a4    SomeFile.py
        #             ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
        git_file_entry = stdout.split()  # split by spaces and tabs
        return git_file_entry[2]

    def get_commit_file_object(self, commit, file_path):
        """
        Given an absolute path to a file contained in a git repo, return
        git's internal object hash associated with the version of that
        file in the commit.
        """
        stdout = self.git("ls-tree", commit, file_path)

        # 100644 blob 7317069f30eafd4d7674612679322d59f9fb65a4    SomeFile.py
        #             ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
        git_file_entry = stdout.split()  # split by spaces and tabs
        return git_file_entry[2]

    def get_object_contents(self, object_hash):
        """
        Given the object hash to a versioned object in the current git repo,
        display the contents of that object.
        """
        return self.git("show", "--no-color", object_hash)

    def get_object_from_string(self, string):
        """
        Given a string, pipe the contents of that string to git and have it
        stored in the current repo, and return an object-hash that can be
        used to diff against.
        """
        stdout = self.git("hash-object", "-w", "--stdin", stdin=string, encode=False)
        return stdout.split("\n")[0]
