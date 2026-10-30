"""Configurazione da interfaccia: email e password dell'area clienti myDolomiti, con reauth e riconfigurazione."""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Mapping
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import TextSelector, TextSelectorConfig, TextSelectorType

from .api import ClientDolomiti, ErroreAutenticazione, ErroreConnessione
from .const import CONF_EMAIL, CONF_PASSWORD, DOMAIN, NOME

_LOGGER = logging.getLogger(__name__)

SCHEMA = vol.Schema({
    vol.Required(CONF_EMAIL): TextSelector(TextSelectorConfig(type=TextSelectorType.EMAIL, autocomplete="username")),
    vol.Required(CONF_PASSWORD): TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD,
                                                                 autocomplete="current-password")),
})


class FlussoDolomiti(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def _prova(self, email: str, password: str) -> tuple[str | None, dict[str, str]]:
        """Login di prova: restituisce (identificativo opaco dell'account, errori)."""
        client = ClientDolomiti(async_get_clientsession(self.hass), email, password)
        try:
            await client.login()
        except ErroreAutenticazione:
            return None, {"base": "credenziali_errate"}
        except ErroreConnessione as err:
            _LOGGER.debug("login di prova non riuscito: %s", err)
            return None, {"base": "connessione"}
        try:
            cf = await client.trova_codice_fiscale()
        except ErroreConnessione as err:
            _LOGGER.debug("profilo cliente non disponibile: %s", err)
            return None, {"base": "connessione"}
        if not cf:
            return None, {"base": "codice_fiscale"}
        # unique_id: hash del codice fiscale, mai il codice in chiaro
        return hashlib.sha256(client.sessione.codice_fiscale.encode()).hexdigest()[:16], {}

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errori: dict[str, str] = {}
        if user_input is not None:
            uid, errori = await self._prova(user_input[CONF_EMAIL].strip(), user_input[CONF_PASSWORD])
            if uid:
                await self.async_set_unique_id(uid)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(title=NOME, data={CONF_EMAIL: user_input[CONF_EMAIL].strip(),
                                                                 CONF_PASSWORD: user_input[CONF_PASSWORD]})
        return self.async_show_form(step_id="user", data_schema=SCHEMA, errors=errori)

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        voce = self._get_reauth_entry()
        errori: dict[str, str] = {}
        if user_input is not None:
            uid, errori = await self._prova(voce.data[CONF_EMAIL], user_input[CONF_PASSWORD])
            if uid:
                await self.async_set_unique_id(uid)
                self._abort_if_unique_id_mismatch(reason="account_diverso")
                return self.async_update_reload_and_abort(voce, data_updates={CONF_PASSWORD: user_input[CONF_PASSWORD]})
        return self.async_show_form(
            step_id="reauth_confirm", errors=errori,
            data_schema=vol.Schema({vol.Required(CONF_PASSWORD): TextSelector(
                TextSelectorConfig(type=TextSelectorType.PASSWORD, autocomplete="current-password"))}))

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        voce = self._get_reconfigure_entry()
        errori: dict[str, str] = {}
        if user_input is not None:
            uid, errori = await self._prova(user_input[CONF_EMAIL].strip(), user_input[CONF_PASSWORD])
            if uid:
                await self.async_set_unique_id(uid)
                self._abort_if_unique_id_mismatch(reason="account_diverso")
                return self.async_update_reload_and_abort(
                    voce, data_updates={CONF_EMAIL: user_input[CONF_EMAIL].strip(),
                                        CONF_PASSWORD: user_input[CONF_PASSWORD]})
        return self.async_show_form(
            step_id="reconfigure", errors=errori,
            data_schema=self.add_suggested_values_to_schema(SCHEMA, {CONF_EMAIL: voce.data.get(CONF_EMAIL)}))
