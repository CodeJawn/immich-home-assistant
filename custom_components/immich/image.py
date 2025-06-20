"""Image device for Immich integration."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
import logging
import random

from homeassistant.components.image import ImageEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_API_KEY, CONF_HOST
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_WATCHED_ALBUMS
from .hub import ImmichHub

SCAN_INTERVAL = timedelta(seconds=30)

# How often to refresh the list of available asset IDs
_ID_LIST_REFRESH_INTERVAL = timedelta(hours=8)

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Immich image platform."""

    hub = ImmichHub(
        host=config_entry.data[CONF_HOST], api_key=config_entry.data[CONF_API_KEY]
    )

    # Create entity for random favorite image
    async_add_entities([ImmichImageFavorite(hass, hub)])

    # Create entities for random image from each watched album
    watched_albums = config_entry.options.get(CONF_WATCHED_ALBUMS, [])
    async_add_entities(
        [
            ImmichImageAlbum(
                hass, hub, album_id=album["id"], album_name=album["albumName"]
            )
            for album in await hub.list_all_albums()
            if album["id"] in watched_albums
        ]
    )

    config_entry.async_on_unload(config_entry.add_update_listener(update_listener))

    # Hardcoded search for specific people as requested by the user
    person_search_payload = {
        "personIds": [
            "062a35df-b9e1-4157-af7f-a39b73eea08b",
            "a75f0e40-48b3-4f42-9139-662b5b0f8110",
            "4b1f3c5b-6c69-455e-a5cb-e5e08ceb8a61",
            "8c125b8d-b1c3-4363-8c68-e52c88a3b428",
        ],

        # show images taken on/after 1 Jan 2011 UTC
        "takenAfter": "2011-01-01T00:00:00.000Z",

        # images only
        "type": "IMAGE",
    }
    
    # Create and add the new entity for the people search
    async_add_entities(
        [
            ImmichImageSearch(
                hass,
                hub,
                search_payload=person_search_payload,
                unique_id="search_people_of_interest",
                name="Immich: People of Interest",
            )
        ]
    )


async def update_listener(hass: HomeAssistant, config_entry: ConfigEntry) -> None:
    """Handle options updates."""
    await hass.config_entries.async_reload(config_entry.entry_id)


class BaseImmichImage(ImageEntity):
    """Base image entity for Immich. Subclasses will define where the random image comes from (e.g. favorite images, by album ID,..)."""

    _attr_has_entity_name = True

    # We want to get a new image every so often, as defined by the refresh interval
    _attr_should_poll = True

    _current_image_bytes: bytes | None = None
    _cached_available_asset_ids: list[str] | None = None
    _available_asset_ids_last_updated: datetime | None = None

    def __init__(self, hass: HomeAssistant, hub: ImmichHub) -> None:
        """Initialize the Immich image entity."""
        super().__init__(hass=hass, verify_ssl=True)
        self.hub = hub
        self.hass = hass

        self._attr_extra_state_attributes = {}

    async def async_update(self) -> None:
        """Force a refresh of the image."""
        await self._load_and_cache_next_image()

    async def async_image(self) -> bytes | None:
        """Return the current image. If no image is available, load and cache the image."""
        if not self._current_image_bytes:
            await self._load_and_cache_next_image()

        return self._current_image_bytes

    async def _refresh_available_asset_ids(self) -> list[str] | None:
        """Return just the IDs Home Assistant needs."""
        assets = await self.hub.search_images(self._search_payload)
        return [a["id"] for a in assets]

    async def _get_next_asset_id(self) -> str | None:
        """Get the asset id of the next image we want to display."""
        if (
            not self._available_asset_ids_last_updated
            or (datetime.now() - self._available_asset_ids_last_updated)
            > _ID_LIST_REFRESH_INTERVAL
        ):
            # If we don't have any available asset IDs yet, or the list is stale, refresh it
            _LOGGER.debug("Refreshing available asset IDs")
            self._cached_available_asset_ids = await self._refresh_available_asset_ids()
            self._available_asset_ids_last_updated = datetime.now()

        if not self._cached_available_asset_ids:
            # If we still don't have any available asset IDs, that's a problem
            _LOGGER.error("No assets are available")
            return None

        # Select random item in list
        random_asset = random.choice(self._cached_available_asset_ids)

        return random_asset

    async def _load_and_cache_next_image(self) -> None:
        """Download and cache the image."""
        asset_bytes = None

        while not asset_bytes:
            asset_id = await self._get_next_asset_id()

            if not asset_id:
                return

            asset_bytes = await self.hub.download_asset(asset_id)

            if not asset_bytes:
                await asyncio.sleep(1)
                continue

            asset_info = await self.hub.get_asset_info(asset_id)

            self._attr_extra_state_attributes["media_filename"] = (
                asset_info.get("originalFileName") or ""
            )
            self._attr_extra_state_attributes["media_exif"] = (
                asset_info.get("exifInfo") or ""
            )
            self._attr_extra_state_attributes["media_localdatetime"] = (
                asset_info.get("localDateTime") or ""
            )

            self._current_image_bytes = asset_bytes
            self._attr_image_last_updated = datetime.now()
            self.async_write_ha_state()


class ImmichImageFavorite(BaseImmichImage):
    """Image entity for Immich that displays a random image from the user's favorites."""

    _attr_unique_id = "favorite_image"
    _attr_name = "Immich: Random favorite image"

    async def _refresh_available_asset_ids(self) -> list[str] | None:
        """Refresh the list of available asset IDs."""
        return [image["id"] for image in await self.hub.list_favorite_images()]


class ImmichImageAlbum(BaseImmichImage):
    """Image entity for Immich that displays a random image from a specific album."""

    def __init__(
        self, hass: HomeAssistant, hub: ImmichHub, album_id: str, album_name: str
    ) -> None:
        """Initialize the Immich image entity."""
        super().__init__(hass, hub)
        self._album_id = album_id
        self._attr_unique_id = album_id
        self._attr_name = f"Immich: {album_name}"

class ImmichImageSearch(BaseImmichImage):
    """Image entity that shows a random image matching a search payload."""

    def __init__(self, hass, hub, search_payload, unique_id, name) -> None:
        super().__init__(hass, hub)
        self._search_payload = search_payload
        self._attr_unique_id = unique_id
        self._attr_name = name

    async def _refresh_available_asset_ids(self) -> list[str] | None:
        """OR-search: query once per personId, then union the results."""
        all_ids: set[str] = set()

        base = {k: v for k, v in self._search_payload.items() if k != "personIds"}
        person_ids = self._search_payload["personIds"]

        async def _one(pid: str) -> list[dict]:
            payload = {**base, "personIds": [pid]}
            return await self.hub.search_images(payload)

        results = await asyncio.gather(*[_one(pid) for pid in person_ids])

        for assets in results:
            all_ids.update(a["id"] for a in assets)

        _LOGGER.debug("OR-search found %s unique assets", len(all_ids))
        return list(all_ids)
