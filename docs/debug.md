# Debug

If you're doing development on GitSavvy, the following commands may be useful.


## `GitSavvy: reload modules (debug)`

This command will reload all GitSavvy-related Python modules and initiate a plugin reset for Sublime Text 3.  Note that the editor's interface may become unresponsive for a second or two while the plugins are reloaded.  However, this workflow is often preferable to closing and re-opening Sublime when testing changes to GitSavvy.

This command will only have an effect if `dev_mode` is set to `true` in `Packages/User/GitSavvy.sublime-settings`.


## `GitSavvy: start logging`

This will start tracking the inputs and outputs of all Git commands that are running under the hood.  Aggregating this data can be useful for those wanting to learn how GitSavvy works, as well as for debugging purposes.

**Note:** If you've logged a sequence of Git commands before, running this again will overwrite what was previously recorded.


## `GitSavvy: stop logging`

This stops all recording Git command inputs and outputs, but does not destroy the record.

## `GitSavvy: view recorded log`

Once you have started and stopped logging, this command will display the log in JSON format in a new scratch view.
