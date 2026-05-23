from unittesting import DeferrableTestCase

from GitSavvy.tests.mockito import mock, unstub, when

from GitSavvy.core.commands import log_graph_main_actions as actions
from GitSavvy.core.commands.log_graph_helper import describe_graph_line


class TestGraphActionDiffTargets(DeferrableTestCase):
    def tearDown(self):
        unstub()

    def test_next_diff_target_down_follows_dots_to_next_decoration(self):
        view = object()
        dot = mock({"pt": 0})
        next_dots = [mock({"pt": 1}), mock({"pt": 2})]
        branches = {
            "main": mock({"is_local": True}),
            "origin/main": mock({"is_local": False})
        }
        lines = {
            1: "● ac0ffee Intermediate commit",
            2: "● baddad (origin/main, main) Branch commit"
        }
        self.assertEqual(
            {
                "commit": "baddad",
                "branches": ["origin/main", "main"],
                "local_branches": ["main"]
            },
            describe_graph_line(lines[2], branches)
        )
        when(actions).follow_dots(dot, forward=True).thenReturn(iter(next_dots))
        when(actions).line_from_pt(view, 1).thenReturn(mock({"text": lines[1]}))
        when(actions).line_from_pt(view, 2).thenReturn(mock({"text": lines[2]}))

        self.assertEqual("main", actions.next_diff_target_down(view, dot, branches))

    def test_next_diff_target_down_stops_at_limit(self):
        view = object()
        dot = mock({"pt": 0})
        next_dots = [mock({"pt": 1}), mock({"pt": 2})]
        lines = {
            1: "● ac0ffee Intermediate commit",
            2: "● baddad (tag: too-far) Tagged commit"
        }
        when(actions).follow_dots(dot, forward=True).thenReturn(iter(next_dots))
        when(actions).line_from_pt(view, 1).thenReturn(mock({"text": lines[1]}))

        self.assertEqual(None, actions.next_diff_target_down(view, dot, {}, limit=1))
