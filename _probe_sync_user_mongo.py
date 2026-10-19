# -*- coding: utf-8 -*-
"""Procura o usuario do ultimo sync no Mongo ares-prod (beacon_devices / logs)."""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, ".")
from gerar_html_entregas_vs_sincronismo import load_env, STAGE_DIR, WORKSPACE
from pymongo import MongoClient

for path in [STAGE_DIR / ".env.ares_mongo", STAGE_DIR / ".env", WORKSPACE / ".env"]:
    for key, value in load_env(path).items():
        os.environ.setdefault(key, value)
uri = (os.getenv("ARES_MONGO_URI") or os.getenv("ARES_MONGODB_URI") or "").strip()
client = MongoClient(uri, serverSelectionTimeoutMS=20000)
db = client[os.getenv("ARES_MONGO_DB") or "ares-prod"]

doc = db["beacon_devices"].find_one({"name": "A9202"})
print("=== beacon_devices A9202: campos ===")
for k, v in sorted((doc or {}).items()):
    txt = str(v)
    print(f"  {k}: {txt[:90]}")

print()
print("=== beacon_devices_logs: ultimo doc do mac, campos ===")
mac = (doc or {}).get("mac")
if mac:
    log = db["beacon_devices_logs"].find_one({"mac": mac}, sort=[("createdAt", -1)])
    for k, v in sorted((log or {}).items()):
        txt = str(v)
        print(f"  {k}: {txt[:90]}")
client.close()
