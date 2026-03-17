import unittest

from GitSavvy.core.commands.intra_line_colorizer import intra_diff_line_by_line
from GitSavvy.core.parse_diff import HunkLine


class TestIntraLineColorizer(unittest.TestCase):
    def test_long_lines_use_tokenized_fallback(self):
        prefix = 'const payload = [' + ('1234567890,' * 30)
        old_text = prefix + '"hello"];\n'
        new_text = prefix + '"world"];\n'

        from_line = HunkLine('-' + old_text, 0, mode_len=1)
        to_line = HunkLine('+' + new_text, len(from_line.text), mode_len=1)

        from_regions, to_regions = intra_diff_line_by_line([from_line], [to_line])

        self.assertEqual([old_text[r.a - 1: r.b - 1] for r in from_regions], ['hello'])
        self.assertEqual([new_text[r.a - to_line.a - 1: r.b - to_line.a - 1] for r in to_regions], ['world'])

    def test_short_lines_keep_existing_word_diff_behavior(self):
        from_line = HunkLine('-hello "hello" world\n', 0, mode_len=1)
        to_line = HunkLine('+hello "world" world\n', len(from_line.text), mode_len=1)

        from_regions, to_regions = intra_diff_line_by_line([from_line], [to_line])

        self.assertEqual([from_line.content[r.a - 1: r.b - 1] for r in from_regions], ['hello'])
        self.assertEqual([to_line.content[r.a - to_line.a - 1: r.b - to_line.a - 1] for r in to_regions], ['world'])
