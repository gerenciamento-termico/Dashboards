# -*- coding: utf-8 -*-
"""Pendencias de Sincronismo — um recorte, tres fontes, uma regra por coluna."""
from __future__ import annotations

import json
import os
import re
import unicodedata
from collections import Counter
from datetime import datetime
from pathlib import Path

import pandas as pd
from openpyxl import load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from sqlalchemy import bindparam, create_engine, text
from sqlalchemy.engine import URL

from env_utils import load_env_file

WORKSPACE = Path(__file__).resolve().parent
SNAPSHOT_DIR = WORKSPACE / "snapshot_pendencias_sincronismo"
SNAPSHOT_JSON = SNAPSHOT_DIR / "pendencias_sincronismo.json"
TEMPLATE = WORKSPACE / "pendencias_sincronismo_template.html"
OUT_HTML = WORKSPACE / "PENDENCIAS_SINCRONISMO.html"
OUT_CSV = WORKSPACE / "PENDENCIAS_SINCRONISMO.csv"
OUT_XLSX = WORKSPACE / "PENDENCIAS_SINCRONISMO.xlsx"
OUT_MANIFEST = WORKSPACE / "MANIFESTO_SNAPSHOT_PENDENCIAS_SINCRONISMO.json"
STAGE_DIR = Path(r"C:\Users\Administrador\Documents\NOVO INDICADOR DE REVERSA - VTC_STAGE")
REVERSA_HTML = WORKSPACE / "REVERSA_DATALOGGERS.html"
TIPOS_FORCADOS = frozenset({"SENSOR WEB", "SHIELD", "SYOS", "SENSOR VTC", "ELITECH"})
SYNC_TOLERANCE = pd.Timedelta(minutes=15)
ARES_NUMERIC_RE = re.compile(r"^(?:0|V)7417\d+$", flags=re.I)

EXPORT_COLS = [
    "Pedido",
    "Logger",
    "Tipo",
    "LPN",
    "UF",
    "Cliente VTC",
    "Coleta",
    "Entrega",
    "Chegada cliente",
    "Status viagem VTC",
    "Romaneio",
    "Modal",
    "Embarque",
    "Desembarque",
    "AWB",
    "Último Sync",
    "Dias sem sync",
    "Em GRU?",
    "Localização",
    "Situação atual",
    "Responsável atual",
    "Destino portal",
    "Finalidade portal",
    "Status recebimento",
    "Prova posição",
    "Tag no dtbPortal",
    "Atualização dtbPortal",
    "CTE",
    "Observação portal",
    "Ação sugerida",
]


def now_brt() -> datetime:
    return pd.Timestamp.now(tz="America/Sao_Paulo").tz_localize(None).to_pydatetime()


def to_brt(value: object) -> pd.Timestamp:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return pd.NaT
    if isinstance(value, datetime) and not isinstance(value, pd.Timestamp):
        value = pd.Timestamp(value)
    if isinstance(value, pd.Timestamp):
        if pd.isna(value):
            return pd.NaT
        if value.tzinfo is not None:
            return value.tz_convert("America/Sao_Paulo").tz_localize(None)
        return value
    raw = str(value).strip()
    if not raw or raw.lower() in {"nan", "nat", "none"}:
        return pd.NaT
    parsed = parse_br(raw)
    if pd.notna(parsed):
        return parsed
    ts = pd.to_datetime(raw, errors="coerce", utc=True)
    if pd.isna(ts):
        return pd.NaT
    return ts.tz_convert("America/Sao_Paulo").tz_localize(None)


def fmt_ts(value: object) -> str:
    ts = to_brt(value)
    if pd.isna(ts):
        return ""
    if ts.hour == 0 and ts.minute == 0 and ts.second == 0:
        return ts.strftime("%d/%m/%Y")
    return ts.strftime("%d/%m/%Y %H:%M")


def parse_br(value: object) -> pd.Timestamp:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return pd.NaT
    if isinstance(value, pd.Timestamp):
        return value if value.tzinfo is None else value.tz_convert("America/Sao_Paulo").tz_localize(None)
    raw = str(value).strip()
    if not raw or raw.lower() in {"nan", "nat", "none"}:
        return pd.NaT
    for fmt, size in (
        ("%d/%m/%Y %H:%M:%S", 19),
        ("%d/%m/%Y %H:%M", 16),
        ("%d/%m/%y %H:%M:%S", 17),
        ("%d/%m/%y %H:%M", 14),
        ("%d/%m/%Y", 10),
        ("%d/%m/%y", 8),
        ("%Y-%m-%dT%H:%M:%S", 19),
        ("%Y-%m-%d %H:%M:%S", 19),
    ):
        try:
            return pd.Timestamp(datetime.strptime(raw[:size], fmt))
        except ValueError:
            continue
    ts = pd.to_datetime(raw, errors="coerce", dayfirst=True)
    if pd.isna(ts):
        return pd.NaT
    if getattr(ts, "tzinfo", None) is not None:
        return ts.tz_convert("America/Sao_Paulo").tz_localize(None)
    return ts


