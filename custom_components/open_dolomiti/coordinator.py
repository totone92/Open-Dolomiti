"""Coordinatore: poche chiamate al giorno, storico orario nelle statistiche esterne di Home Assistant."""

from __future__ import annotations

import hashlib
import logging
import zipfile
from datetime import date, datetime, timedelta
from xml.etree import ElementTree
from zoneinfo import ZoneInfo

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.models import StatisticData, StatisticMeanType, StatisticMetaData
from homeassistant.components.recorder.statistics import async_add_external_statistics
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import ClientDolomiti, ErroreAutenticazione, ErroreConnessione
from .const import (
    DOMAIN, GIORNI_INIZIALI, GIORNI_PER_AGGIORNAMENTO, INTERVALLO, INTERVALLO_CONTRATTI, RITENTA_MIN, TIPO_GAS,
    TIPO_LUCE,
)
from .parsing import (
    ErroreFormato, Fornitura, forniture_da_risposta, giorni_da_xlsx, giorno_da_risposta, mesi_preferiti, righe_statistiche,
    ultima_bolletta,
)

_LOGGER = logging.getLogger(__name__)
VERSIONE_STORE = 1


def chiave_opaca(testo: str) -> str:
    """Identificativo stabile e non reversibile (per unique_id e statistic_id: mai POD o contratto in chiaro)."""
    return hashlib.sha256(f"{DOMAIN}:{testo}".encode()).hexdigest()[:10]


