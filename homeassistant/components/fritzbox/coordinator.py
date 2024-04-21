"""Data update coordinator for AVM FRITZ!SmartHome devices."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from pyfritzhome import Fritzhome, FritzhomeDevice, LoginError
from pyfritzhome.devicetypes import FritzhomeTemplate
from requests.exceptions import ConnectionError as RequestConnectionError, HTTPError

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import CONF_CONNECTIONS, DOMAIN, LOGGER


@dataclass
class FritzboxCoordinatorData:
    """Data Type of FritzboxDataUpdateCoordinator's data."""

    devices: dict[str, FritzhomeDevice]
    templates: dict[str, FritzhomeTemplate]


class FritzboxDataUpdateCoordinator(DataUpdateCoordinator[FritzboxCoordinatorData]):
    """Fritzbox Smarthome device data update coordinator."""

    config_entry: ConfigEntry
    configuration_url: str

    def __init__(self, hass: HomeAssistant, name: str, has_templates: bool) -> None:
        """Initialize the Fritzbox Smarthome device coordinator."""
        super().__init__(
            hass,
            LOGGER,
            name=name,
            update_interval=timedelta(seconds=30),
        )

        self.fritz: Fritzhome = hass.data[DOMAIN][self.config_entry.entry_id][
            CONF_CONNECTIONS
        ]
        self.configuration_url = self.fritz.get_prefixed_host()
        self.has_templates = has_templates
        self.new_devices: set[str] = set()
        self.new_templates: set[str] = set()

        self.data = FritzboxCoordinatorData({}, {})

    async def async_setup(self) -> None:
        """Set up the coordinator."""
        await self.async_config_entry_first_refresh()
        self.cleanup_removed_devices(
            list(self.data.devices) + list(self.data.templates)
        )

    def cleanup_removed_devices(self, avaiable_ains: list[str]) -> None:
        """Cleanup entity and device registry from removed devices."""
        entity_reg = er.async_get(self.hass)
        for entity in er.async_entries_for_config_entry(
            entity_reg, self.config_entry.entry_id
        ):
            if entity.unique_id.split("_")[0] not in avaiable_ains:
                LOGGER.debug("Removing obsolete entity entry %s", entity.entity_id)
                entity_reg.async_remove(entity.entity_id)

        device_reg = dr.async_get(self.hass)
        identifiers = {(DOMAIN, ain) for ain in avaiable_ains}
        for device in dr.async_entries_for_config_entry(
            device_reg, self.config_entry.entry_id
        ):
            if not set(device.identifiers) & identifiers:
                LOGGER.debug("Removing obsolete device entry %s", device.name)
                device_reg.async_update_device(
                    device.id, remove_config_entry_id=self.config_entry.entry_id
                )

    def _update_fritz_devices(self) -> FritzboxCoordinatorData:
        """Update all fritzbox device data."""
        try:
            self.fritz.update_devices()
            if self.has_templates:
                self.fritz.update_templates()
        except RequestConnectionError as ex:
            raise UpdateFailed from ex
        except HTTPError:
            # If the device rebooted, login again
            try:
                self.fritz.login()
            except LoginError as ex:
                raise ConfigEntryAuthFailed from ex
            self.fritz.update_devices()
            if self.has_templates:
                self.fritz.update_templates()

        devices = self.fritz.get_devices()
        device_data = {}
        for device in devices:
            # assume device as unavailable, see #55799
            if (
                device.has_powermeter
                and device.present
                and isinstance(device.voltage, int)
                and device.voltage <= 0
                and isinstance(device.power, int)
                and device.power <= 0
                and device.energy <= 0
            ):
                LOGGER.debug("Assume device %s as unavailable", device.name)
                device.present = False

            device_data[device.ain] = device

        template_data = {}
        if self.has_templates:
            templates = self.fritz.get_templates()
            for template in templates:
                template_data[template.ain] = template

        self.new_devices = device_data.keys() - self.data.devices.keys()
        self.new_templates = template_data.keys() - self.data.templates.keys()

        if (
            self.data.devices.keys() - device_data.keys()
            or self.data.templates.keys() - template_data.keys()
        ):
            self.cleanup_removed_devices(list(device_data) + list(template_data))

        return FritzboxCoordinatorData(devices=device_data, templates=template_data)

    async def _async_update_data(self) -> FritzboxCoordinatorData:
        """Fetch all device data."""
        return await self.hass.async_add_executor_job(self._update_fritz_devices)