def load_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        values[key.strip()] = val.strip().strip('"').strip("'")
    return values


def norm_text(value: object) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    text_value = unicodedata.normalize("NFD", str(value))
    text_value = "".join(ch for ch in text_value if unicodedata.category(ch) != "Mn")
    return " ".join(text_value.strip().upper().split())


def norm_tag(value: object) -> str:
    tag = re.sub(r"\s+", "", norm_text(value)).replace("-", "")
    match = re.fullmatch(r"S(\d+)", tag)
    return "S" + match.group(1).zfill(4) if match else tag


def is_ares_numeric(logger: str) -> bool:
    """Novo ARES numérico (ex.: 0741700439, V741701511). Não mistura com SENSOR WEB 3000…/8691…."""
    return bool(ARES_NUMERIC_RE.match(norm_tag(logger)))


def is_forced(tipo: object) -> bool:
    return norm_text(tipo) in TIPOS_FORCADOS


def is_ares(logger: str, tipo: object) -> bool:
    t = norm_text(tipo)
    if t in {"ARES", "ARES COM SONDA"}:
        return True
    if is_forced(tipo):
        return False
    if is_ares_numeric(logger):
        return True
    return bool(re.match(r"^(A|TA|AS)\d+", logger or "", flags=re.I))


def classify_sync(entrega: pd.Timestamp, sync: pd.Timestamp, chegada: pd.Timestamp, tipo: object) -> str:
    if is_forced(tipo):
        return "SINCRONIZADO"
    if pd.isna(entrega):
        return "NAO_AVALIADO"
    if pd.notna(sync) and sync >= (entrega - SYNC_TOLERANCE):
        return "SINCRONIZADO"
    if pd.notna(chegada) and pd.notna(sync) and sync >= (chegada - SYNC_TOLERANCE):
        return "SINCRONIZADO"
    return "PENDENTE"


def classify_viagem_vtc(chegada, entrega, desembarque, embarque, coleta_emb, entrega_emb, romaneio) -> str:
    if pd.notna(to_brt(chegada)):
        return "RECEBIDO NO CLIENTE"
    if pd.notna(to_brt(entrega)):
        return "ENTREGUE"
    if pd.notna(to_brt(desembarque)) or pd.notna(to_brt(entrega_emb)):
        return "EM TRÂNSITO"
    if pd.notna(to_brt(embarque)) or pd.notna(to_brt(coleta_emb)) or str(romaneio or "").strip():
        return "EM TRÂNSITO"
    return "SEM MOVIMENTO VTC"


