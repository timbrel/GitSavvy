from __future__ import annotations

from unittesting import DeferrableTestCase

from GitSavvy.common import interwebs
from GitSavvy.tests.mockito import mock, unstub, verify, when


class TestHttpRequests(DeferrableTestCase):
    def setUp(self):
        self.connection = mock()
        self.response = mock({"status": 200})
        when(self.connection).request(...).thenReturn(None)
        when(self.connection).getresponse().thenReturn(self.response)
        when(self.connection).close().thenReturn(None)
        when(self.response).read().thenReturn(b'{"ok": true}')
        when(self.response).getheaders().thenReturn([("Content-Type", "application/json")])
        when(self.response).__enter__().thenReturn(self.response)
        when(self.response).__exit__(...).thenReturn(False)

    def tearDown(self):
        unstub()

    def test_timeout_is_opt_in_and_connection_is_closed(self):
        when(interwebs.http.client).HTTPSConnection("example.com", 443).thenReturn(self.connection)
        response = interwebs.get("example.com", 443, "/", https=True)
        self.assertEqual(response.payload, {"ok": True})
        verify(self.response).__enter__()
        verify(self.response).__exit__(None, None, None)
        verify(self.connection).close()

    def test_explicit_timeout_applies_to_the_socket(self):
        when(interwebs.http.client).HTTPSConnection("example.com", 443, timeout=3).thenReturn(self.connection)
        interwebs.post("example.com", 443, "/graphql", https=True, timeout=3, payload={"query": "x"})
        verify(self.connection).request("POST", "/graphql", body='{"query": "x"}', headers={
            "User-Agent": "GitSavvy Sublime Plug-in"
        })

    def test_connection_closes_even_on_read_failure(self):
        when(interwebs.http.client).HTTPConnection("example.com", 80, timeout=2).thenReturn(self.connection)
        when(self.response).read().thenRaise(TimeoutError("timeout"))
        with self.assertRaises(TimeoutError):
            interwebs.get("example.com", 80, "/", timeout=2)
        verify(self.response).__exit__(...)
        verify(self.connection).close()

    def test_missing_content_type_is_not_a_json_failure(self):
        when(interwebs.http.client).HTTPConnection(...).thenReturn(self.connection)
        when(self.response).getheaders().thenReturn([])
        response = interwebs.get("example.com", 80, "/")
        self.assertFalse(response.is_json)
        self.assertEqual(response.payload, b'{"ok": true}')

    def test_post_302_does_not_follow_a_get_redirect(self):
        when(interwebs.http.client).HTTPConnection(...).thenReturn(self.connection)
        self.response.status = 302
        when(interwebs).request_url(...).thenRaise(AssertionError("POST must not redirect"))
        self.assertEqual(interwebs.post("example.com", 80, "/").status, 302)

    def test_get_redirect_preserves_timeout(self):
        when(interwebs.http.client).HTTPConnection(...).thenReturn(self.connection)
        self.response.status = 301
        when(self.response).getheaders().thenReturn([("Location", "https://other.example.com/")])
        when(interwebs).request_url(...).thenReturn("redirected")
        self.assertEqual(interwebs.get("example.com", 80, "/", timeout=4), "redirected")
        verify(interwebs).request_url("GET", "https://other.example.com/", headers={
            "User-Agent": "GitSavvy Sublime Plug-in"
        }, auth=None, timeout=4)
