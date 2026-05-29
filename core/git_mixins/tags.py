from __future__ import annotations
from itertools import chain
import re

from GitSavvy.core.git_command import mixin_base
from GitSavvy.core.caches import cache_in_store_as
from GitSavvy.core.types import FullHash

from typing import Iterable, List, Literal, NamedTuple, Optional, Set


VersionStyle = Literal["calendar", "semver"]


class TagDetails(NamedTuple):
    sha: FullHash
    tag: str
    human_date: str
    relative_date: str


class TagList(NamedTuple):
    regular: List[TagDetails]
    versions: List[TagDetails]
    version_style: VersionStyle = "semver"

    @property
    def all(self):
        return chain(self.regular, self.versions)


SEMVER_TAG_RE = re.compile(
    r"^v?"
    r"\d+\.\d+\.\d+"
    r"(?:-[0-9A-Za-z-.]+)?"
    r"$"
)
CALENDAR_VERSION_TAG_RE = re.compile(
    r"^v?"
    r"(?:19\d{2}|20\d{2})"
    r"\.(?:0?[1-9]|1[0-2])"
    r"\.(?:0?[1-9]|[12]\d|3[01])"
    r"(?:\.(?:[01]?\d|2[0-3])"
    r"(?:\.(?:[0-5]?\d)"
    r"(?:\.(?:[0-5]?\d))?"
    r")?"
    r")?"
    r"$"
)
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
            TagDetails(entry[:40], tag_name, "", "")
            for entry in reversed(porcelain_entries)
            if entry
            if (tag_name := entry[51:])
            if not tag_name.endswith("^{}")
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
        tag_list = self.get_local_tags()
        if tag_list.version_style != "semver":
            return None

        tags = tag_list.versions
        return tags[0].tag if tags else None

    def handle_semver_tags(self, entries):
        # type: (Iterable[TagDetails]) -> TagList
        """
        Split list into version and "other" tags, and sort versions.

        Prefer semantic versions if any are present.  Otherwise, treat calendar
        versions as the repository's version style.
        """
        entries = list(entries)
        semver_entries = [entry for entry in entries if is_semver_tag(entry.tag)]
        if semver_entries:
            return TagList(
                [entry for entry in entries if entry not in semver_entries],
                sort_semver_tags(semver_entries),
                "semver"
            )

        calendar_entries = [entry for entry in entries if is_calendar_version_tag(entry.tag)]
        return TagList(
            [entry for entry in entries if entry not in calendar_entries],
            sort_calendar_version_tags(calendar_entries),
            "calendar"
        )


def is_semver_tag(tag):
    # type: (str) -> bool
    return bool(SEMVER_TAG_RE.match(tag)) and not is_calendar_version_tag(tag)


def is_calendar_version_tag(tag: str) -> bool:
    return bool(CALENDAR_VERSION_TAG_RE.match(tag))


def sort_semver_tags(entries: List[TagDetails]) -> List[TagDetails]:
    return sorted(
        entries,
        key=lambda entry: semver_sort_key(entry.tag),
        reverse=True
    )


def sort_calendar_version_tags(entries: List[TagDetails]) -> List[TagDetails]:
    return sorted(
        entries,
        key=lambda entry: natural_version_key(entry.tag),
        reverse=True
    )


def semver_sort_key(version_str: str) -> tuple[tuple[int, object], ...]:
    base, separator, prerelease = strip_v_prefix(version_str).partition("-")
    return (
        natural_version_key(base)
        + ((2, 0 if separator else 1),)
        + natural_version_key(prerelease)
    )


def natural_version_key(version_str: str) -> tuple[tuple[int, object], ...]:
    return tuple(
        (0, int(part)) if part.isdigit() else (1, part)
        for part in re.split(r'[.-]', version_str)
        if part
    )


def strip_v_prefix(version_str: str) -> str:
    return version_str[1:] if version_str.startswith("v") else version_str
