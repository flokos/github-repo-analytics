"""Failures the pipeline can run into, split by whether retrying can help.

The DAG's retry policy looks only at the two base classes: a `PermanentError` fails the task
immediately, anything else goes through Airflow's normal retries with exponential backoff.
"""


class PipelineError(Exception):
    """Base class for every failure the pipeline recognises."""


class PermanentError(PipelineError):
    """Retrying cannot change the outcome; a person has to act."""


class TransientError(PipelineError):
    """The same call may well succeed a little later."""


# --- GitHub ---------------------------------------------------------------------------------------
class RepositoryUnavailableError(PermanentError):
    """GitHub no longer shows the repository: deleted, made private, or access blocked."""


class GitHubAuthError(PermanentError):
    """GitHub rejected the configured token."""


class RateLimitExhaustedError(PermanentError):
    """The rate limit resets later than the task's retries can wait for."""


class RateLimitedError(TransientError):
    """The rate limit was hit but resets soon."""


class GitHubUnavailableError(TransientError):
    """Timeout, connection failure, server error or an unreadable response."""


# --- Collected metrics ----------------------------------------------------------------------------
class InvalidMetricsError(PermanentError):
    """The fetched metrics are unusable; fetching them again would return the same thing."""


# --- Django internal API --------------------------------------------------------------------------
class InternalApiAuthError(PermanentError):
    """Django rejected the internal API token."""


class InternalApiRejectedError(PermanentError):
    """Django refused the request as invalid."""


class InternalApiUnavailableError(TransientError):
    """Django did not answer, or answered with a server error."""
