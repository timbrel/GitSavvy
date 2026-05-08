from unittesting import DeferrableTestCase

from GitSavvy.core.utils import (
    cached_until_focus_switch,
    until_focus_switch_cache,
)


class Repo:
    def __init__(self, repo_path: str = "/repo"):
        self.repo_path = repo_path
        self.calls = 0

    def expensive(self, kind: str = "remote") -> str:
        self.calls += 1
        return f"{kind}-url"


class TestCachedUntilFocusSwitch(DeferrableTestCase):
    def setUp(self):
        until_focus_switch_cache.clear()

    def test_direct_call_with_bound_method_caches_and_returns(self):
        repo = Repo()

        first = cached_until_focus_switch(repo.expensive)
        second = cached_until_focus_switch(repo.expensive)

        self.assertEqual(first, "remote-url")
        self.assertEqual(second, "remote-url")
        self.assertEqual(repo.calls, 1)

    def test_direct_call_normalizes_positional_and_keyword(self):
        repo = Repo()

        cached_until_focus_switch(repo.expensive, "x")
        cached_until_focus_switch(repo.expensive, kind="x")

        self.assertEqual(repo.calls, 1)

    def test_decorator_form_caches_per_instance(self):
        @cached_until_focus_switch
        def fetch(self, kind="remote"):
            self.calls += 1
            return f"{kind}-url"

        a = Repo("/a")
        b = Repo("/b")

        self.assertEqual(fetch(a), "remote-url")
        self.assertEqual(fetch(a), "remote-url")
        self.assertEqual(fetch(b), "remote-url")

        self.assertEqual(a.calls, 1)
        self.assertEqual(b.calls, 1)

    def test_decorator_form_normalizes_positional_and_keyword(self):
        @cached_until_focus_switch
        def fetch(self, kind="remote"):
            self.calls += 1
            return f"{kind}-url"

        repo = Repo()
        fetch(repo, "x")
        fetch(repo, kind="x")

        self.assertEqual(repo.calls, 1)
