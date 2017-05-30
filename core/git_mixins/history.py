from collections import namedtuple


LogEntry = namedtuple("LogEntry", (
    "short_hash",
    "long_hash",
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
            '--format=%h%n%H%n%s%n%an%n%ae%n%at%x00%B%x00%x00%n',
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

            short_hash, long_hash, summary, author, email, datetime = entry.split("\n")
            entries.append(LogEntry(short_hash, long_hash, summary, raw_body, author, email, datetime))

        return entries

    def commit_generator(self, limit = 6000, follow=False):
        # Generator for show_log_panel
        skip = 0
        while True:
            logs = self.log(branch=self._branch,
                            file_path=self._file_path,
                            follow=follow,
                            limit=limit,
                            skip=skip)
            if not logs:
                break
            for l in logs:
                yield l
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
        return ret[0:(len(ret)-1)]

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
        return sha is not ""

    def get_short_hash(self, commit_hash):
        return self.git("rev-parse", "--short", commit_hash).strip()
