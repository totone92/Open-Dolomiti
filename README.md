# Open Dolomiti

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz/docs/faq/custom_repositories/)

*English summary at the bottom.*

Integrazione per [Home Assistant](https://www.home-assistant.io/) che porta i **consumi elettrici orari** del
proprio contatore 2G dall'area clienti *myDolomiti* nella **dashboard Energia**, con lo storico, più qualche
informazione su contratti e bollette.

> **Progetto non ufficiale.** Non è affiliato, approvato o supportato da Dolomiti Energia. Usa le API non
> documentate del portale clienti, che possono cambiare o smettere di funzionare in qualsiasi momento. È pensato
> per l'uso personale dei **propri** dati, con le **proprie** credenziali. Nomi e marchi appartengono ai rispettivi
> titolari; il progetto non usa loghi né grafica di Dolomiti Energia.

## Cosa fa

- **Statistica esterna oraria in kWh** (somma cumulativa), da usare in *Impostazioni → Dashboard → Energia →
  Consumo dalla rete*. All'installazione scarica gli ultimi 120 giorni, poi aggiunge i giorni mancanti.
- **Sensori**: consumo dell'ultimo giorno disponibile e sua data, mese in corso e mese precedente, picco di
  potenza del giorno con la fascia oraria, offerta e fine contratto di ogni fornitura (luce e gas), ultima bolletta
  (importo, periodo, scadenza, stato).
- Il gas non ha dati orari (niente contatore 2G nel portale): per il gas solo contratto e bollette.

I dati del distributore arrivano con **1–2 giorni di ritardo**: servono per storico e spesa, non per il tempo reale.
Per il consumo istantaneo (e per staccare carichi) serve un misuratore in casa.

## Installazione

Con [HACS](https://hacs.xyz/): *HACS → Integrazioni → menu → Repository personalizzati*, aggiungi l'indirizzo di
questo repository come *Integrazione*, installa, riavvia Home Assistant. Poi *Impostazioni → Dispositivi e servizi →
Aggiungi integrazione → Open Dolomiti (non ufficiale)* e inserisci email e password di myDolomiti.

Manuale: copia `custom_components/open_dolomiti` nella cartella `custom_components` della configurazione
e riavvia.

## Come lavora

- **Login**: lo stesso del portale web (Azure AD B2C, PKCE). Le credenziali restano solo nella voce di
  configurazione di Home Assistant, cioè in chiaro nella cartella `.storage`, come per le altre integrazioni: proteggi
  quella cartella e i suoi backup. L'access token dura un'ora; quando scade si rinnova col refresh token, e se il
  rinnovo non è possibile si rifà il login completo. Se le credenziali vengono rifiutate, Home Assistant chiede una
  nuova autenticazione.
- **Moderazione**: un aggiornamento ogni 4 ore (contratti e bollette una volta al giorno), al massimo 10 giorni
  scaricati uno per uno per aggiornamento; dopo un errore riprova tra 30 minuti, poi 1, 2, 4 ore. Un giorno non
  ancora pronto si riprova all'aggiornamento successivo.
- **Privacy**: codice fiscale (letto dal profilo cliente, serve per le chiamate al portale), POD/PDR, numeri di contratto, email e token non
  finiscono nei log, nei nomi o negli attributi dei sensori; la diagnostica li toglie. Gli identificativi interni
  sono hash troncati, non in chiaro.
  Prima di allegare la diagnostica a una segnalazione, ricontrollala: contiene comunque consumi e importi delle bollette.
- **Cambio d'ora**: a marzo la 02 locale (che non esiste) si somma alla 03; a ottobre la seconda 02 non ha un valore
  separato (il portale dà 24 valori al giorno).

## Problemi

Apri una [segnalazione](https://github.com/totone92/open-dolomiti/issues) con la versione e il messaggio di
errore. Non incollare mai credenziali, codice fiscale, POD/PDR o numeri di contratto.

## Sviluppo

I test (`tests/`) usano solo risposte d'esempio **inventate** e non richiedono Home Assistant:

```bash
python -m pip install pytest
python -m pytest tests
```

## English summary

Unofficial Home Assistant integration for customers of the Italian utility Dolomiti Energia: it imports the hourly
electricity consumption of your 2G meter from the myDolomiti customer area into the Energy dashboard, and adds sensors
for contracts and bills. Not affiliated with Dolomiti Energia; it relies on undocumented APIs that may change at any time.

## Licenza

MIT — © 2026 Marco Dolciami
