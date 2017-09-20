"""
A simple HTTP interface for making GET, PUT and POST requests.
"""

import http.client
import json
from urllib.parse import urlparse, urlencode  # NOQA
from base64 import b64encode
from functools import partial
from collections import namedtuple

Response = namedtuple("Response", ("payload", "headers", "status", "is_json"))


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
        username_password = "{}:{}".format(*auth).encode("ascii")
        headers["Authorization"] = "Basic {}".format(b64encode(username_password).decode("ascii"))

    connection = (http.client.HTTPSConnection(host, port)
                  if https
                  else http.client.HTTPConnection(host, port))
    connection.request(verb, path, body=payload, headers=headers)

    response = connection.getresponse()
    response_payload = response.read()
    response_headers = dict(response.getheaders())
    status = response.status

    is_json = "application/json" in response_headers["Content-Type"]
    if is_json:
        response_payload = json.loads(response_payload.decode("utf-8"))

    response.close()
    connection.close()

    if redirect and verb == "GET" and status == 301 or status == 302:
        return request_url(
            verb,
            response_headers["Location"],
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
