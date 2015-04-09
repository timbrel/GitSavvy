import os
import tempfile
import subprocess
import shlex
from shutil import rmtree


import sublime


class MergeMixin():

    def launch_tool_for_file(self, fpath):
        """
        Given the relative path to a tracked file with a merge conflict,
        launch the configured merge tool against the four versions of that
        file (our version, their version, common ancestor, merged version).
        """
        merge_cmd_tmpl = self.get_merge_cmd_tmpl()
        base_content, ours_content, theirs_content = self.get_versioned_content(fpath)

        temp_dir = tempfile.mkdtemp()
        base_path = os.path.join(temp_dir, "base")
        ours_path = os.path.join(temp_dir, "ours")
        theirs_path = os.path.join(temp_dir, "theirs")
        merge_path = os.path.join(self.repo_path, fpath)

        merge_cmd = merge_cmd_tmpl.replace("$REMOTE", theirs_path)
        merge_cmd = merge_cmd.replace("$BASE", base_path)
        merge_cmd = merge_cmd.replace("$LOCAL", ours_path)
        merge_cmd = merge_cmd.replace("$MERGED", merge_path)
        merge_cmd_args = shlex.split(merge_cmd)

        try:
            with open(base_path, "w") as base:
                base.write(base_content)
            with open(ours_path, "w") as ours:
                ours.write(ours_content)
            with open(theirs_path, "w") as theirs:
                theirs.write(theirs_content)

            startupinfo = None
            if os.name == "nt":
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW

            p = subprocess.Popen(
                merge_cmd_args,
                cwd=self.repo_path,
                env=os.environ,
                startupinfo=startupinfo
                )
            p.wait()

        except Exception as e:
            rmtree(temp_dir)
            raise e

    def get_merge_cmd_tmpl(self):
        """
        Query Git for the command to invoke the external merge tool.
        """
        tool = self.git("config", "merge.tool").strip()
        if not tool:
            sublime.error_message("You have not configured a merge tool for Git.")
            return
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
            sublime.error_message("Unable to merge selected file.")
            return

        # 100644 ffba696331701a1007320c5df88c50f4b0cf0ab9 1   example.js
        # 100644 913b897df13331fec0c959be5a994be48c7dc395 2   example.js
        # 100644 0c11353d13f667542c5b4eeddb7af620ff0055a0 3   example.js
        #        ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
        base_hash, ours_hash, theirs_hash = (entry.split(" ")[1] for entry in entries)

        base_content = self.git("show", base_hash)
        ours_content = self.git("show", ours_hash)
        theirs_content = self.git("show", theirs_hash)

        return base_content, ours_content, theirs_content
