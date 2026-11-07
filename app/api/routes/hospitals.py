"""
app/api/routes/hospitals.py

FastAPI route handlers for all hospital bulk-processing endpoints.

Open/Closed Principle (OCP): Routes are open for extension (add new endpoints)
but closed for modification (existing routes don't change when new features land).

Each route is thin by design:
  - Validate input  (FastAPI / Pydantic handle most of this automatically)
  - Call the appropriate service method
  - Return the result

No business logic lives here. That's the service layer's job.
"""

from __future__ import annotations

import logging
from typing import List

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status

from app.api.dependencies import (
    get_api_client,
    get_batch_repository,
    get_bulk_processor_service,
)
from app.services.hospital_api_client import HospitalAPIClient
from app.core.exceptions import (
    BatchNotFoundError,
    CSVValidationError,
    HospitalAPIError,
)
from app.models.hospital import (
    BatchProgressResponse,
    BatchStatus,
    BatchSummary,
    BulkCreateResponse,
    CSVValidationResponse,
)
from app.repositories.batch_repository import BatchRepository
from app.services.bulk_processor import BulkProcessorService
from app.utils.csv_parser import dry_run_validate_csv

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/hospitals", tags=["Hospitals"])


# ── POST /hospitals/bulk ───────────────────────────────────────────────────────

@router.post(
    "/bulk",
    response_model=BulkCreateResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Bulk create hospitals from CSV",
    description=(
        "Upload a CSV file (columns: name, address, phone[optional]) "
        "to create multiple hospitals at once. "
        "Maximum 20 hospitals per request. "
        "Hospitals are created on the external Hospital Directory API and activated "
        "automatically once all records are processed."
    ),
)
async def bulk_create_hospitals(
    file: UploadFile = File(..., description="CSV file with hospital records"),
    service: BulkProcessorService = Depends(get_bulk_processor_service),
) -> BulkCreateResponse:
    """
    Main endpoint: accepts a CSV upload and processes all hospitals in bulk.

    Steps (handled internally by BulkProcessorService):
    1. Parse and validate the CSV
    2. Generate a unique batch ID
    3. Create each hospital via the external API (concurrently)
    4. Activate the batch
    5. Return detailed results
    """
    # Basic content-type guard (not a hard block — some clients send wrong MIME)
    if file.content_type and "csv" not in file.content_type and "text" not in file.content_type:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Please upload a CSV file (text/csv or text/plain).",
        )

    file_content = await file.read()

    if not file_content:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Uploaded file is empty.",
        )

    try:
        result = await service.process_csv(file_content)
    except CSVValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    except HospitalAPIError as exc:
        logger.error("External API error during bulk create: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"External Hospital API error: {exc}",
        ) from exc
    except Exception as exc:
        logger.exception("Unexpected error during bulk create")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred. Please try again.",
        ) from exc

    return result


# ── POST /hospitals/bulk/validate ──────────────────────────────────────────────

@router.post(
    "/bulk/validate",
    response_model=CSVValidationResponse,
    summary="Validate a CSV file before processing",
    description=(
        "Dry-run endpoint: validates the CSV format and content without "
        "creating any hospitals. Returns a list of errors (if any) and "
        "a preview of the first 5 rows."
    ),
)
async def validate_csv(
    file: UploadFile = File(..., description="CSV file to validate"),
) -> CSVValidationResponse:
    """
    Pre-validation endpoint (Bonus feature).
    Safe to call multiple times — no side effects.
    """
    file_content = await file.read()

    if not file_content:
        return CSVValidationResponse(
            valid=False,
            total_rows=0,
            errors=["Uploaded file is empty."],
        )

    return dry_run_validate_csv(file_content)


# ── GET /hospitals/bulk/{batch_id}/progress ────────────────────────────────────

@router.get(
    "/bulk/{batch_id}/progress",
    response_model=BatchProgressResponse,
    summary="Poll batch processing progress",
    description=(
        "Returns real-time progress for a running or completed batch. "
        "Poll this endpoint periodically to show a progress bar in your UI."
    ),
)
async def get_batch_progress(
    batch_id: str,
    repository: BatchRepository = Depends(get_batch_repository),
) -> BatchProgressResponse:
    """
    Progress-tracking endpoint (Bonus feature).
    Returns current counts and percentage without blocking.
    """
    record = repository.get_batch(batch_id)
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Batch '{batch_id}' not found.",
        )

    return BatchProgressResponse(
        batch_id=record.batch_id,
        status=record.status,
        total_hospitals=record.total_hospitals,
        processed_hospitals=record.processed_hospitals,
        failed_hospitals=record.failed_hospitals,
        progress_percent=record.progress_percent,
        batch_activated=record.batch_activated,
        started_at=record.started_at,
        completed_at=record.completed_at,
    )


# ── GET /hospitals/bulk ────────────────────────────────────────────────────────

@router.get(
    "/bulk",
    response_model=List[BatchSummary],
    summary="List all batches",
    description="Returns a summary of every bulk-processing batch that has been submitted.",
)
async def list_batches(
    repository: BatchRepository = Depends(get_batch_repository),
) -> List[BatchSummary]:
    """Return lightweight summaries of all known batches."""
    return repository.list_batches()


# ── DELETE /hospitals/batch/{batch_id} ─────────────────────────────────────────

@router.delete(
    "/batch/{batch_id}",
    summary="Delete all hospitals in a batch",
    description=(
        "Deletes all hospitals belonging to the specified batch ID from both "
        "the external Hospital Directory API and the local repository."
    ),
)
@router.delete(
    "/bulk/{batch_id}",
    include_in_schema=False,
)
async def delete_batch(
    batch_id: str,
    api_client: HospitalAPIClient = Depends(get_api_client),
    repository: BatchRepository = Depends(get_batch_repository),
):
    """Delete a batch and all its associated hospitals."""
    try:
        await api_client.delete_batch(batch_id)
    except HospitalAPIError as exc:
        logger.error("Failed to delete batch '%s' on external API: %s", batch_id, exc)
        raise HTTPException(
            status_code=exc.status_code or status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to delete batch on external API: {exc}",
        ) from exc

    # Clean up from local repository as well
    repository.delete_batch(batch_id)

    return {
        "message": f"Batch '{batch_id}' successfully deleted.",
        "batch_id": batch_id,
    }


