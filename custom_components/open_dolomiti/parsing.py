"""Funzioni pure (senza Home Assistant né rete): pagine di login, token, risposte delle API, file xlsx.

Separate dal resto perché si provano con i test senza installare Home Assistant.
"""

from __future__ import annotations

import base64
import io
import json
import re
import zipfile
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse
from xml.etree import ElementTree
from zoneinfo import ZoneInfo

CF_RE = re.compile(r"^[A-Z]{6}[0-9LMNPQRSTUV]{2}[A-Z][0-9LMNPQRSTUV]{2}[A-Z][0-9LMNPQRSTUV]{3}[A-Z]$")
CF_CERCA_RE = re.compile(r"(?<![A-Z0-9])[A-Z]{6}[0-9LMNPQRSTUV]{2}[A-Z][0-9LMNPQRSTUV]{2}[A-Z][0-9LMNPQRSTUV]{3}[A-Z](?![A-Z0-9])")


class ErroreFormato(Exception):
    """Una risposta non ha la forma attesa (le API non sono ufficiali e possono cambiare)."""


# ---------------------------------------------------------------- login

def impostazioni_login(html: str) -> dict:
    """Estrae l'oggetto "var SETTINGS = {...};" della pagina di login B2C (csrf, transId, hosts)."""
    m = re.search(r"var SETTINGS = (\{.*?\});", html, re.S)
    if not m:
        raise ErroreFormato("SETTINGS non trovato nella pagina di login")
    try:
        s = json.loads(m.group(1))
    except ValueError as err:
        raise ErroreFormato("SETTINGS non leggibile") from err
    if not s.get("csrf") or not s.get("transId"):
        raise ErroreFormato("csrf o transId mancanti")
    return s


def codice_da_redirect(location: str) -> str:
    """Prende il code dal fragment (o dalla query) del redirect finale; solleva ValueError se c'è un errore."""
    parti = urlparse(location)
    valori = parse_qs(parti.fragment) or parse_qs(parti.query)
    if "error" in valori:
        raise ValueError(valori.get("error_description", valori["error"])[0][:200])
    if "code" not in valori:
        raise ErroreFormato("code mancante nel redirect")
    return valori["code"][0]


def claim_jwt(token: str) -> dict:
    """Payload di un JWT senza verificarne la firma (serve solo a leggere le claim del proprio token)."""
    try:
        corpo = token.split(".")[1]
        corpo += "=" * (-len(corpo) % 4)
        return json.loads(base64.urlsafe_b64decode(corpo))
    except (IndexError, ValueError) as err:
        raise ErroreFormato("token non leggibile") from err


def codice_fiscale(claim) -> str | None:
    """Codice fiscale dalle claim: prima "codice_fiscale", poi qualsiasi valore con la forma di un CF, cercando anche
    dentro liste, oggetti e stringhe JSON (es. customer_profiles)."""
    if isinstance(claim, dict):
        for chiave in ("codice_fiscale", "extension_codice_fiscale", "fiscalCode", "cf"):
            v = claim.get(chiave)
            if isinstance(v, str) and CF_RE.match(v.strip().upper()):
                return v.strip().upper()
        valori = list(claim.values())
    elif isinstance(claim, list):
        valori = claim
    else:
        valori = [claim]
    for v in valori:
        if isinstance(v, (dict, list)):
            trovato = codice_fiscale(v)
            if trovato:
                return trovato
        elif isinstance(v, str):
            t = v.strip()
            if CF_RE.match(t.upper()):
                return t.upper()
            if t[:1] in "[{":
                try:
                    trovato = codice_fiscale(json.loads(t))
                except ValueError:
                    trovato = None
                if trovato:
                    return trovato
            m = CF_CERCA_RE.search(t.upper())
            if m:
                return m.group(0)
    return None


def descrivi_claim(claim: dict) -> dict:
    """Solo nomi e tipi delle claim (mai i valori): per capire dove sta il CF quando non si trova."""
    return {k: type(v).__name__ + (f"[{len(v)}]" if isinstance(v, (list, dict, str)) else "") for k, v in claim.items()}


# ---------------------------------------------------------------- consumi

def _numero(v) -> float:
    if v is None or v == "":
        return 0.0
    if isinstance(v, (int, float)):
        return float(v)
    return float(str(v).replace(",", "."))


def _ora(v, indice: int) -> int:
    """Ora del giorno 0-23 da campi come 0, 1..24, "01", "00:00-01:00"."""
    if isinstance(v, (int, float)):
        return int(v)
    m = re.match(r"\s*(\d{1,2})", str(v or ""))
    return int(m.group(1)) if m else indice


@dataclass
class Giorno:
    giorno: date
    ore: list[float]                    # 24 valori in kWh, ora locale 0..23
    totale: float
    disponibile: bool = True
    picco_kw: float | None = None
    fascia_picco: str | None = None


