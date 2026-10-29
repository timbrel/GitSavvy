from textwrap import dedent

from unittesting import DeferrableTestCase
from GitSavvy.tests.mockito import unstub, when
from GitSavvy.tests.parameterized import parameterized as p

from GitSavvy.core import git_mixins
from GitSavvy.core.git_command import GitCommand
from GitSavvy.core.git_mixins import active_branch
from GitSavvy.github import ci_status


TestShortBranchStatusTestcases = [
    (
        "# branch.oid aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\x00"
        "# branch.head optimize-status-interface",
        "optimize-status-interface"
    ),
    (
        "# branch.oid aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\x00"
        "# branch.head optimize-status-interface\x00",
        "optimize-status-interface"
    ),
    (
        "# branch.oid aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\x00"
        "# branch.head optimize-status-interface\x00"
        "? foo.txt",
        "optimize-status-interface*"
    ),
    (
        "# branch.oid aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\x00"
        "# branch.head optimize-status-interface\x00"
        "# branch.upstream fork/branch-name\x00"
        "# branch.ab +1 -0",
        "optimize-status-interface+1"
    ),
    (
        "# branch.oid aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\x00"
        "# branch.head optimize-status-interface\x00"
        "# branch.upstream fork/branch-name\x00"
        "# branch.ab +1 -0\x00"
        "? foo.txt",
        "optimize-status-interface*+1"
    ),
    (
        "# branch.oid aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\x00"
        "# branch.head dev\x00"
        "# branch.upstream origin/dev\x00"
        "# branch.ab +0 -7",
        "dev-7"
    ),
    (
        "# branch.oid aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\x00"
        "# branch.head dev\x00"
        "# branch.upstream origin/dev\x00"
        "# branch.ab +0 -7\x00"
        "? foo.txt",
        "dev*-7"
    ),
    (
        "# branch.oid aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\x00"
        "# branch.head optimize-status-interface\x00"
        "# branch.upstream fork/branch-name\x00"
        "# branch.ab +1 -2",
        "optimize-status-interface+1-2"
    ),
    (
        "# branch.oid aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\x00"
        "# branch.head optimize-status-interface\x00"
        "# branch.upstream fork/branch-name\x00"
        "# branch.ab +1 -2\x00"
        "1 M. N... 100644 100644 100644 "
        "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa foo",
        "optimize-status-interface*+1-2"
    ),
    (
        "# branch.oid aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\x00"
        "# branch.head improve-diff-view\x00"
        "# branch.upstream fork/improve-diff-view",
        "improve-diff-view"
    ),
    (
        "# branch.oid aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\x00"
        "# branch.head improve-diff-view\x00"
        "# branch.upstream fork/improve-diff-view\x00"
        "? foo",
        "improve-diff-view*"
    ),
    (
        "# branch.oid aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\x00"
        "# branch.head (detached)",
        "DETACHED"
    ),
    (
        "# branch.oid aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\x00"
        "# branch.head (detached)\x00"
        "? foo",
        "DETACHED*"
    ),
    (
        "# branch.oid (initial)\x00"
        "# branch.head master",
        "master"
    ),
    (
        "# branch.oid (initial)\x00"
        "# branch.head master\x00"
        "? foo",
        "master*"
    ),
    (
        "# branch.oid (initial)\x00"
        "# branch.head master\x00"
        "# branch.upstream origin/master",
        "master"
    ),
    (
        "# branch.oid (initial)\x00"
        "# branch.head master\x00"
        "# branch.upstream origin/master\x00"
        "? .travis.yml",
        "master*"
    ),
    # Other unborn branch names
    (
        "# branch.oid (initial)\x00"
        "# branch.head zoom",
        "zoom"
    ),
    (
        "# branch.oid (initial)\x00"
        "# branch.head master\x00"
        "? foo",
        "master*"
    ),
]