def classify_posicao(destino, finalidade, status_recebimento, responsavel) -> dict[str, str]:
    dest = str(destino or "").strip()
    fin = str(finalidade or "").strip()
    status = str(status_recebimento or "").strip()
    dest_n = norm_text(dest)
    fin_n = norm_text(fin)
    status_n = norm_text(status)
    resp_n = norm_text(responsavel)
    prova = " · ".join(part for part in (dest, fin, status) if part) or "sem movimento no dtbPortal"
    estoque = dest_n == "EM ESTOQUE" or ("ESTOQUE" in dest_n and "GRU" in dest_n)
    saldo = fin_n in {"SALDO DE ESTOQUE", "SALDO ESTOQUE"} or "SALDO DE ESTOQUE" in fin_n
    recebido = status_n == "RECEBIDO"
    if estoque and saldo and recebido:
        return {"Em GRU?": "SIM", "Localização": "EM GRU", "Situação atual": "ESTOQUE - GRU", "Prova posição": prova}
    if estoque and saldo:
        return {
            "Em GRU?": "NÃO",
            "Localização": "RETORNANDO PARA GRU (NÃO RECEBIDO)",
            "Situação atual": "RETORNANDO - GRU",
            "Prova posição": prova,
        }
    if "RETORNANDO" in dest_n or "RETORNANDO" in fin_n or ("REC" in dest_n and "VTC" in dest_n):
        return {
            "Em GRU?": "NÃO",
            "Localização": "RETORNANDO PARA GRU (NÃO RECEBIDO)",
            "Situação atual": "RETORNANDO - GRU",
            "Prova posição": prova,
        }
    if "CAMARA" in dest_n:
        if recebido:
            sit_cam = "CÂMARA FRIA"
            if "PACKING" in fin_n:
                sit_cam = "CÂMARA FRIA - PACKING"
            elif "PEDIDO" in fin_n:
                sit_cam = "CÂMARA FRIA - PEDIDOS"
            return {
                "Em GRU?": "SIM",
                "Localização": "CÂMARA FRIA (GRU)",
                "Situação atual": sit_cam,
                "Prova posição": prova,
            }
        return {
            "Em GRU?": "NÃO",
            "Localização": "CÂMARA FRIA (GRU)",
            "Situação atual": "CÂMARA FRIA",
            "Prova posição": prova,
        }
    situacao = ""
    if "MANUTEN" in fin_n:
        situacao = "MANUTENÇÃO"
    elif "AGENTE" in resp_n or "TERCEIRO" in dest_n or "TERCEIRO" in fin_n or "EM ROTA" in fin_n:
        situacao = "AGENTE"
    elif dest_n == "TRANSPORTE" or "TRANSITO" in fin_n:
        situacao = "EM TRÂNSITO"
    elif fin:
        situacao = fin.upper()
    if dest_n in {"TRANSPORTE", "TERCEIROS"} or "TERCEIRO" in dest_n or "AGENTE" in resp_n or "EM ROTA" in fin_n:
        return {"Em GRU?": "NÃO", "Localização": "FORA DE GRU", "Situação atual": situacao or "FORA DE GRU", "Prova posição": prova}
    if not dest_n:
        return {"Em GRU?": "NÃO", "Localização": "SEM POSIÇÃO NO DTBPORTAL", "Situação atual": "SEM POSIÇÃO", "Prova posição": prova}
    return {
        "Em GRU?": "NÃO",
        "Localização": "POSIÇÃO NÃO COMPROVADA EM GRU",
        "Situação atual": situacao or (fin.upper() if fin else "NÃO COMPROVADA"),
        "Prova posição": prova,
    }


def acao_sugerida(localizacao: str, situacao: str) -> str:
    loc = norm_text(localizacao)
    sit = norm_text(situacao)
    if loc == "EM GRU":
        return "SINCRONIZAR EM GRU"
    if "CAMARA" in loc or "CAMARA" in sit:
        return "VALIDAR NA CÂMARA FRIA"
    if loc.startswith("RETORNANDO"):
        return "ACOMPANHAR RECEBIMENTO EM GRU"
    if "NAO COMPROVADA" in loc or loc.startswith("SEM POSICAO"):
        return "VALIDAR CADASTRO/POSIÇÃO NO DTBPORTAL"
    if sit in {"AGENTE", "EM TRANSITO"} or "TERCEIRO" in sit or "EM ROTA" in sit:
        return "ACIONAR AGENTE/RESPONSÁVEL"
    if "MANUTEN" in sit:
        return "VALIDAR COM MANUTENÇÃO"
    return "VALIDAR COM RESPONSÁVEL ATUAL"


def cell(row: list, idx: dict[str, int], name: str) -> str:
    pos = idx.get(name)
    if pos is None or pos >= len(row) or row[pos] is None:
        return ""
    return str(row[pos]).strip()


def load_reversa() -> list[dict]:
    html = REVERSA_HTML.read_text(encoding="utf-8", errors="replace")
    rows = json.loads(re.search(r"const ALL_ROWS=(\[.*?\]);", html).group(1))
    headers = json.loads(re.search(r"const TABLE_HEADERS=(\[.*?\]);", html).group(1))
    idx = {str(name): i for i, name in enumerate(headers)}
    by_key: dict[tuple[str, str], dict] = {}
    for row in rows:
        logger = norm_tag(cell(row, idx, "Logger"))
        pedido = cell(row, idx, "Pedido")
        tipo = cell(row, idx, "Tipo Datalogger")
        if not pedido or not logger or not is_ares(logger, tipo):
            continue
        entrega = parse_br(cell(row, idx, "Data de Entrega"))
        rec = {
            "pedido": pedido,
            "logger": logger,
            "logger_raw": cell(row, idx, "Logger") or logger,
            "tipo": tipo or "ARES",
            "lpn": cell(row, idx, "LPN"),
            "uf": cell(row, idx, "UF Destino") or cell(row, idx, "UF"),
            "agente": cell(row, idx, "Agente"),
            "entrega": entrega,
            "chegada": parse_br(cell(row, idx, "dt_chegadacliente")),
            "romaneio": cell(row, idx, "Romaneio"),
        }
        prev = by_key.get((pedido, logger))
        if prev is None or (pd.notna(entrega) and (pd.isna(prev["entrega"]) or entrega >= prev["entrega"])):
            by_key[(pedido, logger)] = rec
    return list(by_key.values())


