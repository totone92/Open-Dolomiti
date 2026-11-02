# Changelog

## 0.1.1

Il fuso orario viene preso da quello già caricato da Home Assistant: prima veniva riletto dal disco nel ciclo principale, con un'operazione bloccante.

## 0.1.0

Prima versione pubblica: consumi orari nella dashboard Energia, sensori di consumi, contratti e bollette.

Correzioni prima della release: primo avvio con storico vuoto (`await` su una funzione non asincrona del recorder), giorni già scaricati importati anche dopo un errore, file o risposte illeggibili trattati come nuovo tentativo. Le nuove righe proseguono la somma già presente in statistica, così uno storico più vecchio di quello del portale resta coerente.
