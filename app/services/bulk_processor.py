"""
app/services/bulk_processor.py

Core business logic for bulk hospital processing.

This is the heart of the application.  It orchestrates:
  1. Parsing the CSV
  2. Creating hospitals concurrently via the external API
  3. Activating the batch once all hospitals are created
  4. Persisting progress so callers can poll for real-time updates

Design patterns used here:
  - Strategy / Template Method: The processing steps are distinct and
    testable in isolation.
  - Concurrency via asyncio.Semaphore: Limits parallel API calls to
    avoid overwhelming the external service (back-pressure).
  - Observer-like progress: Results are written to the repository as
    each hospital completes, so the polling endpoint always reflects
    the latest state without locking the whole batch.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import List

from app.core.config import get_settings
from app.core.exceptions import CSVValidationError, HospitalAPIError, BatchActivationError
from app.models.hospital import (
    BulkCreateResponse,
    HospitalCSVRow,
    HospitalProcessingStatus,
    HospitalResult,
)
from app.repositories.batch_repository import BatchRepository
from app.services.hospital_api_client import HospitalAPIClient
from app.utils.csv_parser import parse_and_validate_csv

logger = logging.getLogger(__name__)
settings = get_settings()


class BulkProcessorService:
    """
    Orchestrates the entire bulk-create workflow.

    Dependencies (api_client, repository) are injected via the constructor,
    making this class fully testable without touching real HTTP or disk.

    Dependency Injection Principle (D in SOLID):
        High-level policy (this class) depends on abstractions
        (HospitalAPIClient, BatchRepository), not concrete implementations.
    """

    def __init__(self, api_client: HospitalAPIClient, repository: BatchRepository):
        self._api = api_client
        self._repo = repository

    # ── Public entry-point ─────────────────────────────────────────────────

    async def process_csv(self, file_content: bytes) -> BulkCreateResponse:
        """
        Full workflow:
          1. Parse & validate the CSV
          2. Register a new batch in the repository
          3. Create hospitals concurrently (bounded by MAX_CONCURRENT_REQUESTS)
          4. Activate the batch on the external API
          5. Return comprehensive results

        Args:
            file_content: Raw bytes of the uploaded CSV file.

        Returns:
            BulkCreateResponse with per-hospital statuses and timing info.
        """
        # Step 1 — Parse and validate
        rows: List[HospitalCSVRow] = parse_and_validate_csv(file_content)

        # Step 2 — Generate a unique batch ID and register with the repo
        batch_id = str(uuid.uuid4())
        self._repo.create_batch(batch_id, total_hospitals=len(rows))
        self._repo.mark_processing(batch_id)

        logger.info("Starting batch '%s' with %d hospitals", batch_id, len(rows))
        start_time = time.monotonic()

        # Step 3 — Create all hospitals concurrently
        results = await self._create_hospitals_concurrently(rows, batch_id)

        # Step 4 — Activate the batch if at least one hospital was created
        batch_activated = False
        successfully_created = [
            r for r in results if r.status == HospitalProcessingStatus.CREATED
        ]

        if successfully_created:
            try:
                await self._api.activate_batch(batch_id)
                batch_activated = True
                # Update all CREATED → CREATED_AND_ACTIVATED
                for result in results:
                    if result.status == HospitalProcessingStatus.CREATED:
                        result.status = HospitalProcessingStatus.CREATED_AND_ACTIVATED
                logger.info("Batch '%s' activated successfully", batch_id)
            except BatchActivationError as exc:
                logger.error("Batch activation failed for '%s': %s", batch_id, exc)
                # We don't re-raise — hospitals were still created; partial success

        # Step 5 — Finalize timing and persist results
        processing_time = round(time.monotonic() - start_time, 3)

        processed = sum(
            1 for r in results
            if r.status in (
                HospitalProcessingStatus.CREATED,
                HospitalProcessingStatus.CREATED_AND_ACTIVATED,
            )
        )
        failed = sum(1 for r in results if r.status == HospitalProcessingStatus.FAILED)

        self._repo.mark_completed(batch_id, batch_activated, processing_time)

        logger.info(
            "Batch '%s' done: %d created, %d failed, %.2fs",
            batch_id, processed, failed, processing_time,
        )

        return BulkCreateResponse(
            batch_id=batch_id,
            total_hospitals=len(rows),
            processed_hospitals=processed,
            failed_hospitals=failed,
            processing_time_seconds=processing_time,
            batch_activated=batch_activated,
            hospitals=results,
        )

    # ── Private helpers ────────────────────────────────────────────────────

    async def _create_hospitals_concurrently(
        self,
        rows: List[HospitalCSVRow],
        batch_id: str,
    ) -> List[HospitalResult]:
        """
        Fire API calls for all rows concurrently, bounded by a Semaphore.

        The Semaphore (MAX_CONCURRENT_REQUESTS) acts as a throttle — we
        never send more than N requests at the same time. This prevents
        flooding the external API while still being much faster than
        sequential processing.

        Results are ordered by CSV row number regardless of which
        async task finishes first.
        """
        semaphore = asyncio.Semaphore(settings.MAX_CONCURRENT_REQUESTS)
        tasks = [
            self._create_one_hospital(row, batch_id, semaphore)
            for row in rows
        ]
        # gather() runs all tasks concurrently and collects results in order
        return list(await asyncio.gather(*tasks))

    async def _create_one_hospital(
        self,
        row: HospitalCSVRow,
        batch_id: str,
        semaphore: asyncio.Semaphore,
    ) -> HospitalResult:
        """
        Create a single hospital via the API, respecting the concurrency limit.

        The 'async with semaphore' block ensures only N tasks enter the
        actual HTTP call at once; the rest queue up cheaply in memory.
        """
        async with semaphore:
            try:
                api_response = await self._api.create_hospital(
                    name=row.name,
                    address=row.address,
                    batch_id=batch_id,
                    phone=row.phone,
                )
                result = HospitalResult(
                    row=row.row_number,
                    hospital_id=api_response.id,
                    name=row.name,
                    status=HospitalProcessingStatus.CREATED,
                )
                logger.debug("Row %d → hospital_id=%d created", row.row_number, api_response.id)
            except HospitalAPIError as exc:
                logger.warning("Row %d failed: %s", row.row_number, exc)
                result = HospitalResult(
                    row=row.row_number,
                    hospital_id=None,
                    name=row.name,
                    status=HospitalProcessingStatus.FAILED,
                    error=str(exc),
                )

        # Persist to repository right away (progress tracking)
        self._repo.add_hospital_result(batch_id, result)
        return result