def fetch_vtc(pedidos: list[str]) -> dict[tuple[str, str], object]:
    env = load_env(WORKSPACE / ".env.vtc_stage")
    if not env.get("VTC_STAGE_HOST") or not pedidos:
        raise RuntimeError("VTC_STAGE ausente")
    tag_sql = (
        "REPLACE(REPLACE(UPPER(TRIM(COALESCE(NULLIF(TRIM(ds_tag), ''), "
        "NULLIF(TRIM(cd_referencia), '')))), '-', ''), ' ', '')"
    )
    query = text(
        f"""
        SELECT TRIM(nr_pedido::text) AS pedido,
               {tag_sql} AS logger,
               MAX(NULLIF(TRIM(cd_uf), '')) AS uf,
               MAX(NULLIF(TRIM(ds_cliente), '')) AS cliente,
               MAX(NULLIF(TRIM(modal), '')) AS modal,
               MAX(NULLIF(TRIM(cd_lpn::text), '')) AS lpn,
               MAX(NULLIF(TRIM(cd_awb), '')) AS awb,
               MAX(NULLIF(TRIM(nr_romaneio::text), '')) AS romaneio,
               MIN(dt_coletaefetiva) AS coleta,
               MAX(dt_entregaefetiva) AS entrega,
               MIN(dt_chegadacliente) AS chegada,
               MAX(dt_coletaefetivaembarque) AS coleta_embarque,
               MAX(dt_entregaefetivaembarque) AS entrega_embarque,
               MAX(dt_embarquecia) AS embarque_cia,
               MAX(dt_desembarquecia) AS desembarque_cia
        FROM vtc_stage.documentos
        WHERE TRIM(nr_pedido::text) IN :peds
        GROUP BY TRIM(nr_pedido::text), {tag_sql}
        """
    ).bindparams(bindparam("peds", expanding=True))
    url = URL.create(
        "postgresql+psycopg2",
        username=env["VTC_STAGE_USER"],
        password=env["VTC_STAGE_PASSWORD"],
        host=env["VTC_STAGE_HOST"],
        port=int(env.get("VTC_STAGE_PORT") or 5432),
        database=env["VTC_STAGE_NAME"],
    )
    engine = create_engine(url, pool_pre_ping=True)
    with engine.connect() as connection:
        frame = pd.read_sql(query, connection, params={"peds": pedidos})
    out: dict[tuple[str, str], object] = {}
    for row in frame.itertuples(index=False):
        out[(str(row.pedido or "").strip(), norm_tag(row.logger))] = row
    return out


def fetch_mongo(loggers: list[str]) -> dict[str, pd.Timestamp]:
    for path in [STAGE_DIR / ".env.ares_mongo", STAGE_DIR / ".env", WORKSPACE / ".env"]:
        for key, value in load_env(path).items():
            os.environ.setdefault(key, value)
    uri = (os.getenv("ARES_MONGO_URI") or os.getenv("ARES_MONGODB_URI") or "").strip()
    if not uri:
        raise RuntimeError("ARES_MONGO_URI ausente")
    from pymongo import MongoClient

    names = sorted({norm_tag(item) for item in loggers if norm_tag(item)})
    lookup = set()
    for name in names:
        lookup.add(name)
        lookup.add(name + " ")
        match = re.fullmatch(r"(AS)(\d+)", name, flags=re.I)
        if match:
            hyphen = match.group(1).upper() + "-" + match.group(2)
            lookup.add(hyphen)
            lookup.add(hyphen + " ")
    lookup = sorted(lookup)
    client = MongoClient(uri, serverSelectionTimeoutMS=25000, connectTimeoutMS=25000)
    try:
        client.admin.command("ping")
        col = client[os.getenv("ARES_MONGO_DB") or "ares-prod"]["beacon_devices"]
        mapping: dict[str, pd.Timestamp] = {}
        for index in range(0, len(lookup), 2000):
            for doc in col.find({"name": {"$in": lookup[index : index + 2000]}}, {"name": 1, "lastSyncDate": 1}):
                key = norm_tag(doc.get("name"))
                ts = pd.to_datetime(doc.get("lastSyncDate"), errors="coerce", utc=True)
                if key and pd.notna(ts):
                    ts = ts.tz_convert("America/Sao_Paulo").tz_localize(None)
                    prev = mapping.get(key)
                    if prev is None or ts > prev:
                        mapping[key] = ts
        return mapping
    finally:
        client.close()


