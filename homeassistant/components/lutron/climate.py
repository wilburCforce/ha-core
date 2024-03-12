"""Support for interface with a Lutron climate system."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.climate import (
    FAN_AUTO,
    FAN_HIGH,
    FAN_LOW,
    FAN_MEDIUM,
    FAN_OFF,
    FAN_ON,
    PRESET_ECO,
    PRESET_NONE,
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_TEMPERATURE, PRECISION_WHOLE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

# from . import DOMAIN, LUTRON_CONTROLLER, LUTRON_DEVICES, LutronDevice
from . import DOMAIN, LutronData
from .entity import LutronDevice

_LOGGER = logging.getLogger(__name__)


PRESET_SCHEDULE = "FOLLOWING_SCHEDULE"
PRESET_HOLD_PERM = "PERMANENT_HOLD"
PRESET_HOLD_PERM_ECO = "PERMANENT_HOLD_ECO"
PRESET_HOLD_TEMP = "TEMPORARY_HOLD"
PRESET_HOLD_TEMP_ECO = "TEMPORARY_HOLD_ECO"
PRESET_EMHEAT = "EMERGENCY_HEAT"

LUTRON_TO_HVAC_ACTION: dict[str, HVACAction] = {
    "EMGHEAT": HVACAction.HEATING,
    "DRY": HVACAction.DRYING,
    "OFFLASTHEAT": HVACAction.IDLE,
    "HEATS1": HVACAction.HEATING,
    "HEATS1S2": HVACAction.HEATING,
    "HEATS1S2S3": HVACAction.HEATING,
    "HEATS3": HVACAction.HEATING,
    "OFFLASTCOOL": HVACAction.IDLE,
    "COOLS1": HVACAction.COOLING,
    "COOLS1S2": HVACAction.COOLING,
    "OFF": HVACAction.OFF,
}

# Because there are some codes that don't map into HAccccccccccccccccccccccccc
# we need to spell out both directions
# Emergency Heat needs to be addressed with a preset
LUTRON_TO_HVAC_MODES: dict[str, HVACMode] = {
    "OFF": HVACMode.OFF,
    "HEAT": HVACMode.HEAT,
    "COOL": HVACMode.COOL,
    "AUTO": HVACMode.AUTO,
    #'EMERGENCY_HEAT': HVACMode.HEAT,
    "LOCKED": HVACMode.OFF,
    "FAN": HVACMode.FAN_ONLY,
    "DRY": HVACMode.DRY,
}

LUTRON_TO_FAN_MODES: dict[str, str] = {
    "AUTO": FAN_AUTO,
    "ON": FAN_ON,
    "NO_FAN": FAN_OFF,
    "HIGH": FAN_HIGH,
    "MEDIUM": FAN_MEDIUM,
    "LOW": FAN_LOW,
}


def _find_key_by_value(dictionary, value):
    for key, val in dictionary.items():
        if val == value:
            return key
    return None


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Lutron climate platform."""

    entry_data: LutronData = hass.data[DOMAIN][config_entry.entry_id]
    _LOGGER.info("############# TRYING HA HVAC")
    async_add_entities(
        [
            LutronClimate(area_name, device, entry_data.client)
            for area_name, device in entry_data.hvacs
        ],
        True,
    )


