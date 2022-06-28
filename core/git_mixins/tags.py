import re
from collections import namedtuple
from distutils.version import LooseVersion

from GitSavvy.core.git_command import mixin_base

TagDetails = namedtuple("TagDetails", ("sha", "tag", "human_date", "relative_date"))


MYPY = False
if MYPY:
    from typing import List, Tuple


class TagsMixin(mixin_base):

    def get_local_tags(self):
        # type: () -> Tuple[List[TagDetails], List[TagDetails]]
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
        entries = [
            TagDetails(*line.split("\x00"))
            for line in stdout.splitlines()
            if line
        ]
        return self.handle_semver_tags(entries)

    def get_remote_tags(self, remote):
        # type: (str) -> Tuple[List[TagDetails], List[TagDetails]]
        stdout = self.git_throwing_silently(
            "ls-remote",
            "--tags",
            remote,
        )
        porcelain_entries = stdout.splitlines()
        entries = [
            TagDetails(entry[:40], entry[51:], "", "")
            for entry in reversed(porcelain_entries)
            if entry
        ]
        return self.handle_semver_tags(entries)

    def get_last_local_tag(self):
        """
        Return the last tag of the current branch. get_tags() fails to return an ordered list.
        """

        tag = self.git("describe", "--tags", "--abbrev=0", throw_on_error=False).strip()
        return tag

    def handle_semver_tags(self, entries):
        # type: (List[TagDetails]) -> Tuple[List[TagDetails], List[TagDetails]]
        """
        Sorts tags using LooseVersion if there's a tag matching the semver format.
        """

        semver_test = re.compile(r'\d+\.\d+\.?\d*')

        semver_entries, regular_entries = [], []
        for entry in entries:
            if semver_test.search(entry.tag):
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
                # the error might me caused of having tags like 1.2.3.1, 1.2.3.beta
                # exception is cant convert str to int, it is comparing 'beta' to 1
                # if that fails then only take the numbers and sort them
                semver_entries = sorted(
                    semver_entries,
                    key=lambda entry: LooseVersion(
                        semver_test.search(entry.tag).group()  # type: ignore[union-attr]
                    ),
                    reverse=True)

        return (regular_entries, semver_entries)
