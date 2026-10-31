"""Percorso del primo avvio (coordinatore) con Home Assistant simulato: le firme dei finti seguono quelle vere."""
import asyncio
import enum
import importlib
import json
import pathlib
import sys
import types
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

RADICE = pathlib.Path(__file__).resolve().parents[1]
FIX = pathlib.Path(__file__).parent / "fixtures"
ADESSO = datetime(2030, 1, 5, 12, tzinfo=ZoneInfo("Europe/Rome"))


class FintoRecorder:
    def __init__(self):
        self.cancellate = []
        self.precedenti = {}
        self.letture = 0

    def async_clear_statistics(self, statistic_ids, *, on_done=None):
        """In Home Assistant è una @callback: mette in coda e restituisce None, non si può usare con await."""
        self.cancellate.append(statistic_ids)

    async def async_add_executor_job(self, funzione, *argomenti):
        self.letture += 1
        return funzione(self.precedenti, *argomenti[1:])


class FintoStore:
    def __init__(self, *_):
        self.salvato = None

    async def async_load(self):
        return self.salvato

    async def async_save(self, dati):
        self.salvato = json.loads(json.dumps(dati))


class FintoCoordinatore:
    def __class_getitem__(cls, _):
        return cls

    def __init__(self, hass, logger, name, update_interval, config_entry):
        self.hass = hass
        self.update_interval = update_interval
        self.data = None


class Sbagliato(Exception):
    pass


def _modulo(nome, **attributi):
    m = types.ModuleType(nome)
    m.__dict__.update(attributi)
    return m


@pytest.fixture
def ambiente(monkeypatch):
    recorder, importate = FintoRecorder(), []
    moduli = {
        "homeassistant": _modulo("homeassistant"),
        "homeassistant.components": _modulo("homeassistant.components"),
        "homeassistant.components.recorder": _modulo("homeassistant.components.recorder",
                                                     get_instance=lambda _: recorder),
        "homeassistant.components.recorder.models": _modulo(
            "homeassistant.components.recorder.models", StatisticData=dict, StatisticMetaData=dict,
            StatisticMeanType=enum.Enum("StatisticMeanType", "NONE")),
        "homeassistant.components.recorder.statistics": _modulo(
            "homeassistant.components.recorder.statistics",
            async_add_external_statistics=lambda _h, meta, righe: importate.append((meta, righe)),
            statistics_during_period=lambda precedenti, *_: precedenti),
        "homeassistant.config_entries": _modulo("homeassistant.config_entries", ConfigEntry=object),
        "homeassistant.core": _modulo("homeassistant.core", HomeAssistant=object),
        "homeassistant.exceptions": _modulo("homeassistant.exceptions", ConfigEntryAuthFailed=Sbagliato),
        "homeassistant.helpers": _modulo("homeassistant.helpers"),
        "homeassistant.helpers.storage": _modulo("homeassistant.helpers.storage", Store=FintoStore),
        "homeassistant.helpers.update_coordinator": _modulo(
            "homeassistant.helpers.update_coordinator", DataUpdateCoordinator=FintoCoordinatore,
            UpdateFailed=type("UpdateFailed", (Exception,), {})),
        "homeassistant.util": _modulo("homeassistant.util"),
        "homeassistant.util.dt": _modulo("homeassistant.util.dt", now=lambda: ADESSO),
    }
    moduli["homeassistant.util"].dt = moduli["homeassistant.util.dt"]
    pacchetto = types.ModuleType("custom_components.open_dolomiti")   # senza __init__.py: serve solo il coordinatore
    pacchetto.__path__ = [str(RADICE / "custom_components" / "open_dolomiti")]
    moduli["custom_components"] = types.ModuleType("custom_components")
    moduli["custom_components.open_dolomiti"] = pacchetto
    for nome, modulo in moduli.items():
        monkeypatch.setitem(sys.modules, nome, modulo)
    for nome in ("api", "const", "coordinator", "parsing"):
        monkeypatch.delitem(sys.modules, f"custom_components.open_dolomiti.{nome}", raising=False)
    c = importlib.import_module("custom_components.open_dolomiti.coordinator")
    yield types.SimpleNamespace(modulo=c, recorder=recorder, importate=importate,
                                api=importlib.import_module("custom_components.open_dolomiti.api"))
    for nome in ("api", "const", "coordinator", "parsing"):
        sys.modules.pop(f"custom_components.open_dolomiti.{nome}", None)


class FintoClient:
    def __init__(self, ambiente, giorno=None):
        self.chiamate = 0
        self.ambiente = ambiente
        self.giorno = giorno or (lambda _g: json.loads((FIX / "giorno.json").read_text()))

    async def assicura_token(self):
        pass

    async def forniture(self):
        return json.loads((FIX / "forniture.json").read_text())

    async def bollette(self, *_):
        return json.loads((FIX / "bollette.json").read_text())

    async def mesi_preferiti(self):
        return json.loads((FIX / "mesi.json").read_text())

    async def consumi_periodo_xlsx(self, *_):
        return b"file finto"

    async def consumo_giorno(self, _contratto, giorno):
        return self.giorno(giorno)


