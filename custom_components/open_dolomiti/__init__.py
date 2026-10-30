"""Open Dolomiti (non ufficiale): consumi orari del contatore elettrico 2G dall'area clienti myDolomiti.

Progetto non affiliato a Dolomiti Energia. Usa API non ufficiali del portale clienti, che possono cambiare.
"""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import ClientDolomiti
from .const import CONF_EMAIL, CONF_PASSWORD
from .coordinator import CoordinatoreDolomiti

PIATTAFORME = [Platform.SENSOR]

type VoceDolomiti = ConfigEntry[CoordinatoreDolomiti]


async def async_setup_entry(hass: HomeAssistant, voce: VoceDolomiti) -> bool:
    client = ClientDolomiti(async_get_clientsession(hass), voce.data[CONF_EMAIL], voce.data[CONF_PASSWORD])
    coordinatore = CoordinatoreDolomiti(hass, voce, client)
    await coordinatore.async_config_entry_first_refresh()
    voce.runtime_data = coordinatore
    await hass.config_entries.async_forward_entry_setups(voce, PIATTAFORME)
    return True


async def async_unload_entry(hass: HomeAssistant, voce: VoceDolomiti) -> bool:
    return await hass.config_entries.async_unload_platforms(voce, PIATTAFORME)
