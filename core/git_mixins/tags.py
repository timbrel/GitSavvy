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