def _coordinatore(ambiente, client):
    hass = types.SimpleNamespace(config=types.SimpleNamespace(time_zone="Europe/Rome"))
    return ambiente.modulo.CoordinatoreDolomiti(hass, types.SimpleNamespace(entry_id="voce"), client)


def _file(ambiente, monkeypatch):
    """Il file dei consumi dà due giorni, gli altri fino a ieri arrivano uno per uno."""
    monkeypatch.setattr(ambiente.modulo, "giorni_da_xlsx",
                        lambda _: {date(2030, 1, 1): [0.5] * 24, date(2030, 1, 2): [0.5] * 24})


def test_primo_avvio_con_storico_vuoto(ambiente, monkeypatch):
    _file(ambiente, monkeypatch)
    c = _coordinatore(ambiente, FintoClient(ambiente))
    dati = asyncio.run(c._async_update_data())
    assert len(ambiente.recorder.cancellate) == 1
    assert dati["statistic_id"].startswith("open_dolomiti:elettricita_")
    assert dati["ultimo"]["giorno"] == "2030-01-04"
    meta, righe = ambiente.importate[0]
    assert meta["source"] == "open_dolomiti"
    assert len(righe) == 4 * 24
    assert c._store.salvato["riempimento_file"]
    # il secondo aggiornamento non cancella più niente
    asyncio.run(c._async_update_data())
    assert len(ambiente.recorder.cancellate) == 1


def test_errore_dopo_il_file_non_perde_lo_storico(ambiente, monkeypatch):
    _file(ambiente, monkeypatch)

    def giu(_g):
        raise ambiente.api.ErroreConnessione("rete giù")

    client = FintoClient(ambiente, giu)
    c = _coordinatore(ambiente, client)
    with pytest.raises(sys.modules["homeassistant.helpers.update_coordinator"].UpdateFailed):
        asyncio.run(c._async_update_data())
    assert len(ambiente.importate) == 1 and len(ambiente.importate[0][1]) == 2 * 24
    assert sorted(c._store.salvato["giorni"]) == ["2030-01-01", "2030-01-02"]


def test_file_illeggibile_si_scarica_giorno_per_giorno(ambiente):
    c = _coordinatore(ambiente, FintoClient(ambiente))
    dati = asyncio.run(c._async_update_data())   # b"file finto" non è uno zip
    assert ambiente.recorder.cancellate == []
    # massimo 10 giorni per aggiornamento, a partire da 120 giorni fa
    assert dati["ultimo"]["giorno"] == (ADESSO.date() - timedelta(days=111)).isoformat()
    assert len(ambiente.importate[0][1]) == 10 * 24


def test_risposta_inattesa_diventa_nuovo_tentativo(ambiente):
    c = _coordinatore(ambiente, FintoClient(ambiente, lambda _g: {"data": {"hourlyConsumptions": [{"hour": 0, "total": "x"}]}}))
    with pytest.raises(sys.modules["homeassistant.helpers.update_coordinator"].UpdateFailed):
        asyncio.run(c._async_update_data())
    assert c.update_interval == timedelta(minutes=30)


def test_somma_continua_da_uno_storico_piu_vecchio(ambiente, monkeypatch):
    """Righe in statistica più vecchie dell'archivio locale: le nuove proseguono la somma, senza salti all'indietro."""
    c = _coordinatore(ambiente, FintoClient(ambiente))
    c._storico = {"giorni": {"2030-01-02": [0.5] * 24}, "riempimento_file": "2030-01-03", "ultimo": None}
    luce = ambiente.modulo.Fornitura("abc", "ENERGY", None, None, True)
    ambiente.recorder.precedenti = {c.statistic_id(luce): [{"sum": 100.0}, {"sum": 111.825}]}
    giorni = {date(2030, 1, 2): [0.5] * 24, date(2030, 1, 3): [1.0] * 24}
    asyncio.run(c._importa(luce, giorni, date(2030, 1, 3)))
    righe = ambiente.importate[0][1]
    assert len(righe) == 24
    assert righe[0]["sum"] == pytest.approx(111.825 + 12 + 1)
    assert righe[-1]["sum"] == pytest.approx(111.825 + 12 + 24)


def test_dopo_la_cancellazione_la_somma_riparte_da_zero(ambiente, monkeypatch):
    _file(ambiente, monkeypatch)
    c = _coordinatore(ambiente, FintoClient(ambiente))
    ambiente.recorder.precedenti = {"open_dolomiti:elettricita_x": [{"sum": 500.0}]}
    asyncio.run(c._async_update_data())
    assert ambiente.recorder.letture == 0
    assert ambiente.importate[0][1][0]["sum"] == pytest.approx(0.5)

