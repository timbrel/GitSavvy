from __future__ import annotations
from itertools import chain
import re

from GitSavvy.core.git_command import mixin_base
from GitSavvy.core.utils import cache_in_store_as

from typing import Iterable, List, NamedTuple, Optional, Set


class TagDetails(NamedTuple):
    sha: str
    tag: str
    human_date: str
    relative_date: str


class TagList(NamedTuple):
    regular: List[TagDetails]
    versions: List[TagDetails]

    @property
    def all(self):
        return chain(*self)


SEMVER_TEST = re.compile(r'\d+\.\d+\.?\d*')
REMOTE_TAGOPT_RE = re.compile(r"^remote\.(?P<branch_name>.+?)\.tagopt (?P<options>.+)$")


class TagsMixin(mixin_base):

    @cache_in_store_as("local_tags")
    def get_local_tags(self):
        # type: () -> TagList
        stdout = self.git(
            "for-each-ref",
            "--sort=-creatordate",
            "--format={}".format(
                "%00".join((
                    "%(objectname)",
                    "%(refname:short)",
                    "%(creatordate:format:%e %b %Y)",
                    "%(creatordate:relative)",
                ))
            ),
            "refs/tags"
        )
        entries = (
            TagDetails(*line.split("\x00"))
            for line in stdout.splitlines()
            if line
        )
        return self.handle_semver_tags(entries)

    def get_remote_tags(self, remote):
        # type: (str) -> TagList
        stdout = self.git_throwing_silently(
            "ls-remote",
            "--tags",
            remote,
            timeout=20.0,
        )
        porcelain_entries = stdout.splitlines()
        entries = (
            TagDetails(entry[:40], entry[51:], "", "")
            for entry in reversed(porcelain_entries)
            if entry
        )
        return self.handle_semver_tags(entries)

    @cache_in_store_as("remotes_with_no_tags_set")
    def get_remotes_for_which_to_skip_tags(self):
        # type: () -> Set[str]
        return {
            match.group("branch_name")
            for line in self.git(
                "config",
                "--get-regex",
                r"remote\..*\.tagOpt",
                throw_on_error=False
            ).strip("\n").splitlines()
            if (match := REMOTE_TAGOPT_RE.match(line))
            if "--no-tags" in match.group("options")
        }

    def get_last_local_semver_tag(self):
        # type: () -> Optional[str]
        """
        Return the last tag of the current branch. get_tags() fails to return an ordered list.
        """
        _, tags = self.get_local_tags()
        return tags[0].tag if tags else None

    def handle_semver_tags(self, entries):
        # type: (Iterable[TagDetails]) -> TagList
        """
        Split list into semantic versions and "other" tags.
        Also sort the semantic versions.
        """
        semver_entries, regular_entries = [], []
        for entry in entries:
            if SEMVER_TEST.search(entry.tag):
                semver_entries.append(entry)
            else:
                regular_entries.append(entry)
        if len(semver_entries):
            try:
                semver_entries = sorted(
                    semver_entries,
                    key=lambda entry: parse_version(remove_suffix("^{}", entry.tag)),
                    reverse=True
                )
            except Exception:
                # The error might me caused of having tags like 1.2.3.1 and 1.2.3.beta.
                # Exception thrown is "can't convert str to int" as it is comparing
                # 'beta' with 1.
                # Fallback and take only the numbers as sorting key.
                semver_entries = sorted(
                    semver_entries,
                    key=lambda entry: parse_version(
                        SEMVER_TEST.search(entry.tag).group()  # type: ignore[union-attr]
                    ),
                    reverse=True
                )

        return TagList(regular_entries, semver_entries)


def is_semver_tag(tag):
    # type: (str) -> bool
    return bool(SEMVER_TEST.search(tag))


def parse_version(version_str: str) -> tuple[str | int, ...]:
    return tuple(
        int(part) if part.isdigit() else part
        for part in re.split(r'[.-]', version_str)
        if part
    )


def remove_suffix(suffix: str, s: str) -> str:
    return s[:-len(suffix)] if suffix and s.endswith(suffix) else s
