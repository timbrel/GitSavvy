import unittest

from GitSavvy.core.commands.intra_line_colorizer import intra_diff_general_algorithm, intra_diff_line_by_line
from GitSavvy.core.parse_diff import HunkLine


def extract_region_texts(lines, regions):
    texts = []
    for region in regions:
        for line in lines:
            start = line.a + line.mode_len
            end = start + len(line.content)
            if start <= region.a and region.b <= end:
                texts.append(line.content[region.a - start:region.b - start])
                break
    return texts


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

    def test_general_algorithm_marks_changes_across_merged_lines(self):
        from_lines = [
            HunkLine('-alpha beta gamma\n', 0, mode_len=1),
            HunkLine('-delta epsilon zeta\n', len('-alpha beta gamma\n'), mode_len=1),
        ]
        to_lines = [
            HunkLine(
                "+alpha beta gamma delta EPSILON zeta\n",
                len("-alpha beta gamma\n-delta epsilon zeta\n"),
                mode_len=1,
            ),
        ]

        from_regions, to_regions = intra_diff_general_algorithm(from_lines, to_lines)

        self.assertIn('epsilon', extract_region_texts(from_lines, from_regions))
        self.assertIn('EPSILON', extract_region_texts(to_lines, to_regions))
