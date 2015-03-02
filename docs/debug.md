# Debug

If you're doing development on GitSavvy, the following commands may be useful.

## `GitSavvy: reload modules (debug)`

This command will reload all GitSavvy-related Python modules and initiate a plugin reset for Sublime Text 3.  Note that the editor's interface may become unresponsive for a second or two while the plugins are reloaded.  However, this workflow is often preferable to closing and re-opening Sublime when testing changes to GitSavvy.

This command will only have an effect if `dev_mode` is set to `true` in `Packages/User/GitSavvy.sublime-settings`.