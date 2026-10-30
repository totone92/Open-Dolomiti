"""Test con risposte d'esempio INVENTATE (nessun dato reale)."""
import base64
import io
import json
import pathlib
import zipfile
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import pytest

import parsing

FIX = pathlib.Path(__file__).parent / "fixtures"
ROMA = ZoneInfo("Europe/Rome")


def carica(nome):
    return json.loads((FIX / nome).read_text())


def jwt(claim):
    corpo = base64.urlsafe_b64encode(json.dumps(claim).encode()).rstrip(b"=").decode()
    return f"eyJhbGciOiJub25lIn0.{corpo}.firma"


def test_impostazioni_login():
    s = parsing.impostazioni_login((FIX / "login.html").read_text())
    assert s["api"] == "CombinedSigninAndSignup"
    assert s["hosts"]["policy"] == "B2C_1A_signin"
    with pytest.raises(parsing.ErroreFormato):
        parsing.impostazioni_login("<html>niente</html>")


def test_codice_da_redirect():
    assert parsing.codice_da_redirect("https://example.invalid#state=x&code=abc123") == "abc123"
    with pytest.raises(ValueError):
        parsing.codice_da_redirect("https://example.invalid#error=access_denied&error_description=no")


def test_codice_fiscale_dalle_claim():
    cf = "RSSMRA80A01H501U"   # codice fiscale di esempio (Mario Rossi), non reale
    assert parsing.codice_fiscale(parsing.claim_jwt(jwt({"codice_fiscale": cf.lower()}))) == cf
    assert parsing.codice_fiscale({"altro": cf}) == cf
    assert parsing.codice_fiscale({"sub": "123"}) is None
    annidato = {"customer_profiles": json.dumps([{"profile": "Retail", "fiscalCode": cf}])}
    assert parsing.codice_fiscale(annidato) == cf
    assert parsing.codice_fiscale({"customer_profiles": [{"id": f"Retail|{cf}"}]}) == cf
    assert parsing.descrivi_claim({"a": "xy", "b": [1]}) == {"a": "str[2]", "b": "list[1]"}


def test_giorno():
    g = parsing.giorno_da_risposta(carica("giorno.json"), date(2030, 1, 1))
    assert g.disponibile and len(g.ore) == 24
    assert g.picco_kw == 3.1 and g.fascia_picco.startswith("tra le 20")
    assert g.totale == 10.5
    assert not parsing.giorno_da_risposta(carica("giorno_non_pronto.json"), date(2030, 1, 2)).disponibile


def test_ore_da_1_a_24():
    dati = {"data": {"dataAvailable": True, "hourlyConsumptions": [{"hour": h, "total": h} for h in range(1, 25)]}}
    g = parsing.giorno_da_risposta(dati, date(2030, 1, 1))
    assert g.ore[0] == 1 and g.ore[23] == 24


def test_mese_e_preferiti():
    assert parsing.mese_da_risposta(carica("mese.json")) == {date(2030, 1, 1): 9.1, date(2030, 1, 2): 8.4}
    m = parsing.mesi_preferiti(carica("mesi.json"))
    assert m["corrente"]["totale"] == 120.5 and m["precedente"]["al"] == date(2030, 4, 30)


def test_forniture_e_bollette():
    f = parsing.forniture_da_risposta(carica("forniture.json"), lambda t: "h" + str(len(t)))
    assert [x.tipo for x in f] == ["ENERGY", "GAS"]
    assert f[0].contatore_2g and not f[1].contatore_2g and f[0].fine == date(2031, 3, 31)
    assert "IT000" not in repr(f[0])          # POD e contratto fuori dal repr (log)
    b = parsing.ultima_bolletta(carica("bollette.json"))
    assert b["importo"] == 55.9 and b["scadenza"] == date(2030, 5, 20) and b["da_pagare"] is True


