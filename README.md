# Riva — Dashboard Centralizzata Controllo Produzione

Sistema di dashboarding per il titolare/supervisore di Riva, che aggrega — senza
modificare nulla sui PC di linea — i dati numerici prodotti dai sistemi di
controllo qualità installati in produzione. La prima fonte collegata è
**SmartWiches.AI** (controllo estetico tramezzini impacchettati, Linea 1), ma
l'architettura è pensata per aggiungere altre linee o altre fasi di produzione
senza modifiche strutturali.

## Come funziona (in breve)

```
PC Linea 1 (SmartWiches.AI)        PC Centrale Aziendale
┌─────────────────────┐            ┌──────────────────────────────┐
│ smartwiches.db       │  rete LAN  │ sync/sync_to_central.py       │
│ (SQLite, sola lettura│───────────▶│  → legge solo righe nuove      │
│  via cartella         │            │  → scrive data/linea1.jsonl   │
│  condivisa in rete)   │            │  → aggiorna data/sources.json │
└─────────────────────┘            └──────────────┬────────────────┘
                                                    │
                                                    ▼
                                     dashboard/index.html (browser)
                                     grafici, confronti, statistiche
```

Punti chiave:
- **Nessuna immagine viene copiata**: solo id, timestamp, ricetta e punteggi
  delle metriche. La dashboard mostra esclusivamente numeri e statistiche.
- **Nessun software installato sul PC di linea**: lo script gira sul PC
  centrale e legge il DB via una cartella condivisa in rete (sola lettura).
- **Nessun database server da amministrare**: i dati centralizzati sono file
  `.jsonl` (uno per linea/fase) leggibili da chiunque con un editor di testo,
  e la dashboard è un singolo file HTML.
- **Pronto per altre linee/fasi**: basta aggiungere una riga in
  `sync/sources.config.json`, senza toccare lo script o la dashboard.

## 1. Setup della sincronizzazione

Prerequisiti:
- Il PC di Linea 1 condivide in rete (sola lettura, es. `\\PC-LINEA1\SmartWichesShare\`)
  la cartella contenente `smartwiches.db`. Va creata una condivisione Windows
  dedicata con permessi di sola lettura per l'utente/account usato dal PC
  centrale — da configurare con l'IT del cliente o con Oròbix.
- Python 3 installato sul PC centrale (nessuna libreria aggiuntiva richiesta).

Configurazione:

1. Apri `sync/sources.config.json` e imposta il percorso di rete corretto in
   `source_db_path` per `linea1`.
2. Esegui manualmente una prima volta per verificare che funzioni:
   ```
   python sync\sync_to_central.py
   ```
   Al termine dovresti trovare `data\linea1.jsonl` popolato e `data\sources.json`
   aggiornato. Eventuali errori vengono scritti anche in `sync\sync.log`.
3. Pianifica l'esecuzione ricorrente con **Utilità di pianificazione di Windows**
   (Task Scheduler): nuova attività, trigger "ogni 10 minuti", azione
   `python.exe` con argomento il percorso di `sync_to_central.py`.

### Aggiungere una nuova linea o fase in futuro

Aggiungi un blocco in `sync/sources.config.json`:

```json
{
  "id": "linea1_fase2",
  "label": "Linea 1 - Controllo Impasto",
  "phase": "Impasto",
  "source_db_path": "\\\\PC-LINEA1\\AltraShare\\altro.db",
  "enabled": true
}
```

Lo script creerà automaticamente `data/linea1_fase2.jsonl` e la dashboard la
riconoscerà da sola al prossimo aggiornamento pagina — non serve modificare
`index.html`.

## 2. Uso della dashboard

Il modo più semplice e affidabile è pubblicare la cartella `dashboard/` (e la
cartella `data/` accanto ad essa) tramite un piccolo server web statico locale
sul PC centrale, perché i browser bloccano la lettura di file locali aperti
con doppio click. Due opzioni:

- **Rapida/manuale**: apri `dashboard/index.html` con doppio click. Se il
  browser blocca il caricamento automatico dei dati, la pagina te lo segnala e
  permette di caricare a mano i file da `data/` tramite un selettore file.
- **Automatica (consigliata)**: pianifica un servizio che serva la cartella
  del progetto come sito statico, ad es. con Python:
  ```
  cd riva-production-dashboard
  python -m http.server 8080
  ```
  e apri `http://localhost:8080/dashboard/` dal browser del PC centrale. Può
  essere registrato come servizio Windows (es. con NSSM, già usato dal
  sistema di linea) per partire automaticamente all'avvio del PC.

Nella dashboard il supervisore può:
- Filtrare per linea/fase, ricetta e intervallo di tempo (7/30 giorni o tutto).
- Vedere l'andamento nel tempo di ogni metrica di qualità, il confronto tra
  ricette e il confronto tra linee (pronto per quando ce ne saranno altre).
- Impostare una soglia di "difettosità" per il KPI "% ispezioni sotto soglia".
- **Personalizzare le etichette delle metriche** mostrate nei grafici (pulsante
  "⚙ Personalizza etichette") — utile per usare la terminologia interna
  dell'azienda senza dover modificare il codice. La personalizzazione è salvata
  nel browser del PC centrale.
- Passare da grafico a tabella con un click, per leggere i valori esatti.

## 3. Modificare la dashboard (competenze IT di base)

`dashboard/index.html` è un singolo file HTML/CSS/JavaScript, senza framework
né passaggi di build: si apre con un editor di testo e si modifica
direttamente. Non richiede `npm install`, compilazione o server applicativo.

Punti di estensione comuni:
- Aggiungere un nuovo grafico: copia uno dei blocchi `renderXxxChart` in
  `index.html` come esempio (usano le stesse funzioni `drawLineChart` /
  `drawGroupedBarChart`).
- Cambiare i colori: modifica le variabili `--series-1` … `--series-6` nel
  blocco `<style>` in cima al file (valori chiaro/scuro separati).
- Cambiare le soglie o i preset di intervallo temporale: cercare `preset-row`
  e `f-threshold` in `index.html`.

## 4. Struttura del progetto

```
riva-production-dashboard/
├── README.md
├── sync/
│   ├── sources.config.json     # elenco linee/fasi collegate (da configurare)
│   ├── sync_to_central.py      # script di sincronizzazione incrementale
│   └── state.json / sync.log   # generati automaticamente all'esecuzione
├── data/
│   ├── sources.json            # manifest delle linee attive (generato)
│   └── linea1.jsonl            # dati aggregati di Linea 1 (generato)
│                                # NB: nel repo contiene dati di ESEMPIO per demo
└── dashboard/
    └── index.html              # dashboard statica (grafici, filtri, KPI)
```

## Nota sui dati inclusi nel repository

`data/linea1.jsonl` e `data/sources.json` in questo repository contengono
**dati di esempio generati casualmente** (giugno–luglio 2026) solo per mostrare
la dashboard funzionante. Al primo avvio in produzione, sostituiscili
semplicemente lasciando lavorare lo script di sync, oppure svuota la cartella
`data/` prima del primo collegamento reale.
