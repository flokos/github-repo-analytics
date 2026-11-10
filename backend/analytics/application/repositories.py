"""Use cases for managing the list of tracked repositories."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone

from analytics.domain.errors import DuplicateRepository, RepositoryNotFound, RepositoryNotPublic
from analytics.domain.repository_url import RepositoryRef, parse_repository_url
from analytics.models import Repository

from .ports import GitHubGateway, RepositoryMetadata

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class AddRepositoryResult:
    repository: Repository
    reactivated: bool


def add_repository(url: str, *, github: GitHubGateway) -> AddRepositoryResult:
    """Start tracking the repository behind `url`.

    The repository is stored only after GitHub has confirmed that it exists and is public.
    A repository that was removed earlier is reactivated, keeping its id and history.
    """
    ref = parse_repository_url(url)

    # Cheap duplicate check first: it saves a GitHub request (and rate limit) in the common case.
    if _find_by_name(ref, active_only=True) is not None:
        raise DuplicateRepository()

    # The GitHub call happens outside any database transaction so that a slow response never
    # holds a connection or a row lock.
    metadata = github.get_repository(ref)
    if not metadata.is_public:
        raise RepositoryNotPublic()

    try:
        with transaction.atomic():
            return _store(metadata)
    except IntegrityError:
        # A concurrent request stored the same repository between our check and our insert.
        raise DuplicateRepository() from None


def deactivate_repository(repository_id: int) -> None:
    """Stop tracking a repository while keeping its snapshots. Safe to repeat."""
    now = timezone.now()
    updated = Repository.objects.filter(pk=repository_id, is_active=True).update(
        is_active=False, deactivated_at=now, updated_at=now
    )
    if updated:
        logger.info("Repository %s removed from active tracking", repository_id)
    elif not Repository.objects.filter(pk=repository_id).exists():
        raise RepositoryNotFound()


def _find_by_name(ref: RepositoryRef, *, active_only: bool = False) -> Repository | None:
    queryset = Repository.objects.filter(owner__iexact=ref.owner, name__iexact=ref.name)
    if active_only:
        queryset = queryset.filter(is_active=True)
    return queryset.first()


def _store(metadata: RepositoryMetadata) -> AddRepositoryResult:
    # Match on GitHub's id as well as the name: after a rename the same repository arrives under
    # a new name and must not be stored twice.
    existing = (
        Repository.objects.select_for_update()
        .filter(
            Q(github_id=metadata.github_id)
            | Q(owner__iexact=metadata.owner, name__iexact=metadata.name)
        )
        .order_by("id")
        .first()
    )

    if existing is None:
        repository = Repository.objects.create(
            github_id=metadata.github_id, owner=metadata.owner, name=metadata.name
        )
        logger.info("Repository %s is now tracked", repository.full_name)
        return AddRepositoryResult(repository=repository, reactivated=False)

    if existing.is_active:
        raise DuplicateRepository()

    existing.github_id = metadata.github_id
    existing.owner = metadata.owner
    existing.name = metadata.name
    existing.is_active = True
    existing.deactivated_at = None
    existing.save()
    logger.info("Repository %s reactivated", existing.full_name)
    return AddRepositoryResult(repository=existing, reactivated=True)
