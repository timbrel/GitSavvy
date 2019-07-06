from collections import namedtuple


Branch = namedtuple("Branch", (
    "name",
    "remote",
    "name_with_remote",
    "commit_hash",
    "commit_msg",
    "tracking",
    "tracking_status",
    "active",
    "description"
))


class BranchesMixin():

    def get_branches(self, sort_by_recent=False):
        """
        Return a list of all local and remote branches.
        """
        stdout = self.git(
            "for-each-ref",
            "--format=%(HEAD)%00%(refname)%00%(upstream)%00%(upstream:track)%00%(objectname)%00%(contents:subject)",
            "--sort=-committerdate" if sort_by_recent else None,
            "refs/heads",
            "refs/remotes")
        return (branch
                for branch in (self._parse_branch_line(self, line) for line in stdout.split("\n"))
                if branch and branch.name != "HEAD")

    @staticmethod
    def _parse_branch_line(self, line):
        line = line.strip()
        if not line:
            return None
        head, ref, tracking_branch, tracking_status, commit_hash, commit_msg = line.split("\x00")

        active = head == "*"
        is_remote = ref.startswith("refs/remotes/")

        branch_name = ref[13:] if is_remote else ref[11:]
        remote = ref[13:].split("/", 1)[0] if is_remote else None
        tracking_branch = tracking_branch[13:]
        if tracking_status:
            # remove brackets
            tracking_status = tracking_status[1:len(tracking_status) - 1]

        enable_branch_descriptions = self.savvy_settings.get("enable_branch_descriptions")

        hide_description = is_remote or not enable_branch_descriptions
        description = "" if hide_description else self.git(
            "config",
            "branch.{}.description".format(branch_name),
            throw_on_stderr=False
        ).strip("\n")

        return Branch(
            "/".join(branch_name.split("/")[1:]) if is_remote else branch_name,
            remote,
            branch_name,
            commit_hash,
            commit_msg,
            tracking_branch,
            tracking_status,
            active,
            description
        )

    def merge(self, branch_names):
        """
        Merge `branch_names` into active branch.
        """

        self.git("merge", *branch_names)

    def get_local_branch(self, branch_name):
        """
        Get a local Branch tuple from branch name.
        """
        for branch in self.get_branches():
            if not branch.remote and branch.name == branch_name:
                return branch

    def branches_containing_commit(self, commit_hash, local_only=True, remote_only=False):
        """
        Return a list of branches which contain a particular commit.
        """
        branches = self.git(
            "branch",
            "-a" if not local_only and not remote_only else None,
            "-r" if remote_only else None,
            "--contains",
            commit_hash
        ).strip().split("\n")
        return [branch.strip() for branch in branches]

    def validate_branch_name(self, branch):
        ref = "refs/heads/{}".format(branch)
        return self.git("check-ref-format", "--branch", ref, throw_on_stderr=False).strip()
