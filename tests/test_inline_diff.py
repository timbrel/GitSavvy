from unittesting import DeferrableTestCase

from GitSavvy.core.parse_diff import SplittedDiff
import GitSavvy.core.interfaces.inline_diff as module


class TestInlineDiffParsing(DeferrableTestCase):
    def test_renders_hunks_and_preserves_line_translation(self):
        view = FakeView()
        command = FakeCommand(view)
        diff = SplittedDiff.from_string(
            "@@ -2 +2 @@\n"
            "-two\n"
            "+TWO\n"
        )

        contents, hunks = module.gs_inline_diff_refresh.get_inline_diff_contents(
            command,
            "one\ntwo\nthree\n",
            diff.hunks
        )
        self.addCleanup(module.diff_view_hunks.pop, view.id(), None)

        self.assertEqual(contents, "one\ntwo\nTWO\nthree\n")
        self.assertEqual(hunks[0].line_types, ["-", "+"])
        self.assertEqual(hunks[0].lines, ["two\n", "TWO\n"])
        self.assertEqual(
            module.translate_pos_from_diff_view_to_file(view, 2, 4),
            (2, 1)
        )
        self.assertEqual(
            module.translate_pos_from_diff_view_to_file(view, 3, 4),
            (2, 4)
        )
        self.assertEqual(
            module.translate_pos_from_diff_view_to_file(view, 4, 4),
            (3, 4)
        )


class FakeCommand:
    def __init__(self, view):
        self.view = view


class FakeView:
    def id(self):
        return 1
