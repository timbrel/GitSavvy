import re
from collections import namedtuple

Branch = namedtuple("Branch", (
    "name",
    "remote",
    "name_with_remote",
    "commit_hash",
    "commit_msg",
    "tracking",
    "tracking_status",
    "active"
    ))


class BranchesMixin():

    def get_branches(self):
        """
        Return a list of all local and remote branches.
        """
        stdout = self.git("branch", "-a", "-vv", "--no-abbrev")
        return (branch
                for branch in (self._parse_branch_line(line) for line in stdout.split("\n"))
                if branch)

    @staticmethod
    def _parse_branch_line(line):
        line = line.strip()
        if not line:
            return None

        pattern = r"(\* )?(remotes/)?([a-zA-Z0-9\-\_\/\\]+) +([0-9a-f]{40}) (\[([a-zA-Z0-9\-\_\/\\]+)(: ([^\]]+))?\] )?(.*)"
        r"((: ))?"

        match = re.match(pattern, line)
        if not match:
            return None

        (is_active,
         is_remote,
         branch_name,
         commit_hash,
         _,
         tracking_branch,
         _,
         tracking_status,
         commit_msg
         ) = match.groups()

        active = bool(is_active)
        remote = branch_name.split("/")[0] if is_remote else None

        return Branch(
            "".join(branch_name.split("/")[1:]) if is_remote else branch_name,
            remote,
            branch_name,
            commit_hash,
            commit_msg,
            tracking_branch,
            tracking_status,
            active
            )

    def merge(self, branch_name):
        """
        Merge `branch_name` into active branch.
        """
        self.git("merge", branch_name)
