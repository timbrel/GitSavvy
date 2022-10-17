import filecmp
import os
import subprocess
import shlex
import sys
import tempfile
from shutil import rmtree, copyfile

import sublime

from GitSavvy.core.git_command import mixin_base
from ...common import util


class MergeMixin(mixin_base):

    def launch_tool_for_file(self, fpath):
        """
        Given the relative path to a tracked file with a merge conflict,
        launch the configured merge tool against the four versions of that
        file (our version, their version, common ancestor, merged version).
        """
        tool = self.get_configured_tool()
        if not tool:
            sublime.error_message("You have not configured a merge tool for Git.")
            return
        merge_cmd_tmpl = self.get_merge_cmd_tmpl(tool)
        if not merge_cmd_tmpl:
            sublime.error_message("You have not configured a merge tool for Git.")
            return

        versioned_content = self.get_versioned_content(fpath)
        if not versioned_content:
            sublime.error_message("Unable to merge selected file.")
            return

        base_content, ours_content, theirs_content = versioned_content

        temp_dir = tempfile.mkdtemp()
        repo_path = self.repo_path
        base_path = os.path.join(temp_dir, "base")
        ours_path = os.path.join(temp_dir, "ours")
        theirs_path = os.path.join(temp_dir, "theirs")
        backup_path = os.path.join(temp_dir, "backup")
        merge_path = os.path.join(repo_path, fpath)

        merge_cmd = merge_cmd_tmpl.replace("$REMOTE", theirs_path)
        merge_cmd = merge_cmd.replace("$BASE", base_path)
        merge_cmd = merge_cmd.replace("$LOCAL", ours_path)
        merge_cmd = merge_cmd.replace("$MERGED", merge_path)
        merge_cmd_args = shlex.split(merge_cmd)

        try:
            with util.file.safe_open(base_path, "wb") as base:
                base.write(base_content)
            with util.file.safe_open(ours_path, "wb") as ours:
                ours.write(ours_content)
            with util.file.safe_open(theirs_path, "wb") as theirs:
                theirs.write(theirs_content)
            copyfile(merge_path, backup_path)

            startupinfo = None
            if sys.platform == "win32":
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW

            util.debug.log_process(
                merge_cmd_args, repo_path, os.environ, startupinfo
            )

            p = subprocess.Popen(
                merge_cmd_args,
                cwd=repo_path,
                env=os.environ,
                startupinfo=startupinfo
            )
            ret_code = p.wait()

            tool = self.get_configured_tool()
            trust_exit = self.git(
                'config', '--bool', 'mergetool.{}.trustExitCode'.format(tool)) == 'true'

            if ret_code == 0:
                if trust_exit:
                    # use return code regardless of changes to file
                    self.resolve_merge(merge_path)
                elif filecmp.cmp(backup_path, merge_path, shallow=False):
                    # or check if the file was modified
                    self.resolve_merge(merge_path)
            else:
                sublime.error_message("Merge tool failed with status %s, reverting" % ret_code)
                copyfile(backup_path, merge_path)
        except Exception as e:
            rmtree(temp_dir)
            raise e

    def resolve_merge(self, path):
        self.git("add", path)
        sublime.set_timeout_async(
            lambda: util.view.refresh_gitsavvy(sublime.active_window().active_view()))

    def get_configured_tool(self):
        return self.git("config", "merge.tool").strip()

    def get_merge_cmd_tmpl(self, tool):
        """
        Query Git for the command to invoke the external merge tool.
        """
        return self.git("config", "mergetool.{}.cmd".format(tool))

    def get_versioned_content(self, fpath):
        """
        Given the path to a tracked file with a merge conflict, return
        the contents of the base version (common ancestor), the local version
        (ours), and the remote version (theirs).
        """
        entries = self.git("ls-files", "-u", "-s", "-z", "--", fpath).split("\x00")
        entries = tuple(entry for entry in entries if entry)

        if not len(entries) == 3:
            return

        # 100644 ffba696331701a1007320c5df88c50f4b0cf0ab9 1   example.js
        # 100644 913b897df13331fec0c959be5a994be48c7dc395 2   example.js
        # 100644 0c11353d13f667542c5b4eeddb7af620ff0055a0 3   example.js
        #        ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
        base_hash, ours_hash, theirs_hash = (entry.split(" ")[1] for entry in entries)

        base_content = self.git("show", base_hash, decode=False)
        ours_content = self.git("show", ours_hash, decode=False)
        theirs_content = self.git("show", theirs_hash, decode=False)

        return base_content, ours_content, theirs_content
