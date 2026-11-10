"""Parsing of user-supplied GitHub repository URLs.

The URL is only ever parsed, never fetched. Its owner and name are extracted, checked against
GitHub's naming rules, and later used to build a request to the fixed GitHub API host. That is the
whole of the server-side request forgery defence: user input can choose two path segments from a
restricted alphabet and nothing else.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlsplit

from .errors import InvalidRepositoryUrl

SUPPORTED_HOSTS = frozenset({"github.com", "www.github.com"})
MAX_URL_LENGTH = 200

# GitHub logins: 1 to 39 letters, digits or single hyphens, never starting or ending with a hyphen.
_OWNER = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9]|-(?=[A-Za-z0-9])){0,38}")
# Repository names: 1 to 100 letters, digits, dots, hyphens or underscores.
_NAME = re.compile(r"[A-Za-z0-9._-]{1,100}")
_RESERVED_NAMES = frozenset({".", ".."})


@dataclass(frozen=True, slots=True)
class RepositoryRef:
    """A syntactically valid `owner/name` pair; it may or may not exist on GitHub."""

    owner: str
    name: str

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.name}"


def is_valid_owner(value: str) -> bool:
    return _OWNER.fullmatch(value) is not None


def is_valid_name(value: str) -> bool:
    return _NAME.fullmatch(value) is not None and value not in _RESERVED_NAMES


def parse_repository_url(raw: str) -> RepositoryRef:
    """Return the repository a URL points at, or raise `InvalidRepositoryUrl`.

    Accepted: `https://github.com/<owner>/<name>`, optionally with `www.`, a trailing slash or a
    `.git` suffix. Everything else is rejected, including other hosts, other schemes, credentials,
    ports, query strings, fragments and deeper paths such as `/tree/main`.
    """
    if not isinstance(raw, str):
        raise InvalidRepositoryUrl()

    candidate = raw.strip()
    if not candidate or len(candidate) > MAX_URL_LENGTH:
        raise InvalidRepositoryUrl()
    # urlsplit silently drops tabs and newlines, so refuse them before parsing.
    if any(char.isspace() or not char.isprintable() for char in candidate):
        raise InvalidRepositoryUrl()

    try:
        parts = urlsplit(candidate)
        port = parts.port
    except ValueError:
        raise InvalidRepositoryUrl() from None

    if parts.scheme.lower() != "https":
        raise InvalidRepositoryUrl("The address must start with https://github.com/.")
    if parts.username is not None or parts.password is not None or port not in (None, 443):
        raise InvalidRepositoryUrl()
    if (parts.hostname or "") not in SUPPORTED_HOSTS:
        raise InvalidRepositoryUrl("Only github.com repository addresses are supported.")
    if parts.query or parts.fragment:
        raise InvalidRepositoryUrl(
            "Use the repository's main page address, without anything after the repository name."
        )

    # Exactly "/owner/name", with one optional trailing slash.
    segments = parts.path.removesuffix("/").split("/")
    if len(segments) != 3 or segments[0] != "":
        raise InvalidRepositoryUrl(
            "Use the repository's main page address, for example "
            "https://github.com/owner/repository."
        )

    _, owner, name = segments
    name = name.removesuffix(".git")
    if not is_valid_owner(owner) or not is_valid_name(name):
        raise InvalidRepositoryUrl()

    return RepositoryRef(owner=owner, name=name)
