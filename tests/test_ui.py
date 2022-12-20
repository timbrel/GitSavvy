from GitSavvy.common import ui

from unittesting import DeferrableTestCase
from GitSavvy.tests.parameterized import parameterized as p


class TestWhenWeNeedAFullRender(DeferrableTestCase):
    @p.expand([
        (set([]), set([]), True),
        (set([1, 2]), set([]), True),
        (set([1, 2]), set([1]), True),
        (set([1, 2]), set([2, 3]), True),
        (set([1, 2]), set([3, 4]), True),

        (set([]), set([1, 2]), False),
        (set([1]), set([1, 2]), False),
        (set([1, 2]), set([1, 2]), False),

    ])
    def test_do_a_full_render(self, current, previous, RESULT):
        self.assertEqual(ui.should_do_a_full_render(current, previous), RESULT)
