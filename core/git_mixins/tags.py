from collections import namedtuple


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

        return entries

    def get_lastest_local_tag(self):
        """
        Return the latest tag. get_tags() fails to return an ordered list.
        """

        sha = self.git("rev-list", "--tags", "--max-count=1", throw_on_stderr=False).strip()
        tag = self.git("describe", "--tags", sha, throw_on_stderr=False).strip()
        return tag
