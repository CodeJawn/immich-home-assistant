"""Hub for Immich integration."""
from __future__ import annotations

import logging
from urllib.parse import urljoin

import aiohttp

from homeassistant.exceptions import HomeAssistantError

_HEADER_API_KEY = "x-api-key"
_LOGGER = logging.getLogger(__name__)

_ALLOWED_MIME_TYPES = ["image/png", "image/jpeg"]


class ImmichHub:
    """Immich API hub."""

    def __init__(self, host: str, api_key: str) -> None:
        """Initialize."""
        self.host = host
        self.api_key = api_key

    async def authenticate(self) -> bool:
        """Test if we can authenticate with the host."""
        try:
            async with aiohttp.ClientSession() as session:
                url = urljoin(self.host, "/api/auth/validateToken")
                headers = {"Accept": "application/json", _HEADER_API_KEY: self.api_key}

                async with session.post(url=url, headers=headers) as response:
                    if response.status != 200:
                        raw_result = await response.text()
                        _LOGGER.error("Error from API: body=%s", raw_result)
                        return False

                    auth_result = await response.json()

                    if not auth_result.get("authStatus"):
                        raw_result = await response.text()
                        _LOGGER.error("Error from API: body=%s", raw_result)
                        return False

                    return True
        except aiohttp.ClientError as exception:
            _LOGGER.error("Error connecting to the API: %s", exception)
            raise CannotConnect from exception

    async def get_my_user_info(self) -> dict:
        """Get user info."""
        try:
            async with aiohttp.ClientSession() as session:
                url = urljoin(self.host, "/api/users/me")
                headers = {"Accept": "application/json", _HEADER_API_KEY: self.api_key}

                async with session.get(url=url, headers=headers) as response:
                    if response.status != 200:
                        raw_result = await response.text()
                        _LOGGER.error("Error from API: body=%s", raw_result)
                        raise ApiError()

                    user_info: dict = await response.json()

                    return user_info
        except aiohttp.ClientError as exception:
            _LOGGER.error("Error connecting to the API: %s", exception)
            raise CannotConnect from exception

    async def get_asset_info(self, asset_id: str) -> dict | None:
        """Get asset info."""
        try:
            async with aiohttp.ClientSession() as session:
                url = urljoin(self.host, f"/api/assets/{asset_id}")
                headers = {"Accept": "application/json", _HEADER_API_KEY: self.api_key}

                async with session.get(url=url, headers=headers) as response:
                    if response.status != 200:
                        raw_result = await response.text()
                        _LOGGER.error("Error from API: body=%s", raw_result)
                        raise ApiError()

                    asset_info: dict = await response.json()

                    return asset_info
        except aiohttp.ClientError as exception:
            _LOGGER.error("Error connecting to the API: %s", exception)
            raise CannotConnect from exception

    async def download_asset(self, asset_id: str) -> bytes | None:
        """Download the asset."""
        try:
            async with aiohttp.ClientSession() as session:
                url = urljoin(self.host, f"/api/assets/{asset_id}/original")
                headers = {_HEADER_API_KEY: self.api_key}

                async with session.get(url=url, headers=headers) as response:
                    if response.status != 200:
                        _LOGGER.error("Error from API: status=%d", response.status)
                        return None

                    if response.content_type not in _ALLOWED_MIME_TYPES:
                        _LOGGER.error(
                            "MIME type is not supported: %s", response.content_type
                        )
                        return None

                    return await response.read()
        except aiohttp.ClientError as exception:
            _LOGGER.error("Error connecting to the API: %s", exception)
            raise CannotConnect from exception

    async def list_favorite_images(self) -> list[dict]:
        """List all favorite images."""
        try:
            async with aiohttp.ClientSession() as session:
                url = urljoin(self.host, "/api/search/metadata")
                headers = {"Accept": "application/json", _HEADER_API_KEY: self.api_key}
                data = {"isFavorite": "true"}

                async with session.post(url=url, headers=headers, data=data) as response:
                    if response.status != 200:
                        raw_result = await response.text()
                        _LOGGER.error("Error from API: body=%s", raw_result)
                        raise ApiError()

                    favorites = await response.json()
                    assets: list[dict] = favorites["assets"]["items"]

                    filtered_assets: list[dict] = [
                        asset for asset in assets if asset["type"] == "IMAGE"
                    ]

                    return filtered_assets
        except aiohttp.ClientError as exception:
            _LOGGER.error("Error connecting to the API: %s", exception)
            raise CannotConnect from exception


    async def search_images(self, payload: dict) -> list[dict]:
        """Return IMAGE-type assets matching an Immich search."""
        _LOGGER.debug("search_images called with %s", payload)

        url = urljoin(self.host, "/api/search/metadata")
        headers = {"Accept": "application/json", _HEADER_API_KEY: self.api_key}
        full_payload = {"type": "IMAGE", "size": 400, **payload}

        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, json=full_payload) as resp:
                if resp.status != 200:
                    _LOGGER.error("Immich search error: %s", await resp.text())
                    raise ApiError()
                data = await resp.json()

        # ----- tolerant extractor: works with both old and future shapes -----
        if "items" in data and isinstance(data["items"], list):         # future /assets endpoint
            assets = data["items"]
        elif "assets" in data and isinstance(data["assets"], dict):      # current /metadata endpoint
            assets = data["assets"].get("items", [])
        else:
            _LOGGER.error("Unexpected search response: %s", data)
            return []

        allowed = {"image/jpeg", "image/jpg", "image/png", "image/webp", "image/heic", "image/heif"}

        def _mtype(asset: dict) -> str:
            # Immich returns originalMimeType; older builds used mimeType
            return asset.get("originalMimeType", asset.get("mimeType", "")).lower()

        return [a for a in assets if a.get("type") == "IMAGE" and _mtype(a) in allowed]

    
    async def list_all_albums(self) -> list[dict]:
        """List all albums."""
        try:
            async with aiohttp.ClientSession() as session:
                url = urljoin(self.host, "/api/albums")
                headers = {"Accept": "application/json", _HEADER_API_KEY: self.api_key}

                async with session.get(url=url, headers=headers) as response:
                    if response.status != 200:
                        raw_result = await response.text()
                        _LOGGER.error("Error from API: body=%s", raw_result)
                        raise ApiError()

                    album_list: list[dict] = await response.json()

                    return album_list
        except aiohttp.ClientError as exception:
            _LOGGER.error("Error connecting to the API: %s", exception)
            raise CannotConnect from exception

    async def list_album_images(self, album_id: str) -> list[dict]:
        """List all images in an album."""
        try:
            async with aiohttp.ClientSession() as session:
                url = urljoin(self.host, f"/api/albums/{album_id}")
                headers = {"Accept": "application/json", _HEADER_API_KEY: self.api_key}

                async with session.get(url=url, headers=headers) as response:
                    if response.status != 200:
                        raw_result = await response.text()
                        _LOGGER.error("Error from API: body=%s", raw_result)
                        raise ApiError()

                    album_info: dict = await response.json()
                    assets: list[dict] = album_info["assets"]

                    filtered_assets: list[dict] = [
                        asset for asset in assets if asset["type"] == "IMAGE"
                    ]

                    return filtered_assets
        except aiohttp.ClientError as exception:
            _LOGGER.error("Error connecting to the API: %s", exception)
            raise CannotConnect from exception


class CannotConnect(HomeAssistantError):
    """Error to indicate we cannot connect."""


class InvalidAuth(HomeAssistantError):
    """Error to indicate there is invalid auth."""


class ApiError(HomeAssistantError):
    """Error to indicate that the API returned an error."""
