import subprocess
import shutil
from collections import namedtuple

from ..common import log

GitResponse = namedtuple("GitResponse", ["success", "stdout", "stderr"])
FileStatus = namedtuple("FileStatus", ["path", "path_alt", "status", "status_alt"])

git_path = None


class GitBetterError(Exception):
    pass


class BaseCommand():

    @property
    def encoding(self):
        return "UTF-8"

    @property
    def git_binary_path(self):
        global git_path
        if not git_path:
            git_path = shutil.which("git")
        return git_path

    @property
    def repo_path(self):
        return "/Users/daleb/Library/Application Support/Sublime Text 3/Packages/GitBetter"

    def git(self, *args, stdin=None):
        command = (self.git_binary_path, ) + args
        log.info("-- " + " ".join(command))

        try:
            p = subprocess.Popen(command,
                                 stdin=subprocess.PIPE,
                                 stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE,
                                 cwd=self.repo_path)
            stdout, stderr = p.communicate(stdin)
            stdout, stderr = stdout.decode(), stderr.decode()

        except Exception as e:
            raise GitBetterError("Git command failed: {}".format(e))

        log.info(stdout)

        return GitResponse(p.returncode == 0, stdout, stderr)

    def get_status(self):
        cmd = self.git("status", "--porcelain", "-z")
        if not cmd.success:
            return None

        porcelain_entries = cmd.stdout.split("\x00").__iter__()
        entries = []

        for entry in porcelain_entries:
            if not entry:
                continue
            status = entry[0]
            status_alt = entry[1].strip() or None
            path = entry[3:]
            path_alt = porcelain_entries.__next__() if status == "R" else None
            entries.append(FileStatus(path, path_alt, status, status_alt))

        return entries
