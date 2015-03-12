from collections import namedtuple

TagDetails = namedtuple("TagDetails", ("sha", "tag"))
RemoteGroup = namedtuple("RemoteGroup", ("remote", "entries"))


class TagsMixin():

    def get_tags(self):
        """
        Return a list of FileStatus objects.  These objects correspond
        Return a list of TagDetails object. These objects correspond
        to all tags found in the local repository, containing
        hashes and abbreviated reference names.
        """
        entries = []

        stdout_local = self.git("show-ref", "--tags")
        porcelain_entries_local = stdout_local.split("\n").__iter__()

        for entry in porcelain_entries_local:
            if not entry:
                continue
            sha = entry[:40]
            tag = entry[51:]
            entries.append(TagDetails(sha, tag))

        remotes = list(self.get_remotes().keys())
        if remotes:
            for remote in remotes:
                entries_remote = []

                stdout_remote = self.git("ls-remote", "--tags", remote)
                porcelain_entries_remote = stdout_remote.split("\n").__iter__()

                for entry in porcelain_entries_remote:
                    if not entry:
                        continue
                    sha = entry[:40]
                    tag = entry[51:]
                    entries_remote.append(TagDetails(sha, tag))

                entries.append(RemoteGroup(remote, entries=entries_remote))

        return entries
