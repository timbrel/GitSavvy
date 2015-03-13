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

        stdout = self.git("show-ref", "--tags", throw_on_stderr=False)
        procelain_entries = stdout.split("\n").__iter__()

        for entry in procelain_entries:
            if not entry:
                continue
            sha = entry[:40]
            tag = entry[51:]
            entries.append(TagDetails(sha, tag))

        return entries

