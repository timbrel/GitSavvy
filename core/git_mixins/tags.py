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

        semver_test = re.compile('v?\d+\.\d+\.?\d*')

        semver_entries, regular_entries = [], []
        for entry in entries:
            if semver_test.match(entry.tag):
                semver_entries.append(entry)
            else:
                regular_entries.append(entry)
        if len(semver_entries):
            semver_entries = sorted(semver_entries, key=lambda entry: LooseVersion(entry.tag), reverse=True)
        return semver_entries + regular_entries