class TestShortBranchStatus(DeferrableTestCase):
    def setUp(self):
        when(ci_status).on_head_observed(...).thenReturn(None)

    def tearDown(self):
        unstub()

    @p.expand(TestShortBranchStatusTestcases)
    def test_format_branch_status_for_statusbar(self, status_lines, expected):
        git = GitCommand()
        when(git).get_repo_path().thenReturn("somewhere")
        when(git).in_rebase().thenReturn(False)
        when(git).in_merge().thenReturn(False)
        when(git).in_cherry_pick().thenReturn(False)
        when(git).in_revert().thenReturn(False)

        when(git).git("status", ...).thenReturn(status_lines)

        git.get_working_dir_status()
        actual = git.current_state().get("short_status")
        self.assertEqual(actual, expected)

    # TODO: Add tests for `in_rebase` True
    # TODO: Add tests for `in_merge` True
    # TODO: Add tests for ?


TestLongBranchStatusTestcases = [
    (
        "# branch.oid aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\x00"
        "# branch.head optimize-status-interface",
        "On branch `optimize-status-interface`."
    ),
    (
        "# branch.oid aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\x00"
        "# branch.head optimize-status-interface\x00",
        "On branch `optimize-status-interface`."
    ),
    (
        "# branch.oid aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\x00"
        "# branch.head optimize-status-interface\x00"
        "? foo.txt",
        "On branch `optimize-status-interface`."
    ),
    (
        "# branch.oid aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\x00"
        "# branch.head optimize-status-interface\x00"
        "# branch.upstream fork/branch-name\x00"
        "# branch.ab +0 -0",
        "On branch `optimize-status-interface` tracking `fork/branch-name`."
    ),
    (
        "# branch.oid aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\x00"
        "# branch.head optimize-status-interface\x00"
        "# branch.upstream fork/branch-name\x00"
        "# branch.ab +1 -0",
        dedent("""\
        On branch `optimize-status-interface` tracking `fork/branch-name`.
        You're ahead by 1.
        """.rstrip())
    ),
    (
        "# branch.oid aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\x00"
        "# branch.head optimize-status-interface\x00"
        "# branch.upstream fork/branch-name\x00"
        "# branch.ab +1 -0\x00"
        "? foo.txt",
        dedent("""\
        On branch `optimize-status-interface` tracking `fork/branch-name`.
        You're ahead by 1.
        """.rstrip())
    ),
    (
        "# branch.oid aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\x00"
        "# branch.head dev\x00"
        "# branch.upstream origin/dev\x00"
        "# branch.ab +0 -7",
        dedent("""\
        On branch `dev` tracking `origin/dev`.
        You're behind by 7.
        """.rstrip())
    ),
    (
        "# branch.oid aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\x00"
        "# branch.head dev\x00"
        "# branch.upstream origin/dev\x00"
        "# branch.ab +0 -7\x00"
        "? foo.txt",
        dedent("""\
        On branch `dev` tracking `origin/dev`.
        You're behind by 7.
        """.rstrip())
    ),
    (
        "# branch.oid aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\x00"
        "# branch.head optimize-status-interface\x00"
        "# branch.upstream fork/branch-name\x00"
        "# branch.ab +1 -2",
        dedent("""\
        On branch `optimize-status-interface` tracking `fork/branch-name`.
        You're ahead by 1 and behind by 2.
        """.rstrip())
    ),
    (
        "# branch.oid aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\x00"
        "# branch.head optimize-status-interface\x00"
        "# branch.upstream fork/branch-name\x00"
        "# branch.ab +1 -2\x00"
        "1 M. N... 100644 100644 100644 "
        "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa foo",
        dedent("""\
        On branch `optimize-status-interface` tracking `fork/branch-name`.
        You're ahead by 1 and behind by 2.
        """.rstrip())
    ),
    (
        "# branch.oid aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\x00"
        "# branch.head improve-diff-view\x00"
        "# branch.upstream fork/improve-diff-view",
        dedent("""\
        On branch `improve-diff-view` tracking `fork/improve-diff-view`.
        The remote branch is gone.
        """.rstrip())
    ),
    (
        "# branch.oid aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\x00"
        "# branch.head improve-diff-view\x00"
        "# branch.upstream fork/improve-diff-view\x00"
        "? foo",
        dedent("""\
        On branch `improve-diff-view` tracking `fork/improve-diff-view`.
        The remote branch is gone.
        """.rstrip())
    ),
    (
        "# branch.oid aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\x00"
        "# branch.head (detached)",
        "HEAD is in a detached state."
    ),
    (
        "# branch.oid aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\x00"
        "# branch.head (detached)\x00"
        "? foo",
        "HEAD is in a detached state."
    ),
    (
        "# branch.oid (initial)\x00"
        "# branch.head master",
        "On branch `master`."
    ),
    (
        "# branch.oid (initial)\x00"
        "# branch.head master\x00"
        "? foo",
        "On branch `master`."
    ),
    (
        "# branch.oid (initial)\x00"
        "# branch.head master\x00"
        "# branch.upstream origin/master",
        dedent("""\
        On branch `master` tracking `origin/master`.
        The remote branch is gone.
        """.rstrip())
    ),
    (
        "# branch.oid (initial)\x00"
        "# branch.head master\x00"
        "# branch.upstream origin/master\x00"
        "? .travis.yml",
        dedent("""\
        On branch `master` tracking `origin/master`.
        The remote branch is gone.
        """.rstrip())
    ),
    # Other unborn branch names
    (
        "# branch.oid (initial)\x00"
        "# branch.head zoom",
        "On branch `zoom`."
    ),
    (
        "# branch.oid (initial)\x00"
        "# branch.head master\x00"
        "? foo",
        "On branch `master`."
    ),
]


