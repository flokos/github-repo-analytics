"""URL parsing is the first line of the SSRF defence, so every case is spelled out."""

import pytest

from analytics.domain.errors import InvalidRepositoryUrl
from analytics.domain.repository_url import RepositoryRef, parse_repository_url


@pytest.mark.parametrize(
    ("url", "owner", "name"),
    [
        ("https://github.com/django/django", "django", "django"),
        ("https://github.com/django/django/", "django", "django"),
        ("https://github.com/django/django.git", "django", "django"),
        ("https://www.github.com/apache/airflow", "apache", "airflow"),
        ("HTTPS://GitHub.com/Django/Django", "Django", "Django"),
        ("  https://github.com/vuejs/core  ", "vuejs", "core"),
        ("https://github.com:443/vuejs/core", "vuejs", "core"),
        ("https://github.com/my-org/.github", "my-org", ".github"),
        ("https://github.com/a/b.c_d-e", "a", "b.c_d-e"),
    ],
)
def test_accepts_repository_root_urls(url, owner, name):
    assert parse_repository_url(url) == RepositoryRef(owner=owner, name=name)


@pytest.mark.parametrize(
    "url",
    [
        # Not a URL at all
        "",
        "   ",
        "django/django",
        "github.com/django/django",
        # Wrong scheme
        "http://github.com/django/django",
        "ftp://github.com/django/django",
        "file:///etc/passwd",
        # Wrong host, including look-alikes
        "https://gitlab.com/django/django",
        "https://api.github.com/repos/django/django",
        "https://gist.github.com/django/django",
        "https://raw.githubusercontent.com/django/django",
        "https://github.com.evil.example/django/django",
        "https://evil.example/github.com/django/django",
        "https://github.com./django/django",
        "https://127.0.0.1/django/django",
        "https://[::1]/django/django",
        # Credentials and ports
        "https://user:secret@github.com/django/django",
        "https://github.com@evil.example/django/django",
        "https://github.com:8443/django/django",
        "https://github.com:notaport/django/django",
        # Not exactly owner/name
        "https://github.com",
        "https://github.com/",
        "https://github.com/django",
        "https://github.com/django/django/tree/main",
        "https://github.com//django/django",
        # Query strings and fragments
        "https://github.com/django/django?tab=readme-ov-file",
        "https://github.com/django/django#readme",
        # Characters outside GitHub's naming rules
        "https://github.com/-django/django",
        "https://github.com/django-/django",
        "https://github.com/dj--ango/django",
        "https://github.com/django/dj ango",
        "https://github.com/django/..",
        "https://github.com/django/.git",
        "https://github.com/django/%2e%2e",
        "https://github.com/dj\tango/django",
        "https://github.com/django/dja\nngo",
        "https://github.com/django/django\x00",
        # Too long
        "https://github.com/" + "a" * 40 + "/repository",
        "https://github.com/owner/" + "r" * 101,
        "https://github.com/owner/repository" + "/" * 200,
    ],
)
def test_rejects_everything_else(url):
    with pytest.raises(InvalidRepositoryUrl):
        parse_repository_url(url)


@pytest.mark.parametrize("value", [None, 42, b"https://github.com/django/django", ["x"]])
def test_rejects_non_string_input(value):
    with pytest.raises(InvalidRepositoryUrl):
        parse_repository_url(value)
