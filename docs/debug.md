# Debug

If you're doing development on GitSavvy, the following commands may be useful.


## `GitSavvy: Show Command Log`

Open this command from the Command Palette, or select **Output: GitSavvy Command
Log** from Sublime's panel selector, to see GitSavvy's recent and running Git
commands. The log is shared across all Sublime windows and grouped by working
directory, with the newest commands first in each group. It keeps all running
commands plus the 15 most recently finished commands across all repositories.

```text
             -- /path/to/repo --
> (W) [ 2.3s] $ git fetch origin
  (W) [ 47ms] $ git stash list
```

`>` marks a command running for at least one second; the letter in parentheses
is the first character of its thread's name. Commands taking less than 100ms appear only when finished.
Slower commands initially show `[     ]`, then an updating elapsed time after
one second. Opening the panel shows all currently running commands immediately.

After one second, an **Abort** link appears beside a running command. Click it
to stop the command. Aborted commands show `[xxxxx]`. Aborting does not undo changes Git has already made; a rebase or merge
may still need attention afterward.

Git commands have no default timeout. A hidden command on GitSavvy's Worker
that takes more than five seconds shows a rolling spinner with
`◐ · Git is running (open Git Command Log for details)` in the active view's
status bar. This single indicator follows
you across views and windows, and disappears when no qualifying slow commands
remain. Open the Command Log to inspect or abort them.

Calls with an explicit timeout (including `None`) do not trigger the indicator.
Neither do commands using the output panel or calls on dedicated threads, such
as commands waiting for an editor. Explicit numeric timeouts still apply.

The panel becomes available in a window's panel selector after its first Git
command, but does not open automatically. History is tracked while it is hidden,
but animation runs only while visible. It shows commands and timings, not their
stdout or stderr; use the existing output panel for progress or the debug logging
commands below to record command inputs and outputs.


## `GitSavvy: reload modules (debug)`

This command will reload all GitSavvy-related Python modules and initiate a plugin reset for Sublime Text 3.  Note that the editor's interface may become unresponsive for a second or two while the plugins are reloaded.  However, this workflow is often preferable to closing and re-opening Sublime when testing changes to GitSavvy.

This command will only have an effect if `dev_mode` is set to `true` in `GitSavvy` settings.


## `GitSavvy: enable logging`

This will start tracking the inputs and outputs of all Git commands that are running under the hood.  Aggregating this data can be useful for those wanting to learn how GitSavvy works, as well as for debugging purposes.

**Note:** If you've logged a sequence of Git commands before, running this again will overwrite what was previously recorded.


## `GitSavvy: disable logging`

This stops all recording Git command inputs and outputs, but does not destroy the record.

## `GitSavvy: view recorded log`

Once you have started and stopped logging, this command will display the log in JSON format in a new scratch view.

# Providing a Debug Log

Ocasionally when creating a new issue in GitSavvy, you will be requested to provide a debug log. The above commands make it easy to do, by following these steps:

   1. Open sublime, and get to the state just prior to running the failing command.
   2. Open command palette, and run the command "GitSavvy: enable logging".
   3. Perform the failing command.
   4. Run the command "GitSavvy: disable logging"
   5. Run the command "GitSavvy: view recorded log",
      save the file locally and attach it to your issue.
      
      _Note: Take care to remove any sensitive information that may be recorded in GitSavvy._

# Other tools / PATH issues / Different behavior from terminal and inside Sublime

Please check the tool is in your PATH inside sublime.
First find the path to where the tool binaries are, open a system terminal/shell and paste:

    which <tool_name>;

Check if that path is the `$PATH` inside sublime. To do that open the sublime console and paste in:

    from os import environ; environ['PATH']

If the tool path is missing from your sublime environment's `PATH` variable, you need to look into correcting the env variables available to sublime on launch. The unofficial docs mention how to set it globally for windows and mac in [the "Troubleshooting Build Systems" page][2]. Alternatively, you may want to look at the packages ["Environment Settings"][3] and ["Fix Mac Path"][4].

If, however, the path is in your sublime environment, please open an issue and include these values in an issue.

[1]: https://github.com/timbrel/GitSavvy/issues/684#issuecomment-323579850
[2]: http://docs.sublimetext.info/en/latest/reference/build_systems/troubleshooting.html
[3]: https://packagecontrol.io/packages/Environment%20Settings
[4]: https://packagecontrol.io/packages/Fix%20Mac%20Path