class CoordinatoreDolomiti(DataUpdateCoordinator[dict]):
    def __init__(self, hass: HomeAssistant, voce: ConfigEntry, client: ClientDolomiti) -> None:
        super().__init__(hass, _LOGGER, name=DOMAIN, update_interval=INTERVALLO, config_entry=voce)
        self.client = client
        self._store: Store = Store(hass, VERSIONE_STORE, f"{DOMAIN}.{voce.entry_id}")
        self._storico: dict | None = None
        self._forniture: list[Fornitura] = []
        self._bollette: dict[str, dict | None] = {}
        self._contratti_aggiornati: datetime | None = None
        self._errori = 0
        self.ultimo_esito: dict = {}

    @property
    def fuso(self) -> ZoneInfo:
        return ZoneInfo(self.hass.config.time_zone)

    def statistic_id(self, fornitura: Fornitura) -> str:
        return f"{DOMAIN}:elettricita_{fornitura.chiave}"

    # ------------------------------------------------------------ ciclo

    async def _async_update_data(self) -> dict:
        try:
            dati = await self._aggiorna()
        except ErroreAutenticazione as err:
            raise ConfigEntryAuthFailed("credenziali rifiutate da myDolomiti") from err
        except (ErroreConnessione, ErroreFormato, ValueError) as err:
            self._errori += 1
            attesa = min(INTERVALLO, RITENTA_MIN * (2 ** (self._errori - 1)))
            self.update_interval = attesa
            # di un ValueError solo il tipo: il messaggio può riportare valori della risposta
            motivo = type(err).__name__ if isinstance(err, ValueError) else str(err)
            _LOGGER.warning("aggiornamento non riuscito (%s), nuovo tentativo tra %s", motivo, attesa)
            raise UpdateFailed(motivo) from err
        self._errori = 0
        self.update_interval = INTERVALLO
        return dati

    async def _aggiorna(self) -> dict:
        if self._storico is None:
            self._storico = await self._store.async_load() or {"giorni": {}, "ultimo": None}
        chiamate_prima = self.client.chiamate
        adesso = dt_util.now()
        await self.client.assicura_token()

        if not self._forniture or not self._contratti_aggiornati or \
                adesso - self._contratti_aggiornati > INTERVALLO_CONTRATTI:
            self._forniture = forniture_da_risposta(await self.client.forniture(), chiave_opaca)
            for f in self._forniture:
                try:
                    self._bollette[f.chiave] = ultima_bolletta(await self.client.bollette(f.conto, f.pod_pdr, f.tipo))
                except ErroreConnessione as err:
                    _LOGGER.debug("bollette %s non disponibili: %s", f.tipo, err)
            self._contratti_aggiornati = adesso

        mesi = mesi_preferiti(await self.client.mesi_preferiti())
        luce = next((f for f in self._forniture if f.tipo == TIPO_LUCE and f.contatore_2g), None) or \
            next((f for f in self._forniture if f.tipo == TIPO_LUCE), None)
        nuovi = await self._aggiorna_storico(luce, adesso.date()) if luce else 0

        self.ultimo_esito = {"quando": adesso.isoformat(), "chiamate": self.client.chiamate - chiamate_prima,
                             "giorni_nuovi": nuovi, "giorni_in_storico": len(self._storico["giorni"])}
        _LOGGER.debug("aggiornato: %s", self.ultimo_esito)
        return {
            "forniture": [{"chiave": f.chiave, "tipo": f.tipo, "offerta": f.offerta, "fine": f.fine,
                           "contatore_2g": f.contatore_2g} for f in self._forniture],
            "bollette": {f.chiave: self._bollette.get(f.chiave) for f in self._forniture},
            "mesi": mesi,
            "ultimo": self._storico.get("ultimo"),
            "statistic_id": self.statistic_id(luce) if luce else None,
        }

    # ------------------------------------------------------------ storico orario

    async def _aggiorna_storico(self, luce: Fornitura, oggi: date) -> int:
        giorni: dict[str, list] = self._storico["giorni"]
        cambiati: set[date] = set()
        try:
            await self._scarica_giorni(luce, oggi, giorni, cambiati)
        finally:
            # anche dopo un errore: i giorni già scaricati non si riscaricano, senza import mancherebbero per sempre
            if cambiati:
                self._importa(luce, {date.fromisoformat(k): v for k, v in giorni.items()}, min(cambiati))
                await self._store.async_save(self._storico)
        return len(cambiati)

    async def _scarica_giorni(self, luce: Fornitura, oggi: date, giorni: dict[str, list], cambiati: set[date]) -> None:
        if not self._storico.get("riempimento_file"):
            # il file excel (120 giorni) si legge finché non riesce una volta; i giorni scaricati uno per uno restano
            try:
                dal_file = giorni_da_xlsx(await self.client.consumi_periodo_xlsx(luce.contratto, GIORNI_INIZIALI))
            except (ErroreConnessione, ValueError, KeyError, IndexError, zipfile.BadZipFile, ElementTree.ParseError) as err:
                _LOGGER.warning("riempimento iniziale dal file non riuscito (%s): si scarica giorno per giorno",
                                type(err).__name__)
                dal_file = {}
            if dal_file:
                if not giorni:
                    get_instance(self.hass).async_clear_statistics([self.statistic_id(luce)])   # @callback, senza await
                self._storico["riempimento_file"] = date.today().isoformat()
                for g, valori in dal_file.items():
                    giorni[g.isoformat()] = valori
                    cambiati.add(g)
                _LOGGER.info("storico iniziale: %s giorni dal file dei consumi", len(dal_file))
            else:
                _LOGGER.warning("file dei consumi senza righe giornaliere riconoscibili: si scarica giorno per giorno")

        # giorni mancanti fino a ieri, uno per uno, con un tetto per aggiornamento
        ultimo = max((date.fromisoformat(g) for g in giorni), default=oggi - timedelta(days=GIORNI_INIZIALI + 1))
        giorno, scaricati = ultimo + timedelta(days=1), 0
        while giorno < oggi and scaricati < GIORNI_PER_AGGIORNAMENTO:
            g = giorno_da_risposta(await self.client.consumo_giorno(luce.contratto, giorno), giorno)
            scaricati += 1
            if not g.disponibile:
                _LOGGER.debug("%s non ancora disponibile: si riprova al prossimo aggiornamento", giorno)
                break
            giorni[giorno.isoformat()] = g.ore
            cambiati.add(giorno)
            self._storico["ultimo"] = {"giorno": giorno.isoformat(), "totale": round(g.totale, 3), "picco_kw": g.picco_kw,
                                       "fascia_picco": g.fascia_picco}
            giorno += timedelta(days=1)

        # dopo il riempimento dal file: il picco dell'ultimo giorno viene solo dalla risposta giornaliera
        u = self._storico.get("ultimo")
        ultimo_giorno = max(giorni) if giorni else None
        if ultimo_giorno and (not u or u.get("giorno") != ultimo_giorno):
            g = giorno_da_risposta(await self.client.consumo_giorno(luce.contratto,
                                                                    date.fromisoformat(ultimo_giorno)),
                                   date.fromisoformat(ultimo_giorno))
            self._storico["ultimo"] = {"giorno": ultimo_giorno,
                                       "totale": round(g.totale if g.disponibile else sum(giorni[ultimo_giorno]), 3),
                                       "picco_kw": g.picco_kw, "fascia_picco": g.fascia_picco}

    def _importa(self, luce: Fornitura, giorni: dict[date, list], dal: date) -> None:
        righe = righe_statistiche(giorni, self.fuso, dal)
        meta = StatisticMetaData(mean_type=StatisticMeanType.NONE, has_sum=True,
                                 name="Open Dolomiti elettricità (consumi orari)", source=DOMAIN,
                                 statistic_id=self.statistic_id(luce), unit_class="energy",
                                 unit_of_measurement="kWh")
        async_add_external_statistics(self.hass, meta, [StatisticData(start=r["start"], state=r["state"],
                                                                      sum=r["sum"]) for r in righe])
        _LOGGER.debug("importate %s ore dal %s", len(righe), dal)

    def forniture_per_tipo(self, tipo: str) -> list[dict]:
        return [f for f in (self.data or {}).get("forniture", []) if f["tipo"] == tipo]


__all__ = ["CoordinatoreDolomiti", "chiave_opaca", "TIPO_GAS", "TIPO_LUCE"]
