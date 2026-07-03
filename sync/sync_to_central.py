#!/usr/bin/env python3
"""
Sync Riva - Dashboard Centralizzata
------------------------------------
Copia in modo incrementale i risultati delle ispezioni estetiche (SmartWiches.AI)
da ogni PC di linea verso il PC centrale, senza toccare i sistemi installati
sulle linee di produzione.

Cosa fa ad ogni esecuzione, per ogni sorgente elencata in sources.config.json:
  1. Copia il file SQLite della linea (accessibile via share di rete) in una
     cartella temporanea locale, cosi' non si tiene mai un lock sul file di
     produzione e non si dipende dalla rete per la durata della query.
  2. Legge solo le righe nuove (id maggiore dell'ultimo sincronizzato) dalle
     tabelle sandwich_inference + sandwich_metric.
  3. Le aggiunge (append) al file data/<id_linea>.jsonl, un record per
     ispezione con l'elenco delle metriche. Nessuna immagine viene copiata:
     alla dashboard servono solo i numeri.
  4. Aggiorna data/sources.json (elenco linee attive, usato dalla dashboard)
     e sync/state.json (ultimo id sincronizzato per linea, per la ripartenza).

Uso:
    python sync_to_central.py

Da schedulare con Utilita' di pianificazione di Windows (Task Scheduler) ogni
5-15 minuti sul PC CENTRALE (non sul PC di linea). Richiede solo Python 3
standard, nessuna dipendenza esterna.
"""

import json
import logging
import shutil
import sqlite3
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
ROOT_DIR = BASE_DIR.parent
CONFIG_PATH = BASE_DIR / "sources.config.json"
STATE_PATH = BASE_DIR / "state.json"
DATA_DIR = ROOT_DIR / "data"
LOG_PATH = BASE_DIR / "sync.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler(LOG_PATH, encoding="utf-8"), logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("sync")


def load_json(path, default):
    if not path.exists():
        return default
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    tmp.replace(path)


def copy_source_db(source_db_path, tmp_dir):
    """Copia il DB (e i file -wal/-shm se presenti) in locale prima di interrogarlo."""
    src = Path(source_db_path)
    if not src.exists():
        raise FileNotFoundError(f"DB non raggiungibile: {source_db_path}")
    local_db = tmp_dir / src.name
    shutil.copy2(src, local_db)
    for suffix in ("-wal", "-shm"):
        side_file = src.with_name(src.name + suffix)
        if side_file.exists():
            shutil.copy2(side_file, tmp_dir / side_file.name)
    return local_db


def fetch_new_inferences(local_db_path, last_id):
    """Legge da sandwich_inference + sandwich_metric le ispezioni con id > last_id."""
    con = sqlite3.connect(f"file:{local_db_path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            """
            SELECT si.id AS inference_id, si.created_at, si.recipe_id, si.recipe_name,
                   sm.metric_key, sm.metric_label, sm.score, sm.max_score
            FROM sandwich_inference si
            JOIN sandwich_metric sm ON sm.inference_id = si.id
            WHERE si.id > ?
            ORDER BY si.id
            """,
            (last_id,),
        ).fetchall()
    finally:
        con.close()

    inferences = {}
    for row in rows:
        rec = inferences.setdefault(
            row["inference_id"],
            {
                "id": row["inference_id"],
                "ts": row["created_at"],
                "recipe": row["recipe_name"],
                "metrics": [],
            },
        )
        rec["metrics"].append(
            {
                "key": row["metric_key"],
                "label": row["metric_label"],
                "score": row["score"],
                "max": row["max_score"],
            }
        )
    return list(inferences.values())


def append_jsonl(path, records, source_meta):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        for rec in records:
            rec_out = {
                "id": rec["id"],
                "ts": rec["ts"],
                "line": source_meta["id"],
                "phase": source_meta.get("phase", ""),
                "recipe": rec["recipe"],
                "metrics": rec["metrics"],
            }
            f.write(json.dumps(rec_out, ensure_ascii=False) + "\n")


def update_sources_manifest(source_meta, last_sync_iso, record_count):
    manifest_path = DATA_DIR / "sources.json"
    manifest = load_json(manifest_path, [])
    entry = next((s for s in manifest if s["id"] == source_meta["id"]), None)
    if entry is None:
        entry = {"id": source_meta["id"]}
        manifest.append(entry)
    entry.update(
        {
            "label": source_meta.get("label", source_meta["id"]),
            "phase": source_meta.get("phase", ""),
            "file": f"{source_meta['id']}.jsonl",
            "last_sync": last_sync_iso,
        }
    )
    if record_count:
        entry["last_new_records"] = record_count
    save_json(manifest_path, manifest)


def sync_source(source_meta, state):
    source_id = source_meta["id"]
    last_id = state.get(source_id, 0)

    with tempfile.TemporaryDirectory(prefix=f"riva_sync_{source_id}_") as tmp:
        local_db = copy_source_db(source_meta["source_db_path"], Path(tmp))
        records = fetch_new_inferences(local_db, last_id)

    if records:
        append_jsonl(DATA_DIR / f"{source_id}.jsonl", records, source_meta)
        state[source_id] = max(r["id"] for r in records)
        log.info("Linea '%s': %d nuove ispezioni sincronizzate (fino a id=%d)", source_id, len(records), state[source_id])
    else:
        log.info("Linea '%s': nessuna nuova ispezione", source_id)

    update_sources_manifest(source_meta, datetime.now(timezone.utc).isoformat(), len(records))


def main():
    config = load_json(CONFIG_PATH, {"sources": []})
    state = load_json(STATE_PATH, {})

    any_error = False
    for source_meta in config.get("sources", []):
        if not source_meta.get("enabled", True):
            continue
        try:
            sync_source(source_meta, state)
        except Exception as exc:
            any_error = True
            log.error("Errore sincronizzando la linea '%s': %s", source_meta.get("id"), exc)

    save_json(STATE_PATH, state)
    if any_error:
        sys.exit(1)


if __name__ == "__main__":
    main()
