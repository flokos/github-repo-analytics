"""Expected failures, named in business terms.

Use cases and adapters raise these; the API layer is the only place that knows which HTTP status
each one becomes (see `analytics.api.exception_handler`).
"""


class AnalyticsError(Exception):
    """Base class for every expected, explainable failure."""

    default_message = "The request could not be completed."

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message or self.default_message)

    @property
    def message(self) -> str:
        return str(self)


# --- Input ----------------------------------------------------------------------------------------
class InvalidRepositoryUrl(AnalyticsError):
    default_message = (
        "Enter the address of a GitHub repository, for example https://github.com/owner/repository."
    )


# --- Tracked repositories -------------------------------------------------------------------------
class DuplicateRepository(AnalyticsError):
    default_message = "This repository is already being tracked."


class RepositoryNotFound(AnalyticsError):
    default_message = "No tracked repository has this id."


# --- What GitHub told us about a repository -------------------------------------------------------
class RepositoryUnavailable(AnalyticsError):
    # GitHub answers 404 both for repositories that do not exist and for private ones the caller
    # cannot see, so the message must not claim either.
    default_message = (
        "GitHub reports this repository as unavailable. It may not exist, or it may be private."
    )


class RepositoryNotPublic(AnalyticsError):
    default_message = "Only public repositories can be tracked."


class RepositoryAccessDenied(AnalyticsError):
    default_message = "GitHub denied access to this repository."


# --- GitHub itself --------------------------------------------------------------------------------
class GitHubRateLimited(AnalyticsError):
    default_message = "GitHub's API rate limit has been reached. Try again later."

    def __init__(self, message: str | None = None, *, retry_after: int | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class GitHubTimeout(AnalyticsError):
    default_message = "GitHub did not answer in time. Try again in a moment."


class GitHubUnavailable(AnalyticsError):
    default_message = "GitHub is temporarily unavailable. Try again in a moment."
