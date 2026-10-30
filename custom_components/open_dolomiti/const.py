"""Costanti dell'integrazione Open Dolomiti (non ufficiale)."""

from datetime import timedelta

DOMAIN = "open_dolomiti"
NOME = "Open Dolomiti (non ufficiale)"
NOME_DISPOSITIVO = "Open Dolomiti"

# Login Azure AD B2C del portale clienti (client pubblico SPA, PKCE S256). Valori pubblici del portale web.
B2C_HOST = "dolomitienergiab2cprod.b2clogin.com"
B2C_TENANT = "dolomitienergiab2cprod.onmicrosoft.com"
B2C_POLICY = "B2C_1A_signin"
CLIENT_ID = "eb382094-e1b5-40d0-8bc9-aea1de0a76c7"
REDIRECT_URI = "https://mydolomiti.dolomitienergia.it"
ORIGIN = "https://mydolomiti.dolomitienergia.it"
SCOPE = "https://dolomitienergiab2cprod.onmicrosoft.com/private/api/read openid profile offline_access"
AUTHORIZE_URL = f"https://{B2C_HOST}/{B2C_TENANT}/{B2C_POLICY.lower()}/oauth2/v2.0/authorize"
TOKEN_URL = f"https://{B2C_HOST}/{B2C_TENANT}/{B2C_POLICY.lower()}/oauth2/v2.0/token"

API_BASE = "https://agw.dolomitienergia.it"
API_PROFILE = "Retail"

# Moderazione delle chiamate
INTERVALLO = timedelta(hours=4)          # aggiornamento normale (i dati arrivano con 1-2 giorni di ritardo)
INTERVALLO_CONTRATTI = timedelta(hours=24)  # contratti e bollette cambiano di rado
RITENTA_MIN = timedelta(minutes=30)      # primo nuovo tentativo dopo un errore, poi raddoppia fino a INTERVALLO
GIORNI_INIZIALI = 120                    # riempimento iniziale dello storico orario
GIORNI_PER_AGGIORNAMENTO = 10            # massimo di giorni scaricati uno per uno in un aggiornamento
MARGINE_TOKEN = timedelta(minutes=5)     # rinnovo dell'access token prima della scadenza

CONF_EMAIL = "email"
CONF_PASSWORD = "password"

TIPO_LUCE = "ENERGY"
TIPO_GAS = "GAS"

# Chiavi da togliere da diagnostica e log
DA_REDIGERE = {
    "email", "password", "access_token", "refresh_token", "id_token", "codice_fiscale", "cf", "podPdr", "pod",
    "pdr", "contractId", "contractualAccount", "iban", "IBAN", "address", "indirizzo", "businessPartner",
    "business_partner", "name", "given_name", "family_name", "email_address", "oid", "sub",
}
