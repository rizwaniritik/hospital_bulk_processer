"""
tests/test_bulk_processor.py

Unit tests for BulkProcessorService using mocked dependencies.

No real HTTP calls are made — the API client is replaced with an
AsyncMock that returns controlled responses. This lets us test all
code paths (happy path, partial failure, full failure, activation error)
without needing the external service to be running.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone

from app.services.bulk_processor import BulkProcessorService
from app.models.hospital import (
    HospitalAPIResponse,
    HospitalProcessingStatus,
    BatchStatus,
)
from app.repositories.batch_repository import BatchRepository
from app.core.exceptions import HospitalAPIError, BatchActivationError, CSVValidationError


# ── Fixtures ───────────────────────────────────────────────────────────────────

def make_api_response(hospital_id: int, name: str, batch_id: str) -> HospitalAPIResponse:
    """Helper to build a fake HospitalAPIResponse."""
    return HospitalAPIResponse(
        id=hospital_id,
        name=name,
        address="123 Test St",
        phone=None,
        creation_batch_id=batch_id,
        active=False,
        created_at=datetime.now(timezone.utc),
    )


def make_service(api_client, repository=None) -> BulkProcessorService:
    """Build a BulkProcessorService with the given mock API client."""
    if repository is None:
        repository = BatchRepository()
    return BulkProcessorService(api_client=api_client, repository=repository)


VALID_CSV = b"name,address,phone\nGeneral Hospital,123 Main St,555-1234\nCity Clinic,456 Oak Ave,"
TWO_ROW_CSV = b"name,address\nHospital A,Addr A\nHospital B,Addr B"
INVALID_CSV = b"wrong,columns\nfoo,bar"


# ── Happy path ─────────────────────────────────────────────────────────────────

class TestBulkProcessorHappyPath:

    @pytest.mark.asyncio
    async def test_all_hospitals_created_and_activated(self):
        """Full success: every hospital is created and the batch is activated."""
        mock_api = AsyncMock()
        mock_api.create_hospital.side_effect = [
            make_api_response(1, "General Hospital", "batch-1"),
            make_api_response(2, "City Clinic", "batch-1"),
        ]
        mock_api.activate_batch.return_value = True

        service = make_service(mock_api)
        result = await service.process_csv(VALID_CSV)

        assert result.total_hospitals == 2
        assert result.processed_hospitals == 2
        assert result.failed_hospitals == 0
        assert result.batch_activated is True
        assert len(result.hospitals) == 2
        assert all(
            h.status == HospitalProcessingStatus.CREATED_AND_ACTIVATED
            for h in result.hospitals
        )

    @pytest.mark.asyncio
    async def test_batch_id_is_uuid(self):
        """batch_id in the response should be a valid UUID string."""
        import uuid
        mock_api = AsyncMock()
        mock_api.create_hospital.return_value = make_api_response(1, "H", "b")
        mock_api.activate_batch.return_value = True

        service = make_service(mock_api)
        result = await service.process_csv(b"name,address\nH,A")

        # Should not raise
        uuid.UUID(result.batch_id)

    @pytest.mark.asyncio
    async def test_processing_time_is_positive(self):
        mock_api = AsyncMock()
        mock_api.create_hospital.return_value = make_api_response(1, "H", "b")
        mock_api.activate_batch.return_value = True

        service = make_service(mock_api)
        result = await service.process_csv(b"name,address\nH,A")

        assert result.processing_time_seconds >= 0


# ── Partial failure ────────────────────────────────────────────────────────────

class TestBulkProcessorPartialFailure:

    @pytest.mark.asyncio
    async def test_one_hospital_fails_rest_succeed(self):
        """
        If some hospitals fail, the batch should still continue,
        activate the successful ones, and report failures accurately.
        """
        mock_api = AsyncMock()
        mock_api.create_hospital.side_effect = [
            make_api_response(1, "Hospital A", "batch-x"),
            HospitalAPIError("Server error", status_code=500),
        ]
        mock_api.activate_batch.return_value = True

        service = make_service(mock_api)
        result = await service.process_csv(TWO_ROW_CSV)

        assert result.total_hospitals == 2
        assert result.processed_hospitals == 1
        assert result.failed_hospitals == 1
        assert result.batch_activated is True

        statuses = {h.name: h.status for h in result.hospitals}
        assert statuses["Hospital A"] == HospitalProcessingStatus.CREATED_AND_ACTIVATED
        assert statuses["Hospital B"] == HospitalProcessingStatus.FAILED

    @pytest.mark.asyncio
    async def test_failed_hospital_has_error_message(self):
        mock_api = AsyncMock()
        mock_api.create_hospital.side_effect = HospitalAPIError("Timeout")
        mock_api.activate_batch.return_value = True

        service = make_service(mock_api)
        result = await service.process_csv(b"name,address\nH,A")

        assert result.hospitals[0].status == HospitalProcessingStatus.FAILED
        assert result.hospitals[0].error is not None


# ── Activation failure ─────────────────────────────────────────────────────────

class TestBulkProcessorActivationFailure:

    @pytest.mark.asyncio
    async def test_activation_fails_hospitals_still_created(self):
        """
        If batch activation fails, we should NOT raise — hospitals were created.
        batch_activated should be False.
        """
        mock_api = AsyncMock()
        mock_api.create_hospital.return_value = make_api_response(1, "H", "b")
        mock_api.activate_batch.side_effect = BatchActivationError("Activation failed")

        service = make_service(mock_api)
        result = await service.process_csv(b"name,address\nH,A")

        assert result.processed_hospitals == 1
        assert result.batch_activated is False


# ── CSV validation ─────────────────────────────────────────────────────────────

class TestBulkProcessorCSVValidation:

    @pytest.mark.asyncio
    async def test_invalid_csv_raises_before_api_calls(self):
        mock_api = AsyncMock()
        service = make_service(mock_api)

        with pytest.raises(CSVValidationError):
            await service.process_csv(INVALID_CSV)

        # API should never be called if CSV is invalid
        mock_api.create_hospital.assert_not_called()

    @pytest.mark.asyncio
    async def test_empty_csv_raises(self):
        mock_api = AsyncMock()
        service = make_service(mock_api)

        with pytest.raises(CSVValidationError):
            await service.process_csv(b"")


# ── Repository integration ─────────────────────────────────────────────────────

class TestBulkProcessorRepositoryIntegration:

    @pytest.mark.asyncio
    async def test_batch_is_stored_in_repository(self):
        """After processing, the batch should be queryable from the repository."""
        mock_api = AsyncMock()
        mock_api.create_hospital.return_value = make_api_response(1, "H", "b")
        mock_api.activate_batch.return_value = True

        repo = BatchRepository()
        service = BulkProcessorService(api_client=mock_api, repository=repo)
        result = await service.process_csv(b"name,address\nH,A")

        record = repo.get_batch(result.batch_id)
        assert record is not None
        assert record.processed_hospitals == 1

    @pytest.mark.asyncio
    async def test_batch_status_is_completed_on_success(self):
        mock_api = AsyncMock()
        mock_api.create_hospital.return_value = make_api_response(1, "H", "b")
        mock_api.activate_batch.return_value = True

        repo = BatchRepository()
        service = BulkProcessorService(api_client=mock_api, repository=repo)
        result = await service.process_csv(b"name,address\nH,A")

        record = repo.get_batch(result.batch_id)
        assert record.status == BatchStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_batch_status_is_partially_failed(self):
        mock_api = AsyncMock()
        mock_api.create_hospital.side_effect = [
            make_api_response(1, "Hospital A", "b"),
            HospitalAPIError("Error"),
        ]
        mock_api.activate_batch.return_value = True

        repo = BatchRepository()
        service = BulkProcessorService(api_client=mock_api, repository=repo)
        result = await service.process_csv(TWO_ROW_CSV)

        record = repo.get_batch(result.batch_id)
        assert record.status == BatchStatus.PARTIALLY_FAILED

