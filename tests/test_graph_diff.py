
from unittesting import DeferrableTestCase
from GitSavvy.tests.parameterized import parameterized as p


from GitSavvy.core.commands.log_graph_renderer import (
    diff, simplify, normalize_tokens, apply_diff, Ins, Del, Replace, Flush
)


def _(string):
    return [__(s) for s in iter(string)]


def __(string):
    return '* ' + string


def tx(tokens):
    return [
        t._replace(line=__(t.line)) if isinstance(t, Ins)
        else t._replace(text=_(t.text)) if isinstance(t, Replace)
        else t
        for t in tokens
    ]


def filter_same(it):
    return filter(lambda t: t is not Flush, it)


class TestGraphDiff(DeferrableTestCase):
    @p.expand([
        ('abcdef', 'abcdef', []),
        ('bcdef', 'abcdef', [Ins(0, 'a')]),
        ('a', 'xay', [Ins(0, 'x'), Ins(2, 'y')]),
        ('ab', 'xay', [Ins(0, 'x'), Ins(2, 'y'), Del(3, 4)]),
        #                 xabc         xaybc        xayc       xaycz
        ('abc', 'xaycz', [Ins(0, 'x'), Ins(2, 'y'), Del(3, 4), Ins(4, 'z')]),
        ('abcd', 'xbcd', [Ins(0, 'x'), Del(1, 2)]),
        ('abcd', 'pqabcd', [Ins(0, 'p'), Ins(1, 'q')]),
        ('abcd', 'pqab', [Ins(0, 'p'), Ins(1, 'q'), Del(4, 6)]),
        ('abpqcd', 'abcd', [Del(2, 4)]),
        ('abpqcd', 'abc', [Del(2, 4), Del(3, 4)]),
    ])
    def test_a(self, A, B, ops):
        A = _(A)
        B = _(B)
        ops = tx(ops)
        self.assertEqual(list(filter_same(diff(A, B))), ops)

    @p.expand([
        ('abcdef', 'abcdef', []),
        ('bcdef', 'abcdef', [Ins(0, 'a')]),
        ('a', 'xay', [Ins(0, 'x'), Ins(2, 'y')]),
        ('ab', 'xay', [Ins(0, 'x'), Replace(2, 3, 'y')]),
        ('abc', 'xayc', [Ins(0, 'x'), Replace(2, 3, 'y')]),
        #                 xabc         xayc                     xaycz
        ('abc', 'xaycz', [Ins(0, 'x'), Replace(2, 3, 'y'), Ins(4, 'z')]),
        ('abcd', 'xbcd', [Replace(0, 1, 'x')]),
        ('abcd', 'pqabcd', [Replace(0, 0, 'pq')]),
        ('abcd', 'pqrabcd', [Replace(0, 0, 'pqr')]),
        ('abcd', '', [Del(0, 4)]),
        ('abcd', 'pqr', [Replace(0, 4, 'pqr')]),
        ('abcd', 'pqab', [Replace(0, 0, 'pq'), Del(4, 6)]),
        ('abpqcd', 'abcd', [Del(2, 4)]),
        ('abpqcd', 'abc', [Del(2, 4), Del(3, 4)]),
        ('abpqcd', '', [Del(0, 6)]),
        ('', 'abpqcd', [Replace(0, 0, 'abpqcd')]),
    ])
    def test_b(self, A, B, ops):
        A = _(A)
        B = _(B)
        ops = tx(ops)
        self.assertEqual(list(simplify(diff(A, B), 100)), ops)

    @p.expand([
        ('abcdef', 'abcdef', []),
        ('bcdef', 'abcdef', [Replace(0, 0, 'a')]),
        ('abcd', 'xbcd', [Replace(0, 1, 'x')]),
        ('abcd', 'pqabcd', [Replace(0, 0, 'pq')]),
        ('abcd', 'pqab', [Replace(0, 0, 'pq'), Replace(4, 6, '')]),
        ('abpqcd', 'abcd', [Replace(2, 4, '')]),
        ('abpqcd', 'abc', [Replace(2, 4, ''), Replace(3, 4, '')]),
    ])
    def test_c(self, A, B, ops):
        A = _(A)
        B = _(B)
        ops = tx(ops)
        self.assertEqual(list(normalize_tokens(simplify(diff(A, B), 100))), ops)

    @p.expand([
        ('abcdef', 'abcdef', []),
        ('bcdef', 'abcdef', [Replace(0, 0, 'a')]),
        ('abcd', 'xbcd', [Replace(0, 1, 'x')]),
        ('abcd', 'pqabcd', [Replace(0, 1, 'pq')]),
        ('abcd', 'pqab', [Replace(0, 1, 'pq'), Replace(4, 6, '')]),
        ('abpqcd', 'abcd', [Replace(2, 4, '')]),
        ('abpqcd', 'abc', [Replace(2, 4, ''), Replace(3, 4, '')]),
    ])
    def test_d(self, A, B, ops):
        A = _(A)
        B = _(B)
        ops = tx(ops)
        self.assertEqual(apply_diff(A, normalize_tokens(simplify(diff(A, B), 100))), B)
