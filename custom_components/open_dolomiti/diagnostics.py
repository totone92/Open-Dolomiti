"""Diagnostica con redazione: niente credenziali, token, codice fiscale, POD/PDR o numeri di contratto."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from . import VoceDolomiti
from .const import DA_REDIGERE


async def async_get_config_entry_diagnostics(hass: HomeAssistant, voce: VoceDolomiti) -> dict[str, Any]:
    c = voce.runtime_data
    dati = c.data or {}
    return {
        "voce": async_redact_data(dict(voce.data), DA_REDIGERE),
        "sessione": c.client.sessione.info(),
        "chiamate_totali": c.client.chiamate,
        "ultimo_aggiornamento": c.ultimo_esito,
        "intervallo": str(c.update_interval),
        "forniture": [{k: v for k, v in f.items() if k in ("tipo", "offerta", "fine", "contatore_2g")}
                      for f in dati.get("forniture", [])],
        "bollette": [{k: v for k, v in (b or {}).items()} for b in dati.get("bollette", {}).values()],
        "mesi": dati.get("mesi"),
        "ultimo_giorno": dati.get("ultimo"),
        "statistica": "presente" if dati.get("statistic_id") else "assente",
    }
