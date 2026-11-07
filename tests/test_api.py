"""
tests/test_api.py

Integration tests for the FastAPI route layer.

Uses FastAPI's TestClient (which runs the app in-process, no real server needed)
and overrides dependencies so no real HTTP calls go to the external API.

These tests verify:
  - Correct HTTP status codes
  - Response schema matches the declared Pydantic models
  - Error handling returns structured error messages
"""

import pytest
from unittest.mock import AsyncMock, patch
from fastapi.testclient import TestClient
from datetime import datetime, timezone

from app.main import app
from app.api.dependencies import (
    get_api_client,
    get_batch_repository,
    get_bulk_processor_service,
)
from app.repositories.batch_repository import BatchRepository
from app.models.hospital import (
    BulkCreateResponse,
    HospitalResult,
    HospitalProcessingStatus,
    CSVValidationResponse,
    HospitalCSVRow,
)
from app.core.exceptions import CSVValidationError


# ── Helpers ────────────────────────────────────────────────────────────────────

VALID_CSV_CONTENT = b"name,address,phone\nGeneral Hospital,123 Main St,555-1234\n"

MOCK_BULK_RESPONSE = BulkCreateResponse(
    batch_id="test-batch-uuid-1234",
    total_hospitals=1,
    processed_hospitals=1,
    failed_hospitals=0,
    processing_time_seconds=0.5,
    batch_activated=True,
    hospitals=[
        HospitalResult(
            row=1,
            hospital_id=101,
            name="General Hospital",
            status=HospitalProcessingStatus.CREATED_AND_ACTIVATED,
        )
    ],
)


def make_csv_file(content: bytes):
    """Build the files dict for multipart upload in TestClient."""
    return {"file": ("hospitals.csv", content, "text/csv")}


# ── Health endpoints ───────────────────────────────────────────────────────────

class TestHealthEndpoints:

    def test_health_returns_200(self):
        with TestClient(app) as client:
            resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "healthy"

    def test_root_returns_200(self):
        with TestClient(app) as client:
            resp = client.get("/")
        assert resp.status_code == 200
        data = resp.json()
        assert "docs" in data
        assert "dashboard" in data

    def test_dashboard_returns_200(self):
        with TestClient(app) as client:
            resp = client.get("/dashboard")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]
        assert "Hospital Bulk Processor" in resp.text


# ── POST /api/v1/hospitals/bulk ────────────────────────────────────────────────

class TestBulkCreateEndpoint:

    def test_valid_csv_returns_201(self):
        """Happy path: valid CSV → 201 with batch details."""
        mock_service = AsyncMock()
        mock_service.process_csv.return_value = MOCK_BULK_RESPONSE

        with TestClient(app) as client:
            app.dependency_overrides[get_bulk_processor_service] = lambda: mock_service
            resp = client.post(
                "/api/v1/hospitals/bulk",
                files=make_csv_file(VALID_CSV_CONTENT),
            )
            app.dependency_overrides.clear()

        assert resp.status_code == 201
        data = resp.json()
        assert data["batch_id"] == "test-batch-uuid-1234"
        assert data["total_hospitals"] == 1
        assert data["batch_activated"] is True
        assert len(data["hospitals"]) == 1

    def test_csv_validation_error_returns_422(self):
        """Invalid CSV → 422 Unprocessable Entity."""
        mock_service = AsyncMock()
        mock_service.process_csv.side_effect = CSVValidationError("Missing required column: address")

        with TestClient(app) as client:
            app.dependency_overrides[get_bulk_processor_service] = lambda: mock_service
            resp = client.post(
                "/api/v1/hospitals/bulk",
                files=make_csv_file(b"wrong,columns\nfoo,bar"),
            )
            app.dependency_overrides.clear()

        assert resp.status_code == 422
        assert "address" in resp.json()["detail"]

    def test_empty_file_returns_400(self):
        """Empty file → 400 Bad Request."""
        with TestClient(app) as client:
            resp = client.post(
                "/api/v1/hospitals/bulk",
                files=make_csv_file(b""),
            )
        assert resp.status_code == 400

    def test_no_file_returns_422(self):
        """Missing file field → 422 from FastAPI validation."""
        with TestClient(app) as client:
            resp = client.post("/api/v1/hospitals/bulk")
        assert resp.status_code == 422

    def test_response_schema_matches_model(self):
        """Response keys match the BulkCreateResponse model fields."""
        mock_service = AsyncMock()
        mock_service.process_csv.return_value = MOCK_BULK_RESPONSE

        with TestClient(app) as client:
            app.dependency_overrides[get_bulk_processor_service] = lambda: mock_service
            resp = client.post(
                "/api/v1/hospitals/bulk",
                files=make_csv_file(VALID_CSV_CONTENT),
            )
            app.dependency_overrides.clear()

        data = resp.json()
        required_keys = {
            "batch_id", "total_hospitals", "processed_hospitals",
            "failed_hospitals", "processing_time_seconds",
            "batch_activated", "hospitals",
        }
        assert required_keys.issubset(data.keys())


