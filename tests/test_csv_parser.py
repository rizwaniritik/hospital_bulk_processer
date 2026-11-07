"""
tests/test_csv_parser.py

Unit tests for the CSV parsing and validation utilities.

These tests run entirely in-memory — no network, no database, no filesystem.
That makes them fast (milliseconds) and 100% reliable.
"""

import pytest

from app.core.exceptions import CSVValidationError
from app.utils.csv_parser import dry_run_validate_csv, parse_and_validate_csv


# ── Helpers ────────────────────────────────────────────────────────────────────

def csv_bytes(*rows: str) -> bytes:
    """Build CSV bytes from a list of raw lines."""
    return "\n".join(rows).encode("utf-8")


VALID_HEADER = "name,address,phone"
VALID_ROW_1 = "General Hospital,123 Main St,555-1234"
VALID_ROW_2 = "City Clinic,456 Oak Ave,"         # empty phone is fine


# ── parse_and_validate_csv ─────────────────────────────────────────────────────

class TestParseAndValidateCSV:

    def test_valid_csv_returns_rows(self):
        content = csv_bytes(VALID_HEADER, VALID_ROW_1, VALID_ROW_2)
        rows = parse_and_validate_csv(content)
        assert len(rows) == 2
        assert rows[0].name == "General Hospital"
        assert rows[0].phone == "555-1234"
        assert rows[1].phone is None   # empty → None

    def test_phone_is_optional_column_missing(self):
        content = csv_bytes("name,address", "Hospital A,123 Main St")
        rows = parse_and_validate_csv(content)
        assert len(rows) == 1
        assert rows[0].phone is None

    def test_missing_required_column_raises(self):
        content = csv_bytes("name,phone", "Hospital A,555-0000")
        with pytest.raises(CSVValidationError, match="missing required column"):
            parse_and_validate_csv(content)

    def test_blank_name_raises(self):
        content = csv_bytes(VALID_HEADER, ",456 Oak Ave,555-0000")
        with pytest.raises(CSVValidationError, match="'name' is blank"):
            parse_and_validate_csv(content)

    def test_blank_address_raises(self):
        content = csv_bytes(VALID_HEADER, "Hospital A,,555-0000")
        with pytest.raises(CSVValidationError, match="'address' is blank"):
            parse_and_validate_csv(content)

    def test_empty_file_raises(self):
        with pytest.raises(CSVValidationError):
            parse_and_validate_csv(b"")

    def test_header_only_raises(self):
        content = csv_bytes(VALID_HEADER)
        with pytest.raises(CSVValidationError, match="no data rows"):
            parse_and_validate_csv(content)

    def test_exceeds_max_rows_raises(self):
        rows = [VALID_HEADER] + [f"Hospital {i},Address {i}," for i in range(21)]
        content = csv_bytes(*rows)
        with pytest.raises(CSVValidationError, match="maximum allowed"):
            parse_and_validate_csv(content)

    def test_non_utf8_raises(self):
        with pytest.raises(CSVValidationError, match="UTF-8"):
            parse_and_validate_csv(b"\xff\xfe invalid bytes")

    def test_case_insensitive_headers(self):
        content = csv_bytes("NAME,ADDRESS,PHONE", "Hospital A,123 St,555-0000")
        rows = parse_and_validate_csv(content)
        assert rows[0].name == "Hospital A"

    def test_row_numbers_are_correct(self):
        content = csv_bytes(VALID_HEADER, VALID_ROW_1, VALID_ROW_2)
        rows = parse_and_validate_csv(content)
        assert rows[0].row_number == 1
        assert rows[1].row_number == 2


# ── dry_run_validate_csv ───────────────────────────────────────────────────────

class TestDryRunValidateCSV:

    def test_valid_csv_is_valid(self):
        content = csv_bytes(VALID_HEADER, VALID_ROW_1)
        result = dry_run_validate_csv(content)
        assert result.valid is True
        assert result.errors == []
        assert result.total_rows == 1

    def test_missing_column_is_invalid(self):
        content = csv_bytes("name,phone", "Hospital A,555-0000")
        result = dry_run_validate_csv(content)
        assert result.valid is False
        assert any("address" in e for e in result.errors)

    def test_preview_capped_at_5(self):
        rows = [VALID_HEADER] + [f"Hospital {i},Address {i}," for i in range(10)]
        content = csv_bytes(*rows)
        result = dry_run_validate_csv(content)
        assert len(result.preview) == 5

    def test_empty_file_returns_error(self):
        result = dry_run_validate_csv(b"")
        assert result.valid is False
        assert result.total_rows == 0

    def test_over_limit_returns_error(self):
        rows = [VALID_HEADER] + [f"Hospital {i},Address {i}," for i in range(21)]
        content = csv_bytes(*rows)
        result = dry_run_validate_csv(content)
        assert result.valid is False
        assert any("maximum" in e for e in result.errors)

