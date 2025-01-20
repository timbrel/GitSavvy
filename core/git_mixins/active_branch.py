from GitSavvy.core.git_command import mixin_base
from GitSavvy.core.git_mixins.tags import is_semver_tag
from GitSavvy.core.utils import cache_in_store_as

from typing import Iterable, Iterator, List, NamedTuple, Optional
from .branches import Branch


class Commit(NamedTuple):
    hash: str
    decoration: str
    message: str


class ActiveBranchMixin(mixin_base):

    def get_commit_hash_for_head(self) -> str:
        """
        Get the SHA1 commit hash for the commit at HEAD.
        """
        return self.git("rev-parse", "HEAD").strip()

    def get_latest_commit_msg_for_head(self) -> str:
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
                self.update_store({"short_hash_length": len(short_hash)})

        return stdout or "No commits yet."

    @cache_in_store_as("recent_commits")
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
            self.update_store({"short_hash_length": short_hash_length})

        return commits


def format_and_limit(commits, max_items, current_upstream=None, branches=[]):
    # type: (List[Commit], int, Optional[str], Iterable[Branch]) -> Iterator[str]
    remote_to_local_names = {
        b.upstream.canonical_name: b.canonical_name
        for b in branches
        if b.is_local and b.upstream
    }
    for idx, (h, d, s) in enumerate(commits):
        decorations_ = d.strip("( )").split(", ") if d else []
        refs_ = only_refs(decorations_)
        decorations = [
            part for part in decorations_
            if (
                part != "HEAD"
                and not part.startswith("HEAD ->")
                and not part.endswith("/HEAD")
                and remote_to_local_names.get(part) not in refs_
            )
        ]
        decorations_that_break = set(decorations) - {current_upstream}

        if decorations_that_break and idx > 0:
            if idx > max_items:
                yield KONTINUATION
            yield stand_alone_decoration_line(h, decorations)
            break
        elif idx < max_items:
            yield from commit(h, s, decorations)
            if decorations_include_semver_tag(decorations):
                break


KONTINUATION = "\u200B ⋮"


def only_refs(decorations):
    return [
        p[8:] if p.startswith("HEAD ->") else p
        for p in decorations
        if p != "HEAD" and not p.startswith("tag: ")
    ]


def only_tags(decorations):
    return [d[5:] for d in decorations if d.startswith("tag: ")]


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


def decorations_include_semver_tag(decorations):
    return decorations and any(map(is_semver_tag, only_tags(decorations)))
