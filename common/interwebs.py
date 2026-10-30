"""
A simple HTTPS interface for making GET, PUT and POST requests.
"""

import http.client
import json
from urllib.parse import urlparse, urlencode, quote  # NOQA
from base64 import b64encode
from functools import partial
from collections import namedtuple

from GitSavvy.common.util.debug import dprint
from GitSavvy.core.utils import measure_runtime

Response = namedtuple("Response", ("payload", "headers", "status", "is_json"))


class Headers(dict):
    def __init__(self, response):
        for key, value in response.getheaders():
            key = key.lower()
            prev_val = self.get(key)
            if prev_val is not None:
                value = ", ".join((prev_val, value))
            self[key] = value


def request(
    verb, host, port, path, payload=None, headers=None, auth=None,
    redirect=True, timeout=None
):
    """
    Make an HTTPS request with the provided HTTP verb, host FQDN, port number, path,
    payload, headers, and auth information.  Return a response object with
    payload, headers, JSON flag, and HTTP status number.
    """
    if not headers:
        headers = {}
    headers["User-Agent"] = "GitSavvy Sublime Plug-in"

    if auth:
        # use basic authentication
        username_password = "{}:{}".format(*auth).encode("ascii")
        headers["Authorization"] = "Basic {}".format(b64encode(username_password).decode("ascii"))

    if payload and not isinstance(payload, str):
        payload = json.dumps(payload)

    with measure_runtime() as ms:
        options = {"timeout": timeout} if timeout is not None else {}
        connection = http.client.HTTPSConnection(host, port, **options)
        try:
            connection.request(verb, path, body=payload, headers=headers)
            with connection.getresponse() as response:
                response_payload = response.read()
                response_headers = Headers(response)
                status = response.status
        finally:
            connection.close()

    dprint(f" >-> {verb:<7} > https://{host}:{port}{path} [{ms.get()}ms]")
    if payload:
        dprint("    ", payload)

    is_json = "application/json" in response_headers.get("content-type", "")
    if is_json:
        response_payload = json.loads(response_payload.decode("utf-8"))

    if redirect and verb == "GET" and status in (301, 302):
        location = response_headers["location"]
        target = urlparse(location)
        if (target.scheme, target.hostname, target.port or 443) != ("https", host.lower(), port):
            headers = {key: value for key, value in headers.items() if key.lower() != "authorization"}
            auth = None
        return request_url(
            verb,
            location,
            headers=headers,
            auth=auth,
            timeout=timeout
        )

    return Response(response_payload, response_headers, status, is_json)


def request_url(verb, url, payload=None, headers=None, auth=None, timeout=None):
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise ValueError("Only HTTPS URLs are supported")
    return request(
        verb,
        parsed.hostname,
        parsed.port or 443,
        parsed.path,
        payload=payload,
        headers=headers,
        auth=auth,
        timeout=timeout
    )


get = partial(request, "GET")
post = partial(request, "POST")
put = partial(request, "PUT")

get_url = partial(request_url, "GET")
post_url = partial(request_url, "POST")
put_url = partial(request_url, "PUT")
