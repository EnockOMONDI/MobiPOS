#!/usr/bin/env python3
"""Run a read-only smoke journey against a deployed MobiPOS web service."""

from http.cookiejar import CookieJar
from html.parser import HTMLParser
import os
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urljoin, urlparse
from urllib.request import HTTPCookieProcessor, Request, build_opener


class CSRFParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.token = ""

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "input" and attributes.get("name") == "csrfmiddlewaretoken":
            self.token = attributes.get("value", "")


def fail(message):
    print(f"SMOKE FAILED: {message}", file=sys.stderr)
    raise SystemExit(1)


def fetch(opener, url, *, data=None):
    request = Request(
        url,
        data=data,
        headers={"User-Agent": "MobiPOS-Render-Smoke/1.0"},
    )
    try:
        return opener.open(request, timeout=20)
    except (HTTPError, URLError, TimeoutError) as error:
        fail(f"{url} could not be reached: {error}")


def main():
    base_url = os.environ.get("SMOKE_BASE_URL", "").rstrip("/")
    email = os.environ.get("SMOKE_TEST_EMAIL", "")
    password = os.environ.get("SMOKE_TEST_PASSWORD", "")
    if not all((base_url, email, password)):
        fail("Set SMOKE_BASE_URL, SMOKE_TEST_EMAIL and SMOKE_TEST_PASSWORD.")
    if urlparse(base_url).scheme != "https":
        fail("SMOKE_BASE_URL must use HTTPS.")

    opener = build_opener(HTTPCookieProcessor(CookieJar()))
    ready = fetch(opener, urljoin(f"{base_url}/", "health/ready/"))
    if ready.status != 200 or b'"status": "ok"' not in ready.read():
        fail("Readiness check did not return an OK database status.")

    login_url = urljoin(f"{base_url}/", "accounts/login/")
    login_page = fetch(opener, login_url)
    parser = CSRFParser()
    parser.feed(login_page.read().decode("utf-8", errors="replace"))
    if not parser.token:
        fail("Login page did not provide a CSRF token.")

    login_response = fetch(
        opener,
        login_url,
        data=urlencode({
            "csrfmiddlewaretoken": parser.token,
            "username": email,
            "password": password,
            "next": "/",
        }).encode("utf-8"),
    )
    if "/accounts/login/" in login_response.geturl():
        fail("Email login was rejected.")
    dashboard = login_response.read()
    if login_response.status != 200 or b"MobiPOS" not in dashboard:
        fail("Authenticated dashboard did not render correctly.")

    help_page = fetch(opener, urljoin(f"{base_url}/", "help/"))
    if help_page.status != 200 or b"Help" not in help_page.read():
        fail("Authenticated Help route did not render correctly.")

    print("SMOKE PASSED: readiness, email login, dashboard and Help route are healthy.")


if __name__ == "__main__":
    main()
