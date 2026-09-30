"""Private object storage for original business files."""

from urllib.parse import quote

import httpx

from backend.config import Settings


class SupabaseStorageError(RuntimeError):
    """A remote object operation failed without exposing the access key."""


class SupabaseObjectStorage:
    """Small server-side adapter for Supabase Storage's REST API.

    Original documents are stored in a private bucket. The Supabase service
    role key is only used by this backend and is never returned to the browser.
    """

    def __init__(self, settings: Settings) -> None:
        if not settings.supabase_storage_configured:
            raise ValueError("Supabase Storage is not fully configured")
        self.base_url = settings.supabase_url.rstrip("/")
        self.bucket = settings.supabase_storage_bucket.strip()
        self._api_key = settings.supabase_service_role_key.get_secret_value()
        self.timeout_seconds = 45.0

    def _headers(self, media_type: str = "application/octet-stream") -> dict[str, str]:
        return {
            "apikey": self._api_key,
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": media_type,
        }

    def _object_url(self, object_path: str) -> str:
        encoded_bucket = quote(self.bucket, safe="")
        encoded_path = quote(object_path.lstrip("/"), safe="/")
        return f"{self.base_url}/storage/v1/object/{encoded_bucket}/{encoded_path}"

    async def upload(self, object_path: str, content: bytes, media_type: str) -> None:
        """Upload bytes to a private bucket, rejecting accidental overwrites."""
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                response = await client.post(
                    self._object_url(object_path),
                    headers={**self._headers(media_type), "x-upsert": "false"},
                    content=content,
                )
                response.raise_for_status()
        except httpx.HTTPError as exc:
            raise SupabaseStorageError(
                "Could not upload the document to Supabase Storage. Check the "
                "project URL, service-role key, private bucket, and bucket name."
            ) from exc

    async def download(self, object_path: str) -> bytes:
        """Download an object using server-only credentials."""
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                response = await client.get(
                    self._object_url(object_path),
                    headers=self._headers(),
                )
                response.raise_for_status()
                return response.content
        except httpx.HTTPError as exc:
            raise SupabaseStorageError(
                "Could not retrieve the document from Supabase Storage."
            ) from exc

    async def delete(self, object_path: str) -> None:
        """Delete one object from the configured bucket."""
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                response = await client.delete(
                    f"{self.base_url}/storage/v1/object/{quote(self.bucket, safe='')}",
                    headers=self._headers("application/json"),
                    json={"prefixes": [object_path.lstrip("/")]},
                )
                response.raise_for_status()
        except httpx.HTTPError as exc:
            raise SupabaseStorageError(
                "Could not remove the document from Supabase Storage."
            ) from exc