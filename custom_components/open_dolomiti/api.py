"""Client delle API (non ufficiali) dell'area clienti myDolomiti: login Azure AD B2C con PKCE e dati del contatore.

Nei log non finiscono mai email, password, token, codice fiscale, POD/PDR o numeri di contratto.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import date
from typing import Any
from urllib.parse import urlencode

import aiohttp

from .const import (
    API_BASE, API_PROFILE, AUTHORIZE_URL, B2C_HOST, CLIENT_ID, MARGINE_TOKEN, ORIGIN, REDIRECT_URI, SCOPE, TOKEN_URL,
)
from .parsing import (
    ErroreFormato, claim_jwt, codice_da_redirect, codice_fiscale, descrivi_claim, impostazioni_login, nome_chiamata,
)

_LOGGER = logging.getLogger(__name__)
TIMEOUT = aiohttp.ClientTimeout(total=60)
INTESTAZIONI = {"User-Agent": "Mozilla/5.0 (X11; Linux) HomeAssistant open_dolomiti", "Origin": ORIGIN,
                "Referer": ORIGIN + "/"}


class ErroreAutenticazione(Exception):
    """Credenziali rifiutate (serve una nuova autenticazione da parte dell'utente)."""


class ErroreConnessione(Exception):
    """Rete, servizio non disponibile o risposta inattesa: si riprova più tardi."""


@dataclass
class Sessione:
    access_token: str = field(default="", repr=False)
    refresh_token: str = field(default="", repr=False)
    scade_access: float = 0.0          # epoch
    scade_refresh: float = 0.0         # epoch, da refresh_token_expires_in
    codice_fiscale: str = field(default="", repr=False)
    # solo per diagnostica (nessun segreto)
    login_completi: int = 0
    rinnovi: int = 0
    rinnovi_falliti: int = 0
    ultimo_login: float = 0.0
    ultimo_rinnovo: float = 0.0
    storia_refresh: list = field(default_factory=list)   # [(ora, refresh_token_expires_in, token cambiato)]

    def info(self) -> dict:
        adesso = time.time()
        return {
            "access_valido_per_s": round(self.scade_access - adesso),
            "refresh_valido_per_s": round(self.scade_refresh - adesso),
            "login_completi": self.login_completi,
            "rinnovi": self.rinnovi,
            "rinnovi_falliti": self.rinnovi_falliti,
            "ultimo_login": self.ultimo_login,
            "ultimo_rinnovo": self.ultimo_rinnovo,
            "storia_refresh": self.storia_refresh[-20:],
        }


def _pkce() -> tuple[str, str]:
    verifier = base64.urlsafe_b64encode(os.urandom(48)).rstrip(b"=").decode()
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


class ClientDolomiti:
    """Una sessione per account. `sessione_http` serve per le API dati; il login usa una sessione propria con i
    suoi cookie (quelli di B2C), chiusa alla fine."""

    def __init__(self, sessione_http: aiohttp.ClientSession, email: str, password: str) -> None:
        self._http = sessione_http
        self._email = email
        self._password = password
        self.sessione = Sessione()
        self.chiamate = 0           # chiamate HTTP fatte (per la diagnostica)

    # ------------------------------------------------------------ autenticazione

    async def login(self) -> None:
        """Login completo: authorize -> SelfAsserted -> confirmed -> token."""
        verifier, challenge = _pkce()
        parametri = {"client_id": CLIENT_ID, "redirect_uri": REDIRECT_URI, "scope": SCOPE, "response_type": "code",
                     "response_mode": "fragment", "code_challenge": challenge, "code_challenge_method": "S256",
                     "state": base64.urlsafe_b64encode(os.urandom(12)).decode(),
                     "nonce": base64.urlsafe_b64encode(os.urandom(12)).decode()}
        try:
            # quote_cookie=False: i cookie x-ms-cpim-* vanno rimandati tali e quali (tra virgolette B2C dà 400)
            async with aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(quote_cookie=False), timeout=TIMEOUT,
                                             headers={"User-Agent": INTESTAZIONI["User-Agent"]}) as s:
                self.chiamate += 1
                async with s.get(f"{AUTHORIZE_URL}?{urlencode(parametri)}") as r:
                    if r.status != 200:
                        raise ErroreConnessione(f"authorize: HTTP {r.status}")
                    impostazioni = impostazioni_login(await r.text())
                base = f"https://{B2C_HOST}{impostazioni['hosts']['tenant']}"
                politica = impostazioni["hosts"]["policy"]
                tx = impostazioni["transId"]
                self.chiamate += 1
                async with s.post(f"{base}/SelfAsserted", params={"tx": tx, "p": politica},
                                  data={"request_type": "RESPONSE", "signInName": self._email,
                                        "password": self._password},
                                  headers={"X-CSRF-TOKEN": impostazioni["csrf"],
                                           "X-Requested-With": "XMLHttpRequest"}) as r:
                    testo = await r.text()
                    if r.status != 200:
                        raise ErroreConnessione(f"SelfAsserted: HTTP {r.status}")
                    try:
                        esito = await r.json(content_type=None)
                    except ValueError as err:
                        raise ErroreConnessione("SelfAsserted: risposta non JSON") from err
                    if str(esito.get("status")) != "200":
                        _LOGGER.debug("login rifiutato (lunghezza risposta %s)", len(testo))
                        raise ErroreAutenticazione("credenziali rifiutate")
                self.chiamate += 1
                async with s.get(f"{base}/api/{impostazioni.get('api', 'CombinedSigninAndSignup')}/confirmed",
                                 params={"rememberMe": "false", "csrf_token": impostazioni["csrf"], "tx": tx,
                                         "p": politica}, allow_redirects=False) as r:
                    destinazione = r.headers.get("Location", "")
                    if r.status not in (301, 302, 303) or not destinazione:
                        raise ErroreConnessione(f"confirmed: HTTP {r.status} senza redirect")
                try:
                    codice = codice_da_redirect(destinazione)
                except ValueError as err:
                    raise ErroreAutenticazione("autorizzazione rifiutata") from err
            await self._token({"grant_type": "authorization_code", "code": codice, "code_verifier": verifier,
                               "redirect_uri": REDIRECT_URI})
        except (aiohttp.ClientError, TimeoutError) as err:
            raise ErroreConnessione(f"login: {type(err).__name__}") from err
        except ErroreFormato as err:
            raise ErroreConnessione(f"login: {err}") from err
        self.sessione.login_completi += 1
        self.sessione.ultimo_login = time.time()
        _LOGGER.debug("login completato")

    async def rinnova(self) -> None:
        """Nuovo access token col refresh token."""
        vecchio = self.sessione.refresh_token
        try:
            await self._token({"grant_type": "refresh_token", "refresh_token": vecchio})
        except (aiohttp.ClientError, TimeoutError) as err:
            raise ErroreConnessione(f"rinnovo: {type(err).__name__}") from err
        s = self.sessione
        s.rinnovi += 1
        s.ultimo_rinnovo = time.time()
        s.storia_refresh.append((round(s.ultimo_rinnovo), round(s.scade_refresh - s.ultimo_rinnovo),
                                 s.refresh_token != vecchio))
        s.storia_refresh = s.storia_refresh[-50:]

    async def _token(self, campi: dict) -> None:
        corpo = {"client_id": CLIENT_ID, "scope": SCOPE, **campi}
        self.chiamate += 1
        async with self._http.post(TOKEN_URL, data=corpo, headers=INTESTAZIONI, timeout=TIMEOUT) as r:
            try:
                dati = await r.json(content_type=None)
            except ValueError as err:
                raise ErroreConnessione(f"token: HTTP {r.status}, risposta non JSON") from err
            if r.status in (400, 401):
                errore = str(dati.get("error", ""))
                raise ErroreAutenticazione(f"token: {errore or r.status}")
            if r.status != 200 or "access_token" not in dati:
                raise ErroreConnessione(f"token: HTTP {r.status}")
        adesso = time.time()
        s = self.sessione
        s.access_token = dati["access_token"]
        s.refresh_token = dati.get("refresh_token", s.refresh_token)
        s.scade_access = adesso + int(dati.get("expires_in", 3600))
        if "refresh_token_expires_in" in dati:
            s.scade_refresh = adesso + int(dati["refresh_token_expires_in"])
        elif "refresh_token" in dati:
            s.scade_refresh = adesso + 86400
        nomi = {}
        for nome_token in ("id_token", "access_token"):
            t = dati.get(nome_token)
            if t and not s.codice_fiscale:
                try:
                    claim = claim_jwt(t)
                except ErroreFormato:
                    continue
                s.codice_fiscale = codice_fiscale(claim) or ""
                nomi[nome_token] = descrivi_claim(claim)
        if not s.codice_fiscale:
            # di norma non c'è (il portale lo prende da /secure/profile/customer): solo nomi delle claim, mai valori
            _LOGGER.debug("codice fiscale non presente nelle claim del token: %s", nomi)

    async def assicura_token(self) -> None:
        """Access token valido: rinnovo se sta per scadere, login completo se il rinnovo non è possibile."""
        s, adesso = self.sessione, time.time()
        if s.access_token and s.scade_access - adesso > MARGINE_TOKEN.total_seconds():
            return
        if s.refresh_token and s.scade_refresh - adesso > 60:
            try:
                await self.rinnova()
                return
            except ErroreAutenticazione:
                s.rinnovi_falliti += 1
                _LOGGER.debug("rinnovo rifiutato: nuovo login completo")
        await self.login()

    # ------------------------------------------------------------ dati

    async def trova_codice_fiscale(self) -> str:
        """CF del titolare: dalle claim del token o, come fa il portale, dai profili cliente dell'account."""
        await self.assicura_token()
        if not self.sessione.codice_fiscale:
            profili = await self._get("/api/secure/profile/customer", senza_profilo=True)
            self.sessione.codice_fiscale = codice_fiscale(profili.get("data", profili)) or ""
            if not self.sessione.codice_fiscale:
                dati = profili.get("data", profili)
                _LOGGER.warning("codice fiscale non trovato nei profili cliente; campi: %s",
                                descrivi_claim(dati) if isinstance(dati, dict) else type(dati).__name__)
        return self.sessione.codice_fiscale

    async def _get(self, percorso: str, senza_profilo: bool = False, **parametri: Any) -> dict:
        await self.assicura_token()
        if "{CF}" in percorso and not self.sessione.codice_fiscale and not await self.trova_codice_fiscale():
            raise ErroreConnessione("codice fiscale del titolare non trovato")
        url = f"{API_BASE}{percorso.replace('{CF}', self.sessione.codice_fiscale)}"
        parametri = {**({} if senza_profilo else {"profile": API_PROFILE}),
                     **{k: v for k, v in parametri.items() if v is not None}}
        for tentativo in (1, 2):
            self.chiamate += 1
            try:
                async with self._http.get(url, params=parametri, timeout=TIMEOUT,
                                          headers={**INTESTAZIONI,
                                                   "Authorization": f"Bearer {self.sessione.access_token}",
                                                   "Accept": "application/json"}) as r:
                    if r.status == 401 and tentativo == 1:
                        self.sessione.scade_access = 0      # token rifiutato: rinnova e riprova una volta
                        await self.assicura_token()
                        continue
                    if r.status != 200:
                        raise ErroreConnessione(f"{nome_chiamata(percorso)}: HTTP {r.status}")
                    return await r.json(content_type=None)
            except (aiohttp.ClientError, TimeoutError) as err:
                raise ErroreConnessione(f"API: {type(err).__name__}") from err
            except ValueError as err:
                raise ErroreConnessione("API: risposta non JSON") from err
        raise ErroreConnessione("API: token rifiutato due volte")

    async def forniture(self) -> dict:
        return await self._get("/api/secure/supply/{CF}/active")

    async def consumo_giorno(self, contratto: str, giorno: date) -> dict:
        return await self._get(f"/api/secure/supply/{{CF}}/{contratto}/meter2g/consumption", range="DAILY",
                               dateFrom=giorno.isoformat())

    async def consumo_mese(self, contratto: str, primo: date) -> dict:
        return await self._get(f"/api/secure/supply/{{CF}}/{contratto}/meter2g/consumption", range="MONTHLY",
                               dateFrom=primo.replace(day=1).isoformat())

    async def consumi_periodo_xlsx(self, contratto: str, giorni: int) -> bytes:
        dati = await self._get(f"/api/secure/supply/{{CF}}/{contratto}/meter2g/consumption/excel",
                               range="PERIODIC", numberOfDays=giorni)
        file = (dati.get("data") or {}).get("file") if isinstance(dati.get("data"), dict) else dati.get("file")
        if not file:
            raise ErroreConnessione("excel: file mancante")
        return base64.b64decode(file)

    async def mesi_preferiti(self) -> dict:
        return await self._get("/api/secure/supply/{CF}/meter2g/favourite/consumption")

    async def bollette(self, conto: str, pod_pdr: str, tipo: str) -> dict:
        return await self._get("/api/secure/bill/{CF}", contractualAccount=conto, podPdr=pod_pdr, supplyType=tipo,
                               page=1, size=12, year=-1)
