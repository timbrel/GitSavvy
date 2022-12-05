from collections import namedtuple

from GitSavvy.core.git_command import mixin_base
from .. import store


MYPY = False
if MYPY:
    from typing import Iterator, List, NamedTuple, Optional
    Commit = NamedTuple("Commit", [
        ("hash", str),
        ("decoration", str),
        ("message", str),
    ])

else:
    Commit = namedtuple("Commit", "hash decoration message")


class ActiveBranchMixin(mixin_base):

    def get_commit_hash_for_head(self):
        """
        Get the SHA1 commit hash for the commit at HEAD.
        """
        return self.git("rev-parse", "HEAD").strip()

    def get_latest_commit_msg_for_head(self):
        """
        Get last commit msg for the commit at HEAD.
        """
        stdout = self.git(
            "log",
            "-n 1",
            "--pretty=format:%h %s",
            "--abbrev-commit",
            throw_on_error=False
        ).strip()
        if stdout:
            try:
                short_hash = stdout.split(maxsplit=1)[0]
            except IndexError:
                pass
            else:
                store.update_state(self.repo_path, {"short_hash_length": len(short_hash)})

        return stdout or "No commits yet."

    def get_latest_commits(self):
        # type: () -> List[Commit]
        commits = [
            Commit(*line.split("%00"))
            for line in self.git(
                "log",
                "-n", "100",
                (
                    "--format="
                    "%h%00"
                    "%d%00"
                    "%s"
                ),
                throw_on_error=False
            ).strip().splitlines()
        ]
        if commits:
            short_hash_length = len(commits[0].hash)
            store.update_state(self.repo_path, {"short_hash_length": short_hash_length})

        store.update_state(self.repo_path, {
            "recent_commits": commits,
        })
        return commits


def format_and_limit(commits, max_items, current_upstream=None):
    # type: (List[Commit], int, Optional[str]) -> Iterator[str]
    for idx, (h, d, s) in enumerate(commits):
        decorations = [
            part for part in d.lstrip()[1:-1].split(", ")
            if part and part != "HEAD" and "HEAD ->" not in part
        ]
        decoration_that_breaks = set(decorations) - {current_upstream}
        if decoration_that_breaks and idx > 0:
            if idx > max_items:
                yield KONTINUATION
            yield stand_alone_decoration_line(h, decorations)
            break
        elif idx < max_items:
            yield from commit(h, s, decorations)


KONTINUATION = "\u200B ⋮"


def commit(h, s, decorations):
    yield commit_line(h, s)
    if decorations:
        yield additional_decoration_line(h, decorations)


def commit_line(h, s):
    return "{} {}".format(h, s)


def additional_decoration_line(h, decorations):
    return "{}` \u200B{}".format(" " * (len(h) - 4), format_decorations(decorations))


def stand_alone_decoration_line(h, decorations):
    return "{} \u200B{}".format(h, format_decorations(decorations))


def format_decorations(decorations):
    return "({})".format(", ".join(decorations))
