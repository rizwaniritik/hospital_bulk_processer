"""
app/models/hospital.py

Pydantic models (data contracts) for the Hospital domain.

These models serve three purposes:
  1. Request/Response shapes for our own API  (OpenAPI docs are auto-generated)
  2. Internal data transfer objects between layers
  3. Validation — Pydantic rejects bad data before it reaches business logic
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field


# ── Enumerations ──────────────────────────────────────────────────────────────

class HospitalProcessingStatus(str, Enum):
    """
    Tracks what happened to each hospital row during bulk processing.

    Using an Enum (instead of plain strings) prevents typos and makes
    valid states explicit and discoverable.
    """
    PENDING = "pending"
    CREATED = "created"
    CREATED_AND_ACTIVATED = "created_and_activated"
    FAILED = "failed"
    SKIPPED = "skipped"


class BatchStatus(str, Enum):
    """Overall lifecycle state of a bulk-processing batch."""
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    PARTIALLY_FAILED = "partially_failed"
    FAILED = "failed"


# ── Input Models (what callers send us) ───────────────────────────────────────

class HospitalCSVRow(BaseModel):
    """
    Represents a single parsed row from the uploaded CSV.
    'phone' is optional per the assignment spec.
    """
    row_number: int = Field(..., description="1-based row index in the CSV")
    name: str = Field(..., min_length=1, description="Hospital name")
    address: str = Field(..., min_length=1, description="Hospital address")
    phone: Optional[str] = Field(None, description="Contact phone (optional)")


# ── External API Models (what the Hospital Directory API returns) ─────────────

class HospitalAPIResponse(BaseModel):
    """
    Shape of the Hospital object returned by the external Hospital Directory API.
    Matches the API docs exactly so deserialization is safe.
    """
    id: int
    name: str
    address: str
    phone: Optional[str] = None
    creation_batch_id: Optional[str] = None
    active: bool = False
    created_at: Optional[datetime] = None


# ── Internal Processing Models ────────────────────────────────────────────────

class HospitalResult(BaseModel):
    """
    Result for a single hospital row after processing.
    Included in the final batch response so callers know exactly what happened.
    """
    row: int = Field(..., description="CSV row number (1-based)")
    hospital_id: Optional[int] = Field(None, description="ID assigned by the external API")
    name: str
    status: HospitalProcessingStatus
    error: Optional[str] = Field(None, description="Error message if status is FAILED")


# ── Response Models (what our API returns) ────────────────────────────────────

class BulkCreateResponse(BaseModel):
    """
    Full response returned after a bulk hospital creation request.
    Matches the expected shape defined in the assignment PDF exactly.
    """
    batch_id: str = Field(..., description="UUID that identifies this batch")
    total_hospitals: int = Field(..., description="Total rows in the uploaded CSV")
    processed_hospitals: int = Field(..., description="Rows successfully sent to the API")
    failed_hospitals: int = Field(..., description="Rows that failed processing")
    processing_time_seconds: float = Field(..., description="Wall-clock time for the whole operation")
    batch_activated: bool = Field(..., description="Whether the batch was activated on the external API")
    hospitals: List[HospitalResult] = Field(default_factory=list, description="Per-row details")


class BatchProgressResponse(BaseModel):
    """
    Returned by the GET /hospitals/bulk/{batch_id}/progress polling endpoint.
    Clients can poll this to show a live progress bar.
    """
    batch_id: str
    status: BatchStatus
    total_hospitals: int
    processed_hospitals: int
    failed_hospitals: int
    progress_percent: float
    batch_activated: bool
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None


class CSVValidationResponse(BaseModel):
    """
    Result of the CSV pre-validation endpoint.
    Lets callers check a file before committing to full processing.
    """
    valid: bool
    total_rows: int
    errors: List[str] = Field(default_factory=list)
    preview: List[HospitalCSVRow] = Field(default_factory=list, description="First 5 rows")


class BatchSummary(BaseModel):
    """
    Lightweight summary used in the list-all-batches endpoint.
    """
    batch_id: str
    status: BatchStatus
    total_hospitals: int
    processed_hospitals: int
    failed_hospitals: int
    created_at: datetime