class LutronClimate(LutronDevice, ClimateEntity):
    """Representation of a Lutron HVAC device."""

    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.FAN_MODE
        | ClimateEntityFeature.PRESET_MODE
        | ClimateEntityFeature.TURN_ON
        | ClimateEntityFeature.TURN_OFF
    )
    _attr_translation_key = "lutron"
    _attr_name = None
    # need to add support for aux heater and conditionals
    # unit = hass.config.units.temperature_unit

    def __init__(self, area_name, lutron_device, controller) -> None:
        """Initialize the HVAC device."""
        _LOGGER.info("INIT HA HVAC DATA: %s", vars(lutron_device))
        # _LOGGER.info("###### INIT HA HVAC DATA: %s", lutron_device._integration_id)
        # self._hass = hass
        self._lutron_device = lutron_device
        self._attr_hvac_mode = None
        # self._attr_preset_mode = PRESET_NONE
        self._attr_hvac_action = HVACAction.IDLE
        self._attr_current_temperature = None
        self._attr_target_temperature = None
        self._attr_min_temp = (
            float(lutron_device._min_temp_cool)
            if self.hvac_mode == HVACMode.COOL
            else float(lutron_device._min_temp_heat)
        )
        self._attr_max_temp = (
            float(lutron_device._max_temp_cool)
            if self.hvac_mode == HVACMode.COOL
            else float(lutron_device._max_temp_heat)
        )
        # need to add log warning for different units of temp
        self._prev_unit_mode = None
        self._prev_fan_mode = None
        # self._prev_temp = lutron_device.current_temperature
        # self._prev_target_temp = None
        # We use fahrenheit temperature unit and allow HA to convert
        self._attr_temperature_unit = UnitOfTemperature.FAHRENHEIT
        self._attr_precision = PRECISION_WHOLE
        self._attr_hvac_modes = [
            LUTRON_TO_HVAC_MODES[mode]
            for mode in self._lutron_device._operating_modes
            if mode in LUTRON_TO_HVAC_MODES
        ]
        self._attr_fan_modes = [
            LUTRON_TO_FAN_MODES[mode]
            for mode in self._lutron_device._fan_modes
            if mode in LUTRON_TO_FAN_MODES
        ]

        super().__init__(area_name, lutron_device, controller)
        # Need to handle hvac action of fan for replacing the call status

    @property
    def preset_mode(self) -> str | None:
        """Get the current preset mode."""
        _LOGGER.info(
            "&&&&&&&&&&& eco_mode ######### %s", self._lutron_device.last_eco_mode()
        )
        if self._lutron_device.last_sch_stat() == "TEMPORARY_HOLD":
            if self._lutron_device.last_eco_mode():
                return PRESET_HOLD_TEMP_ECO
            return PRESET_HOLD_TEMP
        if self._lutron_device.last_sch_stat() == "PERMANENT_HOLD":
            if self._lutron_device.last_eco_mode():
                return PRESET_HOLD_PERM_ECO
            return PRESET_HOLD_PERM
        if self._lutron_device.last_eco_mode() is True:
            return PRESET_ECO
        if self._lutron_device.last_sch_stat() == "FOLLOWING_SCHEDULE":
            return PRESET_SCHEDULE

        return PRESET_NONE

    @property
    def preset_modes(self) -> list[str] | None:
        """Get the supported preset modes."""
        presets = [PRESET_SCHEDULE, PRESET_HOLD_TEMP]
        _LOGGER.info("&&&&&&&&&&&&&&&&&&&&&& PRESETTING &&&&&&&&&& %s", presets)
        if "SCHEDULE_ON_OFF" in self._lutron_device.avail_misc_features:
            if PRESET_HOLD_PERM not in presets:
                presets.append(PRESET_HOLD_PERM)
        if "ECO" in self._lutron_device.avail_misc_features:
            if PRESET_ECO not in presets:
                presets.append(PRESET_ECO)
            if PRESET_HOLD_PERM_ECO not in presets:
                presets.append(PRESET_HOLD_PERM_ECO)
            if PRESET_HOLD_TEMP_ECO not in presets:
                presets.append(PRESET_HOLD_TEMP_ECO)
        _LOGGER.info("&&&&&&&&&&&&&&&&&&&&&& PRESETTING &&&&&&&&&& %s", presets)
        return presets

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        """Set new target preset mode."""

        _LOGGER.debug(
            "Setting HVAC preset_mode to %s for device %s as %s",
            preset_mode,
            self._lutron_device.name,
            preset_mode,
        )
        if self.preset_modes is not None and preset_mode not in self.preset_modes:
            raise ValueError(f"Invalid preset_mode: {preset_mode}")

        ########### Change to schedule status
        self._lutron_device.preset_mode = preset_mode
        self.async_write_ha_state()

    # @property
    # def precision(self) -> float:
    #    """Return the precision of the system."""
    #    if self.hass.config.units.temperature_unit == UnitOfTemperature.CELSIUS:
    #        return PRECISION_HALVES
    #    return PRECISION_WHOLE

    @property
    def target_temperature_step(self) -> float:
        """Return the precision of the system."""
        return self.precision

    @property
    def hvac_mode(self) -> HVACMode | None:
        """Return the current HVAC mode for the device."""
        new_mode = LUTRON_TO_HVAC_MODES.get(self._lutron_device.last_mode())
        if new_mode != HVACMode.OFF:
            # self._prev_unit_mode = new_mode
            _LOGGER.debug("dfg")
        return new_mode

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Set new target hvac mode."""
        if hvac_mode not in self.hvac_modes:
            raise ValueError(f"Invalid hvac_mode: {hvac_mode}")
        _LOGGER.debug(
            "Setting HVAC mode to %s for device %s as %s",
            hvac_mode,
            self._lutron_device.name,
            _find_key_by_value(LUTRON_TO_HVAC_MODES, hvac_mode),
        )
        self._lutron_device.current_mode = _find_key_by_value(
            LUTRON_TO_HVAC_MODES, hvac_mode
        )
        self.async_write_ha_state()

    """Properties and methods related to the FAN Mode"""  # pylint: disable=pointless-string-statement

    @property
    def fan_mode(self) -> str | None:
        """Return the current fan mode for the device."""
        new_mode = LUTRON_TO_FAN_MODES.get(self._lutron_device.last_fan_mode())
        # if new_mode != FAN_OFF:
        #    self._prev_fan_mode = new_mode
        return new_mode

    async def async_set_fan_mode(self, fan_mode: str) -> None:
        """Set new target fan mode."""
        if self.fan_modes is not None and fan_mode not in self.fan_modes:
            raise ValueError(f"Invalid fan mode: {fan_mode}")
        _LOGGER.info(
            "Setting FAN mode to %s for device %s",
            _find_key_by_value(LUTRON_TO_FAN_MODES, fan_mode),
            self._lutron_device.name,
        )
        self._lutron_device.current_fan = _find_key_by_value(
            LUTRON_TO_FAN_MODES, fan_mode
        )
        self.async_write_ha_state()

    @property
    def hvac_action(self) -> HVACAction | None:
        """Return the current running hvac operation if supported."""
        if self.hvac_mode == HVACMode.OFF:
            return None
        return LUTRON_TO_HVAC_ACTION.get(self._lutron_device.last_status())

    @property
    def current_temperature(self) -> float:
        """Return the reported current temperature for the device."""
        new_temp = self._lutron_device.last_temp_f()
        # if new_temp != 0:
        #    self._prev_temp = new_temp  # pylint: disable=attribute-defined-outside-init
        return new_temp

    @property
    def target_temperature(self) -> float:
        """Return the target temperature for the device."""
        if self.hvac_mode == HVACMode.COOL:
            new_temp = self._lutron_device.last_setpoint_cool_f()
        elif self.hvac_mode == HVACMode.HEAT:
            new_temp = self._lutron_device.last_setpoint_heat_f()
        else:
            new_temp = 0
        # if new_temp != 0:
        #    self._prev_target_temp = new_temp
        return new_temp

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Set new target temperature.

        Not all Lutron climate controls support getting the drifts. However they
        require that the heat and cool setpoints be separated by the drift
        amount. For greatest compatibility we calculate a default drift of
        3 degrees to account for this.
        """
        if ATTR_TEMPERATURE not in kwargs:
            raise ValueError(f"Missing parameter {ATTR_TEMPERATURE}")

        temperature = kwargs[ATTR_TEMPERATURE]
        if self.hvac_mode == HVACMode.COOL:
            if round(temperature) - self._lutron_device.setpoint_heat_f < 3:
                self._lutron_device.setpoint_heat_f = round(temperature) - 3
            self._lutron_device.setpoint_cool_f = round(temperature)
        elif self.hvac_mode == HVACMode.HEAT:
            if self._lutron_device.setpoint_cool_f - round(temperature) < 3:
                self._lutron_device.setpoint_cool_f = round(temperature) + 3
            self._lutron_device.setpoint_heat_f = round(temperature)
        self.async_write_ha_state()

    def _request_state(self) -> None:
        """Request the state from the device."""
        self._lutron_device.current_mode  # pylint: disable=pointless-statement
        self._lutron_device.current_temp_f  # pylint: disable=pointless-statement
        self._lutron_device.setpoint_cool_f  # pylint: disable=pointless-statement
        self._lutron_device.call_status  # pylint: disable=pointless-statement
        self._lutron_device.current_fan  # pylint: disable=pointless-statement
        self._lutron_device.schedule_status  # pylint: disable=pointless-statement
        self._lutron_device.eco_mode  # pylint: disable=pointless-statement

    def _update_attrs(self) -> None:
        """Update thermostat attributes."""
        # self._lutron_device.update()
        _LOGGER.info("#######$$ UPD ATTRS ############")
        # self._attr_current_temperature = self._lutron_device.last_temp_f()
        # self._attr_hvac_mode = LUTRON_TO_HVAC_MODES.get(self._lutron_device.last_mode())
        # self._attr_hvac_action = LUTRON_TO_HVAC_ACTION.get(
        #    self._lutron_device.last_status()
        # )

        # if LUTRON_TO_HVAC_MODES.get(self._lutron_device.last_mode()) == HVACMode.COOL:
        #    self._prev_target_temp = self._lutron_device.setpoint_cool_f
        # elif LUTRON_TO_HVAC_MODES.get(self._lutron_device.last_mode()) == HVACMode.HEAT:
        #    self._prev_target_temp = self._lutron_device.setpoint_heat_f
        # else:
        #    self._prev_target_temp = None
        # self._attr_target_temperature = self._prev_target_temp
        # last_stat = self._lutron_device.last_status()
        # _LOGGER.info(
        #    "#######$$$###$$$$$##############  %s is a %s val: %s",
        #    last_stat,
        #    type(last_stat),
        #    "last_stat.name",
        # )
        # self._attr_hvac_action = LUTRON_TO_HVAC_ACTION[last_stat]

        # need to init preset and work on its logic
        # self._preset_mode = TOUCHLINE_HA_PRESETS.get(
        #    (self.unit.get_operation_mode(), self.unit.get_week_program())
        # )