def fetch_dtbportal(tags: list[str]) -> dict[str, object]:
    tags = sorted({norm_tag(item) for item in tags if norm_tag(item)})
    if not tags:
        return {}
    load_env_file(WORKSPACE / ".env")
    env = load_env(WORKSPACE / ".env")
    if not env.get("AURA_POSTGRES_HOST"):
        raise RuntimeError("dtbPortal ausente")
    tag_norm = "REPLACE(REPLACE(UPPER(TRIM(vwt.ds_tag)), '-', ''), ' ', '')"
    query = text(
        f"""
        SELECT DISTINCT ON ({tag_norm})
               {tag_norm} AS logger,
               UPPER(TRIM(vwt.ds_tag)) AS tag_portal,
               vwt.ds_tipodatalogger,
               vwt.ds_destino,
               vwt.ds_finalidade,
               vwt.ds_responsavel,
               vwt.ds_statusrecebimento,
               vwt.dt_atualizacao,
               vwt.ds_cte,
               vwt.ds_observacao
        FROM vwTabelaMovDataloggers vwt
        WHERE {tag_norm} IN :tags
        ORDER BY {tag_norm},
                 vwt.dt_atualizacao DESC NULLS LAST,
                 COALESCE(vwt.ds_statusrecebimento, ''),
                 COALESCE(vwt.ds_destino, '')
        """
    ).bindparams(bindparam("tags", expanding=True))
    url = URL.create(
        "postgresql+psycopg2",
        username=env["AURA_POSTGRES_USER"],
        password=env["AURA_POSTGRES_PASSWORD"],
        host=env["AURA_POSTGRES_HOST"],
        port=int(env.get("AURA_POSTGRES_PORT") or 5432),
        database=env["AURA_POSTGRES_NAME"],
    )
    engine = create_engine(url, pool_pre_ping=True)
    with engine.connect() as connection:
        frame = pd.read_sql(query, connection, params={"tags": tags})
    out: dict[str, object] = {}
    for row in frame.itertuples(index=False):
        key = norm_tag(row.logger)
        if key and key not in out:
            out[key] = row
    if len(out) != len(frame):
        print(f"AVISO: dtbPortal DISTINCT ON ainda tinha dup; mantida 1 linha/tag ({len(frame)} -> {len(out)})")
    return out


def rank_trip(entrega: pd.Timestamp, chegada: pd.Timestamp, pedido: str) -> tuple:
    sentinela = pd.Timestamp("1678-01-01")
    return (
        entrega if pd.notna(entrega) else sentinela,
        chegada if pd.notna(chegada) else sentinela,
        str(pedido or ""),
    )


