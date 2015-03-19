# History

## `git: log`

Opens a panel with a chronological list of all ancestors of the current commit.  To find a particular commit, you can start typing either the commit hash (the shortened form is included), or the first line of the commit message itself.  What you type will be fuzzy-string matched against the commit records by Sublime.  Author and age information is included, but not directly searchable with this command.

By default, the list is paginated at 6,000 commits for performance reasons.  To view the next 6,000 commits, start typing `>>> NEXT`.  Whens selected, you will see a list with the next 6,000 commits.

## `git: log current file`

Performs a similar function to `git: log`, but restricts your search to the history of the currently open file.

## `git: log by author`

Performs a similar function to `git: log`, but restricts your search to a particular committer.  When run, you will be prompted for the name and/or email of the committer you're searching for - the field will be pre-populated with your own name and email.  The full name/email is not required for the search to succeed.

## `git: blame current file`

A GitHub-style blame view is displayed.  Each hunk of the file will be shown on the right, with the associated commit info shown to its left.  This includes the beginning of the commit message, commit hash, author, and age.

Pressing `SUPER-Enter` (`CTRL-Enter` in Windows) while your cursor is inside a hunk will take you to that specific commit.

## `git: graph`

Opens a special view that displays an ASCII-graphic representation of the repo's commit and branch history.

Pressing `Enter` while your cursor is over a particular line will display the commit reflected on that line.  Pressing `SUPER-Enter` (`CTRL-Enter` in Windows) will check out the commit.  Not that a successful commit will not be visually reflected in the graph view.
