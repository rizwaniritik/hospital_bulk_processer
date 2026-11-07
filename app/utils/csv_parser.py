"""
app/utils/csv_parser.py

CSV parsing and validation utilities.

Single Responsibility Principle (SRP): This module only knows how to read
and validate CSVs. It knows nothing about HTTP, databases, or business logic.
"""

from __future__ import annotations

import csv
import io
from typing import List, Tuple

from app.core.config import get_settings
from app.core.exceptions import CSVValidationError
from app.models.hospital import HospitalCSVRow, CSVValidationResponse

settings = get_settings()


def _normalize_headers(headers: List[str]) -> List[str]:
    """Strip whitespace and lowercase all column names for case-insensitive comparison."""
    return [h.strip().lower() for h in headers]


def parse_and_validate_csv(file_content: bytes) -> List[HospitalCSVRow]:
    """
    Parse a CSV file from raw bytes and return a list of validated rows.

    Raises:
        CSVValidationError: If the file is empty, missing required columns,
                            has too many rows, or contains blank required fields.
    """
    try:
        text = file_content.decode("utf-8")
    except UnicodeDecodeError:
        raise CSVValidationError("CSV file must be UTF-8 encoded.")

    reader = csv.DictReader(io.StringIO(text))

    # ── Column validation ──────────────────────────────────────────────────
    if reader.fieldnames is None:
        raise CSVValidationError("CSV file is empty or has no header row.")

    normalized_headers = _normalize_headers(list(reader.fieldnames))

    missing = [
        col for col in settings.REQUIRED_CSV_COLUMNS
        if col not in normalized_headers
    ]
    if missing:
        raise CSVValidationError(
            f"CSV is missing required column(s): {', '.join(missing)}. "
            f"Expected: {', '.join(settings.REQUIRED_CSV_COLUMNS)}"
        )

    # ── Row parsing ────────────────────────────────────────────────────────
    rows: List[HospitalCSVRow] = []
    errors: List[str] = []

    for row_num, raw_row in enumerate(reader, start=1):
        # Normalize keys so we can access them case-insensitively
        row_data = {k.strip().lower(): (v.strip() if v else "") for k, v in raw_row.items()}

        name = row_data.get("name", "")
        address = row_data.get("address", "")
        phone = row_data.get("phone") or None   # treat empty string as None

        row_errors = []
        if not name:
            row_errors.append("'name' is blank")
        if not address:
            row_errors.append("'address' is blank")

        if row_errors:
            errors.append(f"Row {row_num}: {'; '.join(row_errors)}")
            continue

        rows.append(
            HospitalCSVRow(
                row_number=row_num,
                name=name,
                address=address,
                phone=phone,
            )
        )

    if errors:
        raise CSVValidationError(
            f"CSV contains {len(errors)} invalid row(s): " + " | ".join(errors)
        )

    # ── Size guard ─────────────────────────────────────────────────────────
    if len(rows) == 0:
        raise CSVValidationError("CSV has a header but no data rows.")

    if len(rows) > settings.MAX_CSV_ROWS:
        raise CSVValidationError(
            f"CSV contains {len(rows)} hospitals but the maximum allowed is "
            f"{settings.MAX_CSV_ROWS}."
        )

    return rows


def dry_run_validate_csv(file_content: bytes) -> CSVValidationResponse:
    """
    Validate a CSV without processing it — used by the /validate endpoint.

    Returns a structured response describing validity and any problems found,
    instead of raising an exception, so callers receive a full error list.
    """
    errors: List[str] = []
    rows: List[HospitalCSVRow] = []

    try:
        text = file_content.decode("utf-8")
    except UnicodeDecodeError:
        return CSVValidationResponse(
            valid=False,
            total_rows=0,
            errors=["CSV file must be UTF-8 encoded."],
        )

    reader = csv.DictReader(io.StringIO(text))

    if reader.fieldnames is None:
        return CSVValidationResponse(
            valid=False,
            total_rows=0,
            errors=["CSV file is empty or has no header row."],
        )

    normalized_headers = _normalize_headers(list(reader.fieldnames))
    missing = [
        col for col in settings.REQUIRED_CSV_COLUMNS
        if col not in normalized_headers
    ]
    if missing:
        errors.append(f"Missing required column(s): {', '.join(missing)}")

    all_raw_rows = list(reader)
    total_rows = len(all_raw_rows)

    if total_rows > settings.MAX_CSV_ROWS:
        errors.append(
            f"File has {total_rows} rows; maximum allowed is {settings.MAX_CSV_ROWS}."
        )

    for row_num, raw_row in enumerate(all_raw_rows, start=1):
        row_data = {k.strip().lower(): (v.strip() if v else "") for k, v in raw_row.items()}
        name = row_data.get("name", "")
        address = row_data.get("address", "")
        phone = row_data.get("phone") or None

        row_errors = []
        if not name:
            row_errors.append("'name' is blank")
        if not address:
            row_errors.append("'address' is blank")

        if row_errors:
            errors.append(f"Row {row_num}: {'; '.join(row_errors)}")
        else:
            rows.append(
                HospitalCSVRow(row_number=row_num, name=name, address=address, phone=phone)
            )

    return CSVValidationResponse(
        valid=len(errors) == 0,
        total_rows=total_rows,
        errors=errors,
        preview=rows[:5],   # show at most 5 preview rows
    )

