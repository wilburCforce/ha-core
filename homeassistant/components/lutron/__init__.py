"""Component for interacting with a Lutron RadioRA 2 system."""

from dataclasses import dataclass
import logging

from pylutron import (
    HVAC,
    Button,
    Keypad,
    Led,
    Lutron,
    LutronEvent,
    OccupancyGroup,
    Output,
)
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    ATTR_ID,
    CONF_HOST,
    CONF_PASSWORD,
    CONF_USERNAME,
    Platform,
)
from homeassistant.core import DOMAIN as HOMEASSISTANT_DOMAIN, HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import device_registry as dr, entity_registry as er
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.issue_registry import IssueSeverity, async_create_issue
from homeassistant.helpers.typing import ConfigType
from homeassistant.util import slugify

from .const import DOMAIN

PLATFORMS = [
    Platform.BINARY_SENSOR,
    Platform.CLIMATE,
    Platform.COVER,
    Platform.FAN,
    Platform.LIGHT,
    Platform.SCENE,
    Platform.SWITCH,
]

_LOGGER = logging.getLogger(__name__)

# Attribute on events that indicates what action was taken with the button.
ATTR_ACTION = "action"
ATTR_FULL_ID = "full_id"
ATTR_UUID = "uuid"

CONFIG_SCHEMA = vol.Schema(
    {
        DOMAIN: vol.Schema(
            {
                vol.Required(CONF_HOST): cv.string,
                vol.Required(CONF_PASSWORD): cv.string,
                vol.Required(CONF_USERNAME): cv.string,
            }
        )
    },
    extra=vol.ALLOW_EXTRA,
)


async def _async_import(hass: HomeAssistant, base_config: ConfigType) -> None:
    """Import a config entry from configuration.yaml."""

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_IMPORT},
        data=base_config[DOMAIN],
    )
    if (
        result["type"] == FlowResultType.CREATE_ENTRY
        or result["reason"] == "single_instance_allowed"
    ):
        async_create_issue(
            hass,
            HOMEASSISTANT_DOMAIN,
            f"deprecated_yaml_{DOMAIN}",
            breaks_in_ha_version="2024.7.0",
            is_fixable=False,
            issue_domain=DOMAIN,
            severity=IssueSeverity.WARNING,
            translation_key="deprecated_yaml",
            translation_placeholders={
                "domain": DOMAIN,
                "integration_title": "Lutron",
            },
        )
        return
    async_create_issue(
        hass,
        DOMAIN,
        f"deprecated_yaml_import_issue_{result['reason']}",
        breaks_in_ha_version="2024.7.0",
        is_fixable=False,
        issue_domain=DOMAIN,
        severity=IssueSeverity.WARNING,
        translation_key=f"deprecated_yaml_import_issue_{result['reason']}",
        translation_placeholders={
            "domain": DOMAIN,
            "integration_title": "Lutron",
        },
    )


async def async_setup(hass: HomeAssistant, base_config: ConfigType) -> bool:
    """Set up the Lutron component."""
    if DOMAIN in base_config:
        hass.async_create_task(_async_import(hass, base_config))
    return True


class LutronButton:
    """Representation of a button on a Lutron keypad.

    This is responsible for firing events as keypad buttons are pressed
    (and possibly released, depending on the button type). It is not
    represented as an entity; it simply fires events.
    """

    def __init__(
        self, hass: HomeAssistant, area_name: str, keypad: Keypad, button: Button
    ) -> None:
        """Register callback for activity on the button."""
        name = f"{keypad.name}: {button.name}"
        if button.name == "Unknown Button":
            name += f" {button.number}"
        self._hass = hass
        self._has_release_event = (
            button.button_type is not None and "RaiseLower" in button.button_type
        )
        self._id = slugify(name)
        self._keypad = keypad
        self._area_name = area_name
        self._button_name = button.name
        self._button = button
        self._event = "lutron_event"
        self._full_id = slugify(f"{area_name} {name}")
        self._uuid = button.uuid

        button.subscribe(self.button_callback, None)

    def button_callback(
        self, _button: Button, _context: None, event: LutronEvent, _params: dict
    ) -> None:
        """Fire an event about a button being pressed or released."""
        # Events per button type:
        #   RaiseLower -> pressed/released
        #   SingleAction -> single
        action = None
        if self._has_release_event:
            if event == Button.Event.PRESSED:
                action = "pressed"
            else:
                action = "released"
        elif event == Button.Event.PRESSED:
            action = "single"

        if action:
            data = {
                ATTR_ID: self._id,
                ATTR_ACTION: action,
                ATTR_FULL_ID: self._full_id,
                ATTR_UUID: self._uuid,
            }
            self._hass.bus.fire(self._event, data)


@dataclass(slots=True, kw_only=True)
class LutronData:
    """Storage class for platform global data."""

    client: Lutron
    binary_sensors: list[tuple[str, OccupancyGroup]]
    buttons: list[LutronButton]
    covers: list[tuple[str, Output]]
    fans: list[tuple[str, Output]]
    hvacs: list[tuple[str, HVAC]]
    lights: list[tuple[str, Output]]
    scenes: list[tuple[str, Keypad, Button, Led]]
    switches: list[tuple[str, Output]]


