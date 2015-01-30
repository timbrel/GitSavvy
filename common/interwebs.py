import http.client
import json
from functools import partial
from collections import namedtuple

Response = namedtuple("Response", ("payload", "headers", "status", "is_json"))


def request(verb, host, port, path, payload=None, timeout=10, https=False, headers=None):
    if not headers:
        headers = {}
    headers["User-Agent"] = "GitGadget Sublime Plug-in"

    connection = (http.client.HTTPSConnection(host, port)
                  if https
                  else http.client.HTTPConnection(host, port))
    connection.request(verb, path, body=payload, headers=headers)

    response = connection.getresponse()
    payload = response.read()
    headers = dict(response.getheaders())
    status = response.status

    is_json = "application/json" in headers["Content-Type"]
    if is_json:
        payload = json.loads(payload.decode("utf-8"))

    response.close()
    connection.close()

    return Response(payload, headers, status, is_json)


get = partial(request, "GET")
post = partial(request, "POST")
put = partial(request, "PUT")
