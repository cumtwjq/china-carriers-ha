"""Forward each carrier account to its own sensor definitions."""

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_PROVIDER, PROVIDER_MOBILE, PROVIDER_UNICOM
from . import mobile_sensor, unicom_sensor


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    if entry.data[CONF_PROVIDER] == PROVIDER_MOBILE:
        await mobile_sensor.async_setup_entry(hass, entry, async_add_entities)
    elif entry.data[CONF_PROVIDER] == PROVIDER_UNICOM:
        await unicom_sensor.async_setup_entry(hass, entry, async_add_entities)