# ── POST /api/v1/hospitals/bulk/validate ──────────────────────────────────────

class TestValidateCSVEndpoint:

    def test_valid_csv_returns_valid_true(self):
        with TestClient(app) as client:
            resp = client.post(
                "/api/v1/hospitals/bulk/validate",
                files=make_csv_file(VALID_CSV_CONTENT),
            )
        assert resp.status_code == 200
        assert resp.json()["valid"] is True
        assert resp.json()["errors"] == []

    def test_invalid_csv_returns_valid_false(self):
        with TestClient(app) as client:
            resp = client.post(
                "/api/v1/hospitals/bulk/validate",
                files=make_csv_file(b"wrong_col,another\nfoo,bar"),
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["valid"] is False
        assert len(data["errors"]) > 0

    def test_empty_file_returns_error(self):
        with TestClient(app) as client:
            resp = client.post(
                "/api/v1/hospitals/bulk/validate",
                files=make_csv_file(b""),
            )
        assert resp.status_code == 200
        assert resp.json()["valid"] is False


# ── GET /api/v1/hospitals/bulk/{batch_id}/progress ────────────────────────────

class TestProgressEndpoint:

    def test_nonexistent_batch_returns_404(self):
        with TestClient(app) as client:
            resp = client.get("/api/v1/hospitals/bulk/nonexistent-id/progress")
        assert resp.status_code == 404

    def test_existing_batch_returns_progress(self):
        """
        Register a batch directly in the repository, then poll its progress.
        Verifies the full read-path without needing to process a CSV.
        """
        repo = BatchRepository()
        repo.create_batch("test-batch-999", total_hospitals=5)

        with TestClient(app) as client:
            app.dependency_overrides[get_batch_repository] = lambda: repo
            resp = client.get("/api/v1/hospitals/bulk/test-batch-999/progress")
            app.dependency_overrides.clear()

        assert resp.status_code == 200
        data = resp.json()
        assert data["batch_id"] == "test-batch-999"
        assert data["total_hospitals"] == 5
        assert data["progress_percent"] == 0.0


# ── GET /api/v1/hospitals/bulk ────────────────────────────────────────────────

class TestListBatchesEndpoint:

    def test_empty_store_returns_empty_list(self):
        repo = BatchRepository()
        with TestClient(app) as client:
            app.dependency_overrides[get_batch_repository] = lambda: repo
            resp = client.get("/api/v1/hospitals/bulk")
            app.dependency_overrides.clear()

        assert resp.status_code == 200
        assert resp.json() == []

    def test_returns_all_batches(self):
        repo = BatchRepository()
        repo.create_batch("b1", 3)
        repo.create_batch("b2", 7)

        with TestClient(app) as client:
            app.dependency_overrides[get_batch_repository] = lambda: repo
            resp = client.get("/api/v1/hospitals/bulk")
            app.dependency_overrides.clear()

        assert resp.status_code == 200
        assert len(resp.json()) == 2


# ── DELETE /api/v1/hospitals/batch/{batch_id} ────────────────────────────────────────

class TestDeleteBatchEndpoint:

    def test_delete_batch_success(self):
        mock_api = AsyncMock()
        mock_api.delete_batch.return_value = True

        repo = BatchRepository()
        repo.create_batch("batch-to-delete", 3)

        with TestClient(app) as client:
            app.dependency_overrides[get_api_client] = lambda: mock_api
            app.dependency_overrides[get_batch_repository] = lambda: repo
            resp = client.delete("/api/v1/hospitals/batch/batch-to-delete")
            app.dependency_overrides.clear()

        assert resp.status_code == 200
        assert "successfully deleted" in resp.json()["message"]
        assert repo.get_batch("batch-to-delete") is None
        mock_api.delete_batch.assert_called_once_with("batch-to-delete")


