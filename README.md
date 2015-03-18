# GitSavvy

Sublime Text 3 plugin providing the following features:

- basic Git functionality; `init`, `add`, `commit`, `amend`, `checkout`, `pull`, `push`, etc.
- inline diff viewing, including quick navigation between modified hunks and the ability to (un)stage files by hunk or by line (inspired by SourceTree)
- GitHub integration
    + issue/collaborator referencing when committing
    + opening the current file on GitHub at the selected line
- GitHub-style blame view, showing hunk metadata and ability to view the commit that made the change
- `git diff` view, allowing user to (un)stage hunks across all files
- status, branch, and tag dashboards

**Note:** Sublime Text 2 is not supported.  Also, GitSavvy takes advantage of certain features of ST3 that have bugs in earlier ST3 releases.  For the best experience, use the latest ST3 dev build.


## Documentation

Feature documentation can be found [here](docs/README.md).  It can also be accessed from within Sublime by opening the command palette and typing `GitSavvy: help`.


## Highlights

| Inline-diff | Status dashboard |
|:-----------:|:----------------:|
| ![inline-diff](https://cloud.githubusercontent.com/assets/5016978/6471628/886430f8-c1a1-11e4-99e9-883837dba86f.gif) | ![status-dashboard-medium](https://cloud.githubusercontent.com/assets/5016978/6704171/2f236466-cd02-11e4-9b7d-22cc880b5e9d.png) |
| (Un)stage and revert individual lines and hunks. | Display and overview and offer actions to manipulate your project state. |

| Branch dashboard | Tags dashboard |
|:----------------:|:--------------:|
| ![branch-dashboard-medium](https://cloud.githubusercontent.com/assets/5016978/6704168/2b2e7b84-cd02-11e4-90f4-8dd96b21edeb.png) | ![tags-dashboard-medium](https://cloud.githubusercontent.com/assets/5016978/6704169/2c80beac-cd02-11e4-8940-986ea0f0d6bb.png) |
| View and manipulate local and remote branches. | View and manipulate local and remote tags. |

| Github integration |
|:------------------:|
| ![github-integration](https://cloud.githubusercontent.com/assets/5016978/6704029/8fcaddbe-cd00-11e4-83b6-32276a2c2b65.gif) |
| Reference issues and collaborators in commits.  Open files on GitHub in the browser, with lines pre-selected. |


## Installation

### Simple

1. Install the [Sublime Text Package Control](https://packagecontrol.io/) plugin if you don't have it already.
2. Open the command palette and start typing `Package Control: Install Package`.
3. Enter `GitSavvy`.

### Less simple

If you want more control over what you pull down, or if you'd like to submit changes to GitSavvy, you should pull down the repository directly and restart the editor.

```
# on a Mac
cd "$HOME/Library/Application Support/Sublime Text 3/Packages"
# on Linux
cd $HOME/.config/sublime-text-3/Packages
# on Windows (PowerShell)
cd "$env:appdata\Sublime Text 3\Packages\"

git clone git@github.com:divmain/GitSavvy.git
```
