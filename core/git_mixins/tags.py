from collections import namedtuple

TagDetails = namedtuple("TagDetails", ("sha", "tag"))


class TagsMixin():

    def get_tags(self):
        """
        Return a list of FileStatus objects.  These objects correspond
        Return a list of TagDetails object. These objects correspond
        to all tags found in the local repository, containing
        hashes and abbreviated reference names.
        """
        stdout = self.git("show-ref", "--tags")

        porcelain_entries = stdout.split("\n").__iter__()
        entries = []

        for entry in porcelain_entries:
            if not entry:
                continue
            sha = entry[0:40]
            tag = entry[51:]
            entries.append(TagDetails(sha, tag))

        return entries