async def async_setup_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:
    """Set up the Lutron integration."""

    host = config_entry.data[CONF_HOST]
    uid = config_entry.data[CONF_USERNAME]
    pwd = config_entry.data[CONF_PASSWORD]

    lutron_client = Lutron(host, uid, pwd)
    await hass.async_add_executor_job(lutron_client.load_xml_db)
    lutron_client.connect()
    _LOGGER.info("Connected to main repeater at %s", host)

    entity_registry = er.async_get(hass)
    device_registry = dr.async_get(hass)

    entry_data = LutronData(
        client=lutron_client,
        binary_sensors=[],
        buttons=[],
        covers=[],
        fans=[],
        hvacs=[],
        lights=[],
        scenes=[],
        switches=[],
    )
    _LOGGER.info("############## LUTRON DATA %s", vars(lutron_client))
    # Sort our devices into types
    _LOGGER.debug("Start adding devices")
    for area in lutron_client.areas:
        _LOGGER.debug("Working on area %s", area.name)
        for output in area.outputs:
            platform = None
            _LOGGER.debug("Working on output %s", output.type)
            if output.type == "SYSTEM_SHADE":
                entry_data.covers.append((area.name, output))
                platform = Platform.COVER
            elif output.type == "CEILING_FAN_TYPE":
                entry_data.fans.append((area.name, output))
                platform = Platform.FAN
                # Deprecated, should be removed in 2024.8
                entry_data.lights.append((area.name, output))
            elif output.is_dimmable:
                entry_data.lights.append((area.name, output))
                platform = Platform.LIGHT
            else:
                entry_data.switches.append((area.name, output))
                platform = Platform.SWITCH

            _async_check_entity_unique_id(
                hass,
                entity_registry,
                platform,
                output.uuid,
                output.legacy_uuid,
                entry_data.client.guid,
            )
            _async_check_device_identifiers(
                hass,
                device_registry,
                output.uuid,
                output.legacy_uuid,
                entry_data.client.guid,
            )

        for keypad in area.keypads:
            for button in keypad.buttons:
                # If the button has a function assigned to it, add it as a scene
                if button.name != "Unknown Button" and button.button_type in (
                    "SingleAction",
                    "Toggle",
                    "SingleSceneRaiseLower",
                    "MasterRaiseLower",
                ):
                    # Associate an LED with a button if there is one
                    led = next(
                        (led for led in keypad.leds if led.number == button.number),
                        None,
                    )
                    entry_data.scenes.append((area.name, keypad, button, led))

                    platform = Platform.SCENE
                    _async_check_entity_unique_id(
                        hass,
                        entity_registry,
                        platform,
                        button.uuid,
                        button.legacy_uuid,
                        entry_data.client.guid,
                    )
                    if led is not None:
                        platform = Platform.SWITCH
                        _async_check_entity_unique_id(
                            hass,
                            entity_registry,
                            platform,
                            led.uuid,
                            led.legacy_uuid,
                            entry_data.client.guid,
                        )

                entry_data.buttons.append(LutronButton(hass, area.name, keypad, button))
        if area.occupancy_group is not None:
            entry_data.binary_sensors.append((area.name, area.occupancy_group))
            platform = Platform.BINARY_SENSOR
            _async_check_entity_unique_id(
                hass,
                entity_registry,
                platform,
                area.occupancy_group.uuid,
                area.occupancy_group.legacy_uuid,
                entry_data.client.guid,
            )
            _async_check_device_identifiers(
                hass,
                device_registry,
                area.occupancy_group.uuid,
                area.occupancy_group.legacy_uuid,
                entry_data.client.guid,
            )
    for hvac in lutron_client.hvacs:
        _LOGGER.info(f"HASSDATA CLIMATE: {hvac.name} DATA: {str(hvac)}")  # noqa: G004
        entry_data.hvacs.append((hvac.name, hvac))

    device_registry.async_get_or_create(
        config_entry_id=config_entry.entry_id,
        identifiers={(DOMAIN, lutron_client.guid)},
        manufacturer="Lutron",
        name="Main repeater",
    )

    hass.data.setdefault(DOMAIN, {})[config_entry.entry_id] = entry_data

    await hass.config_entries.async_forward_entry_setups(config_entry, PLATFORMS)

    return True


def _async_check_entity_unique_id(
    hass: HomeAssistant,
    entity_registry: er.EntityRegistry,
    platform: str,
    uuid: str,
    legacy_uuid: str,
    controller_guid: str,
) -> None:
    """If uuid becomes available update to use it."""

    if not uuid:
        return

    unique_id = f"{controller_guid}_{legacy_uuid}"
    entity_id = entity_registry.async_get_entity_id(
        domain=platform, platform=DOMAIN, unique_id=unique_id
    )

    if entity_id:
        new_unique_id = f"{controller_guid}_{uuid}"
        _LOGGER.debug("Updating entity id from %s to %s", unique_id, new_unique_id)
        entity_registry.async_update_entity(entity_id, new_unique_id=new_unique_id)


def _async_check_device_identifiers(
    hass: HomeAssistant,
    device_registry: dr.DeviceRegistry,
    uuid: str,
    legacy_uuid: str,
    controller_guid: str,
) -> None:
    """If uuid becomes available update to use it."""

    if not uuid:
        return

    unique_id = f"{controller_guid}_{legacy_uuid}"
    device = device_registry.async_get_device(identifiers={(DOMAIN, unique_id)})
    if device:
        new_unique_id = f"{controller_guid}_{uuid}"
        _LOGGER.debug("Updating device id from %s to %s", unique_id, new_unique_id)
        device_registry.async_update_device(
            device.id, new_identifiers={(DOMAIN, new_unique_id)}
        )


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Clean up resources and entities associated with the integration."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
