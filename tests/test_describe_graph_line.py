from unittesting import DeferrableTestCase
from GitSavvy.tests.parameterized import parameterized as p

from GitSavvy.core.commands.log_graph import describe_graph_line


examples = [
    (
        "|",
        {},
        None
    ),
    (
        "● a3062b2 (HEAD -> optimize-graph-render, origin/optimize-graph-render) Abort .. | Thu 21:07, herr kaste",
        {"origin"},
        {
            "commit": "a3062b2",
            "HEAD": "optimize-graph-render",
            "branches": ["optimize-graph-render", "origin/optimize-graph-render"],
            "local_branches": ["optimize-graph-render"]
        }
    ),
    (
        "● a3062b2 (HEAD, origin/optimize-graph-render) Abort re.. | Thu 21:07, herr kaste",
        {"origin"},
        {
            "commit": "a3062b2",
            "HEAD": "a3062b2",
            "branches": ["origin/optimize-graph-render"]
        }
    ),
    (
        "● a3062b2 (HEAD -> optimize-graph-render, feat/optimize-graph-render) Abort .. | Thu 21:07, herr kaste",
        {"origin"},
        {
            "commit": "a3062b2",
            "HEAD": "optimize-graph-render",
            "branches": ["optimize-graph-render", "feat/optimize-graph-render"],
            "local_branches": ["optimize-graph-render", "feat/optimize-graph-render"]
        }
    ),
    (
        "● ad6d88c (HEAD) Use view from the argument instead of on self                   | Thu 20:56, herr kaste",
        {"origin"},
        {
            "commit": "ad6d88c",
            "HEAD": "ad6d88c",
        }
    ),
    (
        "● ad6d88c Use view from the argument instead of on self                          | Thu 20:56, herr kaste",
        {"origin"},
        {
            "commit": "ad6d88c",
        }

    ),
    (
        "| ●   153dca0 (HEAD, tag: 2.20.0) Merge branch 'dev' (2 months ago) <herr kaste>",
        {"origin"},
        {
            "commit": "153dca0",
            "HEAD": "153dca0",
            "tags": ["2.20.0"]
        }
    ),
]


class TestDescribeGraphLine(DeferrableTestCase):
    @p.expand(examples)
    def test_a(self, input_line, remotes, output):
        self.assertEqual(output, describe_graph_line(input_line, remotes))
