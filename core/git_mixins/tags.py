import re
from collections import namedtuple
from distutils.version import LooseVersion


TagDetails = namedtuple("TagDetails", ("sha", "tag"))


class TagsMixin():

    def get_tags(self, remote=None, reverse=False):
        """
        Return a list of TagDetails object. These objects correspond
        to all tags found in the repository, containing abbreviated
        hashes and reference names.
        """
        stdout = self.git(
            "ls-remote" if remote else "show-ref",
            "--tags",
            remote if remote else None,
            throw_on_stderr=False
            )
        porcelain_entries = stdout.split("\n")
        if reverse:
            porcelain_entries.reverse()

        entries = [TagDetails(entry[:40], entry[51:]) for entry in iter(porcelain_entries) if entry]
        entries = self.handle_semver_tags(entries)

        return entries

    def get_lastest_local_tag(self):
        """
        Return the latest tag of the current branch. get_tags() fails to return an ordered list.
        """

        tag = self.git("describe", "--tags", "--abbrev=0", throw_on_stderr=False).strip()
        return tag

    def handle_semver_tags(self, entries):
        """
        Sorts tags using LooseVersion if there's a tag matching the semver format.
        """

        semver_test = re.compile('\d+\.\d+\.\d+')
        semver_entries = [True for entry in entries if semver_test.match(entry.tag)]
        semver_tags_present = len(semver_entries) > 0
        if semver_tags_present:
            entries = sorted(entries, key=lambda entry: LooseVersion(entry.tag), reverse=True)
        return entries
