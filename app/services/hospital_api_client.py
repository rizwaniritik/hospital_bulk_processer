"""
app/services/hospital_api_client.py

HTTP client for the external Hospital Directory API.

Adapter / Gateway Pattern: Wraps the external API behind a clean interface.
Our services call methods like create_hospital() — they never deal with raw
HTTP status codes or JSON parsing. If the API changes its shape, only this
file needs updating.

Also uses the Factory Method pattern: get_http_client() creates and caches
an httpx.AsyncClient so we reuse TCP connections (keep-alive), which is
critical for performance when creating 20 hospitals in parallel.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import Optional

import httpx

from app.core.config import get_settings
from app.core.exceptions import HospitalAPIError, BatchActivationError
from app.models.hospital import HospitalAPIResponse

logger = logging.getLogger(__name__)
settings = get_settings()


# ── HTTP Client Factory ────────────────────────────────────────────────────────

@lru_cache(maxsize=1)
def get_http_client() -> httpx.AsyncClient:
    """
    Factory that returns a single shared AsyncClient (Singleton via lru_cache).

    Sharing the client means TCP connections are reused across requests,
    giving a ~40% speed improvement on batches vs creating a new client per call.
    """
    return httpx.AsyncClient(
        base_url=settings.HOSPITAL_API_BASE_URL,
        timeout=settings.API_TIMEOUT_SECONDS,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )


# ── API Client ─────────────────────────────────────────────────────────────────

class HospitalAPIClient:
    """
    Thin wrapper around the Hospital Directory REST API.

    Every public method maps 1:1 to an API endpoint:
      create_hospital()       → POST /hospitals/
      activate_batch()        → PATCH /hospitals/batch/{batch_id}/activate
      delete_batch()          → DELETE /hospitals/batch/{batch_id}
      get_batch_hospitals()   → GET /hospitals/batch/{batch_id}
    """

    def __init__(self, client: Optional[httpx.AsyncClient] = None):
        # Accept an injected client (great for unit tests with a mock)
        # or fall back to the shared singleton.
        self._client = client or get_http_client()

    async def create_hospital(
        self,
        name: str,
        address: str,
        batch_id: str,
        phone: Optional[str] = None,
    ) -> HospitalAPIResponse:
        """
        POST /hospitals/
        Creates a single hospital tied to the given batch_id.
        Returns the API response as a typed HospitalAPIResponse.
        """
        payload = {
            "name": name,
            "address": address,
            "creation_batch_id": batch_id,
        }
        if phone:
            payload["phone"] = phone

        logger.debug("Creating hospital '%s' in batch '%s'", name, batch_id)

        try:
            response = await self._client.post("/hospitals/", json=payload)
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise HospitalAPIError(
                f"Failed to create hospital '{name}': {exc.response.text}",
                status_code=exc.response.status_code,
            ) from exc
        except httpx.RequestError as exc:
            raise HospitalAPIError(
                f"Network error while creating hospital '{name}': {exc}"
            ) from exc

        return HospitalAPIResponse(**response.json())

    async def activate_batch(self, batch_id: str) -> bool:
        """
        PATCH /hospitals/batch/{batch_id}/activate
        Activates all hospitals in the batch on the external API.
        Returns True on success.
        """
        logger.info("Activating batch '%s'", batch_id)

        try:
            response = await self._client.patch(
                f"/hospitals/batch/{batch_id}/activate"
            )
            response.raise_for_status()
            return True
        except httpx.HTTPStatusError as exc:
            raise BatchActivationError(
                f"Failed to activate batch '{batch_id}': {exc.response.text}"
            ) from exc
        except httpx.RequestError as exc:
            raise BatchActivationError(
                f"Network error while activating batch '{batch_id}': {exc}"
            ) from exc

    async def delete_batch(self, batch_id: str) -> bool:
        """
        DELETE /hospitals/batch/{batch_id}
        Deletes all hospitals in a batch from the external API.
        """
        logger.info("Deleting batch '%s'", batch_id)

        try:
            response = await self._client.delete(f"/hospitals/batch/{batch_id}")
            response.raise_for_status()
            return True
        except httpx.HTTPStatusError as exc:
            raise HospitalAPIError(
                f"Failed to delete batch '{batch_id}': {exc.response.text}",
                status_code=exc.response.status_code,
            ) from exc
        except httpx.RequestError as exc:
            raise HospitalAPIError(
                f"Network error while deleting batch '{batch_id}': {exc}"
            ) from exc

    async def get_batch_hospitals(self, batch_id: str) -> list[HospitalAPIResponse]:
        """
        GET /hospitals/batch/{batch_id}
        Fetches all hospitals belonging to a batch.
        """
        try:
            response = await self._client.get(f"/hospitals/batch/{batch_id}")
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise HospitalAPIError(
                f"Failed to fetch hospitals for batch '{batch_id}': {exc.response.text}",
                status_code=exc.response.status_code,
            ) from exc
        except httpx.RequestError as exc:
            raise HospitalAPIError(
                f"Network error while fetching batch '{batch_id}': {exc}"
            ) from exc

        return [HospitalAPIResponse(**h) for h in response.json()]

