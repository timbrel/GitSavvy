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

    def test_uses_the_shared_colorizer_for_asymmetric_hunks(self):
        view = FakeView()
        command = FakeCommand(view)
        diff = SplittedDiff.from_string(
            "@@ -1,2 +1 @@\n"
            "-value = old\n"
            "-unused\n"
            "+value = new\n"
        )
        contents, hunks = module.gs_inline_diff_refresh.get_inline_diff_contents(
            command,
            "value = old\nunused\n",
            diff.hunks
        )
        view.content = contents
        self.addCleanup(module.diff_view_hunks.pop, view.id(), None)

        module.gs_inline_diff_refresh.highlight_regions(command, hunks)

        self.assertEqual(view.region_contents("git-savvy-removed-bold"), ["old"])
        self.assertEqual(view.region_contents("git-savvy-added-bold"), ["new"])

    def test_shared_colorizer_skips_low_similarity_lines(self):
        view = FakeView()
        command = FakeCommand(view)
        diff = SplittedDiff.from_string(
            "@@ -1 +1 @@\n"
            "-aaaaaaaa\n"
            "+zzzzzzzz\n"
        )
        contents, hunks = module.gs_inline_diff_refresh.get_inline_diff_contents(
            command,
            "aaaaaaaa\n",
            diff.hunks
        )
        view.content = contents
        self.addCleanup(module.diff_view_hunks.pop, view.id(), None)

        module.gs_inline_diff_refresh.highlight_regions(command, hunks)

        self.assertEqual(view.region_contents("git-savvy-removed-bold"), [])
        self.assertEqual(view.region_contents("git-savvy-added-bold"), [])

    def test_shared_colorizer_ignores_no_newline_markers(self):
        view = FakeView()
        command = FakeCommand(view)
        diff = SplittedDiff.from_string(
            "@@ -1 +1 @@\n"
            "-value = old\n"
            "\\ No newline at end of file\n"
            "+value = new\n"
            "\\ No newline at end of file\n"
        )
        contents, hunks = module.gs_inline_diff_refresh.get_inline_diff_contents(
            command,
            "value = old",
            diff.hunks
        )
        view.content = contents
        self.addCleanup(module.diff_view_hunks.pop, view.id(), None)

        module.gs_inline_diff_refresh.highlight_regions(command, hunks)

        self.assertEqual(view.region_contents("git-savvy-removed-bold"), ["old"])
        self.assertEqual(view.region_contents("git-savvy-added-bold"), ["new"])


class FakeCommand:
    def __init__(self, view):
        self.view = view


class FakeView:
    content = ""

    def __init__(self):
        self.regions = {}

    def id(self):
        return 1

    def text_point(self, row, col):
        return sum(map(len, self.content.splitlines(keepends=True)[:row])) + col

    def full_line(self, pt):
        lines = self.content.splitlines(keepends=True)
        start = 0
        for line in lines:
            end = start + len(line)
            if pt < end:
                return module.sublime.Region(start, end)
            start = end
        return module.sublime.Region(start, start)

    def add_regions(self, key, regions, **kwargs):
        self.regions[key] = regions

    def region_contents(self, key):
        return [self.content[region.a:region.b] for region in self.regions[key]]
