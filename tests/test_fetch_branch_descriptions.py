from textwrap import dedent

from unittesting import DeferrableTestCase
from GitSavvy.tests.mockito import when
from GitSavvy.tests.parameterized import parameterized as p

from GitSavvy.core.git_command import GitCommand


examples = [
    (
        dedent("""\
        branch.status-bar-updater.description One\\nTwo
        branch.revert-o-behavior.description Another branch.asd.description
        branch.opt-fetching-descriptions.description This is the subject

        And here is more text
        and even more

        branch.description.description Another description
        """.rstrip()),
        {
            "status-bar-updater": "One\\nTwo",
            "revert-o-behavior": "Another branch.asd.description",
            "opt-fetching-descriptions": "This is the subject",
            "description": "Another description"
        }
    ),
]


class TestFetchBranchDescriptions(DeferrableTestCase):
    @p.expand(examples)
    def test_description_subjects(self, git_output, expected):
        test = GitCommand()
        when(test).get_repo_path().thenReturn("probably/here")
        when(test, strict=False).git("config", ...).thenReturn(git_output)
        self.assertEqual(expected, test.fetch_branch_description_subjects())
