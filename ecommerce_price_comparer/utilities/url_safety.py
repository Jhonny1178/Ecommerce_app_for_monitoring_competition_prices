import ipaddress
import socket
from urllib.parse import urljoin, urlparse

import requests

MAX_REDIRECTS = 5


class BlockedUrlError(requests.RequestException):
    pass


def is_public_url(url):
    try:
        parsed = urlparse(url)
        hostname = parsed.hostname
    except ValueError:
        return False

    if parsed.scheme not in ("http", "https") or not hostname:
        return False

    if any(ord(char) <= 32 or ord(char) == 127 for char in hostname):
        return False

    try:
        infos = socket.getaddrinfo(hostname, None)
    except socket.gaierror:
        return True
    except ValueError:
        return False

    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global or ip.is_multicast:
            return False

    return True


def safe_request(method, url, **kwargs):
    kwargs.pop("allow_redirects", None)

    for _ in range(MAX_REDIRECTS + 1):
        if not is_public_url(url):
            raise BlockedUrlError(f"Niedozwolony adres: {url}")

        response = requests.request(method, url, allow_redirects=False, **kwargs)
        if not response.is_redirect:
            return response

        url = urljoin(url, response.headers["Location"])
        response.close()

    raise BlockedUrlError(f"Zbyt wiele przekierowań: {url}")


def safe_get(url, **kwargs):
    return safe_request("GET", url, **kwargs)


def safe_head(url, **kwargs):
    return safe_request("HEAD", url, **kwargs)