def build_records(pagina_em: datetime) -> tuple[list[dict], dict[str, int]]:
    reversa = load_reversa()
    print(f"Universo reversa ARES: {len(reversa)} | loggers unicos: {len({r['logger'] for r in reversa})}")
    vtc = fetch_vtc(sorted({r["pedido"] for r in reversa}))
    print(f"portal VTC: {len(vtc)} pares pedido+logger")
    mongo = fetch_mongo([r["logger"] for r in reversa])
    print(f"Mongo lastSyncDate: {len(mongo)} loggers")

    por_logger: dict[str, dict] = {}
    stats = Counter()
    for base in reversa:
        vtc_row = vtc.get((base["pedido"], base["logger"]))
        if vtc_row is not None:
            entrega = to_brt(vtc_row.entrega)
            chegada = to_brt(vtc_row.chegada)
            stats["vtc_match"] += 1
        else:
            entrega = base["entrega"]
            chegada = base["chegada"]
            stats["vtc_sem_match"] += 1
        if pd.isna(entrega):
            entrega = base["entrega"]
        atual = {
            "base": base,
            "vtc": vtc_row,
            "entrega": entrega,
            "chegada": chegada,
            "rank": rank_trip(entrega, chegada, base["pedido"]),
        }
        prev = por_logger.get(base["logger"])
        if prev is None or atual["rank"] > prev["rank"]:
            if prev is not None:
                stats["viagens_descartadas_logger"] += 1
            por_logger[base["logger"]] = atual
        else:
            stats["viagens_descartadas_logger"] += 1
    stats["universo_loggers"] = len(por_logger)
    print(
        f"Viagem vigente por logger: {len(por_logger)} | "
        f"viagens anteriores descartadas: {stats['viagens_descartadas_logger']}"
    )

    candidatos = []
    for logger, item in por_logger.items():
        sync = mongo.get(logger, pd.NaT)
        item["sync"] = sync
        status = classify_sync(item["entrega"], sync, item["chegada"], item["base"]["tipo"])
        stats[status] += 1
        if pd.isna(sync):
            stats["sem_lastsync"] += 1
        if status != "PENDENTE":
            continue
        vtc_row = item["vtc"]
        item["viagem"] = (
            classify_viagem_vtc(
                vtc_row.chegada,
                vtc_row.entrega,
                vtc_row.desembarque_cia,
                vtc_row.embarque_cia,
                vtc_row.coleta_embarque,
                vtc_row.entrega_embarque,
                vtc_row.romaneio,
            )
            if vtc_row is not None
            else "SEM MATCH VTC"
        )
        candidatos.append(item)

    portal = fetch_dtbportal([c["base"]["logger"] for c in candidatos])
    print(
        f"dtbPortal: {len(portal)} tags | loggers unicos: {len(por_logger)} | "
        f"pendentes lastSyncDate: {len(candidatos)}"
    )

    records = []
    for item in candidatos:
        base, vtc_row = item["base"], item["vtc"]
        entrega, chegada, sync = item["entrega"], item["chegada"], item["sync"]
        row = portal.get(base["logger"])
        dest = getattr(row, "ds_destino", "") if row is not None else ""
        fin = getattr(row, "ds_finalidade", "") if row is not None else ""
        status_rec = getattr(row, "ds_statusrecebimento", "") if row is not None else ""
        responsavel = str(getattr(row, "ds_responsavel", "") or base["agente"] or "").strip()
        pos = classify_posicao(dest, fin, status_rec, responsavel)
        rec = {
            "Pedido": base["pedido"],
            "Logger": base["logger_raw"],
            "Tipo": base["tipo"],
            "LPN": (str(vtc_row.lpn).strip() if vtc_row is not None and vtc_row.lpn else "") or base["lpn"],
            "UF": (str(vtc_row.uf).strip().upper() if vtc_row is not None and vtc_row.uf else "") or base["uf"] or "SEM UF",
            "Cliente VTC": str(vtc_row.cliente or "").strip() if vtc_row is not None else "",
            "Coleta": fmt_ts(vtc_row.coleta) if vtc_row is not None else "",
            "Entrega": fmt_ts(entrega),
            "Chegada cliente": fmt_ts(chegada),
            "Status viagem VTC": item["viagem"],
            "Romaneio": (str(vtc_row.romaneio or "").strip() if vtc_row is not None else "") or base["romaneio"],
            "Modal": str(vtc_row.modal or "").strip() if vtc_row is not None else "",
            "Embarque": (fmt_ts(vtc_row.embarque_cia) or fmt_ts(vtc_row.coleta_embarque)) if vtc_row is not None else "",
            "Desembarque": (fmt_ts(vtc_row.desembarque_cia) or fmt_ts(vtc_row.entrega_embarque)) if vtc_row is not None else "",
            "AWB": str(vtc_row.awb or "").strip() if vtc_row is not None else "",
            "Último Sync": fmt_ts(sync) if pd.notna(sync) else "",
            "Dias sem sync": int((pd.Timestamp(pagina_em) - entrega).total_seconds() // 86400) if pd.notna(entrega) else 0,
            "Responsável atual": responsavel,
            "Destino portal": str(dest or "").strip(),
            "Finalidade portal": str(fin or "").strip(),
            "Status recebimento": str(status_rec or "").strip(),
            "Tag no dtbPortal": str(getattr(row, "tag_portal", "") or base["logger_raw"]) if row is not None else base["logger_raw"],
            "Atualização dtbPortal": fmt_ts(getattr(row, "dt_atualizacao", None)) if row is not None else "",
            "CTE": str(getattr(row, "ds_cte", "") or "").strip() if row is not None else "",
            "Observação portal": str(getattr(row, "ds_observacao", "") or "").strip() if row is not None else "",
            **pos,
        }
        rec["Ação sugerida"] = acao_sugerida(rec["Localização"], rec["Situação atual"])
        records.append(rec)
    return records, dict(stats)


def validar(records: list[dict], mongo: dict[str, pd.Timestamp] | None = None) -> dict[str, int]:
    erros = Counter()
    loggers = [norm_tag(r["Logger"]) for r in records]
    if len(loggers) != len(set(loggers)):
        erros["dup_logger"] = len(loggers) - len(set(loggers))
    keys = [(r["Pedido"], norm_tag(r["Logger"])) for r in records]
    if len(keys) != len(set(keys)):
        erros["dup_pedido_logger"] = len(keys) - len(set(keys))
    tags_portal = [norm_tag(r.get("Tag no dtbPortal")) for r in records if norm_tag(r.get("Tag no dtbPortal"))]
    if len(tags_portal) != len(set(tags_portal)):
        erros["dup_tag_dtbportal"] = len(tags_portal) - len(set(tags_portal))
    for rec in records:
        entrega = parse_br(rec.get("Entrega"))
        chegada = parse_br(rec.get("Chegada cliente"))
        sync = parse_br(rec.get("Último Sync"))
        if classify_sync(entrega, sync, chegada, rec.get("Tipo")) != "PENDENTE":
            erros["falso_pendente_vs_datas_exibidas"] += 1
        esperado = classify_posicao(
            rec.get("Destino portal"),
            rec.get("Finalidade portal"),
            rec.get("Status recebimento"),
            rec.get("Responsável atual"),
        )
        if rec.get("Em GRU?") != esperado["Em GRU?"] or rec.get("Localização") != esperado["Localização"]:
            erros["gru_inconsistente"] += 1
        if rec.get("Em GRU?") == "SIM" and rec.get("Status recebimento", "").upper() != "RECEBIDO":
            erros["gru_sem_recebido"] += 1
        dest_n = norm_text(rec.get("Destino portal"))
        if "CAMARA" in dest_n and rec.get("Status recebimento", "").upper() == "RECEBIDO" and rec.get("Em GRU?") != "SIM":
            erros["camara_recebido_sem_gru"] += 1
        viagem = rec.get("Status viagem VTC")
        if viagem == "RECEBIDO NO CLIENTE" and not rec.get("Chegada cliente"):
            erros["recebido_sem_chegada"] += 1
        if viagem == "ENTREGUE" and not rec.get("Entrega"):
            erros["entregue_sem_entrega"] += 1
        if viagem == "ENTREGUE" and rec.get("Chegada cliente"):
            erros["entregue_com_chegada"] += 1
    print("VALIDACAO", dict(erros) or "OK")
    return dict(erros)


def build_resumo(records: list[dict]) -> list[dict]:
    grupos: dict[str, dict] = {}
    for rec in records:
        uf = rec.get("UF") or "SEM UF"
        item = grupos.setdefault(
            uf,
            {"UF": uf, "Pendências": 0, "Em GRU": 0, "Câmara fria": 0, "Fora de GRU": 0, "Retornando p/ GRU": 0, "dias": []},
        )
        item["Pendências"] += 1
        dias = int(rec.get("Dias sem sync") or 0)
        item["dias"].append(dias)
        loc = str(rec.get("Localização") or "")
        if "CÂMARA" in loc.upper() or "CAMARA" in norm_text(loc):
            item["Câmara fria"] += 1
        elif rec.get("Em GRU?") == "SIM":
            item["Em GRU"] += 1
        elif loc.startswith("RETORNANDO"):
            item["Retornando p/ GRU"] += 1
        else:
            item["Fora de GRU"] += 1
    out = []
    for item in grupos.values():
        dias = item.pop("dias")
        out.append(
            {
                **item,
                "Dias sem sync (máx)": max(dias) if dias else 0,
                "Dias sem sync (média)": round(sum(dias) / len(dias), 1) if dias else 0,
            }
        )
    return sorted(out, key=lambda row: (-int(row["Pendências"]), str(row["UF"])))


def write_excel(records: list[dict], resumo: list[dict], pagina_em: datetime, snapshot_em: str) -> None:
    cabecalho = pd.DataFrame(
        [
            ("Página atualizada em", fmt_ts(pagina_em)),
            ("Snapshot em", snapshot_em),
            ("Total de pendências", len(records)),
            ("UFs", len({r.get("UF") for r in records if r.get("UF")})),
            ("Regra sync", "lastSyncDate BRT >= entrega - 15 min"),
            ("Regra GRU", "estoque+saldo+RECEBIDO ou câmara+RECEBIDO"),
            ("Regra recebido cliente", "dt_chegadacliente no VTC"),
        ],
        columns=["Indicador", "Valor"],
    )
    detalhe = pd.DataFrame(records)
    for col in EXPORT_COLS:
        if col not in detalhe.columns:
            detalhe[col] = ""
    detalhe = detalhe[EXPORT_COLS]
    with pd.ExcelWriter(OUT_XLSX, engine="openpyxl") as writer:
        cabecalho.to_excel(writer, sheet_name="REGRAS", index=False)
        pd.DataFrame(resumo).to_excel(writer, sheet_name="RESUMO_UF", index=False)
        detalhe.to_excel(writer, sheet_name="PENDENCIAS", index=False)
    workbook = load_workbook(OUT_XLSX)
    header_fill = PatternFill("solid", fgColor="1F4E78")
    header_font = Font(color="FFFFFF", bold=True)
    for sheet in workbook.worksheets:
        sheet.freeze_panes = "A2"
        sheet.auto_filter.ref = sheet.dimensions
        for cell in sheet[1]:
            cell.fill = header_fill
            cell.font = header_font
            cell.alignment = Alignment(horizontal="center", vertical="center")
        for index, cells in enumerate(sheet.columns, 1):
            values = [str(c.value or "") for c in list(cells)[:400]]
            sheet.column_dimensions[get_column_letter(index)].width = min(max(max((len(v) for v in values), default=0) + 2, 10), 42)
    workbook.save(OUT_XLSX)


def write_html(records: list[dict], pagina_em: datetime, snapshot_em: str) -> None:
    pagina_txt = fmt_ts(pagina_em)
    meta = {"pagina_em": pagina_txt, "snapshot_em": snapshot_em, "fontes": ["Mongo ares-prod", "dtbPortal", "portal VTC"], "linhas": len(records)}
    html = (
        TEMPLATE.read_text(encoding="utf-8")
        .replace("__PAGINA_EM__", pagina_txt)
        .replace("__SNAPSHOT_EM__", snapshot_em)
        .replace("__DADOS_JSON__", json.dumps(records, ensure_ascii=False, default=str))
        .replace("__META_JSON__", json.dumps(meta, ensure_ascii=False))
    )
    OUT_HTML.write_text(html, encoding="utf-8")


def main() -> None:
    SNAPSHOT_DIR.mkdir(exist_ok=True)
    pagina_em = now_brt()
    records, stats = build_records(pagina_em)
    for rec in records:
        for key, value in list(rec.items()):
            if value is None or (isinstance(value, float) and pd.isna(value)) or str(value).lower() == "nan":
                rec[key] = ""
        rec["Dias sem sync"] = int(rec.get("Dias sem sync") or 0)
        rec["UF"] = rec.get("UF") or "SEM UF"

    erros = validar(records)
    resumo = build_resumo(records)
    snapshot_em = pagina_em.strftime("%Y-%m-%dT%H:%M:%S")
    detalhe = pd.DataFrame(records)
    for col in EXPORT_COLS:
        if col not in detalhe.columns:
            detalhe[col] = ""
    detalhe[EXPORT_COLS].to_csv(OUT_CSV, index=False, encoding="utf-8-sig", sep=";")
    write_html(records, pagina_em, fmt_ts(pagina_em))
    try:
        write_excel(records, resumo, pagina_em, fmt_ts(pagina_em))
    except OSError as exc:
        print(f"AVISO: XLSX nao gravado ({exc})")

    manifesto = {
        "gerado_em": snapshot_em,
        "snapshot_em": snapshot_em,
        "status": "VALIDADO" if not erros else "VALIDADO_COM_ERROS",
        "linhas": len(records),
        "ufs": len({r.get("UF") for r in records if r.get("UF") and r.get("UF") != "SEM UF"}),
        "regras": {
            "sync": "lastSyncDate BRT >= entrega VTC (fallback reversa) - 15 min",
            "gru": "EM ESTOQUE+SALDO+RECEBIDO ou CÂMARA FRIA+RECEBIDO",
            "recebido_cliente": "dt_chegadacliente",
            "dedup": "1 logger = 1 lastSyncDate; viagem = entrega VTC mais recente",
        },
        "contagens": {
            "localizacao": dict(Counter(r.get("Localização") for r in records)),
            "situacao": dict(Counter(r.get("Situação atual") for r in records)),
            "viagem_vtc": dict(Counter(r.get("Status viagem VTC") for r in records)),
            "em_gru": sum(1 for r in records if r.get("Em GRU?") == "SIM"),
            "loggers": len(records),
            "pipeline": stats,
        },
        "erros_validacao": erros,
        "fontes": ["reversa VTC", "vtc_stage.documentos", "mongo lastSyncDate", "dtbPortal"],
        "arquivos": [OUT_HTML.name, OUT_CSV.name, OUT_XLSX.name],
    }
    SNAPSHOT_JSON.write_text(json.dumps({"manifesto": manifesto, "dados": records, "resumo": resumo}, ensure_ascii=False), encoding="utf-8")
    OUT_MANIFEST.write_text(json.dumps(manifesto, ensure_ascii=False, indent=2), encoding="utf-8")
    print("posicao", manifesto["contagens"]["localizacao"])
    print("situacao", manifesto["contagens"]["situacao"])
    print("viagem VTC", manifesto["contagens"]["viagem_vtc"])
    print("em GRU", manifesto["contagens"]["em_gru"])
    print(f"STATUS {manifesto['status']}")
    print(f"Pendencias: {len(records)} | UFs: {manifesto['ufs']}")
    print(f"HTML: {OUT_HTML}")


if __name__ == "__main__":
    main()
