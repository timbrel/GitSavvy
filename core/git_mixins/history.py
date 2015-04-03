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


class HistoryMixin():

    def log(self, limit=None, skip=None, author=None, fpath=None, start_end=None, reverse=False):

        log_output = self.git(
            "log",
            "-{}".format(limit) if limit else None,
            "--skip={}".format(skip) if skip else None,
            "--author={}".format(author) if author else None,
            "--reverse" if reverse else None,
            '--format=%h%n%H%n%s%n%an%n%ae%n%at%x00%B%x00%x00%n',
            "{}..{}".format(*start_end) if start_end else None,
            "--" if fpath else None,
            fpath
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
