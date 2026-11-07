"""
app/api/dependencies.py

FastAPI dependency injection providers.

Dependency Injection Pattern: Instead of hard-coding service instances
inside route handlers, we declare them here and FastAPI injects them
automatically. This makes routes thin and testable — in tests you can
swap any dependency with a mock via app.dependency_overrides.
"""

from app.repositories.batch_repository import batch_repository, BatchRepository
from app.services.hospital_api_client import HospitalAPIClient, get_http_client
from app.services.bulk_processor import BulkProcessorService


def get_batch_repository() -> BatchRepository:
    """Provide the singleton batch repository."""
    return batch_repository


def get_api_client() -> HospitalAPIClient:
    """Provide an API client backed by the shared HTTP connection pool."""
    return HospitalAPIClient(client=get_http_client())


def get_bulk_processor_service() -> BulkProcessorService:
    """
    Compose the BulkProcessorService with its dependencies.

    This is the composition root for the bulk processing feature —
    the only place that knows how all pieces fit together.
    """
    return BulkProcessorService(
        api_client=get_api_client(),
        repository=get_batch_repository(),
    )