def giorno_da_risposta(dati: dict, giorno: date) -> Giorno:
    """Risposta di consumption?range=DAILY -> Giorno (disponibile=False se il dato non è ancora pronto)."""
    d = dati.get("data", dati) or {}
    disponibile = bool(d.get("dataAvailable", True))
    righe = d.get("hourlyConsumptions") or []
    if not disponibile or not righe:
        return Giorno(giorno, [0.0] * 24, 0.0, disponibile=False)
    ore = [_ora(r.get("hour"), i) for i, r in enumerate(righe)]
    if min(ore) == 1 and max(ore) == 24:   # 1..24 = ora che finisce
        ore = [h - 1 for h in ore]
    valori = [0.0] * 24
    for h, r in zip(ore, righe):
        if 0 <= h <= 23:
            valori[h] += _numero(r.get("total"))
    totale = _numero(d.get("total")) if d.get("total") is not None else sum(valori)
    picco = d.get("powerPeak")
    # "2.1 tra le 18:00 e le 19:00": il portale mette davanti il picco arrotondato, si tiene solo la fascia
    fascia = re.sub(r"^\s*[\d.,]+\s*(kW)?\s*", "", str(d.get("rangePeakPower") or "")) or None
    return Giorno(giorno, valori, round(totale, 3), True, _numero(picco) if picco is not None else None, fascia)


def mese_da_risposta(dati: dict) -> dict[date, float]:
    """Risposta di consumption?range=MONTHLY -> {giorno: kWh}."""
    d = dati.get("data", dati) or {}
    out = {}
    for r in d.get("dailyConsumptions") or []:
        g = data_da_testo(r.get("day"))
        if g:
            out[g] = _numero(r.get("total"))
    return out


def data_da_testo(v) -> date | None:
    """Date nei formati visti nelle API e nei file: AAAA-MM-GG[...], GG/MM/AAAA, numero seriale di Excel."""
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)) or re.fullmatch(r"\d+(\.\d+)?", str(v)):
        n = float(v)
        if 20000 < n < 80000:   # seriale di Excel (giorni dal 30/12/1899)
            return date(1899, 12, 30) + timedelta(days=int(n))
        return None
    t = str(v).strip()
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", t)
    if m:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    m = re.search(r"(?<!\d)(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})(?!\d)", t)   # anche "lun 29/06/2026"
    if m:
        return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    return None


# ---------------------------------------------------------------- xlsx (senza openpyxl)

_NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
       "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships"}


def _colonna(rif: str) -> int:
    n = 0
    for c in re.match(r"[A-Z]+", rif).group(0):
        n = n * 26 + ord(c) - 64
    return n - 1


def righe_xlsx(contenuto: bytes, foglio: str | None = None) -> list[list]:
    """Righe del foglio (per nome, o il primo) come liste di valori (stringhe o numeri)."""
    with zipfile.ZipFile(io.BytesIO(contenuto)) as z:
        condivise = []
        if "xl/sharedStrings.xml" in z.namelist():
            radice = ElementTree.fromstring(z.read("xl/sharedStrings.xml"))
            for si in radice.findall("m:si", _NS):
                condivise.append("".join(t.text or "" for t in si.iter(f"{{{_NS['m']}}}t")))
        libro = ElementTree.fromstring(z.read("xl/workbook.xml"))
        fogli = [(s.get("name"), s.get(f"{{{_NS['r']}}}id")) for s in libro.find("m:sheets", _NS)]
        rel = ElementTree.fromstring(z.read("xl/_rels/workbook.xml.rels"))
        destinazioni = {r.get("Id"): r.get("Target") for r in rel}
        scelto = next((f for f in fogli if foglio and f[0] == foglio), fogli[0])
        percorso = destinazioni[scelto[1]].lstrip("/")
        percorso = percorso if percorso.startswith("xl/") else "xl/" + percorso
        radice = ElementTree.fromstring(z.read(percorso))
    righe = []
    for riga in radice.iter(f"{{{_NS['m']}}}row"):
        valori: dict[int, object] = {}
        for c in riga.findall("m:c", _NS):
            tipo, v = c.get("t"), c.find("m:v", _NS)
            if tipo == "inlineStr":
                testo = "".join(t.text or "" for t in c.iter(f"{{{_NS['m']}}}t"))
            elif v is None:
                continue
            elif tipo == "s":
                testo = condivise[int(v.text)]
            elif tipo in ("str", "b"):
                testo = v.text
            else:
                try:
                    testo = float(v.text)
                except (TypeError, ValueError):
                    testo = v.text
            valori[_colonna(c.get("r"))] = testo
        if valori:
            righe.append([valori.get(i) for i in range(max(valori) + 1)])
    return righe


