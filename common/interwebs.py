"""
A simple HTTP interface for making GET, PUT and POST requests.
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


def request(verb, host, port, path, payload=None, https=False, headers=None, auth=None, redirect=True):
    """
    Make an HTTP(S) request with the provided HTTP verb, host FQDN, port number, path,
    payload, protocol, headers, and auth information.  Return a response object with
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
        connection = (http.client.HTTPSConnection(host, port)
                      if https
                      else http.client.HTTPConnection(host, port))
        connection.request(verb, path, body=payload, headers=headers)

        response = connection.getresponse()

    scheme = "https" if https else "http"
    dprint(f" >-> {verb:<7} > {scheme}://{host}:{port}{path} [{ms.get()}ms]")
    if payload:
        dprint("    ", payload)

    response_payload = response.read()
    response_headers = Headers(response)
    status = response.status

    is_json = "application/json" in response_headers["content-type"]
    if is_json:
        response_payload = json.loads(response_payload.decode("utf-8"))

    response.close()
    connection.close()

    if redirect and verb == "GET" and status == 301 or status == 302:
        return request_url(
            verb,
            response_headers["location"],
            headers=headers,
            auth=auth
        )

    return Response(response_payload, response_headers, status, is_json)


def request_url(verb, url, payload=None, headers=None, auth=None):
    parsed = urlparse(url)
    https = parsed.scheme == "https"
    return request(
        verb,
        parsed.hostname,
        parsed.port or 443 if https else 80,
        parsed.path,
        payload=payload,
        https=https,
        headers=headers,
        auth=([parsed.username, parsed.password]
              if parsed.username and parsed.password
              else None)
    )


get = partial(request, "GET")
post = partial(request, "POST")
put = partial(request, "PUT")

get_url = partial(request_url, "GET")
post_url = partial(request_url, "POST")
put_url = partial(request_url, "PUT")
