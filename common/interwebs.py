"""
A simple HTTP interface for making GET, PUT and POST requests.
"""

import http.client
import json
from base64 import b64encode
from functools import partial
from collections import namedtuple

Response = namedtuple("Response", ("payload", "headers", "status", "is_json"))


def request(verb, host, port, path, payload=None, https=False, headers=None, auth=None):
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
    headers = dict(response.getheaders())
    status = response.status

    is_json = "application/json" in headers["Content-Type"]
    if is_json:
        response_payload = json.loads(response_payload.decode("utf-8"))

    response.close()
    connection.close()

    return Response(response_payload, headers, status, is_json)


get = partial(request, "GET")
post = partial(request, "POST")
put = partial(request, "PUT")