def _xlsx(righe):
    """xlsx minimo (foglio Periodico) con stringhe in linea e numeri."""
    def cella(rif, v):
        if isinstance(v, str):
            return f'<c r="{rif}" t="inlineStr"><is><t>{v}</t></is></c>'
        return f'<c r="{rif}"><v>{v}</v></c>'

    def col(i):
        s = ""
        i += 1
        while i:
            i, r = divmod(i - 1, 26)
            s = chr(65 + r) + s
        return s
    xml = "".join(f'<row r="{n + 1}">' + "".join(cella(f"{col(i)}{n + 1}", v) for i, v in enumerate(r)) + "</row>"
                  for n, r in enumerate(righe))
    m = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    rel = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("xl/workbook.xml", f'<workbook xmlns="{m}" xmlns:r="{rel}"><sheets>'
                   f'<sheet name="Medie" sheetId="1" r:id="rId1"/><sheet name="Periodico" sheetId="2" r:id="rId2"/>'
                   f'</sheets></workbook>')
        z.writestr("xl/_rels/workbook.xml.rels",
                   '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   '<Relationship Id="rId1" Target="worksheets/sheet1.xml"/>'
                   '<Relationship Id="rId2" Target="worksheets/sheet2.xml"/></Relationships>')
        z.writestr("xl/worksheets/sheet1.xml", f'<worksheet xmlns="{m}"><sheetData/></worksheet>')
        z.writestr("xl/worksheets/sheet2.xml", f'<worksheet xmlns="{m}"><sheetData>{xml}</sheetData></worksheet>')
    return buf.getvalue()


def test_xlsx_periodico():
    """Stessa forma del file del portale: medie orarie e per giorno della settimana, poi una riga per giorno
    ("lun 29/06/2030", 24 ore, una colonna vuota, il totale) e i totali. Valori inventati."""
    ore = [f"{h:02d}" for h in range(24)]
    righe = [["Data", "Ora"], [None] + ore, ["Periodo"] + [0.2] * 24, ["Lunedì"] + [0.3] * 24,
             [None] * 26 + ["Totale"],
             ["lun 01/01/2030"] + [0.5] * 24 + [None, 12.0],
             ["mar 02/01/2030"] + [0.25] * 24 + [None, 6.0],
             [None] * 26 + ["Totale"], [None] * 26 + [18.0]]
    giorni = parsing.giorni_da_xlsx(_xlsx(righe))
    assert set(giorni) == {date(2030, 1, 1), date(2030, 1, 2)}
    assert sum(giorni[date(2030, 1, 1)]) == 12.0 and len(giorni[date(2030, 1, 2)]) == 24
    assert parsing.giorni_da_xlsx(_xlsx([["dom 03/01/2030"] + [0.1] * 24])) == {date(2030, 1, 3): [0.1] * 24}
    assert parsing.data_da_testo(47485.0) == date(2030, 1, 2)


def test_fascia_del_picco():
    dati = {"data": {"dataAvailable": True, "total": 1, "powerPeak": 2.074, "rangePeakPower": "2.1 tra le 18:00 e le 19:00",
                     "hourlyConsumptions": [{"hour": h, "total": 0.1} for h in range(24)]}}
    g = parsing.giorno_da_risposta(dati, date(2030, 1, 1))
    assert g.fascia_picco == "tra le 18:00 e le 19:00" and g.picco_kw == 2.074


def test_statistiche_cumulative_e_cambio_ora():
    giorni = {date(2030, 3, 31): [1.0] * 24, date(2030, 4, 1): [2.0] * 24}   # 31/03/2030: passaggio all'ora legale
    righe = parsing.righe_statistiche(giorni, ROMA)
    assert righe[-1]["sum"] == 72.0                       # nessun kWh perso
    marzo = parsing.ore_utc(date(2030, 3, 31), [1.0] * 24, ROMA)
    assert len(marzo) == 23 and max(marzo.values()) == 2.0   # la 02 locale (che non esiste) va con la 03
    assert all(r["start"].tzinfo == timezone.utc and r["start"].minute == 0 for r in righe)
    solo = parsing.righe_statistiche(giorni, ROMA, dal=date(2030, 4, 1))
    assert solo[0]["sum"] == 26.0 and len(solo) == 24     # la somma riparte da quella del giorno prima
    ottobre = parsing.ore_utc(date(2030, 10, 27), [1.0] * 24, ROMA)   # ritorno all'ora solare
    assert sum(ottobre.values()) == 24.0 and min(ottobre) == datetime(2030, 10, 26, 22, tzinfo=timezone.utc)


def test_nome_chiamata_senza_dati_personali():
    assert parsing.nome_chiamata("/api/secure/supply/{CF}/C-0000001/meter2g/consumption") == "consumption"
    assert parsing.nome_chiamata("/api/secure/supply/{CF}/C-0000001/meter2g/consumption/excel") == "excel"
    assert parsing.nome_chiamata("/api/secure/bill/{CF}") == "bollette"