class TestLongBranchStatus(DeferrableTestCase):
    def setUp(self):
        when(ci_status).on_head_observed(...).thenReturn(None)

    def tearDown(self):
        unstub()

    @p.expand(TestLongBranchStatusTestcases)
    def test_format_branch_status_for_status_dashboard(self, status_lines, expected):
        git = GitCommand()
        when(git).get_repo_path().thenReturn("somewhere")
        when(git).in_rebase().thenReturn(False)
        when(git).in_merge().thenReturn(False)
        when(git).in_cherry_pick().thenReturn(False)
        when(git).in_revert().thenReturn(False)

        when(git).git("status", ...).thenReturn(status_lines)

        git.get_working_dir_status()
        actual = "\n".join(
            line.lstrip()
            for line in git.current_state().get("long_status").splitlines()
        )
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


class TestRecentCommitsFormat(DeferrableTestCase):
    @p.expand([
        (
            "empty lists do not throw",
            [],
            []
        ),

        (
            "format one normal commit",
            [["abc", "", "message"]],
            ["abc message"]
        ),
        (
            "format up to max_items commits",
            [
                ["abc", "", "message"],
                ["abc", "", "message"],
                ["abc", "", "message"],
                ["abc", "", "message"],
                ["end", "", "message"],
            ],
            [
                "abc message",
                "abc message",
                "abc message",
                "abc message",
                "end message",
            ]
        ),
        (
            "drop sixth (max_items+1) element",
            [
                ["abc", "", "message"],
                ["abc", "", "message"],
                ["abc", "", "message"],
                ["abc", "", "message"],
                ["end", "", "message"],
                ["def", "", "dropped"],
            ],
            [
                "abc message",
                "abc message",
                "abc message",
                "abc message",
                "end message",
            ]
        ),

        (
            "ignore HEAD decoration on first commit (detached variant)",
            [
                ["abc", " (HEAD)", "message"],
                ["abc", "", "message"],
            ],
            [
                "abc message",
                "abc message",
            ]
        ),
        (
            "ignore HEAD decoration on first commit (on branch variant)",
            [
                ["abc", " (HEAD -> feature-branch)", "message"],
                ["abc", "", "message"],
            ],
            [
                "abc message",
                "abc message",
            ]
        ),
        (
            "ignore `*/HEAD` refs",
            [
                ["abc", " (HEAD -> feature-branch, origin/HEAD)", "message"],
            ],
            [
                "abc message",
            ]

        ),

        (
            "special format decorated commit: "
            "ensure lstrip() is called on decoration and "
            "the invisible space is added",
            [
                ["abc", " (HEAD)", "message"],
                ["abc", " (tag: 1.0.0)", "message"],
            ],
            [
                "abc message",
                "abc \u200B(tag: 1.0.0)",
            ]
        ),

        (
            "stop after first decoration (drop normal commit)",
            [
                ["abc", " (HEAD)", "message"],
                ["abc", " (tag: 1.0.0)", "message"],
                ["def", "", "dropped"],
            ],
            [
                "abc message",
                "abc \u200B(tag: 1.0.0)",
            ]
        ),
        (
            "stop after first decoration (drop another decorated line)",
            [
                ["abc", " (HEAD)", "message"],
                ["abc", " (tag: 1.0.0)", "message"],
                ["def", " (tag: dropped)", "dropped"],
            ],
            [
                "abc message",
                "abc \u200B(tag: 1.0.0)",
            ]
        ),

        (
            "do not drop decoration if on 6th (max_items+1) position",
            [
                ["abc", " (HEAD)", "message"],
                ["abc", "", "message"],
                ["abc", "", "message"],
                ["abc", "", "message"],
                ["abc", "", "message"],
                ["abc", " (tag: 1.0.0)", "message"],
            ],
            [
                "abc message",
                "abc message",
                "abc message",
                "abc message",
                "abc message",
                "abc \u200B(tag: 1.0.0)",
            ]
        ),
        (
            "drop 6th commit and "
            "show continuation line because a decoration follows",
            [
                ["abc", " (HEAD)", "message"],
                ["abc", "", "message"],
                ["abc", "", "message"],
                ["abc", "", "message"],
                ["abc", "", "message"],
                ["def", "", "dropped"],
                ["abc", " (tag: 1.0.0)", "message"],
            ],
            [
                "abc message",
                "abc message",
                "abc message",
                "abc message",
                "abc message",
                "\u200B ⋮",
                "abc \u200B(tag: 1.0.0)",
            ]
        ),

        (
            "do not stop when the HEAD commit is decorated",
            [
                ["abc", " (HEAD -> feature-branch, another-feat)", "message"],
                ["not", "", "dropped"],
            ],
            [
                "abc message",
                "` \u200B(another-feat)",
                "not dropped",
            ]
        ),

        (
            "suppress upstream on HEAD",
            # because the header already starts with:
            # On branch `master` tracking `origin/master`
            [
                ["abc", " (HEAD -> master, origin/master)", "message"],
            ],
            [
                "abc message",
            ]
        ),
        (
            "do not stop after the upstream branch decoration (on HEAD)",
            [
                ["abc", " (HEAD -> master, origin/master)", "message"],
                ["abc", "", "message"],
            ],
            [
                "abc message",
                "abc message",
            ]
        ),
        (
            "do not stop after the upstream branch decoration (on a later commit)",
            [
                ["abc", " (HEAD -> master)", "message"],
                ["bcd", " (origin/master)", "message"],
                ["abc", "", "message"],
            ],
            [
                "abc message",
                "bcd message",
                "` \u200B(origin/master)",
                "abc message",
            ]
        ),

    ])
    def test_formatting_and_limiting(self, _, lines, expected):
        branches = [
            git_mixins.branches.Branch(
                "master",
                None,
                "master",
                "89b79cd737465ed308ecc00289d00a6f923f2da5",
                "The Subject",
                False,
                False,
                0,
                "now",
                "now",
                git_mixins.branches.Upstream(
                    "origin", "master", "origin/master", ""
                ),
                git_mixins.branches.AheadBehind(ahead=0, behind=0),
                None
            )
        ]
        actual = list(active_branch.format_and_limit(lines, 5, "origin/master", branches))
        self.assertEqual(actual, expected)
