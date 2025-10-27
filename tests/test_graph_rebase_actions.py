from textwrap import dedent

from unittesting import DeferrableTestCase
from GitSavvy.tests.parameterized import parameterized as p

from GitSavvy.core.commands.log_graph_rebase_actions import (
    extract_commits, change_first_action, copy_commits, fixup_commits, squash_commits,
    Commit
)


fixup_examples = [
    (
        [Commit("6c8cb2e7", "fixup! Look upwards in the graph for potential fixup..")],
        "056c8f6d",
        dedent("""\
        pick 056c8f6d Look upwards in the graph for potential fixup commits
        pick 496d0266 Remove the padding as it has no effect
        pick 2ed5650d fixup! Look upwards in the graph for potential fixup commits
        pick 0d721988 fixup! Look upwards in the graph for potential fixup commits
        pick fc5da4c3 Simplify `gs_reset_branch`
        pick 8e5e700b Fix typo in `reverse_adjust_line_according_to_hunks`
        pick 6c8cb2e7 fixup! Look upwards in the graph for potential fixup commits
        pick 19978225 Check `returncode` to decide if `git log` failed

        # Rebase 6bd508b2..0b0409f8 onto 6bd508b2 (8 commands)
        """),
        dedent("""\
        pick 056c8f6d Look upwards in the graph for potential fixup commits
        fixup 6c8cb2e7 fixup! Look upwards in the graph for potential fixup..
        pick 496d0266 Remove the padding as it has no effect
        pick 2ed5650d fixup! Look upwards in the graph for potential fixup commits
        pick 0d721988 fixup! Look upwards in the graph for potential fixup commits
        pick fc5da4c3 Simplify `gs_reset_branch`
        pick 8e5e700b Fix typo in `reverse_adjust_line_according_to_hunks`
        pick 19978225 Check `returncode` to decide if `git log` failed

        """),
    ),
]

quick_examples = [
    (
        "reword",
        "0b0409f8",
        dedent("""\
        pick 0b0409f8 Let `QuickAction` be a function `str -> str` for flexibility

        # Rebase fee0447b..0b0409f8 onto fee0447b (1 command)
        """),
        dedent("""\
        reword 0b0409f8 Let `QuickAction` be a function `str -> str` for flexibility

        """)
    ),
    (
        "edit",
        "142972fd",
        dedent("""\
        pick 142972fd Mark first arg of continuation function "positional only"
        pick fee0447b Simplify `ask_for_local_branch`
        pick 0b0409f8 Let `QuickAction` be a function `str -> str` for flexibility

        # Rebase 2bcb7211..0b0409f8 onto 2bcb7211 (3 commands)
        """),
        dedent("""\
        edit 142972fd Mark first arg of continuation function "positional only"
        pick fee0447b Simplify `ask_for_local_branch`
        pick 0b0409f8 Let `QuickAction` be a function `str -> str` for flexibility

        """)
    ),
    (
        "drop",
        "fee0447b",
        dedent("""\
        label onto

        reset onto
        pick fee0447b Simplify `ask_for_local_branch`
        pick 0b0409f8 Let `QuickAction` be a function `str -> str` for flexibility
        """),
        dedent("""\
        label onto

        reset onto
        drop fee0447b Simplify `ask_for_local_branch`
        pick 0b0409f8 Let `QuickAction` be a function `str -> str` for flexibility
        """)
    ),
]


