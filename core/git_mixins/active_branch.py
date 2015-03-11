import re


class ActiveBranchMixin():

    def get_current_branch_name(self):
        """
        Return the name of the last checkout-out branch.
        """
        stdout = self.git("branch")
        try:
            correct_line = next(line for line in stdout.split("\n") if line.startswith("*"))
            return correct_line[2:]
        except StopIteration:
            return None

    def get_branch_status(self):
        """
        Return a string that gives:

          1) the name of the active branch
          2) the status of the active local branch
             compared to its remote counterpart.

        If no remote or tracking branch is defined, do not include remote-data.
        If HEAD is detached, provide that status instead.
        """
        stdout = self.git("status", "-b", "--porcelain").strip()

        if stdout == "## HEAD (no branch)":
            return "HEAD is in a detached state."

        first_line, *_ = stdout.split("\n", 1)
        if first_line.startswith("## Initial commit on "):
            return "Initial commit on `{}`.".format(first_line[21:])

        short_status_pattern = r"## ([A-Za-z0-9\-_\/]+)(\.\.\.([A-Za-z0-9\-_\/]+)( \[((ahead (\d+))(, )?)?(behind (\d+))?\])?)?"
        status_match = re.match(short_status_pattern, first_line)

        if not status_match:
            branch_name = first_line.split("\n", 2)[1]
            return "On branch `{}`.".format(branch_name)

        branch, _, remote, _, _, _, ahead, _, _, behind = status_match.groups()

        output = "On branch `{}`".format(branch)

        if remote:
            output += " tracking `{}`".format(remote)

        if ahead and behind:
            output += ". You're ahead by {} and behind by {}".format(ahead, behind)
        elif ahead:
            output += ". You're ahead by {}".format(ahead)
        elif behind:
            output += ". You're behind by {}".format(behind)

        output += "."

        return output

    def get_commit_hash_for_head(self):
        """
        Get the SHA1 commit hash for the commit at HEAD.
        """
        return self.git("rev-parse", "HEAD").strip()

    def get_latest_commit_msg_for_head(self):
        """
        Get last commit msg for the commit at HEAD.
        """
        return self.git("log", "-n 1", "--pretty=format:%h %s", "--abbrev-commit").strip()
