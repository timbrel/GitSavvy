# History

## `git: log`

Opens a panel with a chronological list of all ancestors of the current commit.  To find a particular commit, you can start typing either the commit hash (the shortened form is included), or the first line of the commit message itself.  What you type will be fuzzy-string matched against the commit records by Sublime.  Author and age information is included, but not directly searchable with this command.

By default, the list is paginated at 6,000 commits for performance reasons.  To view the next 6,000 commits, start typing `>>> NEXT`.  Whens selected, you will see a list with the next 6,000 commits.

On selection of a commit you can choose from the following options:
- `Show commit`: Display the commit.
- `Checkout commit`: Checkout the commit.
- `Revert commit`: Revert the commit.
- `Compare Commit against ...`: Run the `git: compare against ...` command.
- `Copy the full SHA`: Copy the full SHA hash of the commit to clipboard.
- `Diff commit`: Display the diff between the commit and the working directory.
- `Diff commit (cached)`: Display the diff between the commit and staged changes in the index.

## `git: log current file`

Performs a similar function to `git: log`, but restricts your search to the history of the currently open file.  Has additional option upon selection of a commit:
- `Show file at commit`: Display the file at the time of the commit.


## `git: graph`

Opens a special view that displays an ASCII-graphic representation of the repo's commit and branch history.

Use `.` to go to next commit and use `,` to go to previous commit.

Pressing `m` while your cursor is over a particular line will toggle display of a quick panel containing more info about the selected commit.  If you've changed your is `graph_show_more_commit_info` settting to `false`, the quick panel will not display automatically.

Pressing `Enter` while your cursor is over a particular line will show the options similar to that of `git: log`.

## `git: graph current file`

Same as `git: graph current branch` but only for current file.

## `git: compare against ...`

Perform `git diff` between a selected commit and the current commit.

## `git: compare current file against ...`

Perform `git diff` between a selected commit and the current commit but only restrict to the current file.


## `git: blame current file`

A GitHub-style blame view is displayed.  Each hunk of the file will be shown on the right, with the associated commit info shown to its left.  This includes the beginning of the commit message, commit hash, author, and age.

### Blame options
When run, you will be prompted for how you want the blame view to search for changes:

#### Default
Use default `git blame` behaviour.

#### Ignore whitespace
Ignore whitespace only changes when finding the last commit that changed the line (`git blame -w`).

#### Detect moved or copied lines within same file
Ignore whitespace, and detect when lines have been moved or copied within the file, attributing lines to the original commit rather than the commit that moved or copied them (`git blame -M`).

#### Detect moved or copied lines within same commit
Ignore whitespace, and detect when lines have been moved or copied from any file modified in the same commit, attributing lines to the original commit rather than the commit that moved or copied them (`git blame -C`).

#### Detect moved or copied lines across all commits
Ignore whitespace, and detect when lines have been moved or copied from any file across the full commit history of the repository, attributing lines to the original commit rather than the commit that moved or copied them (`git blame -CCC`).

## Action in blame view

When you have open the blame view you have a new set of commands

Use `.` to go to next chunk and `,` to go to previous. The tab title is the file name and commit hash.
Use `Enter` to select a action. If you hit `?` you will see a pop-up with all key bindings and actions.

Actions
- `Open Commit`:  Open the commit of the chunk in which the cursor is located
- `Blame a commit before <comit>`:  Find the chunk you are in and jump to the commit before that. Handy when you know exactly what you are looking for. It can be hard to keep track of where you were in the file since the line number probably changed.
- `Blame older commit`:  Same as above but only jump one commit at a time. 
- `Blame newer commit`:  Same as above but jumps in the opposite direction.
- `Pick a new commit to blame`:  Pick a commit to checkout (Time travel)
- `Show file at current commit`:  Show the file with syntax highlighting at current blame commit
- `Show file at <commit>`:  Same as above but takes the commit from the chunk in which the cursor is located

## `git: reflog`

Like `git: log`, this command will show a panel of commit entries, but uses `git reflog` rather than `git log` as the source of available commits.

## `git: revert`, undo a commit

Like `git: log`, this command will show a panel of commit entries, pick one to revert it. Revert is git's way of undoing. Revert will create a new commit.
