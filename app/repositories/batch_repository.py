"""
app/repositories/batch_repository.py

In-memory storage for batch processing state.

Repository Pattern: Isolates data-access logic from business logic.
If we ever switch to a real database (PostgreSQL, Redis, etc.) we only change
this file — services stay untouched.

Singleton Pattern: A single shared store across the app lifetime ensures
all requests see the same data.
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Dict, List, Optional

from app.models.hospital import (
    BatchStatus,
    BatchSummary,
    HospitalResult,
    HospitalProcessingStatus,
)


class _BatchRecord:
    """
    Internal record stored per batch.
    Not exposed directly — the repository translates it into public models.
    """

    def __init__(self, batch_id: str, total_hospitals: int):
        self.batch_id = batch_id
        self.total_hospitals = total_hospitals
        self.status = BatchStatus.PENDING
        self.hospitals: List[HospitalResult] = []
        self.batch_activated = False
        self.started_at: Optional[datetime] = None
        self.completed_at: Optional[datetime] = None
        self.processing_time_seconds: float = 0.0
        self.created_at: datetime = datetime.now(timezone.utc)

    # ── Convenience properties ─────────────────────────────────────────────

    @property
    def processed_hospitals(self) -> int:
        return sum(
            1 for h in self.hospitals
            if h.status in (
                HospitalProcessingStatus.CREATED,
                HospitalProcessingStatus.CREATED_AND_ACTIVATED,
            )
        )

    @property
    def failed_hospitals(self) -> int:
        return sum(1 for h in self.hospitals if h.status == HospitalProcessingStatus.FAILED)

    @property
    def progress_percent(self) -> float:
        if self.total_hospitals == 0:
            return 0.0
        done = len(self.hospitals)   # rows we've attempted (success + fail)
        return round((done / self.total_hospitals) * 100, 2)


class BatchRepository:
    """
    Thread-safe in-memory store for batch records.

    Singleton: instantiated once at module level and injected via
    dependency injection in FastAPI routes.
    """

    def __init__(self):
        # dict mapping batch_id -> _BatchRecord
        self._store: Dict[str, _BatchRecord] = {}
        # A reentrant lock so concurrent async tasks don't corrupt state
        self._lock = threading.RLock()

    # ── Write operations ───────────────────────────────────────────────────

    def create_batch(self, batch_id: str, total_hospitals: int) -> None:
        """Register a new batch before processing starts."""
        with self._lock:
            self._store[batch_id] = _BatchRecord(batch_id, total_hospitals)

    def mark_processing(self, batch_id: str) -> None:
        """Move batch to PROCESSING state and record start time."""
        with self._lock:
            record = self._get_or_raise(batch_id)
            record.status = BatchStatus.PROCESSING
            record.started_at = datetime.now(timezone.utc)

    def add_hospital_result(self, batch_id: str, result: HospitalResult) -> None:
        """Append a per-hospital processing result to the batch."""
        with self._lock:
            record = self._get_or_raise(batch_id)
            record.hospitals.append(result)

    def mark_completed(
        self,
        batch_id: str,
        batch_activated: bool,
        processing_time_seconds: float,
    ) -> None:
        """
        Finalize the batch.
        Chooses COMPLETED vs PARTIALLY_FAILED based on failure count.
        """
        with self._lock:
            record = self._get_or_raise(batch_id)
            record.batch_activated = batch_activated
            record.processing_time_seconds = processing_time_seconds
            record.completed_at = datetime.now(timezone.utc)

            if record.failed_hospitals == 0:
                record.status = BatchStatus.COMPLETED
            elif record.processed_hospitals > 0:
                record.status = BatchStatus.PARTIALLY_FAILED
            else:
                record.status = BatchStatus.FAILED

    def mark_failed(self, batch_id: str) -> None:
        """Mark the whole batch as FAILED (e.g. unexpected exception)."""
        with self._lock:
            record = self._get_or_raise(batch_id)
            record.status = BatchStatus.FAILED
            record.completed_at = datetime.now(timezone.utc)

    # ── Read operations ────────────────────────────────────────────────────

    def get_batch(self, batch_id: str) -> Optional[_BatchRecord]:
        """Return the raw record (None if not found)."""
        with self._lock:
            return self._store.get(batch_id)

    def list_batches(self) -> List[BatchSummary]:
        """Return lightweight summaries of all known batches."""
        with self._lock:
            return [
                BatchSummary(
                    batch_id=r.batch_id,
                    status=r.status,
                    total_hospitals=r.total_hospitals,
                    processed_hospitals=r.processed_hospitals,
                    failed_hospitals=r.failed_hospitals,
                    created_at=r.created_at,
                )
                for r in self._store.values()
            ]

    def delete_batch(self, batch_id: str) -> bool:
        """Remove a batch from the repository."""
        with self._lock:
            return self._store.pop(batch_id, None) is not None

    def batch_exists(self, batch_id: str) -> bool:
        with self._lock:
            return batch_id in self._store

    # ── Private helpers ────────────────────────────────────────────────────

    def _get_or_raise(self, batch_id: str) -> _BatchRecord:
        record = self._store.get(batch_id)
        if record is None:
            raise KeyError(f"Batch '{batch_id}' not found in store.")
        return record


# ── Singleton instance ─────────────────────────────────────────────────────────
# Created once at import time; injected into services via FastAPI Depends().
batch_repository = BatchRepository()

