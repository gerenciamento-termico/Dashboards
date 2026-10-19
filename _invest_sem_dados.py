# -*- coding: utf-8 -*-
"""Audita 'sincronizou mas sem dados': lastSyncDate avancou, porem
beacon_devices_logs nao tem leituras no periodo do transporte.

Pedidos citados pelo Caio em 19/10/2026.
"""
import json
import os
import re
import sys
from datetime import datetime, timedelta

sys.path.insert(0, ".")
from gerar_html_entregas_vs_sincronismo import (
    STAGE_DIR,
    WORKSPACE,
    _mongo_lookup_names,
    load_env,
    norm_tag,
)
from pymongo import MongoClient

PEDIDOS = ["571952", "571968", "571772", "571776", "571446", "571702"]
UTC_OFF = timedelta(hours=3)  # BRT -> UTC


def parse_br(txt):
    txt = (txt or "").strip()
    if not txt:
        return None
    return datetime.strptime(txt, "%d/%m/%Y %H:%M")


def fmt(dt_utc):
    if not dt_utc:
        return "-"
    return (dt_utc - UTC_OFF).strftime("%d/%m %H:%M") + " BRT"


html = (WORKSPACE / "ENTREGAS_VS_SINCRONISMO.html").read_text(encoding="utf-8")
rows = json.loads(re.search(r"const ROWS\s*=\s*(\[.*?\]);", html, re.DOTALL).group(1).replace("NaN", "null"))
alvos = [r for r in rows if str(r.get("p")) in PEDIDOS]
print(f"linhas na fonte para os 6 pedidos: {len(alvos)}")

for path in [STAGE_DIR / ".env.ares_mongo", STAGE_DIR / ".env", WORKSPACE / ".env"]:
    for key, value in load_env(path).items():
        os.environ.setdefault(key, value)
uri = (os.getenv("ARES_MONGO_URI") or os.getenv("ARES_MONGODB_URI") or "").strip()
client = MongoClient(uri, serverSelectionTimeoutMS=20000)
db = client[os.getenv("ARES_MONGO_DB") or "ares-prod"]
dev_col = db["beacon_devices"]
log_col = db["beacon_devices_logs"]

for r in sorted(alvos, key=lambda x: str(x.get("p"))):
    p = str(r.get("p"))
    tag = norm_tag(r.get("l"))
    st = r.get("st")
    ent = parse_br(r.get("e")) or parse_br(r.get("emb"))
    sync_pg = r.get("s") or "-"
    doc = None
    for cand in _mongo_lookup_names([tag]):
        doc = dev_col.find_one({"name": cand}, {"name": 1, "mac": 1, "lastSyncDate": 1})
        if doc:
            break
    print(f"\n=== pedido {p} | logger {tag} | status pagina={st} | entrega={r.get('e')} | sync pagina={sync_pg}")
    if not doc or not doc.get("mac"):
        print("    [!] logger nao encontrado no beacon_devices (ou sem mac)")
        continue
    mac = doc["mac"]
    lsd = doc.get("lastSyncDate")
    print(f"    lastSyncDate (Mongo): {fmt(lsd)}")
    if ent is None:
        print("    [!] sem data de entrega na fonte")
        continue
    ent_utc = ent + UTC_OFF
    ini = ent_utc - timedelta(days=6)   # janela do transporte: 6 dias antes da entrega
    fim = ent_utc + timedelta(days=3)   # ate 3 dias depois
    qtd = log_col.count_documents({"mac": mac, "createdAt": {"$gte": ini, "$lte": fim}})
    ultimo_antes = log_col.find_one({"mac": mac, "createdAt": {"$lte": fim}}, {"createdAt": 1, "temperature": 1},
                                    sort=[("createdAt", -1)])
    ultimo_geral = log_col.find_one({"mac": mac}, {"createdAt": 1, "temperature": 1, "user.name": 1},
                                    sort=[("createdAt", -1)])
    print(f"    leituras na janela do transporte ({fmt(ini)} a {fmt(fim)}): {qtd}")
    if ultimo_geral:
        u = (ultimo_geral.get("user") or {}).get("name", "")
        print(f"    ultima leitura registrada (geral): {fmt(ultimo_geral.get('createdAt'))} | temp={ultimo_geral.get('temperature')} | user={u}")
    if ultimo_antes and ultimo_antes is not ultimo_geral:
        print(f"    ultima leitura ate o fim da janela: {fmt(ultimo_antes.get('createdAt'))} | temp={ultimo_antes.get('temperature')}")
    if lsd and qtd == 0:
        print("    >>> VEREDITO: SINCRONIZOU SEM DADOS (sync registrado, zero leituras no transporte)")
    elif lsd and ultimo_geral and lsd - ultimo_geral["createdAt"] > timedelta(hours=12):
        gap_h = (lsd - ultimo_geral["createdAt"]).total_seconds() / 3600
        print(f"    >>> VEREDITO: parou de registrar {gap_h:.0f}h antes do sync (dados ate {fmt(ultimo_geral['createdAt'])})")
    else:
        print("    >>> VEREDITO: dados presentes na janela (aparentemente ok)")
client.close()
