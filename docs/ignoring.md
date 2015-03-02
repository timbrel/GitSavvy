# Ignoring changes

**Note:** None of the following commands are destructive.  However, keep in mind that their use may result in unexpected results if specific files are ignored and forgotten.


## `git: ignore current file`

This command adds an entry for the currently open file to the Git repository's root `.gitignore` file.  The command is accessible both through the command palette and through the status dashboard.


## `git: ignore pattern`

This command adds an entry to the Git repository's root `.gitignore` file.  When it is run, you will be prompted for the pattern to add (based on the currently open file).


## `git: assume file unchanged`

This command instructs Git to temporarily treat the currently open file as if it is unchanged.  This may be useful if editing configuration files, etc, without adding an entry to `.gitignore` (which would also show up in the Git status).

**Note:**  These entries are stored in the local `.git` directory and are not tracked in any transparent way.

## `git: restore file assumed unchanged`

This command displays a list of any files for which you've run `git: assume file unchanged`.  When selected, the file will no longer be treated as unchanged by Git.
