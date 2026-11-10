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

Branches checked out in other worktrees have a `■` marker after their name,
for example `(feature ■, bookmark)`. Only `feature` is checked out elsewhere;
`bookmark` merely points to the same commit. The current checkout keeps its
usual `HEAD` decoration.

A detached checkout in another worktree appears as a standalone marker:
`(main, ■)`, or `(■)` when the commit has no other decorations. Markers are
display-only: copying refs and running graph actions still use the original
branch names, tags, and commit IDs.

The `■` character is reserved for worktree markers in graph decorations.

Left-click a branch name or commit hash to select the whole label. Use
Alt+click and drag for normal text selection; Alt+double-click selects a word
and Alt+triple-click selects a line. Alt-selection also works in the status,
branches, and tags dashboards.

Right-click a local branch name or commit hash to open its action menu.
Right-click elsewhere opens Sublime Text's normal context menu. With saved
multiselections, right-click a selected commit hash for actions on those commits
(such as squash); right-click elsewhere clears the multiselections instead.
Left-click and hold no longer opens an action menu.

**Checkout here** switches the current working directory; a commit hash is
checked out detached. **Checkout in a worktree** creates a new worktree and
opens it in a new window. It attaches to an available branch, but stays
detached when the branch is already checked out here or elsewhere, or when
opening a commit hash. For branches checked out elsewhere, **Checkout here**
is hidden; **Open worktree in a new window** opens their existing checkout
without modifying it.

Branch menus also offer **Push** (**Push...** without an upstream), plus
**Fetch** when an upstream is configured. **Pull** is available for the current
branch and unchecked branches with an upstream. Pulling an unchecked branch
only fast-forwards it; divergent histories are rejected. Pull is not offered
for branches checked out elsewhere; open their worktree to pull there.

To move a local branch, press its name and hold, or drag straight to another
commit without waiting. The source name becomes `|`; the destination previews
its new decoration. Release and choose **Move 'branch name' here** to confirm.
Dismiss the menu to abort; releasing on the original commit also aborts.
Remote-tracking branches and branches checked out in another worktree cannot be
dragged. Moving the branch checked out here uses `checkout -B`, including the
usual offer to merge local changes if needed. Other branches move without
switching checkout and support GitSavvy's ref undo.

Use `.` to go to next commit and use `,` to go to previous commit.

Pressing `m` while your cursor is over a particular line will toggle display of a quick panel containing more info about the selected commit. GitSavvy remembers the selection for the next graph view.

Pressing `Enter` while your cursor is over a particular line will show the options similar to that of `git: log`.

## `git: open current commit in a worktree`

Creates a detached worktree at the current `HEAD` commit and opens it in a new
window, leaving the current checkout unchanged.

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
