"""Sensori: ultimo giorno disponibile, mesi, picco di potenza, contratti e ultima bolletta.

Il consumo orario va nelle statistiche esterne (dashboard Energia), non in un sensore: i dati arrivano con 1-2
giorni di ritardo e un sensore "in tempo reale" sarebbe fuorviante.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorEntityDescription
from homeassistant.const import UnitOfEnergy, UnitOfPower
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import VoceDolomiti
from .const import DOMAIN, NOME_DISPOSITIVO, TIPO_GAS, TIPO_LUCE
from .coordinator import CoordinatoreDolomiti


@dataclass(frozen=True, kw_only=True)
class Descrizione(SensorEntityDescription):
    valore: Callable[[dict], Any]
    attributi: Callable[[dict], dict] | None = None


def _ultimo(d: dict) -> dict:
    return d.get("ultimo") or {}


def _mese(d: dict, quale: str) -> dict:
    return (d.get("mesi") or {}).get(quale) or {}


def _data(v):
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(v) if v else None
    except (TypeError, ValueError):
        return None


GENERALI = (
    Descrizione(key="ultimo_giorno", translation_key="ultimo_giorno", device_class=SensorDeviceClass.ENERGY,
                native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR, suggested_display_precision=2,
                valore=lambda d: _ultimo(d).get("totale"),
                attributi=lambda d: {"giorno": _ultimo(d).get("giorno")}),
    Descrizione(key="data_ultimo_giorno", translation_key="data_ultimo_giorno", device_class=SensorDeviceClass.DATE,
                valore=lambda d: _data(_ultimo(d).get("giorno"))),
    Descrizione(key="picco_potenza", translation_key="picco_potenza", device_class=SensorDeviceClass.POWER,
                native_unit_of_measurement=UnitOfPower.KILO_WATT, suggested_display_precision=3,
                valore=lambda d: _ultimo(d).get("picco_kw"),
                attributi=lambda d: {"fascia": _ultimo(d).get("fascia_picco"), "giorno": _ultimo(d).get("giorno")}),
    Descrizione(key="fascia_picco", translation_key="fascia_picco", valore=lambda d: _ultimo(d).get("fascia_picco")),
    Descrizione(key="mese_corrente", translation_key="mese_corrente", device_class=SensorDeviceClass.ENERGY,
                native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR, suggested_display_precision=2,
                valore=lambda d: _mese(d, "corrente").get("totale"),
                attributi=lambda d: {"dal": _mese(d, "corrente").get("dal"), "al": _mese(d, "corrente").get("al")}),
    Descrizione(key="mese_precedente", translation_key="mese_precedente", device_class=SensorDeviceClass.ENERGY,
                native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR, suggested_display_precision=2,
                valore=lambda d: _mese(d, "precedente").get("totale"),
                attributi=lambda d: {"dal": _mese(d, "precedente").get("dal"),
                                     "al": _mese(d, "precedente").get("al")}),
)


def _fornitura(d: dict, chiave: str) -> dict:
    return next((f for f in d.get("forniture", []) if f["chiave"] == chiave), {})


def _bolletta(d: dict, chiave: str) -> dict:
    return (d.get("bollette") or {}).get(chiave) or {}


def per_fornitura(chiave: str, nome_tipo: str) -> tuple[Descrizione, ...]:
    p = {"tipo": nome_tipo}
    return (
        Descrizione(key=f"offerta_{chiave}", translation_key="offerta", translation_placeholders=p,
                    valore=lambda d: _fornitura(d, chiave).get("offerta")),
        Descrizione(key=f"fine_contratto_{chiave}", translation_key="fine_contratto", translation_placeholders=p,
                    device_class=SensorDeviceClass.DATE, valore=lambda d: _data(_fornitura(d, chiave).get("fine"))),
        Descrizione(key=f"bolletta_importo_{chiave}", translation_key="bolletta_importo", translation_placeholders=p,
                    device_class=SensorDeviceClass.MONETARY, native_unit_of_measurement="EUR",
                    suggested_display_precision=2, valore=lambda d: _bolletta(d, chiave).get("importo"),
                    attributi=lambda d: {"dal": _bolletta(d, chiave).get("dal"), "al": _bolletta(d, chiave).get("al"),
                                         "da_pagare": _bolletta(d, chiave).get("da_pagare")}),
        Descrizione(key=f"bolletta_periodo_{chiave}", translation_key="bolletta_periodo", translation_placeholders=p,
                    valore=lambda d: _periodo(_bolletta(d, chiave))),
        Descrizione(key=f"bolletta_scadenza_{chiave}", translation_key="bolletta_scadenza",
                    translation_placeholders=p, device_class=SensorDeviceClass.DATE,
                    valore=lambda d: _data(_bolletta(d, chiave).get("scadenza"))),
        Descrizione(key=f"bolletta_stato_{chiave}", translation_key="bolletta_stato", translation_placeholders=p,
                    valore=lambda d: _bolletta(d, chiave).get("stato")),
    )


def _periodo(b: dict) -> str | None:
    dal, al = _data(b.get("dal")), _data(b.get("al"))
    if not dal and not al:
        return None
    return f"{dal:%d/%m/%Y} – {al:%d/%m/%Y}" if dal and al else f"{(dal or al):%d/%m/%Y}"


async def async_setup_entry(hass: HomeAssistant, voce: VoceDolomiti,
                            async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    c = voce.runtime_data
    entita = [SensoreDolomiti(c, voce.entry_id, d) for d in GENERALI]
    conteggio: dict[str, int] = {}
    for f in (c.data or {}).get("forniture", []):
        nome = {TIPO_LUCE: "luce", TIPO_GAS: "gas"}.get(f["tipo"], f["tipo"].lower())
        conteggio[nome] = conteggio.get(nome, 0) + 1
        nome_tipo = nome if conteggio[nome] == 1 else f"{nome} {conteggio[nome]}"
        entita += [SensoreDolomiti(c, voce.entry_id, d) for d in per_fornitura(f["chiave"], nome_tipo)]
    async_add_entities(entita)


class SensoreDolomiti(CoordinatorEntity[CoordinatoreDolomiti], SensorEntity):
    _attr_has_entity_name = True
    entity_description: Descrizione

    def __init__(self, c: CoordinatoreDolomiti, entry_id: str, descrizione: Descrizione) -> None:
        super().__init__(c)
        self.entity_description = descrizione
        self._attr_unique_id = f"{entry_id}_{descrizione.key}"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, entry_id)}, name=NOME_DISPOSITIVO,
                                            manufacturer="Integrazione non ufficiale",
                                            model="Area clienti myDolomiti", entry_type=DeviceEntryType.SERVICE)

    @property
    def native_value(self):
        return self.entity_description.valore(self.coordinator.data or {})

    @property
    def extra_state_attributes(self) -> dict | None:
        if not self.entity_description.attributi:
            return None
        return {k: (v.isoformat() if isinstance(v, date) else v)
                for k, v in self.entity_description.attributi(self.coordinator.data or {}).items()}
