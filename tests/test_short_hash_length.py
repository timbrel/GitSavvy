from unittesting import DeferrableTestCase

from GitSavvy.tests.mockito import unstub, verify, when
from GitSavvy.tests.parameterized import parameterized as p
from GitSavvy.core.git_command import GitCommand


class TestShortHashLength(DeferrableTestCase):
    def setUp(self) -> None:
        self.repo = GitCommand()
        self.state = {}
        when(self.repo).current_state().thenReturn(self.state)
        when(self.repo).update_store(...).thenAnswer(self.state.update)

    def tearDown(self) -> None:
        unstub()

    @p.expand([(0, 7, 7), (7, 9, 9), (9, 7, 9)])
    def test_head_message_never_shrinks_cached_length(self, cached, observed, expected):
        if cached:
            self.state["short_hash_length"] = cached
        when(self.repo).git("log", ...).thenReturn("a" * observed + " Subject")
        self.repo.get_latest_commit_msg_for_head()
        self.assertEqual(expected, self.repo.get_short_hash_length())

    @p.expand([(0, [7, 9, 7], 9), (7, [9, 7], 9), (9, [7, 7], 9)])
    def test_recent_commits_remember_the_longest_observed_length(self, cached, lengths, expected):
        if cached:
            self.state["short_hash_length"] = cached
        when(self.repo).git("log", ...).thenReturn("\n".join(
            "a" * length + "%00%00Subject" for length in lengths
        ))
        self.repo.get_latest_commits()
        self.assertEqual(expected, self.repo.get_short_hash_length())

    def test_resolve_seeds_an_unknown_length(self) -> None:
        when(self.repo).resolve("HEAD", short=True).thenReturn("a" * 8)
        self.assertEqual(8, self.repo.get_short_hash_length())
        self.assertEqual(8, self.state["short_hash_length"])

    def test_known_length_needs_no_resolution(self) -> None:
        self.state["short_hash_length"] = 9
        when(self.repo).resolve(...).thenReturn("a" * 7)
        self.assertEqual(9, self.repo.get_short_hash_length())
        verify(self.repo, times=0).resolve(...)

    def test_empty_logs_leave_the_cached_length_unchanged(self) -> None:
        self.state["short_hash_length"] = 9
        when(self.repo).git("log", ...).thenReturn("")
        self.assertEqual("No commits yet.", self.repo.get_latest_commit_msg_for_head())
        self.assertEqual([], self.repo.get_latest_commits())
        self.assertEqual(9, self.state["short_hash_length"])