class TestRebaseActions(DeferrableTestCase):
    @p.expand(fixup_examples)
    def test_applying_fixups(self, fixups, base_commit, input, expected):
        actual = fixup_commits(fixups, base_commit, input)
        self.maxDiff = None
        self.assertEqual(expected, actual)

    @p.expand(quick_examples)
    def test_applying_quick_actions(self, action, base_commit, input, expected):
        actual = change_first_action(action, base_commit, input)
        self.maxDiff = None
        self.assertEqual(expected, actual)

    @p.expand([
        (
            "refs/pols/fooz",
            ["fee0447b", "0b0409f8"],
            dedent("""\
            pick 142972fd Mark first arg of continuation function "positional only"
            pick fee0447b Simplify `ask_for_local_branch`
            pick 0b0409f8 Let `QuickAction` be a function `str -> str` for flexibility

            # Rebase 2bcb7211..0b0409f8 onto 2bcb7211 (3 commands)
            """),
            dedent("""\
            label onto
            pick fee0447b
            pick 0b0409f8
            u refs/pols/fooz
            reset onto

            pick 142972fd Mark first arg of continuation function "positional only"
            pick fee0447b Simplify `ask_for_local_branch`
            pick 0b0409f8 Let `QuickAction` be a function `str -> str` for flexibility

            # Rebase 2bcb7211..0b0409f8 onto 2bcb7211 (3 commands)
            """),
        ),
        (
            "refs/pols/fooz",
            ["fee0447b"],
            dedent("""\
            pick 142972fd Mark first arg of continuation function "positional only"
            pick fee0447b Simplify `ask_for_local_branch`
            update-ref refs/pols/fooz
            pick 0b0409f8 Let `QuickAction` be a function `str -> str` for flexibility

            # Rebase 2bcb7211..0b0409f8 onto 2bcb7211 (3 commands)
            """),
            dedent("""\
            label onto
            pick fee0447b
            u refs/pols/fooz
            reset onto

            pick 142972fd Mark first arg of continuation function "positional only"
            pick fee0447b Simplify `ask_for_local_branch`
            update-ref refs/pols/fooz--local
            pick 0b0409f8 Let `QuickAction` be a function `str -> str` for flexibility

            # Rebase 2bcb7211..0b0409f8 onto 2bcb7211 (3 commands)
            """),
        )
    ])
    def test_copy_commits(self, ref, commits, input, expected):
        # copy_commits(ref: str, commits: List[str], buffer_content: str)
        actual = copy_commits(ref, commits, input)
        self.maxDiff = None
        self.assertEqual(expected, actual)

    @p.expand([
        (
            "refs/pols/fooz",
            ["fee0447b"],
            dedent("""\
            pick 142972fd Mark first arg of continuation function "positional only"
            pick fee0447b Simplify `ask_for_local_branch`
            update-ref refs/pols/fooz
            pick 0b0409f8 Let `QuickAction` be a function `str -> str` for flexibility

            # Rebase 2bcb7211..0b0409f8 onto 2bcb7211 (3 commands)
            """),
            dedent("""\
            label onto
            pick fee0447b
            u refs/pols/fooz
            reset onto

            pick 142972fd Mark first arg of continuation function "positional only"
            drop fee0447b Simplify `ask_for_local_branch`
            pick 0b0409f8 Let `QuickAction` be a function `str -> str` for flexibility

            # Rebase 2bcb7211..0b0409f8 onto 2bcb7211 (3 commands)
            """),
        )
    ])
    def test_extract_commits(self, ref, commits, input, expected):
        # extract_commits(ref: str, commits: List[str], buffer_content: str)
        actual = extract_commits(ref, commits, input)
        self.maxDiff = None
        self.assertEqual(expected, actual)

    @p.expand([
        (
            "142972fd",
            ["0b0409f8"],
            dedent("""\
            pick 142972fd Mark first arg of continuation function "positional only"
            pick fee0447b Simplify `ask_for_local_branch`
            pick 0b0409f8 Let `QuickAction` be a function `str -> str` for flexibility

            # Rebase 2bcb7211..0b0409f8 onto 2bcb7211 (3 commands)
            """),
            dedent("""\
            pick 142972fd Mark first arg of continuation function "positional only"
            squash 0b0409f8
            pick fee0447b Simplify `ask_for_local_branch`

            # Rebase 2bcb7211..0b0409f8 onto 2bcb7211 (3 commands)
            """),
        )
    ])
    def test_squah_commits(self, base_commit, commits, input, expected):
        # extract_commits(ref: str, commits: List[str], buffer_content: str)
        actual = squash_commits(commits, base_commit, input)
        self.maxDiff = None
        self.assertEqual(expected, actual)
