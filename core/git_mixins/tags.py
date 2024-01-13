from distutils.version import LooseVersion
from itertools import chain
import re

from GitSavvy.core import store
from GitSavvy.core.git_command import mixin_base


from typing import Iterable, List, NamedTuple, Optional


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


class TagsMixin(mixin_base):

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
        rv = self.handle_semver_tags(entries)
        store.update_state(self.repo_path, {
            "local_tags": rv,
        })
        return rv

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
        Sorts tags using LooseVersion if there's a tag matching the semver format.
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
                    key=lambda entry: LooseVersion(entry.tag),
                    reverse=True
                )
            except Exception:
                # The error might me caused of having tags like 1.2.3.1 and 1.2.3.beta.
                # Exception thrown is "can't convert str to int" as it is comparing
                # 'beta' with 1.
                # Fallback and take only the numbers as sorting key.
                semver_entries = sorted(
                    semver_entries,
                    key=lambda entry: LooseVersion(
                        SEMVER_TEST.search(entry.tag).group()  # type: ignore[union-attr]
                    ),
                    reverse=True
                )

        return TagList(regular_entries, semver_entries)


def is_semver_tag(tag):
    # type: (str) -> bool
    return bool(SEMVER_TEST.search(tag))
