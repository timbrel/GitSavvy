from collections import namedtuple

TagDetails = namedtuple("TagDetails", ("sha", "tag"))


class TagsMixin():

    def get_tags(self, remote=None):
        """
        Return a list of TagDetails object. These objects correspond
        to all tags found in the repository, containing abbreviated
        hashes and reference names.
        """
        entries = []

        stdout = self.git(
            "ls-remote" if remote else "show-ref",
            "--tags",
            remote if remote else None,
            throw_on_stderr=False
            )
        porcelain_entries = stdout.split("\n").__iter__()

        for entry in porcelain_entries:
            if not entry:
                continue
            sha = entry[:40]
            tag = entry[51:]
            entries.append(TagDetails(sha, tag))

        return entries