def giorni_da_xlsx(contenuto: bytes, foglio: str = "Periodico") -> dict[date, list[float]]:
    """File "Consumi.xlsx" (foglio Periodico): righe con una data e 24 valori orari -> {giorno: [24 kWh]}."""
    out = {}
    for riga in righe_xlsx(contenuto, foglio):
        if not riga:
            continue
        g = next((data_da_testo(v) for v in riga[:3] if data_da_testo(v)), None)
        if not g:
            continue
        numeri = []
        for v in riga[riga.index(next(v for v in riga[:3] if data_da_testo(v))) + 1:]:
            try:
                numeri.append(_numero(v))
            except ValueError:
                numeri.append(None)
        numeri = [n for n in numeri if n is not None]
        if len(numeri) >= 24:
            out[g] = numeri[:24]
    return out


# ---------------------------------------------------------------- statistiche orarie

def ore_utc(giorno: date, valori: list[float], fuso: ZoneInfo) -> dict[datetime, float]:
    """Valori 0..23 dell'ora locale -> {inizio ora UTC: kWh}. Nei giorni del cambio d'ora: la 02 che non esiste
    si somma alla 03 (marzo); a ottobre la seconda 02 resta senza valore (il dato del contatore è uno per ora)."""
    out: dict[datetime, float] = {}
    for h, v in enumerate(valori[:24]):
        inizio = datetime(giorno.year, giorno.month, giorno.day, h, tzinfo=fuso).astimezone(timezone.utc)
        inizio = inizio.replace(minute=0, second=0, microsecond=0)
        out[inizio] = out.get(inizio, 0.0) + float(v or 0)
    return out


def righe_statistiche(giorni: dict[date, list[float]], fuso: ZoneInfo, dal: date | None = None,
                      base: float = 0.0) -> list[dict]:
    """Righe orarie con somma cumulativa (kWh) da "base" all'inizio dello storico; solo quelle da "dal" in poi."""
    somma, righe = base, []
    for g in sorted(giorni):
        for inizio, v in sorted(ore_utc(g, giorni[g], fuso).items()):
            somma += v
            if dal is None or g >= dal:
                righe.append({"start": inizio, "state": round(somma, 4), "sum": round(somma, 4), "kwh": round(v, 4)})
    return righe


# ---------------------------------------------------------------- contratti e bollette

@dataclass
class Fornitura:
    chiave: str           # identificativo opaco (hash), mai il POD o il numero di contratto
    tipo: str             # ENERGY o GAS
    offerta: str | None
    fine: date | None
    contatore_2g: bool
    contratto: str = field(repr=False, default="")
    conto: str = field(repr=False, default="")
    pod_pdr: str = field(repr=False, default="")


def forniture_da_risposta(dati: dict, chiave) -> list[Fornitura]:
    d = dati.get("data", dati) or {}
    out = []
    for s in d.get("supplies") or []:
        contratto = str(s.get("contractId") or "")
        out.append(Fornitura(
            chiave=chiave(contratto or str(s.get("podPdr") or "")),
            tipo=str(s.get("supplyType") or "").upper(),
            offerta=s.get("offerName"),
            fine=data_da_testo(s.get("endDate")),
            contatore_2g=bool(s.get("meterType2G")),
            contratto=contratto,
            conto=str(s.get("contractualAccount") or ""),
            pod_pdr=str(s.get("podPdr") or ""),
        ))
    return out


def ultima_bolletta(dati: dict) -> dict | None:
    d = dati.get("data", dati) or {}
    bollette = d.get("bills") if isinstance(d, dict) else d
    if not bollette:
        return None

    def quando(b):
        return data_da_testo(b.get("billedPeriodAt")) or data_da_testo(b.get("expirationDate")) or date.min

    b = max(bollette, key=quando)
    importo = b.get("billAmount")
    return {
        "importo": _numero(importo) if importo is not None else None,
        "dal": data_da_testo(b.get("billedPeriodFrom")),
        "al": data_da_testo(b.get("billedPeriodAt")),
        "scadenza": data_da_testo(b.get("expirationDate")),
        "stato": b.get("status"),
        "da_pagare": b.get("payable"),
    }


def mesi_preferiti(dati: dict) -> dict:
    d = dati.get("data", dati) or {}
    out = {}
    for chiave, nome in (("currentMonth", "corrente"), ("previousMonth", "precedente")):
        m = d.get(chiave) or {}
        if m:
            out[nome] = {"totale": _numero(m.get("total")) if m.get("total") is not None else None,
                         "dal": data_da_testo(m.get("firstDay")), "al": data_da_testo(m.get("lastDay")),
                         "mese": m.get("month")}
    return out


def nome_chiamata(percorso: str) -> str:
    """Nome della chiamata per i messaggi di errore: mai codice fiscale o numero di contratto presenti nel percorso."""
    return "excel" if percorso.endswith("/excel") else percorso.rstrip("/").rsplit("/", 1)[-1].replace("{CF}", "bollette")
