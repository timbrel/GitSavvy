# Contribute

If you're interested in adding features, reporting/fixing bugs, or just discussing the future of GitSavvy, this is the place to start.  Please feel free to reach out if you have questions or comments by opening an [issue](https://github.com/timbrel/GitSavvy/issues).


## Adding new features

If there's a feature you'd like to see that isn't already in-progress or documented, there are two ways you can go about it:

- open an issue for discussion; or
- fork and submit a PR.

Either way is fine, so long as you remember your PR may not be accepted as-is or at all.  If a feature request is reasonable, though, it'll most likely be included one way or another.

Some other things to remember:

- Please respect the project layout and hierarchy, to keep things organized.
- Include docstrings for all classes and functions, unless functionality is obvious at a glance.
- Include descriptions for issues and PRs.


## Code hygiene

Prefer lines around 80 columns and generally stay within 100. The 120-column
lint limit is a pragmatic ceiling, not a target: it leaves room for long
strings, URLs, and type comments without adding suppression comments.
Ruff's 90-column wrapping setting is separate from that lint ceiling.

Run both pinned lint checks locally, just as in CI:

```sh
uvx ruff@0.14.13 check .
uvx --python 3.8 flake8@5.0.4 --select F401 .
```

Run tests headlessly with our Docker runner: `ut-run-tests .` (or
`ut-run-tests . --file tests/test_app_state.py` for one file).
See [Package Testing](docs/testing.md#run-the-tests-headlessly-docker) for setup.


# Bugs

If you encounter a bug, please check for an open issue that already captures the problem you've run into.  If it doesn't exist yet, create it!

Please include the following information when filing a bug:

- Sublime Text version number
- Git version number
- OS type and version
- Console error output
- A description of the problem.
- Steps to reproduce, if possible
- If the bug is caused by a failing command, [include a debug log](docs/debug.md#providing-a-debug-log).


# Documentation

If you make changes, please remember to update the user documentation to reflect the new behavior.


## Website preview

The website lives in `docs/index.md`, independently of the root README.
The in-editor documentation starts at `docs/help.md`. Website styling and
layout live in `docs/assets/` and `docs/_layouts/`.

With Docker Desktop running, start the preview from the repository root:

```sh
docker compose -f compose.docs.yml up --build
```

Open <http://localhost:4000/GitSavvy/>. Edit the Markdown or CSS in Sublime
and save; Jekyll rebuilds the site and reloads the browser automatically.
Polling makes file watching work with Windows bind mounts. The first build
downloads Ruby and the pinned GitHub Pages dependencies; later starts reuse
the image. No local Ruby installation is needed.

This renders the actual Jekyll layout, including caption classes such as
`{: .caption }`, which generic Markdown previews may not support. There is
no browser-based editor: edit the source files in your usual editor.

Press Ctrl+C to stop. To remove the stopped container:

```sh
docker compose -f compose.docs.yml down
```

Only localhost can access the preview. Source files are mounted read-only;
generated HTML stays inside the container. Publishing is still handled by
GitHub Pages from the branch's `/docs` directory, not by this preview command.


## Package Testing

Check the implementation details of the [tests](docs/testing.md).

# Publishing

Hotfixes should be submitted to `master` branch. As part of the PR process,
you will be asked to provide the information necessary to make that happen.

Pull requests on new features should be submitted to `dev` branch and will be
regularly merged into `master` after evaluations over an extended period of
time.

# Issue Triage [![Open Source Helpers](https://www.codetriage.com/divmain/gitsavvy/badges/users.svg)](https://www.codetriage.com/divmain/gitsavvy)

You can triage issues which may include reproducing bug reports or asking for vital information, such as version numbers or reproduction instructions. If you would like to start triaging issues, one easy way to get started is to [subscribe to gitsavvy on CodeTriage](https://www.codetriage.com/divmain/gitsavvy).
