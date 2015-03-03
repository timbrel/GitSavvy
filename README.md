# GitSavvy

Sublime Text 3 plugin providing the following features:

- basic Git functionality; `init`, `add`, `commit`, `amend`, `checkout`, `pull`, `push`, etc.
- inline diff viewing, including quick navigation between modified hunks and the ability to (un)stage files by hunk or by line (inspired by SourceTree)
- GitHub integration
    + issue/collaborator referencing when committing
    + opening the current file on GitHub at the selected line
- GitHub-style blame view, showing hunk metadata and ability to view the commit that made the change
- `git diff` view, allowing user to (un)stage hunks across all files
- a status dashboard, exposing much of the available functionality

**Note:** Sublime Text 2 is not supported.  Also, GitSavvy takes advantage of certain features of ST3 that have bugs in earlier ST3 releases.  For the best experience, use the latest ST3 dev build.


## Documentation

Feature documentation can be found [here](docs/README.md).  It can also be accessed from within Sublime by opening the command palette and typing `GitSavvy: help`.

## Highlights

### Inline-diff

Stage and revert individual lines or hunks.

![inline-diff](https://cloud.githubusercontent.com/assets/5016978/6471628/886430f8-c1a1-11e4-99e9-883837dba86f.gif)

### Status dashboard

![status dashboard](https://cloud.githubusercontent.com/assets/5016978/6471645/b115ff18-c1a1-11e4-9d2e-d3c1ceb64d51.png)

### GitHub integration

![GitHub integration](https://cloud.githubusercontent.com/assets/5016978/6471672/e36e8c00-c1a1-11e4-91a1-dd5481d57c36.png)

## Installation

GitSavvy is still beta software, and not yet available via the Sublime [Package Manager](https://packagecontrol.io/).

### Simple

If you have Package Management installed in Sublime:

1. Open your command palette and start typing `Package Control: Add Repository`.
2. At the prompt, enter the following URL: `https://github.com/divmain/GitSavvy`.
3. Open the command palette again and start typing `Package Control: Install Package`.
4. Type `GitSavvy`.

This should keep your version auto-updated with any fixes and changes that are released.

### Less simple

If you want more control over what you pull down, or if you'd like to submit changes, you should pull down the repository directly and restart the editor.

```
# on a Mac
cd "/Users/$(whoami)/Library/Application Support/Sublime Text 3/Packages"
# on Windows (PowerShell)
cd "$env:appdata\Sublime Text 3\Packages\"

git clone git@github.com:divmain/GitSavvy.git
```
