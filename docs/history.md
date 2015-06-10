# History

## `git: log`

Opens a panel with a chronological list of all ancestors of the current commit.  To find a particular commit, you can start typing either the commit hash (the shortened form is included), or the first line of the commit message itself.  What you type will be fuzzy-string matched against the commit records by Sublime.  Author and age information is included, but not directly searchable with this command.

By default, the list is paginated at 6,000 commits for performance reasons.  To view the next 6,000 commits, start typing `>>> NEXT`.  Whens selected, you will see a list with the next 6,000 commits.

On selection of a commit you can choose from the following options:
- `Show commit`: Display the commit.
- `Compare commit against working directory`: Display the diff between the commit and the working directory.
- `Compare commit against index`: Display the diff between the commit and staged changes in the index.

## `git: log current file`

Performs a similar function to `git: log`, but restricts your search to the history of the currently open file.

## `git: log by author`

Performs a similar function to `git: log`, but restricts your search to a particular committer.  When run, you will be prompted for the name and/or email of the committer you're searching for - the field will be pre-populated with your own name and email.  The full name/email is not required for the search to succeed.

## `git: blame current file`

A GitHub-style blame view is displayed.  Each hunk of the file will be shown on the right, with the associated commit info shown to its left.  This includes the beginning of the commit message, commit hash, author, and age.

Pressing `SUPER-Enter` (`CTRL-Enter` in Windows) while your cursor is inside a hunk will take you to that specific commit.

## `git: graph current branch`

Opens a special view that displays an ASCII-graphic representation of the repo's commit and branch history.

Use `.` to go to next commit and use `,` to go to previous commit.

Pressing `m` while your cursor is over a particular line will display a quick panel with  more info about that commit. If you have the `log_graph_view_toggle_more` setting set to `false` it will not show more content about the commit when browsing log.

Pressing `Enter` while your cursor is over a particular line will display the commit reflected on that line.  Pressing `SUPER-Enter` (`CTRL-Enter` in Windows) will check out the commit.  Note that a successful commit will not be visually reflected in the graph view.

## `git: graph all branches`

Same as `git: graph current branch`. The only difference is that we add '--all' flag to the command. This will show all commits and stashes on all branches.
