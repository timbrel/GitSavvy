from __future__ import annotations

from unittesting import DeferrableTestCase

from GitSavvy.common import interwebs
from GitSavvy.tests.mockito import mock, unstub, verify, when
from GitSavvy.tests.parameterized import parameterized as p


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

    def test_requests_use_https_by_default_and_close_connection(self):
        when(interwebs.http.client).HTTPSConnection("example.com", 443).thenReturn(self.connection)
        response = interwebs.get("example.com", 443, "/")
        self.assertEqual(response.payload, {"ok": True})
        verify(self.response).__enter__()
        verify(self.response).__exit__(None, None, None)
        verify(self.connection).close()

    def test_explicit_timeout_applies_to_the_socket(self):
        when(interwebs.http.client).HTTPSConnection("example.com", 443, timeout=3).thenReturn(self.connection)
        interwebs.post("example.com", 443, "/graphql", timeout=3, payload={"query": "x"})
        verify(self.connection).request("POST", "/graphql", body='{"query": "x"}', headers={
            "User-Agent": "GitSavvy Sublime Plug-in"
        })

    def test_connection_closes_even_on_read_failure(self):
        when(interwebs.http.client).HTTPSConnection("example.com", 443, timeout=2).thenReturn(self.connection)
        when(self.response).read().thenRaise(TimeoutError("timeout"))
        with self.assertRaises(TimeoutError):
            interwebs.get("example.com", 443, "/", timeout=2)
        verify(self.response).__exit__(...)
        verify(self.connection).close()

    def test_missing_content_type_is_not_a_json_failure(self):
        when(interwebs.http.client).HTTPSConnection(...).thenReturn(self.connection)
        when(self.response).getheaders().thenReturn([])
        response = interwebs.get("example.com", 443, "/")
        self.assertFalse(response.is_json)
        self.assertEqual(response.payload, b'{"ok": true}')

    def test_post_302_does_not_follow_a_get_redirect(self):
        when(interwebs.http.client).HTTPSConnection(...).thenReturn(self.connection)
        self.response.status = 302
        when(interwebs).request_url(...).thenRaise(AssertionError("POST must not redirect"))
        self.assertEqual(interwebs.post("example.com", 443, "/").status, 302)

    def test_get_redirect_preserves_timeout(self):
        when(interwebs.http.client).HTTPSConnection(...).thenReturn(self.connection)
        self.response.status = 301
        when(self.response).getheaders().thenReturn([("Location", "https://other.example.com/")])
        when(interwebs).request_url(...).thenReturn("redirected")
        self.assertEqual(interwebs.get("example.com", 443, "/", timeout=4), "redirected")
        verify(interwebs).request_url("GET", "https://other.example.com/", headers={
            "User-Agent": "GitSavvy Sublime Plug-in"
        }, auth=None, timeout=4)

    @p.expand([
        ("https://other.example.com/", 443),
        ("https://example.com.evil/", 443),
        ("https://example.com:8443/", 443),
        ("https://example.com/", 8443),
    ])
    def test_cross_origin_redirects_strip_auth(self, location: str, port: int) -> None:
        when(interwebs.http.client).HTTPSConnection(...).thenReturn(self.connection)
        self.response.status = 302
        when(self.response).getheaders().thenReturn([("Location", location)])
        when(interwebs).request_url(...).thenReturn("redirected")
        headers = {"Accept": "application/json"}

        result = interwebs.get(
            "example.com", port, "/", headers=headers, auth=("token", "x-oauth-basic"), timeout=4
        )

        self.assertEqual(result, "redirected")
        verify(interwebs).request_url("GET", location, headers={
            "Accept": "application/json",
            "User-Agent": "GitSavvy Sublime Plug-in"
        }, auth=None, timeout=4)
        self.assertEqual(headers["Authorization"], "Basic dG9rZW46eC1vYXV0aC1iYXNpYw==")

    @p.expand([("Authorization",), ("authorization",), ("AUTHORIZATION",)])
    def test_cross_origin_redirects_strip_auth_headers_case_insensitively(self, header: str) -> None:
        when(interwebs.http.client).HTTPSConnection(...).thenReturn(self.connection)
        self.response.status = 301
        location = "https://other.example.com/"
        when(self.response).getheaders().thenReturn([("Location", location)])
        when(interwebs).request_url(...).thenReturn("redirected")
        headers = {header: "Bearer token"}

        interwebs.get("example.com", 443, "/", headers=headers)

        verify(interwebs).request_url("GET", location, headers={
            "User-Agent": "GitSavvy Sublime Plug-in"
        }, auth=None, timeout=None)
        self.assertEqual(headers[header], "Bearer token")

    @p.expand([
        ("https://example.com/", 443),
        ("https://example.com:443/", 443),
        ("https://EXAMPLE.COM/", 443),
        ("https://example.com:8443/", 8443),
    ])
    def test_same_origin_redirects_keep_auth(self, location: str, port: int) -> None:
        when(interwebs.http.client).HTTPSConnection(...).thenReturn(self.connection)
        self.response.status = 302
        when(self.response).getheaders().thenReturn([("Location", location)])
        when(interwebs).request_url(...).thenReturn("redirected")
        auth = ("token", "x-oauth-basic")

        interwebs.get("EXAMPLE.COM", port, "/", auth=auth)

        verify(interwebs).request_url("GET", location, headers={
            "User-Agent": "GitSavvy Sublime Plug-in",
            "Authorization": "Basic dG9rZW46eC1vYXV0aC1iYXNpYw=="
        }, auth=auth, timeout=None)

    def test_url_helpers_use_https_and_forward_timeout(self) -> None:
        when(interwebs.http.client).HTTPSConnection("example.com", 443, timeout=3).thenReturn(self.connection)

        response = interwebs.get_url("https://example.com/path", timeout=3)

        self.assertEqual(response.payload, {"ok": True})
        verify(self.connection).request("GET", "/path", body=None, headers={
            "User-Agent": "GitSavvy Sublime Plug-in"
        })

    def test_request_url_uses_explicit_auth(self) -> None:
        when(interwebs.http.client).HTTPSConnection("example.com", 443).thenReturn(self.connection)

        interwebs.request_url("HEAD", "https://example.com/path", auth=("token", "x-oauth-basic"))

        verify(self.connection).request("HEAD", "/path", body=None, headers={
            "User-Agent": "GitSavvy Sublime Plug-in",
            "Authorization": "Basic dG9rZW46eC1vYXV0aC1iYXNpYw=="
        })

    def test_url_credentials_do_not_override_explicit_auth(self) -> None:
        when(interwebs.http.client).HTTPSConnection("example.com", 443).thenReturn(self.connection)

        interwebs.get_url("https://other:secret@example.com/path", auth=("token", "x-oauth-basic"))

        verify(self.connection).request("GET", "/path", body=None, headers={
            "User-Agent": "GitSavvy Sublime Plug-in",
            "Authorization": "Basic dG9rZW46eC1vYXV0aC1iYXNpYw=="
        })

    def test_https_urls_preserve_explicit_ports(self) -> None:
        when(interwebs.http.client).HTTPSConnection("example.com", 8443).thenReturn(self.connection)

        interwebs.get_url("https://example.com:8443/path")

        verify(interwebs.http.client).HTTPSConnection("example.com", 8443)

    def test_url_helpers_reject_non_https_urls_before_connecting(self) -> None:
        when(interwebs.http.client).HTTPConnection(...).thenReturn(self.connection)
        when(interwebs.http.client).HTTPSConnection(...).thenReturn(self.connection)

        for url in ("http://example.com/", "ftp://example.com/", "//example.com/", "/path"):
            with self.subTest(url=url):
                with self.assertRaisesRegex(ValueError, "Only HTTPS URLs are supported"):
                    interwebs.get_url(url, headers={"Authorization": "Bearer token"})

        verify(interwebs.http.client, times=0).HTTPConnection(...)
        verify(interwebs.http.client, times=0).HTTPSConnection(...)

    def test_redirects_cannot_downgrade_to_http(self) -> None:
        when(interwebs.http.client).HTTPConnection(...).thenReturn(self.connection)
        when(interwebs.http.client).HTTPSConnection(...).thenReturn(self.connection)
        self.response.status = 302
        when(self.response).getheaders().thenReturn([("Location", "http://other.example.com/")])

        with self.assertRaisesRegex(ValueError, "Only HTTPS URLs are supported"):
            interwebs.get("example.com", 443, "/", auth=("token", "x-oauth-basic"))

        verify(interwebs.http.client).HTTPSConnection("example.com", 443)
        verify(interwebs.http.client, times=0).HTTPConnection(...)
        verify(interwebs.http.client, times=0).HTTPSConnection("other.example.com", ...)
        verify(self.connection).close()
