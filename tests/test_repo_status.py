from textwrap import dedent

import sublime

from unittesting import DeferrableTestCase
from GitSavvy.tests.mockito import expect, unstub, when
from GitSavvy.tests.parameterized import parameterized as p

from GitSavvy.core.git_command import GitCommand


TestShortBranchStatusTestcases = [
(
    "## optimize-status-interface",
    "optimize-status-interface"
),
(
    "## optimize-status-interface\n",
    "optimize-status-interface"
),
(
    "## optimize-status-interface\n?? foo.txt",
    "optimize-status-interface*"
),
(
    "## optimize-status-interface...fork/branch-name [ahead 1]",
    "optimize-status-interface+1"
),
(
    "## optimize-status-interface...fork/branch-name [ahead 1]\n?? foo.txt",
    "optimize-status-interface*+1"
),
(
    "## dev...origin/dev [behind 7]",
    "dev-7"
),
(
    "## dev...origin/dev [behind 7]\n?? foo.txt",
    "dev*-7"
),
(
    "## optimize-status-interface...fork/branch-name [ahead 1, behind 2]",
    "optimize-status-interface+1-2"
),
(
    "## optimize-status-interface...fork/branch-name [ahead 1, behind 2]\nM foo",
    "optimize-status-interface*+1-2"
),
(
    "## improve-diff-view...fork/improve-diff-view [gone]",
    "improve-diff-view"
),
(
    "## improve-diff-view...fork/improve-diff-view [gone]\n?? foo",
    "improve-diff-view*"
),
(
    "## HEAD (no branch)",
    "DETACHED"
),
(
    "## HEAD (no branch)\n?? foo",
    "DETACHED*"
),
(
    "## No commits yet on master",
    "master"
),
(
    "## No commits yet on master\n?? foo",
    "master*"
),
# Previous versions of git instead emitted this before the initial commit
(
    "## Initial commit on zoom",
    "zoom"
),
(
    "## Initial commit on master\n?? foo",
    "master*"
),
]


class TestShortBranchStatus(DeferrableTestCase):
    def tearDown(self):
        unstub()

    @p.expand(TestShortBranchStatusTestcases)
    def test_format_branch_status_for_statusbar(self, status_lines, expected):
        git = GitCommand()
        when(git).in_rebase().thenReturn(False)
        when(git).in_merge().thenReturn(False)

        when(git).git("status", ...).thenReturn(status_lines)

        actual = git.get_branch_status_short()
        self.assertEqual(actual, expected)

    # TODO: Add tests for `in_rebase` True
    # TODO: Add tests for `in_merge` True
    # TODO: Add tests for ?


TestLongBranchStatusTestcases = [
(
    "## optimize-status-interface",
    "On branch `optimize-status-interface`."
),
(
    "## optimize-status-interface\n",
    "On branch `optimize-status-interface`."
),
(
    "## optimize-status-interface\n?? foo.txt",
    "On branch `optimize-status-interface`."
),
(
    "## optimize-status-interface...fork/branch-name",
    "On branch `optimize-status-interface` tracking `fork/branch-name`."
),
(
    "## optimize-status-interface...fork/branch-name [ahead 1]",
    dedent("""\
    On branch `optimize-status-interface` tracking `fork/branch-name`.
    You're ahead by 1.
    """.rstrip())
),
(
    "## optimize-status-interface...fork/branch-name [ahead 1]\n?? foo.txt",
    dedent("""\
    On branch `optimize-status-interface` tracking `fork/branch-name`.
    You're ahead by 1.
    """.rstrip())
),
(
    "## dev...origin/dev [behind 7]",
    dedent("""\
    On branch `dev` tracking `origin/dev`.
    You're behind by 7.
    """.rstrip())
),
(
    "## dev...origin/dev [behind 7]\n?? foo.txt",
    dedent("""\
    On branch `dev` tracking `origin/dev`.
    You're behind by 7.
    """.rstrip())
),
(
    "## optimize-status-interface...fork/branch-name [ahead 1, behind 2]",
    dedent("""\
    On branch `optimize-status-interface` tracking `fork/branch-name`.
    You're ahead by 1 and behind by 2.
    """.rstrip())
),
(
    "## optimize-status-interface...fork/branch-name [ahead 1, behind 2]\nM foo",
    dedent("""\
    On branch `optimize-status-interface` tracking `fork/branch-name`.
    You're ahead by 1 and behind by 2.
    """.rstrip())
),
(
    "## improve-diff-view...fork/improve-diff-view [gone]",
    dedent("""\
    On branch `improve-diff-view` tracking `fork/improve-diff-view`.
    The remote branch is gone.
    """.rstrip())
),
(
    "## improve-diff-view...fork/improve-diff-view [gone]\n?? foo",
    dedent("""\
    On branch `improve-diff-view` tracking `fork/improve-diff-view`.
    The remote branch is gone.
    """.rstrip())
),
(
    "## HEAD (no branch)",
    "HEAD is in a detached state."
),
(
    "## HEAD (no branch)\n?? foo",
    "HEAD is in a detached state."
),
(
    "## No commits yet on master",
    "Initial commit on `master`."
),
(
    "## No commits yet on master\n?? foo",
    "Initial commit on `master`."
),
# Previous versions of git instead emitted this before the initial commit
(
    "## Initial commit on zoom",
    "Initial commit on `zoom`."
),
(
    "## Initial commit on master\n?? foo",
    "Initial commit on `master`."
),
]


class TestLongBranchStatus(DeferrableTestCase):
    def tearDown(self):
        unstub()

    @p.expand(TestLongBranchStatusTestcases)
    def test_format_branch_status_for_status_dashboard(self, status_lines, expected):
        git = GitCommand()
        when(git).in_rebase().thenReturn(False)
        when(git).in_merge().thenReturn(False)

        when(git).git("status", ...).thenReturn(status_lines)

        actual = git.get_branch_status(delim="\n")
        self.assertEqual(actual, expected)

    # TODO: Add tests for `in_rebase` True
    # TODO: Add tests for `in_merge` True
    # TODO: Add tests for ?


# TestLongBranchStatusTestcases = [
# ("""\
# ## optimize-status-interface...fork/optimize-status-interface [ahead 1]
# ?? tests/test_repo_status.py""".strip().splitlines(), "optimize-status-interface*+1"),

# ("""\
# ## dev...origin/dev [behind 7]
# ?? tests/test_repo_status.py
# """.strip().splitlines(), "dev*-7"),

# ("""\
# ## optimize-status-interface...fork/optimize-status-interface [ahead 1, behind 2]
# ?? tests/test_repo_status.py
# """.strip().splitlines(), "optimize-status-interface*+1-2"),

# ("""\
# ## improve-diff-view...fork/improve-diff-view [gone]
# ?? tests/test_repo_status.py
# """.strip().splitlines(), "improve-diff-view*")

# ]


"""\
## optimize-status-interface...fork/optimize-status-interface [ahead 1]
 M core/interfaces/status.py
?? tests/test_repo_status.py
"""