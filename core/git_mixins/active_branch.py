from GitSavvy.core.git_command import mixin_base
from .. import store


MYPY = False
if MYPY:
    from typing import List


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

    def get_latest_commits(self, max_items=5):
        # type: (int) -> List[str]
        lines = [
            line.split("%00")
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
        try:
            short_hash = lines[0][0]
        except IndexError:
            pass
        else:
            store.update_state(self.repo_path, {"short_hash_length": len(short_hash)})

        rv = list(format_and_limit(lines, max_items)) or ["No commits yet."]
        store.update_state(self.repo_path, {
            "recent_commits": rv,
        })
        return rv


def format_and_limit(lines, max_items):
    for idx, (h, d, s) in enumerate(lines):
        decorations = [
            part for part in d.lstrip()[1:-1].split(", ")
            if part and part != "HEAD" and "HEAD ->" not in part
        ]
        if decorations:
            if idx == 0:
                yield from commit(h, s, decorations)
            else:
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
    return "{}└──  \u200B{}".format(" " * (len(h) - 4), format_decorations(decorations))


def stand_alone_decoration_line(h, decorations):
    return "{} \u200B{}".format(h, format_decorations(decorations))


def format_decorations(decorations):
    return "({})".format(", ".join(decorations))
