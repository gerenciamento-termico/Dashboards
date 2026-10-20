# -*- coding: utf-8 -*-
"""Entregas do dia versus pendencia de sincronismo do dia — analitico.

Fontes (mesma cadeia do PENDENCIAS_SINCRONISMO, sem snapshot da reversa):
  - vtc_stage.documentos      -> pedidos/loggers entregues (dt_entregaefetivaembarque)
  - Mongo ares-prod           -> lastSyncDate por logger ARES
  - dtbTransporte tbdMovimento (nr_Referencia + ds_Agente) -> Agente/base
    mesma view da aba Base ESL do Vinculo.xlsx, consulta direta neste indicador
Regra de sincronismo:
    lastSyncDate BRT >= entrega efetiva embarque - 15 min (ou >= chegada cliente - 15 min
    so se dt_chegadacliente < dt_entregaefetivaembarque; lancamento atrasado).
Cobranca ao agente so entra com dt_entregaefetivaembarque E nr_romaneio.
Sem romaneio e trava: o pedido nao saiu. Romaneio sozinho nao prova entrega.
Se o logger ja esta Em GRU agora ou teve estoque/restauracao DEPOIS desta entrega, a linha
permanece visivel e a cobranca vai para GRU (nao some, nao usa retornando nem restauracao no mesmo dia antes).
Janela: ultimos 20 dias (serie diaria + rankings por agente e UF, filtros).
Latencia: T0 = dt_chegadacliente so se for anterior ao embarque; senao dt_entregaefetivaembarque.
1o createdAt em beacon_devices_logs >= T0 (sem tolerancia de 15 min).
media/mediana/P90 so de quem sincronizou no agente (1o sync antes de estoque/restauracao pos-embarque).
"""
from __future__ import annotations

import csv
import json
import os
import re
import secrets
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pandas as pd
from sqlalchemy import bindparam, create_engine, text
from sqlalchemy.engine import URL

from env_utils import load_env_file
from gerar_html_pendencias_sincronismo import (
    STAGE_DIR,
    classify_posicao,
    classify_sync,
    fetch_dtbportal,
    fmt_ts,
    is_ares,
    is_ares_numeric,
    load_env,
    norm_tag,
    now_brt,
    to_brt,
)

COBRANCA_GRU = "GRU"
COBRANCA_AGENTE = "AGENTE"

WORKSPACE = Path(__file__).resolve().parent
REVERSA_HTML = WORKSPACE / "REVERSA_DATALOGGERS.html"
OUT_HTML = WORKSPACE / "ENTREGAS_VS_SINCRONISMO.html"
OUT_CSV = WORKSPACE / "ENTREGAS_VS_SINCRONISMO.csv"
OUT_MANIFEST = WORKSPACE / "MANIFESTO_ENTREGAS_VS_SINCRONISMO.json"
OUT_OCULTOS_JSON = WORKSPACE / "ocultos_operacao_entregas_vs.json"
WINDOW_DAYS = 20

# paginas isoladas por base (menor privilegio): so as linhas da propria base
BASES_DIR = WORKSPACE / "bases_sincronismo"
BASES_TOKENS_LOCAL = WORKSPACE / "bases_tokens_local.json"   # nao publicado
BASES_LINKS_CSV = WORKSPACE / "LINKS_BASES_SINCRONISMO.csv"  # nao publicado
BASES_URL = "https://gerenciamento-termico.github.io/Dashboards/bases_sincronismo/"


def infer_tipo(tag_value) -> str:
    tag = "" if tag_value is None else str(tag_value).strip().upper()
    if not tag:
        return "SEM TAG"
    if is_ares_numeric(tag):
        return "ARES"
    if tag.startswith("AS"):
        return "ARES COM SONDA"
    if tag.startswith("TA") or tag.startswith("A"):
        return "ARES"
    if tag.startswith("S"):
        return "SYOS"
    if tag.startswith(("V", "B")):
        return "SHIELD"
    if re.match(r"^\d", tag):
        return "SENSOR WEB"
    return "TIPO_NAO_CLASSIFICADO"


def load_agentes() -> dict[str, str]:
    """Mapa pedido -> agente, extraido do HTML publicado da reversa (enriquecimento)."""
    if not REVERSA_HTML.exists():
        print("AVISO: REVERSA_DATALOGGERS.html ausente; agentes ficarao SEM AGENTE")
        return {}
    try:
        html = REVERSA_HTML.read_text(encoding="utf-8", errors="replace")
        rows = json.loads(re.search(r"const ALL_ROWS=(\[.*?\]);", html).group(1))
        headers = json.loads(re.search(r"const TABLE_HEADERS=(\[.*?\]);", html).group(1))
        idx = {str(name): i for i, name in enumerate(headers)}
        i_ped, i_ag = idx.get("Pedido"), idx.get("Agente")
        if i_ped is None or i_ag is None:
            print("AVISO: colunas Pedido/Agente nao encontradas na reversa; SEM AGENTE")
            return {}
        mapa: dict[str, str] = {}
        for row in rows:
            pedido = str(row[i_ped] or "").strip() if i_ped < len(row) else ""
            agente = str(row[i_ag] or "").strip().upper() if i_ag < len(row) else ""
            if pedido and agente and pedido not in mapa:
                mapa[pedido] = agente
        return mapa
    except Exception as exc:  # enriquecimento nao derruba o indicador
        print(f"AVISO: falha ao ler agentes da reversa ({exc}); SEM AGENTE")
        return {}


def _norm_pedido(value: object) -> str:
    raw = str(value or "").strip()
    if raw.lower() in {"nan", "none", "nat"}:
        return ""
    if raw.endswith(".0") and raw[:-2].isdigit():
        return raw[:-2]
    return raw


def _sqlserver_cfg() -> dict[str, object]:
    load_env_file(WORKSPACE / ".env")
    env = load_env(WORKSPACE / ".env")
    return {
        "host": env.get("AURA_SQLSERVER_HOST") or os.getenv("AURA_SQLSERVER_HOST", ""),
        "port": int(env.get("AURA_SQLSERVER_PORT") or os.getenv("AURA_SQLSERVER_PORT") or 1466),
        "database": env.get("AURA_SQLSERVER_DATABASE") or os.getenv("AURA_SQLSERVER_DATABASE") or "dtbTransporte",
        "user": env.get("AURA_SQLSERVER_USER") or os.getenv("AURA_SQLSERVER_USER", ""),
        "password": (env.get("AURA_SQLSERVER_PASSWORD") or os.getenv("AURA_SQLSERVER_PASSWORD") or "").strip().strip('"'),
    }


def fetch_agentes_dtbtransporte(pedidos: list[str]) -> tuple[dict[str, str], dict[str, int]]:
    """Mesma view da Base ESL: tbdMovimento.nr_Referencia -> ds_Agente (cadastro 2025+)."""
    stats = {"consultados": 0, "mapeados": 0, "linhas": 0}
    pedidos = sorted({_norm_pedido(p) for p in pedidos if _norm_pedido(p)})
    stats["consultados"] = len(pedidos)
    if not pedidos:
        return {}, stats
    cfg = _sqlserver_cfg()
    if not cfg["host"] or not cfg["user"]:
        raise RuntimeError("dtbTransporte ausente (.env AURA_SQLSERVER_*)")
    import pytds

    mapa: dict[str, tuple[str, object]] = {}
    query = """
        SELECT
            LTRIM(RTRIM(CAST(nr_Referencia AS varchar(40)))) AS pedido_ref,
            LTRIM(RTRIM(CAST(nr_PedidoCliente AS varchar(40)))) AS pedido_cli,
            LTRIM(RTRIM(ds_Agente)) AS agente,
            dt_Cadastro
        FROM tbdMovimento
        WHERE dt_Cadastro >= '20250101'
          AND NULLIF(LTRIM(RTRIM(ds_Agente)), '') IS NOT NULL
          AND (
                LTRIM(RTRIM(CAST(nr_Referencia AS varchar(40)))) IN ({ph})
             OR LTRIM(RTRIM(CAST(nr_PedidoCliente AS varchar(40)))) IN ({ph})
          )
    """
    with pytds.connect(
        server=cfg["host"],
        port=int(cfg["port"]),
        database=str(cfg["database"]),
        user=str(cfg["user"]),
        password=str(cfg["password"]),
        timeout=60,
        login_timeout=30,
        validate_host=False,
    ) as conn:
        cur = conn.cursor()
        for i in range(0, len(pedidos), 800):
            chunk = pedidos[i : i + 800]
            ph = ",".join(["%s"] * len(chunk))
            cur.execute(query.format(ph=ph), tuple(chunk) + tuple(chunk))
            cols = [c[0] for c in cur.description]
            for raw in cur.fetchall():
                row = dict(zip(cols, raw))
                agente = str(row.get("agente") or "").strip()
                cad = row.get("dt_Cadastro")
                if not agente:
                    continue
                stats["linhas"] += 1
                for key in (_norm_pedido(row.get("pedido_ref")), _norm_pedido(row.get("pedido_cli"))):
                    if not key:
                        continue
                    prev = mapa.get(key)
                    if prev is None or (cad is not None and (prev[1] is None or cad > prev[1])):
                        mapa[key] = (agente, cad)
    out = {k: v[0] for k, v in mapa.items()}
    stats["mapeados"] = sum(1 for p in pedidos if p in out)
    print(
        f"dtbTransporte tbdMovimento: {stats['mapeados']}/{stats['consultados']} pedidos "
        f"com ds_Agente ({stats['linhas']} linhas)"
    )
    return out, stats


def resolver_agente(pedido: str, transporte: dict[str, str], reversa: dict[str, str]) -> str:
    return transporte.get(pedido) or reversa.get(pedido) or "SEM AGENTE"


def _mongo_lookup_names(loggers: list[str]) -> list[str]:
    names = sorted({norm_tag(item) for item in loggers if norm_tag(item)})
    lookup: set[str] = set()
    for name in names:
        lookup.add(name)
        lookup.add(name + " ")
        match = re.fullmatch(r"(AS)(\d+)", name, flags=re.I)
        if match:
            hyphen = match.group(1).upper() + "-" + match.group(2)
            lookup.add(hyphen)
            lookup.add(hyphen + " ")
    return sorted(lookup)


def _mongo_ts(value) -> pd.Timestamp:
    ts = pd.to_datetime(value, errors="coerce", utc=True)
    if pd.isna(ts):
        return pd.NaT
    return ts.tz_convert("America/Sao_Paulo").tz_localize(None)


def _t0_to_utc(ts: pd.Timestamp):
    t = pd.Timestamp(ts)
    if t.tzinfo is None:
        t = t.tz_localize("America/Sao_Paulo")
    return t.tz_convert("UTC").to_pydatetime()


def chegada_se_antes(chegada, embarque):
    """Chegada so vale se for anterior ao embarque (lancamento atrasado no sistema)."""
    if pd.notna(chegada) and pd.notna(embarque) and chegada < embarque:
        return chegada
    return pd.NaT


def load_ocultos_operacao() -> tuple[set[tuple[str, str, str]], str]:
    """Lista pontual pedido+logger+dia. Nao e regra de cobranca."""
    if not OUT_OCULTOS_JSON.exists():
        return set(), ""
    raw = json.loads(OUT_OCULTOS_JSON.read_text(encoding="utf-8"))
    motivo = str(raw.get("motivo_padrao") or "ajuste operacional")
    chaves: set[tuple[str, str, str]] = set()
    for item in raw.get("itens") or []:
        ped = _norm_pedido(item.get("pedido"))
        log = norm_tag(item.get("logger"))
        dia = str(item.get("dia") or "").strip()
        if ped and log and dia:
            chaves.add((ped, log, dia))
    return chaves, motivo


def aplicar_ocultos_operacao(ares_rows: list[dict]) -> tuple[list[dict], list[dict], dict]:
    chaves, motivo = load_ocultos_operacao()
    if not chaves:
        return ares_rows, [], {"lista": 0, "encontrados": 0, "ausentes": 0}
    visiveis: list[dict] = []
    ocultos: list[dict] = []
    seen: set[tuple[str, str, str]] = set()
    for rec in ares_rows:
        key = (_norm_pedido(rec.get("p")), norm_tag(rec.get("l")), str(rec.get("d") or ""))
        if key in chaves:
            hid = dict(rec)
            hid["ox"] = motivo
            ocultos.append(hid)
            seen.add(key)
        else:
            visiveis.append(rec)
    return visiveis, ocultos, {
        "lista": len(chaves),
        "encontrados": len(ocultos),
        "ausentes": len(chaves - seen),
    }


def _quant(vals: list[float], q: float) -> float | None:
    if not vals:
        return None
    ordered = sorted(vals)
    if len(ordered) == 1:
        return round(float(ordered[0]), 2)
    idx = (len(ordered) - 1) * q
    lo = int(idx)
    hi = min(lo + 1, len(ordered) - 1)
    frac = idx - lo
    return round(ordered[lo] * (1 - frac) + ordered[hi] * frac, 2)


def fetch_mongo_devices(loggers: list[str]) -> dict[str, dict]:
    """name -> {sync, mac} em beacon_devices (mesmo lookup do lastSyncDate)."""
    for path in [STAGE_DIR / ".env.ares_mongo", STAGE_DIR / ".env", WORKSPACE / ".env"]:
        for key, value in load_env(path).items():
            os.environ.setdefault(key, value)
    uri = (os.getenv("ARES_MONGO_URI") or os.getenv("ARES_MONGODB_URI") or "").strip()
    if not uri:
        raise RuntimeError("ARES_MONGO_URI ausente")
    from pymongo import MongoClient

    lookup = _mongo_lookup_names(loggers)
    client = MongoClient(uri, serverSelectionTimeoutMS=25000, connectTimeoutMS=25000)
    try:
        client.admin.command("ping")
        col = client[os.getenv("ARES_MONGO_DB") or "ares-prod"]["beacon_devices"]
        mapping: dict[str, dict] = {}
        for index in range(0, len(lookup), 2000):
            for doc in col.find(
                {"name": {"$in": lookup[index : index + 2000]}},
                {"name": 1, "lastSyncDate": 1, "mac": 1},
            ):
                key = norm_tag(doc.get("name"))
                if not key:
                    continue
                ts = _mongo_ts(doc.get("lastSyncDate"))
                mac = str(doc.get("mac") or "").strip()
                prev = mapping.get(key)
                if prev is None or (pd.notna(ts) and (pd.isna(prev["sync"]) or ts > prev["sync"])):
                    mapping[key] = {"sync": ts, "mac": mac or (prev or {}).get("mac", "")}
                elif mac and not prev.get("mac"):
                    prev["mac"] = mac
        return mapping
    finally:
        client.close()


def fetch_primeiro_sync_apos(jobs: list[dict]) -> dict[tuple[str, str], pd.Timestamp]:
    """Primeiro createdAt do log >= T0-15min, por pedido+logger."""
    if not jobs:
        return {}
    for path in [STAGE_DIR / ".env.ares_mongo", STAGE_DIR / ".env", WORKSPACE / ".env"]:
        for key, value in load_env(path).items():
            os.environ.setdefault(key, value)
    uri = (os.getenv("ARES_MONGO_URI") or os.getenv("ARES_MONGODB_URI") or "").strip()
    if not uri:
        raise RuntimeError("ARES_MONGO_URI ausente")
    from pymongo import ASCENDING, MongoClient

    pend: list[dict] = []
    for job in jobs:
        t0 = job["t0"]
        if pd.isna(t0) or not job.get("mac"):
            continue
        last = job.get("last")
        if pd.notna(last) and last < t0:
            continue
        pend.append({**job, "floor": t0})
    print(f"[latencia] logs a consultar: {len(pend)} (de {len(jobs)} saiu com mac; lastSync ja anterior = pulado)")
    if not pend:
        return {}

    client = MongoClient(uri, serverSelectionTimeoutMS=25000, connectTimeoutMS=25000)
    col = client[os.getenv("ARES_MONGO_DB") or "ares-prod"]["beacon_devices_logs"]
    out: dict[tuple[str, str], pd.Timestamp] = {}

    def _one(job: dict) -> tuple[tuple[str, str], pd.Timestamp]:
        key = (job["pedido"], job["logger"])
        try:
            doc = col.find_one(
                {"mac": job["mac"], "createdAt": {"$gte": _t0_to_utc(job["floor"])}},
                {"createdAt": 1},
                sort=[("createdAt", ASCENDING)],
                max_time_ms=8000,
            )
        except Exception:
            doc = None
        ts = _mongo_ts((doc or {}).get("createdAt"))
        return key, ts

    done = 0
    try:
        client.admin.command("ping")
        workers = 12
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(_one, job) for job in pend]
            for fut in as_completed(futures):
                key, ts = fut.result()
                if pd.notna(ts):
                    out[key] = ts
                done += 1
                if done % 800 == 0 or done == len(pend):
                    print(f"[latencia] logs {done}/{len(pend)}")
    finally:
        client.close()
    print(f"[latencia] primeiro sync encontrado: {len(out)}")
    return out


def fetch_entregas(cutoff_brt: pd.Timestamp) -> pd.DataFrame:
    env = load_env(WORKSPACE / ".env.vtc_stage")
    if not env.get("VTC_STAGE_HOST"):
        raise RuntimeError("VTC_STAGE ausente (.env.vtc_stage)")
    tag_sql = (
        "COALESCE(REPLACE(REPLACE(UPPER(TRIM(COALESCE(NULLIF(TRIM(ds_tag), ''), "
        "NULLIF(TRIM(cd_referencia), '')))), '-', ''), ' ', ''), '')"
    )
    query = text(
        f"""
        SELECT TRIM(nr_pedido::text) AS pedido,
               {tag_sql} AS logger,
               (ARRAY_AGG(NULLIF(TRIM(nr_romaneio::text), '')
                   ORDER BY dt_entregaefetivaembarque DESC NULLS LAST, dt_entregaefetiva DESC))[1] AS romaneio,
               MAX(NULLIF(TRIM(cd_uf), '')) AS uf,
               MAX(NULLIF(TRIM(ds_cliente), '')) AS cliente,
               MAX(NULLIF(TRIM(modal), '')) AS modal,
               MAX(dt_entregaefetiva) AS entrega_doc,
               MAX(dt_entregaefetivaembarque) AS embarque,
               MIN(dt_chegadacliente) AS chegada
        FROM vtc_stage.documentos
        WHERE NULLIF(TRIM(nr_pedido::text), '') IS NOT NULL
          AND (
                dt_entregaefetivaembarque >= :cutoff
             OR (dt_entregaefetivaembarque IS NULL AND dt_entregaefetiva >= :cutoff)
          )
        GROUP BY TRIM(nr_pedido::text), {tag_sql}
        """
    )
    url = URL.create(
        "postgresql+psycopg2",
        username=env["VTC_STAGE_USER"],
        password=env["VTC_STAGE_PASSWORD"],
        host=env["VTC_STAGE_HOST"],
        port=int(env.get("VTC_STAGE_PORT") or 5432),
        database=env["VTC_STAGE_NAME"],
    )
    engine = create_engine(url, pool_pre_ping=True)
    cutoff = cutoff_brt.tz_localize("America/Sao_Paulo").to_pydatetime()
    with engine.connect() as connection:
        frame = pd.read_sql(query, connection, params={"cutoff": cutoff})
    return frame


def fetch_entregas_dia() -> list[dict]:
    """Pedidos previstos para hoje e/ou entregues hoje (independente de sincronismo)."""
    env = load_env(WORKSPACE / ".env.vtc_stage")
    if not env.get("VTC_STAGE_HOST"):
        raise RuntimeError("VTC_STAGE ausente (.env.vtc_stage)")
    query = text(
        """
        SELECT TRIM(nr_pedido::text) AS pedido,
               MAX(NULLIF(TRIM(cd_uf), '')) AS uf,
               MAX(NULLIF(TRIM(ds_cliente), '')) AS cliente,
               MAX(NULLIF(TRIM(modal), '')) AS modal,
               MIN(dt_previstaentrega) AS prevista,
               MAX(COALESCE(dt_entregaefetivaembarque, dt_entregaefetiva)) AS entrega,
               COUNT(DISTINCT NULLIF(TRIM(ds_tag), '')) AS loggers,
               BOOL_OR(dt_previstaentrega::date = CURRENT_DATE) AS prev_hoje,
               BOOL_OR(COALESCE(dt_entregaefetivaembarque, dt_entregaefetiva)::date = CURRENT_DATE) AS ent_hoje
        FROM vtc_stage.documentos
        WHERE NULLIF(TRIM(nr_pedido::text), '') IS NOT NULL
          AND (dt_previstaentrega::date = CURRENT_DATE
               OR COALESCE(dt_entregaefetivaembarque, dt_entregaefetiva)::date = CURRENT_DATE)
        GROUP BY 1
        """
    )
    url = URL.create(
        "postgresql+psycopg2",
        username=env["VTC_STAGE_USER"],
        password=env["VTC_STAGE_PASSWORD"],
        host=env["VTC_STAGE_HOST"],
        port=int(env.get("VTC_STAGE_PORT") or 5432),
        database=env["VTC_STAGE_NAME"],
    )
    engine = create_engine(url, pool_pre_ping=True)
    rows: list[dict] = []
    with engine.connect() as connection:
        for rec in connection.execute(query):
            if rec.ent_hoje:
                st = "E"  # entregue hoje
            elif rec.prev_hoje and rec.entrega is None:
                st = "F"  # previsto para hoje, falta entregar
            else:
                st = "O"  # previsto para hoje, mas entregue em outra data
            rows.append(
                {
                    "p": rec.pedido,
                    "uf": rec.uf or "",
                    "cli": rec.cliente or "",
                    "mo": rec.modal or "",
                    "prev": f"{pd.Timestamp(rec.prevista):%H:%M}" if rec.prevista is not None else "",
                    "ent": f"{pd.Timestamp(rec.entrega):%d/%m %H:%M}" if rec.entrega is not None else "",
                    "lg": int(rec.loggers or 0),
                    "ph": 1 if rec.prev_hoje else 0,
                    "st": st,
                }
            )
    engine.dispose()
    return rows


def fetch_tipos_cadastro(tags: list[str]) -> dict[str, str]:
    """Tipo oficial do datalogger no cadastro do dtbPortal (ds_tipodatalogger).

    Fonte de verdade para classificar ARES independente do padrao da tag;
    o padrao de tag (infer_tipo) vira fallback para tags fora do cadastro.
    """
    tags = sorted({norm_tag(item) for item in tags if norm_tag(item)})
    if not tags:
        return {}
    load_env_file(WORKSPACE / ".env")
    env = load_env(WORKSPACE / ".env")
    if not env.get("AURA_POSTGRES_HOST"):
        raise RuntimeError("dtbPortal ausente")
    url = URL.create(
        "postgresql+psycopg2",
        username=env["AURA_POSTGRES_USER"],
        password=env["AURA_POSTGRES_PASSWORD"],
        host=env["AURA_POSTGRES_HOST"],
        port=int(env.get("AURA_POSTGRES_PORT") or 5432),
        database=env["AURA_POSTGRES_NAME"],
    )
    engine = create_engine(url, pool_pre_ping=True)
    out: dict[str, str] = {}
    query = text(
        """
        SELECT REPLACE(REPLACE(UPPER(TRIM(ds_tag)), '-', ''), ' ', '') AS logger,
               MAX(UPPER(TRIM(COALESCE(ds_tipodatalogger, '')))) AS tipo
        FROM public.vwtabelamovdataloggers
        WHERE REPLACE(REPLACE(UPPER(TRIM(ds_tag)), '-', ''), ' ', '') IN :tags
        GROUP BY 1
        """
    ).bindparams(bindparam("tags", expanding=True))
    with engine.connect() as connection:
        for i in range(0, len(tags), 800):
            chunk = tags[i : i + 800]
            frame = pd.read_sql(query, connection, params={"tags": chunk})
            for rec in frame.itertuples(index=False):
                tipo = str(rec.tipo or "").strip().upper()
                key = norm_tag(rec.logger)
                if tipo and key:
                    out[key] = tipo
    engine.dispose()
    return out


def fetch_ultimo_estoque(tags: list[str], desde: pd.Timestamp) -> dict[str, pd.Timestamp]:
    """Ultimo recebimento/restauracao em estoque saldo por tag (regra Reversa, sem RETORNANDO)."""
    tags = sorted({norm_tag(item) for item in tags if norm_tag(item)})
    if not tags:
        return {}
    load_env_file(WORKSPACE / ".env")
    env = load_env(WORKSPACE / ".env")
    if not env.get("AURA_POSTGRES_HOST"):
        raise RuntimeError("dtbPortal ausente")
    url = URL.create(
        "postgresql+psycopg2",
        username=env["AURA_POSTGRES_USER"],
        password=env["AURA_POSTGRES_PASSWORD"],
        host=env["AURA_POSTGRES_HOST"],
        port=int(env.get("AURA_POSTGRES_PORT") or 5432),
        database=env["AURA_POSTGRES_NAME"],
    )
    engine = create_engine(url, pool_pre_ping=True)
    desde_naive = pd.Timestamp(desde).tz_localize(None) if getattr(desde, "tzinfo", None) else pd.Timestamp(desde)
    out: dict[str, pd.Timestamp] = {}
    query = text(
        """
        SELECT REPLACE(REPLACE(UPPER(TRIM(d.ds_tag)), '-', ''), ' ', '') AS logger,
               MAX(dh.dt_inclusao) AS dt_historico
        FROM public.tbddataloggerhistoricos dh
        INNER JOIN vwtipos tam ON ds_tipo='tipoacaomovimentacao' AND dh.tp_acaomovimentacao=tam.id
        INNER JOIN public.tbdcaddataloggerdestinos dd ON dh.id_destino=dd.id_destino
        INNER JOIN public.tbdcaddataloggerfinalidades df ON dh.id_finalidade=df.id_finalidade
        INNER JOIN tbdcaddataloggers d ON dh.id_datalogger=d.id_datalogger
        WHERE REPLACE(REPLACE(UPPER(TRIM(d.ds_tag)), '-', ''), ' ', '') IN :tags
          AND dh.dt_inclusao >= :desde
          AND (
                UPPER(TRIM(tam.text)) LIKE '%RESTAUR%'
             OR UPPER(TRIM(tam.text)) LIKE '%RECEB%'
          )
          AND UPPER(TRIM(dd.ds_destino)) = 'EM ESTOQUE'
          AND UPPER(TRIM(df.ds_finalidade)) LIKE '%SALDO%'
          AND UPPER(TRIM(COALESCE(tam.text, ''))) NOT LIKE '%RETORNANDO%'
        GROUP BY 1
        """
    ).bindparams(bindparam("tags", expanding=True))
    with engine.connect() as connection:
        for i in range(0, len(tags), 800):
            chunk = tags[i : i + 800]
            frame = pd.read_sql(query, connection, params={"tags": chunk, "desde": desde_naive.to_pydatetime()})
            for rec in frame.itertuples(index=False):
                key = norm_tag(rec.logger)
                ts = to_brt(rec.dt_historico)
                if key and pd.notna(ts):
                    prev = out.get(key)
                    if prev is None or ts > prev:
                        out[key] = ts
    return out


def dedupe_logger_dia(rows: list[dict]) -> tuple[list[dict], int]:
    """1 logger por dia. Varios pedidos/produtos na mesma caixa nao repetem o aparelho."""
    if not rows:
        return rows, 0
    rank = {"P": 0, "G": 1, "E": 2, "S": 3, "N": 4}
    frame = pd.DataFrame(rows)
    before = len(frame)
    frame["_rk"] = frame["st"].map(rank).fillna(9)
    frame["_emb"] = frame["emb"].fillna("")
    vazios = frame[frame["l"].eq("")].drop(columns=["_rk", "_emb"])
    cheios = frame[frame["l"].ne("")].sort_values(
        ["d", "l", "_rk", "_emb", "p"], ascending=[True, True, True, False, True]
    )
    cheios = cheios.drop_duplicates(["d", "l"], keep="first").drop(columns=["_rk", "_emb"])
    out = pd.concat([cheios, vazios], ignore_index=True)
    return out.to_dict("records"), before - len(out)


def classificar_cobranca_gru(entrega, portal_row, ultimo_estoque) -> tuple[bool, str]:
    dest = getattr(portal_row, "ds_destino", "") if portal_row is not None else ""
    fin = getattr(portal_row, "ds_finalidade", "") if portal_row is not None else ""
    status_rec = getattr(portal_row, "ds_statusrecebimento", "") if portal_row is not None else ""
    responsavel = getattr(portal_row, "ds_responsavel", "") if portal_row is not None else ""
    pos = classify_posicao(dest, fin, status_rec, responsavel)
    em_gru = pos.get("Em GRU?") == "SIM"
    retornou = bool(pd.notna(ultimo_estoque) and pd.notna(entrega) and ultimo_estoque > entrega)
    if em_gru and retornou:
        return True, "já retornou"
    if retornou:
        return True, "já retornou"
    if em_gru:
        return True, "em GRU"
    return False, ""


def build() -> dict:
    pagina_em = now_brt()
    hoje = pd.Timestamp(pagina_em).normalize()
    cutoff = hoje - pd.Timedelta(days=WINDOW_DAYS - 1)

    frame = fetch_entregas(cutoff)
    print(f"VTC_STAGE: {len(frame)} pares pedido+logger desde {cutoff:%d/%m/%Y}")

    try:
        dia_hoje_rows = fetch_entregas_dia()
        n_ent = sum(1 for r in dia_hoje_rows if r["st"] == "E")
        n_falta = sum(1 for r in dia_hoje_rows if r["st"] == "F")
        print(f"Entregas do dia: {n_ent} entregues hoje, {n_falta} previstas ainda sem entrega")
    except Exception as exc:
        print(f"AVISO: entregas do dia indisponivel ({exc})")
        dia_hoje_rows = []

    frame["pedido"] = frame["pedido"].astype(str).str.strip()
    frame["logger"] = frame["logger"].map(norm_tag)
    frame["entrega_doc"] = frame["entrega_doc"].map(to_brt)
    frame["embarque"] = frame["embarque"].map(to_brt)
    frame["chegada"] = frame["chegada"].map(to_brt)
    limite = hoje + pd.Timedelta(days=1)
    com_emb = frame["embarque"].notna() & (frame["embarque"] >= cutoff) & (frame["embarque"] < limite)
    patio = frame["embarque"].isna() & frame["entrega_doc"].notna() & (frame["entrega_doc"] >= cutoff) & (frame["entrega_doc"] < limite)
    frame = frame[com_emb | patio].copy()
    frame["entrega"] = frame["embarque"]
    frame["dia"] = frame["embarque"].fillna(frame["entrega_doc"]).dt.normalize()
    try:
        tipos_cad = fetch_tipos_cadastro(frame["logger"].tolist())
    except Exception as exc:
        print(f"AVISO: cadastro de tipos indisponivel ({exc}); usando padrao de tag")
        tipos_cad = {}
    frame["tipo"] = [tipos_cad.get(lg) or infer_tipo(lg) for lg in frame["logger"]]
    if tipos_cad:
        unicos = set(frame["logger"]) - {""}
        n_cad = sum(1 for lg in unicos if lg in tipos_cad)
        diverg = sorted(lg for lg in unicos if lg in tipos_cad and tipos_cad[lg] != infer_tipo(lg))
        print(
            f"Tipo via cadastro dtbPortal: {n_cad}/{len(unicos)} tags "
            f"(fallback padrao de tag nas demais); divergencias cadastro x padrao: {len(diverg)}"
        )
        if diverg[:10]:
            print(f"  divergentes (ate 10): {[(lg, tipos_cad[lg], infer_tipo(lg)) for lg in diverg[:10]]}")
    frame["uf"] = frame["uf"].map(lambda v: str(v or "").strip().upper() or "SEM UF")
    frame["romaneio"] = frame["romaneio"].map(lambda v: str(v or "").strip() if pd.notna(v) else "")

    dup = frame.duplicated(subset=["pedido", "logger"]).sum()
    if dup:
        raise RuntimeError(f"BLOQUEADO_DUP_PEDIDO_LOGGER ({dup})")

    agentes_rev = load_agentes()
    print(f"Agentes via reversa publicada: {len(agentes_rev)} pedidos mapeados")
    agentes_trp, trp_stats = fetch_agentes_dtbtransporte(frame["pedido"].tolist())
    frame["agente"] = [resolver_agente(p, agentes_trp, agentes_rev) for p in frame["pedido"]]
    fonte_trp = int(frame["pedido"].isin(agentes_trp).sum())
    fonte_rev = int((~frame["pedido"].isin(agentes_trp) & frame["pedido"].isin(agentes_rev)).sum())
    sem = int(frame["agente"].eq("SEM AGENTE").sum())
    print(f"Agente preenchido: dtbTransporte={fonte_trp} reversa={fonte_rev} SEM AGENTE={sem} (pares)")

    ares = frame[frame["logger"].ne("") & frame.apply(lambda r: is_ares(r["logger"], r["tipo"]), axis=1)].copy()
    n_num = int(ares["logger"].map(is_ares_numeric).sum())
    print(f"Loggers ARES entregues na janela: {len(ares)} (de {len(frame)} pares; 07417 numerico={n_num})")

    devices = fetch_mongo_devices(sorted(set(ares["logger"]))) if len(ares) else {}
    print(f"Mongo lastSyncDate: {sum(1 for d in devices.values() if pd.notna(d.get('sync')))} loggers | mac={sum(1 for d in devices.values() if d.get('mac'))}")
    ares["sync"] = ares["logger"].map(lambda tag: devices.get(tag, {}).get("sync", pd.NaT))
    ares["mac"] = ares["logger"].map(lambda tag: devices.get(tag, {}).get("mac", ""))
    ares["tem_romaneio"] = ares["romaneio"].fillna("").astype(str).str.strip().ne("")
    ares["saiu"] = ares["embarque"].notna() & ares["tem_romaneio"]
    n_trava = int((ares["embarque"].notna() & ~ares["tem_romaneio"]).sum())
    print(f"Trava romaneio: {n_trava} com embarque sem romaneio (nao saiu)")
    ares["chegada_t0"] = [
        chegada_se_antes(c, e) for c, e in zip(ares["chegada"], ares["embarque"])
    ]
    ares["status"] = ares.apply(
        lambda r: classify_sync(r["embarque"], r["sync"], r["chegada_t0"], r["tipo"]), axis=1
    )

    cand = ares[ares["status"].eq("PENDENTE") & ares["saiu"]].copy()
    portal = fetch_dtbportal(cand["logger"].tolist()) if len(cand) else {}
    saiu_tags = ares.loc[ares["saiu"], "logger"].tolist()
    estoque = fetch_ultimo_estoque(saiu_tags, cutoff - pd.Timedelta(days=30)) if saiu_tags else {}
    print(f"dtbPortal posicao: {len(portal)} tags | estoque/restauracao: {len(estoque)} tags")

    ares["t0"] = ares["chegada_t0"].where(ares["chegada_t0"].notna(), ares["embarque"])
    lat_jobs = []
    for rec in ares.itertuples(index=False):
        if rec.saiu and pd.notna(rec.t0) and rec.mac:
            lat_jobs.append(
                {
                    "pedido": rec.pedido,
                    "logger": rec.logger,
                    "mac": rec.mac,
                    "t0": rec.t0,
                    "last": rec.sync,
                }
            )
    first_map = fetch_primeiro_sync_apos(lat_jobs)
    ares["first_sync"] = [
        first_map.get((rec.pedido, rec.logger), pd.NaT) for rec in ares.itertuples(index=False)
    ]
    n_sem_mac = int((ares["saiu"] & ares["mac"].eq("")).sum())
    print(f"[latencia] saiu sem mac: {n_sem_mac}")
    gru_flag: dict[tuple, tuple[bool, str]] = {}
    for rec in cand.itertuples(index=False):
        vai, mot = classificar_cobranca_gru(rec.embarque, portal.get(rec.logger), estoque.get(rec.logger, pd.NaT))
        gru_flag[(rec.pedido, rec.logger)] = (vai, mot)
    n_gru = sum(1 for vai, _ in gru_flag.values() if vai)
    print(f"Cobranca redirecionada para GRU (Em GRU agora ou estoque depois da entrega): {n_gru}")

    # Dataset 1: pedidos distintos por dia (entrega efetiva embarque)
    tem_ro = frame["romaneio"].fillna("").astype(str).str.strip().ne("")
    ped = (
        frame[frame["embarque"].notna() & tem_ro]
        .groupby(["pedido", "dia"], as_index=False)
        .agg(uf=("uf", "max"), agente=("agente", "max"))
    )
    pedidos_rows = [
        {
            "d": row.dia.strftime("%Y-%m-%d"),
            "p": row.pedido,
            "uf": row.uf,
            "ag": row.agente,
        }
        for row in ped.itertuples(index=False)
    ]

    # Dataset 2: loggers ARES avaliados (base dos rankings/detalhe)
    ares_sorted = ares.sort_values(["dia", "pedido", "logger"], ascending=[False, True, True])
    ares_rows = []
    for row in ares_sorted.itertuples(index=False):
        pend_sync = row.status == "PENDENTE"
        saiu = bool(row.saiu)
        vai_gru, mot = gru_flag.get((row.pedido, row.logger), (False, ""))
        if not saiu:
            st = "E"
        elif pend_sync and vai_gru:
            st = "G"
        elif pend_sync:
            st = "P"
        elif row.status == "SINCRONIZADO":
            st = "S"
        else:
            st = "N"
        first = row.first_sync
        t0 = row.t0
        hours = None
        lk = ""
        if saiu and pd.notna(t0):
            if pd.isna(first):
                lk = "P"
            else:
                est = estoque.get(row.logger, pd.NaT)
                if pd.notna(est) and pd.notna(row.embarque) and est > row.embarque and first >= est:
                    lk = "G"
                else:
                    raw_h = (first - t0).total_seconds() / 3600.0
                    hours = round(max(0.0, raw_h), 2)
                    lk = "A"
        ares_rows.append(
            {
                "d": row.dia.strftime("%Y-%m-%d"),
                "p": row.pedido,
                "ro": str(row.romaneio or "").strip(),
                "l": row.logger,
                "t": row.tipo,
                "uf": row.uf,
                "ag": row.agente,
                "cli": str(row.cliente or "").strip(),
                "mo": str(row.modal or "").strip(),
                "e": fmt_ts(row.embarque) if pd.notna(row.embarque) else "",
                "emb": fmt_ts(row.embarque) if pd.notna(row.embarque) else "",
                "ch": fmt_ts(row.chegada),
                "s": fmt_ts(row.sync) if pd.notna(row.sync) else "",
                "t0": fmt_ts(t0) if pd.notna(t0) else "",
                "fs": fmt_ts(first) if pd.notna(first) else "",
                "th": hours,
                "lk": lk,
                "saiu": "S" if saiu else "N",
                "st": st,
                "cob": COBRANCA_GRU if st == "G" else (COBRANCA_AGENTE if st == "P" else ""),
                "mot": mot,
                "ds": int((pd.Timestamp(pagina_em) - row.embarque).total_seconds() // 86400) if st in {"P", "G"} else 0,
                "_e_iso": row.embarque.isoformat() if pd.notna(row.embarque) else "",
                "_ch_iso": row.chegada.isoformat() if pd.notna(row.chegada) else "",
                "_s_iso": row.sync.isoformat() if pd.notna(row.sync) else "",
            }
        )
    ares_rows, n_dedup = dedupe_logger_dia(ares_rows)
    print(f"Chave dia+logger: {n_dedup} linha(s) repetida(s) de produto/pedido na mesma caixa removida(s)")
    ares_rows, ocultos_rows, oc_stats = aplicar_ocultos_operacao(ares_rows)
    print(
        f"Ocultos operacao: {oc_stats['encontrados']} de {oc_stats['lista']} "
        f"(ausentes nesta janela={oc_stats['ausentes']})"
    )

    dias = pd.date_range(cutoff, hoje, freq="D")
    daily = []
    by_dia = {}
    for rec in ares_rows:
        by_dia.setdefault(rec["d"], []).append(rec)
    for dia in dias:
        iso = dia.strftime("%Y-%m-%d")
        rows_dia = frame[frame["dia"].eq(dia) & frame["embarque"].notna()]
        ares_dia = by_dia.get(iso, [])
        pend_dia = [r for r in ares_dia if r["st"] == "P"]
        gru_dia = [r for r in ares_dia if r["st"] == "G"]
        patio_n = sum(1 for r in ares_dia if r["st"] == "E")
        loggers_n = sum(1 for r in ares_dia if r["st"] != "E")
        pend_n = len(pend_dia)
        lat_dia = [float(r["th"]) for r in ares_dia if r.get("lk") == "A" and r.get("th") is not None]
        daily.append(
            {
                "dia_iso": iso,
                "dia": dia.strftime("%d/%m"),
                "dia_full": dia.strftime("%d/%m/%Y"),
                "pedidos": int(rows_dia["pedido"].nunique()),
                "loggers": loggers_n,
                "sincronizados": sum(1 for r in ares_dia if r["st"] == "S"),
                "pendentes": pend_n,
                "sem_embarque": patio_n,
                "cobranca_gru": len(gru_dia),
                "pedidos_pendentes": len({r["p"] for r in pend_dia}),
                "pct": round(pend_n / loggers_n * 100, 1) if loggers_n else 0.0,
                "n_latencia": len(lat_dia),
                "media_horas": round(sum(lat_dia) / len(lat_dia), 2) if lat_dia else None,
                "mediana_horas": _quant(lat_dia, 0.5),
                "p90_horas": _quant(lat_dia, 0.9),
                "n_sync_so_gru": sum(1 for r in ares_dia if r.get("lk") == "G"),
                "n_sem_primeiro_sync": sum(1 for r in ares_dia if r.get("lk") == "P"),
            }
        )

    # Validacoes fail-closed simples (sobre timestamps com segundos)
    erros = Counter()
    pend_rows = [r for r in ares_rows if r["st"] == "P"]
    gru_rows = [r for r in ares_rows if r["st"] == "G"]
    if sum(d["pendentes"] for d in daily) != len(pend_rows):
        erros["detalhe_vs_serie"] += 1
    if sum(d["cobranca_gru"] for d in daily) != len(gru_rows):
        erros["gru_vs_serie"] += 1
    for d in daily:
        if d["pendentes"] > d["loggers"]:
            erros["pendentes_maior_que_loggers"] += 1
        if d["sincronizados"] + d["pendentes"] + d["cobranca_gru"] != d["loggers"]:
            erros["soma_status_diferente_loggers"] += 1
    for rec in pend_rows + gru_rows:
        emb_iso = to_brt(rec["_e_iso"])
        ch_iso = chegada_se_antes(to_brt(rec["_ch_iso"]), emb_iso)
        if classify_sync(emb_iso, to_brt(rec["_s_iso"]), ch_iso, rec["t"]) != "PENDENTE":
            erros["falso_pendente_vs_iso"] += 1
        if rec.get("saiu") != "S" or not rec.get("emb") or not str(rec.get("ro") or "").strip():
            erros["pendente_sem_saida"] += 1
    for rec in gru_rows:
        if rec.get("cob") != COBRANCA_GRU or not rec.get("mot"):
            erros["gru_sem_motivo"] += 1
    for rec in pend_rows:
        if rec.get("cob") != COBRANCA_AGENTE:
            erros["pendente_cobranca_errada"] += 1
    chaves = [(r["d"], r["l"]) for r in ares_rows if r.get("l")]
    if len(chaves) != len(set(chaves)):
        erros["logger_repetido_no_dia"] += 1
    for rec in ares_rows:
        if rec.get("lk") == "A" and rec.get("th") is None:
            erros["latencia_agente_sem_horas"] += 1
        if rec.get("th") is not None and rec.get("th") < 0:
            erros["latencia_negativa"] += 1
        if rec.get("lk") == "A" and rec.get("saiu") != "S":
            erros["latencia_sem_saida"] += 1
        if rec.get("lk") == "A" and not rec.get("fs"):
            erros["latencia_sem_primeiro_sync"] += 1
    print("VALIDACAO", dict(erros) or "OK")

    # remove campos internos antes de embutir no HTML
    for rec in ares_rows:
        rec.pop("_e_iso", None)
        rec.pop("_ch_iso", None)
        rec.pop("_s_iso", None)

    top_ag = Counter(r["ag"] for r in pend_rows)
    top_uf = Counter(r["uf"] for r in pend_rows)

    return {
        "pagina_em": pagina_em,
        "hoje_iso": hoje.strftime("%Y-%m-%d"),
        "cutoff": cutoff,
        "daily": daily,
        "pedidos_rows": pedidos_rows,
        "ares_rows": ares_rows,
        "ocultos_rows": ocultos_rows,
        "dia_hoje": dia_hoje_rows,
        "erros": dict(erros),
        "stats": {
            "pares_janela": int(len(frame)),
            "pedidos_janela": int(frame["pedido"].nunique()),
            "loggers_ares_janela": int(len(ares_rows)),
            "loggers_ares_antes_dedup": int(len(ares)),
            "dedup_dia_logger": n_dedup,
            "ocultos_operacao": len(ocultos_rows),
            "ocultos_lista": oc_stats["lista"],
            "ocultos_ausentes": oc_stats["ausentes"],
            "pendentes_janela": len(pend_rows),
            "cobranca_gru_janela": len(gru_rows),
            "sem_embarque_efetiva": int((~ares["saiu"]).sum()),
            "com_embarque_efetiva": int(ares["saiu"].sum()),
            "loggers_sem_lastsync": int(ares["sync"].isna().sum()),
            "nao_avaliados": int(ares["status"].eq("NAO_AVALIADO").sum()),
            "pedidos_com_agente": int(frame["agente"].ne("SEM AGENTE").sum()),
            "dtbtransporte": trp_stats,
            "top10_agentes_pendencias": dict(top_ag.most_common(10)),
            "top10_ufs_pendencias": dict(top_uf.most_common(10)),
            "latencia": {
                "n_agente": sum(1 for r in ares_rows if r.get("lk") == "A"),
                "n_so_gru": sum(1 for r in ares_rows if r.get("lk") == "G"),
                "n_sem_primeiro_sync": sum(1 for r in ares_rows if r.get("lk") == "P"),
                "media_horas": (lambda vs: round(sum(vs) / len(vs), 2) if vs else None)(
                    [float(r["th"]) for r in ares_rows if r.get("lk") == "A" and r.get("th") is not None]
                ),
                "mediana_horas": _quant(
                    [float(r["th"]) for r in ares_rows if r.get("lk") == "A" and r.get("th") is not None], 0.5
                ),
                "p90_horas": _quant(
                    [float(r["th"]) for r in ares_rows if r.get("lk") == "A" and r.get("th") is not None], 0.9
                ),
            },
        },
    }


TEMPLATE = """<!DOCTYPE html><html lang="pt-BR"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"><title>Analítico — Entregas x Pendência de Sincronismo</title><link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin><link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet"><script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script><script src="https://cdn.jsdelivr.net/npm/xlsx@0.18.5/dist/xlsx.full.min.js"></script>
<style>
:root{--bg:#1c3e5f;--panel:#1c3e5f91;--line:rgba(170,200,230,.22);--line-strong:rgba(170,200,230,.38);--txt:#eaf2fb;--soft:#a9c1da;--brand:#3d8bfd;--ok:#4ade80;--warn:#fbbf24;--danger:#fb7185}
*{margin:0;padding:0;box-sizing:border-box}
body{background:radial-gradient(1100px 500px at 15% -10%,rgba(90,150,230,.20) 0%,transparent 60%),radial-gradient(900px 420px at 95% -5%,rgba(120,140,240,.14) 0%,transparent 55%),var(--bg);color:var(--txt);font-family:'Inter','Segoe UI',sans-serif;padding:20px clamp(14px,3vw,36px) 40px;-webkit-font-smoothing:antialiased}
.top{display:flex;flex-wrap:nowrap;gap:14px;align-items:flex-start;justify-content:space-between;border-bottom:2px solid var(--line-strong);padding-bottom:14px;margin-bottom:14px}
.top > div:first-child{flex:1;min-width:0}
.top h1{font-size:1.18rem;font-weight:800;letter-spacing:.01em}
.top h1 span{color:#8fc0ff}
.top .sub{color:var(--soft);font-size:.74rem;margin-top:3px;line-height:1.5}
.stamp{color:var(--soft);font-size:.72rem;text-align:right;line-height:1.6;flex:0 0 auto;margin-left:auto;white-space:nowrap}
.stamp strong{color:#d7e7f8}
.filters{display:grid;grid-template-columns:repeat(6,minmax(130px,1fr));gap:10px;align-items:end;background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:12px 14px;margin-bottom:14px}
.f{min-width:0}
.f label{display:block;color:var(--soft);font-size:.66rem;font-weight:700;text-transform:uppercase;letter-spacing:.07em;margin:0 2px 5px}
.select,.busca{background:#173350;border:1px solid var(--line-strong);border-radius:10px;color:#e2eefb;font-weight:500;font-size:.82rem;padding:8px 12px;width:100%;min-width:0;font-family:inherit;outline:none}
.clear{background:transparent;border:1px solid var(--line-strong);border-radius:10px;color:#c2d6ea;cursor:pointer;font-size:.78rem;font-weight:600;padding:9px 16px;font-family:inherit;width:100%}
.filters-det{grid-template-columns:repeat(6,minmax(130px,1fr))}
@media(max-width:1100px){.filters{grid-template-columns:repeat(3,minmax(140px,1fr))}.filters-det{grid-template-columns:repeat(2,minmax(140px,1fr))}}
@media(max-width:700px){.filters{grid-template-columns:1fr 1fr}}
.clear:hover{color:#fff;background:rgba(255,255,255,.08)}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px;margin-bottom:14px}
.kpi{background:linear-gradient(155deg,#2a4f74 0%,#1e4263 70%);border:1px solid var(--line);border-left:4px solid #4d9bff;border-radius:14px;padding:13px 15px}
.kpi.warn{border-left-color:var(--warn)}
.kpi.ok{border-left-color:var(--ok)}
.kpi.gru{border-left-color:#67e8f9}
.kpi.danger{border-left-color:var(--danger)}
.kpi .lbl{color:var(--soft);font-size:.68rem;font-weight:700;text-transform:uppercase;letter-spacing:.06em}
.kpi .val{font-size:1.65rem;font-weight:800;margin-top:4px}
.kpi.warn .val{color:var(--warn)}
.kpi.ok .val{color:var(--ok)}
.kpi.gru .val{color:#67e8f9}
.kpi.danger .val{color:var(--danger)}
.kpi .sub{color:var(--soft);font-size:.7rem;margin-top:3px}
.op-sec{margin-bottom:16px}
.op-kicker{display:block;color:#8fc0ff;font-size:.62rem;font-weight:800;letter-spacing:.12em;text-transform:uppercase;margin:0 0 5px}
.op-sec > h2{font-size:.95rem;font-weight:800;color:#eaf2fb;margin:0 0 12px}
.op-sec .kpis{grid-template-columns:repeat(3,minmax(0,1fr));margin-bottom:0}
@media(max-width:1100px){.op-sec .kpis{grid-template-columns:1fr}}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-bottom:14px}
@media(max-width:1100px){.grid2{grid-template-columns:1fr}}
.panel{background:var(--panel);border:1px solid var(--line);border-radius:16px;padding:14px}
.panel h2{font-size:.86rem;font-weight:700;color:#d7e7f8;margin:0 4px 10px}
.panel h2 small{color:var(--soft);font-weight:500}
table{border-collapse:collapse;width:100%;font-size:.78rem}
thead tr{background:#173350}
th{padding:9px 11px;font-weight:600;color:#b9d3ec;text-align:left;white-space:nowrap;text-transform:uppercase;font-size:.62rem;letter-spacing:.06em;border-bottom:1px solid var(--line-strong)}
th.num,td.num{text-align:right}
#tbl-det th:first-child,#tbl-det td:first-child{text-align:left;letter-spacing:0;width:2.2em}
th.sort{cursor:pointer;user-select:none}
th.sort:hover{color:#fff}
th.sort.on{color:#fff}
th.sort .ar{opacity:.45;margin-left:4px;font-size:.7rem}
th.sort.on .ar{opacity:1;color:#9ec5fe}
td{padding:7px 11px;border-bottom:1px solid var(--line);white-space:nowrap}
#wrap-det,#wrap-lat,#wrap-op{max-height:70vh;overflow:auto}
#tbl-det thead th,#tbl-lat thead th,#tbl-op thead th{position:sticky;top:0;z-index:2;background:#173350}
#tab-erros .panel-op{background:rgba(12,24,40,.58);border-color:rgba(170,200,230,.18)}
#wrap-op{background:rgba(8,16,26,.22);border-radius:10px}
#tbl-op thead tr,#tbl-op thead th{background:#142838}
#tbl-op tbody td{background:rgba(8,16,28,.20);border-bottom-color:rgba(170,200,230,.12)}
#tbl-op tbody tr:hover td{background:rgba(20,36,54,.50)}
#tbl-op td.ts-emb{color:var(--warn);font-weight:700}
#tbl-op td.ts-sync{color:var(--ok);font-weight:700}
tbody tr:hover{background:rgba(77,155,255,.12)}
tr.hoje td{background:rgba(61,139,253,.16);font-weight:700}
.pill-pend{display:inline-block;padding:2px 9px;border-radius:999px;font-weight:600;font-size:.7rem;color:#fcd34d;background:rgba(251,191,36,.14);border:1px solid rgba(251,191,36,.38)}
.pill-ok{display:inline-block;padding:2px 9px;border-radius:999px;font-weight:600;font-size:.7rem;color:#6ee7a0;background:rgba(74,222,128,.14);border:1px solid rgba(74,222,128,.38)}
.pill-patio{display:inline-block;padding:2px 9px;border-radius:999px;font-weight:600;font-size:.7rem;color:#93c5fd;background:rgba(61,139,253,.14);border:1px solid rgba(61,139,253,.38)}
.pill-gru{display:inline-block;padding:2px 9px;border-radius:999px;font-weight:600;font-size:.7rem;color:#67e8f9;background:rgba(103,232,249,.14);border:1px solid rgba(103,232,249,.38)}
.bar-cell{position:relative;min-width:120px}
.bar{position:absolute;left:0;top:15%;height:70%;background:rgba(251,191,36,.28);border-radius:4px}
.bar-txt{position:relative;z-index:1}
.tbl-scroll{overflow-x:auto}
.detail-panel{margin-bottom:14px}
.dl-bar{display:flex;flex-wrap:wrap;gap:8px;margin:0 0 12px}
.csv{display:inline-block;background:linear-gradient(135deg,#2f6fdb,#3d8bfd);color:#fff;border:1px solid rgba(157,197,254,.5);border-radius:10px;padding:8px 16px;font-weight:600;font-size:.8rem;text-decoration:none;cursor:pointer;font-family:inherit}
.csv:hover{filter:brightness(1.12)}
.tabs{display:flex;flex-wrap:wrap;gap:6px;margin:0 0 14px;border-bottom:1px solid var(--line-strong)}
.tab-btn{background:transparent;border:1px solid transparent;border-bottom:none;border-radius:10px 10px 0 0;color:var(--soft);cursor:pointer;font-family:inherit;font-weight:600;font-size:.82rem;padding:10px 18px;margin-bottom:-1px}
.tab-btn:hover{color:#fff}
.tab-btn.active{background:var(--panel);border-color:var(--line-strong);color:#eaf2fb}
.tab-page{display:none}
.tab-page.on{display:block}
.legenda{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:12px 16px;margin-bottom:14px}
.legenda p{color:var(--soft);font-size:.74rem;line-height:1.45;margin:0 0 8px}
.legenda-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px}
.legenda-item{background:rgba(23,51,80,.55);border:1px solid var(--line);border-radius:10px;padding:8px 10px}
.legenda-item strong{display:block;color:#d7e7f8;font-size:.72rem;letter-spacing:.04em;text-transform:uppercase;margin-bottom:3px}
.legenda-item span{display:block;color:var(--soft);font-size:.72rem;line-height:1.4}
@media(max-width:1100px){.legenda-grid{grid-template-columns:1fr 1fr}}
@media(max-width:700px){.legenda-grid{grid-template-columns:1fr}}
footer{color:var(--soft);font-size:.72rem;margin-top:20px;text-align:center}
::-webkit-scrollbar{width:10px;height:10px}
::-webkit-scrollbar-track{background:transparent}
::-webkit-scrollbar-thumb{background:#3c6285;border-radius:8px}
</style></head><body>
<div class="top">
  <div>
    <h1>ANALÍTICO <span>&middot;</span> Entregas x Pendência de Sincronismo</h1>
    <div class="sub">Cobrança, tempo até o 1º sync e erros operacionais &middot; filtros do topo valem nas três abas &middot; data = Data de entrega</div>
  </div>
  <div class="stamp"><strong>Atualizado em:</strong> __PAGINA_EM__<br><strong>Janela:</strong> __JANELA__</div>
</div>
<div class="filters" id="filtros-topo">
  <div class="f"><label>Dia da entrega</label><select class="select" id="f-dia" onchange="applyFrom('f')"></select></div>
  <div class="f"><label>UF</label><select class="select" id="f-uf" onchange="applyFrom('f')"></select></div>
  <div class="f"><label>Agente</label><select class="select" id="f-ag" onchange="applyFrom('f')"></select></div>
  <div class="f"><label>Tipo</label><select class="select" id="f-tipo" onchange="applyFrom('f')"></select></div>
  <div class="f"><label>Pedido / Logger / Romaneio</label><input class="busca" id="f-busca" placeholder="buscar..." oninput="applyFrom('f')"></div>
  <button class="clear" onclick="limpar()">Limpar filtros</button>
</div>
<div class="tabs" id="tabs">
  <button type="button" class="tab-btn active" data-tab="cobranca" onclick="showTab('cobranca')">Cobrança</button>
  <button type="button" class="tab-btn" data-tab="hoje" id="tab-btn-hoje" onclick="showTab('hoje')">Entregas do dia</button>
  <button type="button" class="tab-btn" data-tab="tempo" onclick="showTab('tempo')">Tempo até o 1º sync</button>
  <button type="button" class="tab-btn" data-tab="erros" id="tab-btn-erros" onclick="showTab('erros')">Erros operacionais</button>
  <button type="button" class="tab-btn" data-tab="individual" id="tab-btn-individual" onclick="showTab('individual')">Cobrança individual</button>
</div>
<div id="tab-individual" class="tab-page">
<div class="legenda"><p>Página exclusiva por base, com <b>somente as pendências da própria base</b> — mesma fonte e mesma rotina de atualização deste indicador. Envie a cada base <b>apenas o link dela</b>. A lista completa também fica no arquivo local LINKS_BASES_SINCRONISMO.csv.</p></div>
<div class="panel detail-panel"><h2>Cobrança individual por base <small id="bases-count"></small></h2>
<div class="tbl-scroll"><table id="tbl-bases"><thead><tr>
<th>#</th><th>Base / agente</th><th class="num">Loggers pendentes agora</th><th>Página individual</th>
</tr></thead><tbody id="tb-bases"></tbody></table></div></div>
</div>
<div id="tab-hoje" class="tab-page">
<div class="kpis" id="kpis-hoje"></div>
<div class="legenda"><p>Visão do dia (independe de sincronismo e dos filtros do topo): pedidos com <b>previsão de entrega para hoje</b> (dt_previstaentrega) e pedidos <b>entregues hoje</b> (data de entrega lançada). "Faltam entregar" = previstos para hoje ainda sem registro de entrega.</p></div>
<div class="grid2">
  <div class="panel"><h2>Faltam entregar hoje <small id="hj-falta-count"></small></h2>
  <div class="tbl-scroll"><table><thead><tr><th>#</th><th>Pedido</th><th>UF</th><th>Cliente</th><th>Modal</th><th>Previsto p/</th><th class="num">Loggers</th></tr></thead><tbody id="tb-hj-falta"></tbody></table></div></div>
  <div class="panel"><h2>Entregues hoje <small id="hj-ent-count"></small></h2>
  <div class="tbl-scroll"><table><thead><tr><th>#</th><th>Pedido</th><th>UF</th><th>Cliente</th><th>Modal</th><th>Previsto p/</th><th>Entregue em</th><th class="num">Loggers</th></tr></thead><tbody id="tb-hj-ent"></tbody></table></div></div>
</div>
<div class="panel detail-panel"><h2>Previstos para hoje entregues em outra data <small id="hj-outra-count"></small></h2>
<div class="tbl-scroll"><table><thead><tr><th>#</th><th>Pedido</th><th>UF</th><th>Cliente</th><th>Modal</th><th>Previsto p/</th><th>Entregue em</th><th class="num">Loggers</th></tr></thead><tbody id="tb-hj-outra"></tbody></table></div></div>
</div>
<div id="tab-cobranca" class="tab-page on">
<div class="kpis" id="kpis"></div>
<div class="panel" style="margin-bottom:14px"><h2>Série diária — pedidos entregues x loggers pendentes <small>(filtros de UF/agente/tipo aplicados)</small></h2><div id="g-serie" style="height:330px"></div></div>
<div class="grid2">
  <div class="panel"><h2>Top 10 agentes — loggers pendentes de sincronismo</h2><div class="tbl-scroll"><table><thead><tr><th>#</th><th>Agente</th><th class="num">Entregues</th><th class="num">Pendentes</th><th class="num">% pend.</th><th>Volume</th></tr></thead><tbody id="tb-ag"></tbody></table></div></div>
  <div class="panel"><h2>Top 10 UF — loggers pendentes de sincronismo</h2><div class="tbl-scroll"><table><thead><tr><th>#</th><th>UF</th><th class="num">Entregues</th><th class="num">Pendentes</th><th class="num">% pend.</th><th>Volume</th></tr></thead><tbody id="tb-uf"></tbody></table></div></div>
</div>
<div class="panel detail-panel"><h2>Resumo por dia e origem das pendências</h2>
<div class="tbl-scroll"><table id="tbl-dia"><thead><tr>
<th class="sort" data-k="d" onclick="sortTbl('dia','d')">Dia<span class="ar"></span></th>
<th class="sort num" data-k="ped" onclick="sortTbl('dia','ped')">Pedidos entregues<span class="ar"></span></th>
<th class="sort num" data-k="log" onclick="sortTbl('dia','log')">Loggers ARES<span class="ar"></span></th>
<th class="sort num" data-k="sync" onclick="sortTbl('dia','sync')">Sincronizados<span class="ar"></span></th>
<th class="sort num" data-k="pend" onclick="sortTbl('dia','pend')">Pendentes agente<span class="ar"></span></th>
<th class="sort num" data-k="gru" onclick="sortTbl('dia','gru')">Cobrança GRU<span class="ar"></span></th>
<th class="sort num" data-k="patio" onclick="sortTbl('dia','patio')">Registro de entrega ausente<span class="ar"></span></th>
<th class="sort num" data-k="pct" onclick="sortTbl('dia','pct')">% pendência<span class="ar"></span></th>
<th class="sort num" data-k="pped" onclick="sortTbl('dia','pped')">Pedidos c/ pendência<span class="ar"></span></th>
</tr></thead><tbody id="tb-dia"></tbody></table></div></div>
<div class="panel detail-panel"><h2><span id="det-title">Pendência de cobrança de agentes</span> <small id="det-count"></small></h2>
<div class="filters filters-det" id="filtros-detalhe">
  <div class="f"><label>UF</label><select class="select" id="d-uf" onchange="applyDet()"></select></div>
  <div class="f"><label>Pedido / Logger / Romaneio</label><input class="busca" id="d-busca" placeholder="buscar..." oninput="applyDet()"></div>
  <div class="f"><label>Romaneio</label><select class="select" id="d-ro" onchange="applyDet()"></select></div>
  <div class="f"><label>Agente / base</label><select class="select" id="d-ag" onchange="applyDet()"></select></div>
  <div class="f"><label>Situação</label><select class="select" id="d-cob" onchange="applyDet()"><option value="">Pendência de cobrança de agentes</option><option value="GRU">Pendentes de cobrança em GRU</option><option value="PATIO">Registro de entrega ausente</option><option value="PG">Agentes + GRU</option></select></div>
  <button class="clear" onclick="limparDet()">Limpar filtros</button>
</div>
<div class="dl-bar">
  <button class="csv" type="button" id="btn-csv-det" onclick="downloadCsv('det')">&#8595; CSV</button>
  <button class="csv" type="button" id="btn-xlsx-det" onclick="downloadXlsx('det')">&#8595; XLSX</button>
</div>
<div class="tbl-scroll" id="wrap-det"><table id="tbl-det"><thead><tr>
<th>#</th>
<th class="sort" data-k="d" onclick="sortTbl('det','d')">Dia<span class="ar"></span></th>
<th class="sort" data-k="p" onclick="sortTbl('det','p')">Pedido<span class="ar"></span></th>
<th class="sort" data-k="ro" onclick="sortTbl('det','ro')">Romaneio<span class="ar"></span></th>
<th class="sort" data-k="l" onclick="sortTbl('det','l')">Logger<span class="ar"></span></th>
<th class="sort" data-k="t" onclick="sortTbl('det','t')">Tipo<span class="ar"></span></th>
<th class="sort" data-k="uf" onclick="sortTbl('det','uf')">UF<span class="ar"></span></th>
<th class="sort" data-k="ag" onclick="sortTbl('det','ag')">Agente<span class="ar"></span></th>
<th class="sort" data-k="cob" onclick="sortTbl('det','cob')">Cobrança<span class="ar"></span></th>
<th class="sort" data-k="cli" onclick="sortTbl('det','cli')">Cliente<span class="ar"></span></th>
<th class="sort" data-k="mo" onclick="sortTbl('det','mo')">Modal<span class="ar"></span></th>
<th class="sort" data-k="emb" onclick="sortTbl('det','emb')">Data de entrega<span class="ar"></span></th>
<th class="sort" data-k="s" onclick="sortTbl('det','s')">Último sync<span class="ar"></span></th>
<th class="sort num" data-k="ds" onclick="sortTbl('det','ds')">Dias sem sync<span class="ar"></span></th>
<th class="sort" data-k="st" onclick="sortTbl('det','st')">Situação<span class="ar"></span></th>
</tr></thead><tbody id="tb-det"></tbody></table></div></div>
</div>
<div id="tab-tempo" class="tab-page">
<div class="kpis" id="kpis-lat"></div>
<div class="legenda">
  <p>Tempo do 1º sync no agente: da chegada no cliente até o primeiro log ARES, só se a chegada for antes da entrega lançada no sistema. Se não houver chegada, ou se ela for depois do lançamento, o relógio é a data de entrega. Só entram quem sincronizou no agente — pendente e só-GRU ficam de fora.</p>
  <div class="legenda-grid">
    <div class="legenda-item"><strong>N</strong><span>Quantos loggers daquele agente ou UF já sincronizaram no agente (mín. 5 no ranking).</span></div>
    <div class="legenda-item"><strong>Mediana</strong><span>Tempo do meio. Metade syncou mais rápido, metade mais lento. É o que ordena o ranking.</span></div>
    <div class="legenda-item"><strong>Média</strong><span>Soma dos tempos ÷ N. Um logger muito lento puxa esse número para cima.</span></div>
    <div class="legenda-item"><strong>P90</strong><span>90% sincronizaram até esse tempo. Só os 10% mais lentos passaram disso.</span></div>
  </div>
</div>
<div class="panel" style="margin-bottom:14px"><h2>Série diária — tempo até o 1º sync após a entrega <small>(só quem sincronizou no agente; filtros de UF/agente/tipo aplicados)</small></h2><div id="g-lat" style="height:300px"></div></div>
<div class="grid2">
  <div class="panel"><h2>Top 10 agentes — tempo até sincronizar <small>(mediana; mín. 5 loggers)</small></h2><div class="tbl-scroll"><table><thead><tr><th>#</th><th>Agente</th><th class="num">N</th><th class="num">Mediana</th><th class="num">Média</th><th class="num">P90</th></tr></thead><tbody id="tb-lat-ag"></tbody></table></div></div>
  <div class="panel"><h2>Top 10 UF — tempo até sincronizar <small>(mediana; mín. 5 loggers)</small></h2><div class="tbl-scroll"><table><thead><tr><th>#</th><th>UF</th><th class="num">N</th><th class="num">Mediana</th><th class="num">Média</th><th class="num">P90</th></tr></thead><tbody id="tb-lat-uf"></tbody></table></div></div>
</div>
<div class="panel detail-panel"><h2>1º sync no agente <small id="lat-slow-count"></small></h2>
<div class="filters filters-det" id="filtros-lat">
  <div class="f"><label>UF</label><select class="select" id="l-uf" onchange="applyLat()"></select></div>
  <div class="f"><label>Pedido / Logger</label><input class="busca" id="l-busca" placeholder="buscar..." oninput="applyLat()"></div>
  <div class="f"><label>Agente / base</label><select class="select" id="l-ag" onchange="applyLat()"></select></div>
  <div class="f"><label>Tipo</label><select class="select" id="l-tipo" onchange="applyLat()"></select></div>
  <div class="f"><label>Tempo até o 1º sync</label><select class="select" id="l-tempo" onchange="applyLat()"><option value="">Todos</option><option value="lt1">Até 1 hora</option><option value="1a24">1 a 24 horas</option><option value="gt24">Mais de 1 dia</option></select></div>
  <button class="clear" onclick="limparLat()">Limpar filtros</button>
</div>
<div class="tbl-scroll" id="wrap-lat"><table id="tbl-lat"><thead><tr>
<th>#</th>
<th class="sort" data-k="d" onclick="sortTbl('lat','d')">Dia<span class="ar"></span></th>
<th class="sort" data-k="p" onclick="sortTbl('lat','p')">Pedido<span class="ar"></span></th>
<th class="sort" data-k="l" onclick="sortTbl('lat','l')">Logger<span class="ar"></span></th>
<th class="sort" data-k="uf" onclick="sortTbl('lat','uf')">UF<span class="ar"></span></th>
<th class="sort" data-k="ag" onclick="sortTbl('lat','ag')">Agente<span class="ar"></span></th>
<th class="sort" data-k="t0" onclick="sortTbl('lat','t0')">T0 (chegada se antes da entrega)<span class="ar"></span></th>
<th class="sort" data-k="fs" onclick="sortTbl('lat','fs')">1º sync<span class="ar"></span></th>
<th class="sort num" data-k="th" onclick="sortTbl('lat','th')">Tempo<span class="ar"></span></th>
</tr></thead><tbody id="tb-lat-slow"></tbody></table></div></div>
</div>
<div id="tab-erros" class="tab-page">
<div class="op-sec">
  <span class="op-kicker">Indicadores</span>
  <h2>Visão dos erros</h2>
  <div class="kpis" id="kpis-op-visao"></div>
</div>
<div class="op-sec">
  <span class="op-kicker">Causadores</span>
  <h2>Quem lança errado no aplicativo de entregas</h2>
  <div class="kpis" id="kpis-op-causa"></div>
</div>
<div class="legenda"><p>Lançamento errado no aplicativo de entregas: a dt_entregaefetivaembarque foi digitada depois da entrega real. Sai da cobrança e do tempo até o 1º sync. Laranja = hora lançada no app. Verde = último sync. Quem causa = agente/base que registrou a entrega. Filtros do topo valem nos indicadores e na grade.</p></div>
<div class="grid2">
  <div class="panel panel-op"><h2>Top 10 agentes — quem lança errado no aplicativo de entregas</h2><div class="tbl-scroll"><table><thead><tr><th>#</th><th>Agente</th><th class="num">Loggers</th><th class="num">Pedidos</th><th class="num">% erros</th><th>Volume</th></tr></thead><tbody id="tb-op-ag"></tbody></table></div></div>
  <div class="panel panel-op"><h2>Top 10 UF — quem lança errado no aplicativo de entregas</h2><div class="tbl-scroll"><table><thead><tr><th>#</th><th>UF</th><th class="num">Loggers</th><th class="num">Pedidos</th><th class="num">% erros</th><th>Volume</th></tr></thead><tbody id="tb-op-uf"></tbody></table></div></div>
</div>
<div class="panel detail-panel panel-op"><h2>Lançamento errado no aplicativo de entregas <small id="op-count"></small></h2>
<div class="dl-bar"><button class="csv" type="button" onclick="downloadOcultos()">&#8595; CSV</button></div>
<div class="tbl-scroll" id="wrap-op"><table id="tbl-op"><thead><tr>
<th>#</th>
<th class="sort" data-k="d" onclick="sortTbl('erros','d')">Dia<span class="ar"></span></th>
<th class="sort" data-k="p" onclick="sortTbl('erros','p')">Pedido<span class="ar"></span></th>
<th class="sort" data-k="ro" onclick="sortTbl('erros','ro')">Romaneio<span class="ar"></span></th>
<th class="sort" data-k="l" onclick="sortTbl('erros','l')">Logger<span class="ar"></span></th>
<th class="sort" data-k="uf" onclick="sortTbl('erros','uf')">UF<span class="ar"></span></th>
<th class="sort" data-k="ag" onclick="sortTbl('erros','ag')">Agente<span class="ar"></span></th>
<th class="sort" data-k="emb" onclick="sortTbl('erros','emb')">Data de entrega<span class="ar"></span></th>
<th class="sort" data-k="s" onclick="sortTbl('erros','s')">Último sync<span class="ar"></span></th>
<th class="sort" data-k="ox" onclick="sortTbl('erros','ox')">Motivo<span class="ar"></span></th>
</tr></thead><tbody id="tb-ocultos"></tbody></table></div></div>
</div>
<footer>Gerado em __PAGINA_EM__ &middot; VTC LOG - BI Qualidade &middot; Analítico Entregas x Sincronismo</footer>
<script>
const PEDIDOS=__PEDIDOS__;const ROWS=__ROWS__;const OCULTOS=__OCULTOS__;const HOJE=__HOJE__;const DIAS=__DIAS__;const DIAHOJE=__DIAHOJE__;const BASESLINKS=__BASESLINKS__;
function esc(v){return String(v==null?'':v).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
function fmtInt(n){return Number(n||0).toLocaleString('pt-BR')}
function fmtPct(v){return Number(v||0).toLocaleString('pt-BR',{minimumFractionDigits:1,maximumFractionDigits:1})+'%'}
function fmtHoras(h){if(h==null||h==='')return '—';const n=Number(h);if(!isFinite(n))return '—';if(n<1){const m=Math.round(n*60);return m<=0?'<1 min':m.toLocaleString('pt-BR')+' min'}if(n<24)return n.toLocaleString('pt-BR',{minimumFractionDigits:1,maximumFractionDigits:1})+' h';const d=Math.floor(n/24);const hr=n-d*24;return d+'d '+hr.toLocaleString('pt-BR',{minimumFractionDigits:1,maximumFractionDigits:1})+'h'}
function showTab(id){TAB=id;document.querySelectorAll('.tab-btn').forEach(b=>b.classList.toggle('active',b.dataset.tab===id));document.querySelectorAll('.tab-page').forEach(p=>p.classList.toggle('on',p.id==='tab-'+id));if(window.Plotly){if(id==='cobranca'&&document.getElementById('g-serie'))Plotly.Plots.resize('g-serie');if(id==='tempo'&&document.getElementById('g-lat'))Plotly.Plots.resize('g-lat')}}
function matchOc(r){const b=F.busca.trim().toUpperCase();if(F.dia&&r.d!==F.dia)return false;if(F.uf&&r.uf!==F.uf)return false;if(F.ag&&r.ag!==F.ag)return false;if(F.tipo&&r.t!==F.tipo)return false;if(b&&!(String(r.p).toUpperCase().includes(b)||String(r.l||'').toUpperCase().includes(b)||String(r.ro||'').toUpperCase().includes(b)))return false;return true}
function uniqCount(rows,k){return new Set(rows.map(r=>r[k]).filter(Boolean)).size}
function topKey(rows,k){const g={};rows.forEach(r=>{const v=r[k]||'—';g[v]=(g[v]||0)+1});let best='—',n=0;Object.entries(g).forEach(function(e){if(e[1]>n){best=e[0];n=e[1]}});return {k:best,n:n}}
function hoursLate(r){const a=parseTs(r.emb),b=parseTs(r.s);if(!a||!b||a<=b)return null;return (a-b)/3600000}
function paintOpKpis(all,rows){
 const vis=document.getElementById('kpis-op-visao');
 const cau=document.getElementById('kpis-op-causa');
 if(!vis||!cau)return;
 const peds=uniqCount(rows,'p');
 const ags=uniqCount(rows,'ag');
 const ufs=uniqCount(rows,'uf');
 const ros=uniqCount(rows,'ro');
 const top=topKey(rows,'ag');
 const med=quantile(rows.map(hoursLate).filter(function(h){return h!=null}),0.5);
 const subFilt=rows.length!==all.length?fmtInt(all.length)+' no total · ':'';
 vis.innerHTML=
  '<div class="kpi warn"><div class="lbl">Lançamento errado no app</div><div class="val">'+fmtInt(rows.length)+'</div><div class="sub">'+subFilt+'loggers fora da cobrança</div></div>'
 +'<div class="kpi warn"><div class="lbl">Pedidos afetados</div><div class="val">'+fmtInt(peds)+'</div><div class="sub">'+fmtInt(ros)+' romaneio(s)</div></div>'
 +'<div class="kpi warn"><div class="lbl">Atraso do lançamento</div><div class="val">'+fmtHoras(med)+'</div><div class="sub">mediana da hora digitada no app vs último sync</div></div>';
 cau.innerHTML=
  '<div class="kpi danger"><div class="lbl">Agentes causando</div><div class="val">'+fmtInt(ags)+'</div><div class="sub">quem lançou a entrega no aplicativo</div></div>'
 +'<div class="kpi danger"><div class="lbl">Quem mais lança errado</div><div class="val">'+fmtInt(top.n)+'</div><div class="sub" title="'+esc(top.k)+'">'+esc(top.k)+'</div></div>'
 +'<div class="kpi"><div class="lbl">UFs</div><div class="val">'+fmtInt(ufs)+'</div><div class="sub">bases com hora lançada depois da entrega real</div></div>';
}
function renderOpRank(campo,rows,tbId){
 const g={};rows.forEach(function(r){const k=r[campo]||'—';if(!g[k])g[k]={n:0,p:{}};g[k].n++;if(r.p)g[k].p[r.p]=1});
 const tot=rows.length||1;
 const arr=Object.entries(g).map(function(e){return {k:e[0],n:e[1].n,ped:Object.keys(e[1].p).length,pct:e[1].n/tot*100}}).sort(function(a,b){return b.n-a.n||b.ped-a.ped}).slice(0,10);
 const max=arr.length?arr[0].n:1;
 const el=document.getElementById(tbId);
 if(el) el.innerHTML=arr.map(function(x,i){return '<tr><td>'+(i+1)+'</td><td title="'+esc(x.k)+'">'+esc(x.k)+'</td><td class="num"><span class="pill-pend">'+fmtInt(x.n)+'</span></td><td class="num">'+fmtInt(x.ped)+'</td><td class="num">'+fmtPct(x.pct)+'</td><td class="bar-cell"><div class="bar" style="width:'+Math.round(x.n/max*100)+'%"></div><span class="bar-txt">&nbsp;</span></td></tr>'}).join('')||'<tr><td colspan="6" style="color:#a9c1da">Sem lançamento errado para os filtros</td></tr>';
}
function paintOcultos(){
 const btn=document.getElementById('tab-btn-erros');
 if(btn){btn.style.display='';btn.textContent='Erros operacionais ('+fmtInt(OCULTOS.length)+')'}
 const rows=OCULTOS.filter(matchOc);
 paintOpKpis(OCULTOS,rows);
 renderOpRank('ag',rows,'tb-op-ag');
 renderOpRank('uf',rows,'tb-op-uf');
 const sc=document.getElementById('op-count');
 if(sc)sc.textContent='('+fmtInt(rows.length)+(rows.length!==OCULTOS.length?' de '+fmtInt(OCULTOS.length):'')+')';
 const list=applySort(rows,SORT.erros);
 markSort('tbl-op',SORT.erros);
 const tb=document.getElementById('tb-ocultos');
 if(tb)tb.innerHTML=list.map(function(r,i){return '<tr><td>'+(i+1)+'</td><td>'+diaBR(r.d)+'</td><td>'+esc(r.p)+'</td><td>'+esc(r.ro||'')+'</td><td>'+esc(r.l)+'</td><td>'+esc(r.uf)+'</td><td title="'+esc(r.ag)+'">'+esc(r.ag)+'</td><td class="ts-emb">'+(r.emb?esc(r.emb):'—')+'</td><td class="ts-sync">'+(r.s?esc(r.s):'—')+'</td><td>'+esc(r.ox||'')+'</td></tr>'}).join('')||'<tr><td colspan="10" style="color:#a9c1da">Nenhum lançamento errado para os filtros</td></tr>';
}
function baixarBlob(blob,nome){const a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download=nome;a.style.display='none';document.body.appendChild(a);a.click();setTimeout(function(){document.body.removeChild(a);URL.revokeObjectURL(a.href)},3000)}
function downloadOcultos(){const rows=applySort(OCULTOS.filter(matchOc),SORT.erros);const h=['#','Dia','Pedido','Romaneio','Logger','UF','Agente','Data de entrega','Ultimo sync','Motivo'];const m=[h].concat(rows.map((r,i)=>[i+1,diaBR(r.d),r.p,r.ro||'',r.l,r.uf,r.ag,r.emb||'',r.s||'',r.ox||'']));const nl=String.fromCharCode(13,10);const body=m.map(row=>row.map(csvEscape).join(';')).join(nl);const blob=new Blob([String.fromCharCode(65279)+body],{type:'text/csv;charset=utf-8'});baixarBlob(blob,'entregas_vs_sincronismo_ocultos.csv')}
let TAB='cobranca';
function quantile(arr,q){if(!arr.length)return null;const a=arr.slice().sort((x,y)=>x-y);if(a.length===1)return a[0];const i=(a.length-1)*q;const lo=Math.floor(i),hi=Math.ceil(i);return lo===hi?a[lo]:a[lo]*(hi-i)+a[hi]*(i-lo)}
function latRows(rows){return rows.filter(r=>r.lk==='A'&&r.th!=null)}
function diaBR(iso){return iso.slice(8,10)+'/'+iso.slice(5,7)+'/'+iso.slice(0,4)}
function opt(sel,vals,labelTodos){if(!sel)return;sel.innerHTML='<option value="">'+labelTodos+'</option>'+vals.map(v=>'<option value="'+esc(v)+'">'+esc(v)+'</option>').join('')}
const F={dia:'',uf:'',ag:'',tipo:'',busca:''};
function fillBar(pre){
 const dia=document.getElementById(pre+'-dia');
 if(!dia)return;
 dia.innerHTML='<option value="">Todos ('+DIAS.length+' dias)</option>'+DIAS.slice().reverse().map(d=>'<option value="'+d+'">'+diaBR(d)+(d===HOJE?' (hoje)':'')+'</option>').join('');
 const src=ROWS.concat(OCULTOS||[]);
 opt(document.getElementById(pre+'-uf'),[...new Set(src.map(r=>r.uf).filter(Boolean))].sort(),'Todas');
 opt(document.getElementById(pre+'-ag'),[...new Set(src.map(r=>r.ag).filter(Boolean))].sort(),'Todos');
 opt(document.getElementById(pre+'-tipo'),[...new Set(src.map(r=>r.t).filter(Boolean))].sort(),'Todos');
}
fillBar('f');
const FD={uf:'',ag:'',busca:'',cob:'',ro:''};
function fillRos(){const el=document.getElementById('d-ro');if(!el)return;const keep=el.value;const src=FD.cob==='PATIO'?ROWS.filter(r=>r.st==='E'):FD.cob==='GRU'?ROWS.filter(r=>r.st==='G'):FD.cob==='PG'?ROWS.filter(r=>r.st==='P'||r.st==='G'):ROWS.filter(r=>r.st==='P');const ros=[...new Set(src.map(r=>r.ro).filter(Boolean))].sort((a,b)=>String(a).localeCompare(String(b),undefined,{numeric:true}));opt(el,ros,'Todos');if(keep&&ros.some(v=>String(v)===keep))el.value=keep}
function fillDet(){opt(document.getElementById('d-uf'),[...new Set(ROWS.map(r=>r.uf))].sort(),'Todas');opt(document.getElementById('d-ag'),[...new Set(ROWS.map(r=>r.ag))].sort(),'Todos');fillRos()}
fillDet();
function applyDet(){FD.uf=document.getElementById('d-uf').value;FD.ag=document.getElementById('d-ag').value;FD.busca=document.getElementById('d-busca').value;const c=document.getElementById('d-cob');const cobWas=FD.cob;FD.cob=c?c.value:'';if(FD.cob!==cobWas)fillRos();const ro=document.getElementById('d-ro');FD.ro=ro?ro.value:'';paintDet()}
function limparDet(){FD.uf='';FD.ag='';FD.busca='';FD.cob='';FD.ro='';const u=document.getElementById('d-uf'),a=document.getElementById('d-ag'),b=document.getElementById('d-busca'),c=document.getElementById('d-cob'),ro=document.getElementById('d-ro');if(u)u.value='';if(a)a.value='';if(b)b.value='';if(c)c.value='';if(ro)ro.value='';paintDet()}
const FL={uf:'',ag:'',tipo:'',busca:'',tempo:''};
function fillLat(){const src=DATA.lat||[];const keepU=document.getElementById('l-uf'),keepA=document.getElementById('l-ag'),keepT=document.getElementById('l-tipo');const vu=keepU?keepU.value:'',va=keepA?keepA.value:'',vt=keepT?keepT.value:'';opt(keepU,[...new Set(src.map(r=>r.uf))].sort(),'Todas');opt(keepA,[...new Set(src.map(r=>r.ag))].sort(),'Todos');opt(keepT,[...new Set(src.map(r=>r.t).filter(Boolean))].sort(),'Todos');if(keepU&&[...keepU.options].some(o=>o.value===vu))keepU.value=vu;if(keepA&&[...keepA.options].some(o=>o.value===va))keepA.value=va;if(keepT&&[...keepT.options].some(o=>o.value===vt))keepT.value=vt}
function applyLat(){const u=document.getElementById('l-uf'),a=document.getElementById('l-ag'),t=document.getElementById('l-tipo'),b=document.getElementById('l-busca'),tp=document.getElementById('l-tempo');FL.uf=u?u.value:'';FL.ag=a?a.value:'';FL.tipo=t?t.value:'';FL.busca=b?b.value:'';FL.tempo=tp?tp.value:'';paintLat()}
function limparLat(){FL.uf='';FL.ag='';FL.tipo='';FL.busca='';FL.tempo='';const u=document.getElementById('l-uf'),a=document.getElementById('l-ag'),t=document.getElementById('l-tipo'),b=document.getElementById('l-busca'),tp=document.getElementById('l-tempo');if(u)u.value='';if(a)a.value='';if(t)t.value='';if(b)b.value='';if(tp)tp.value='';paintLat()}
function matchLat(r){const b=FL.busca.trim().toUpperCase();if(FL.uf&&r.uf!==FL.uf)return false;if(FL.ag&&r.ag!==FL.ag)return false;if(FL.tipo&&r.t!==FL.tipo)return false;if(FL.tempo==='lt1'&&!(r.th<1))return false;if(FL.tempo==='1a24'&&!(r.th>=1&&r.th<24))return false;if(FL.tempo==='gt24'&&!(r.th>=24))return false;if(b&&!(String(r.p).toUpperCase().includes(b)||String(r.l||'').toUpperCase().includes(b)))return false;return true}
function latGridRows(){return (DATA.lat||[]).filter(matchLat)}
function matchDet(r){const b=FD.busca.trim().toUpperCase();if(FD.uf&&r.uf!==FD.uf)return false;if(FD.ag&&r.ag!==FD.ag)return false;if(FD.cob==='PATIO'){if(r.st!=='E')return false}else if(FD.cob==='PG'){if(r.st!=='P'&&r.st!=='G')return false}else if(FD.cob){if(r.cob!==FD.cob)return false}else if(r.st!=='P')return false;if(FD.ro&&String(r.ro||'')!==FD.ro)return false;if(b&&!(String(r.p).toUpperCase().includes(b)||String(r.l||'').toUpperCase().includes(b)||String(r.ro||'').toUpperCase().includes(b)))return false;return true}
function detRows(){return DATA.det.filter(matchDet)}
function writeBar(pre){const dia=document.getElementById(pre+'-dia');if(!dia)return;dia.value=F.dia;document.getElementById(pre+'-uf').value=F.uf;document.getElementById(pre+'-ag').value=F.ag;document.getElementById(pre+'-tipo').value=F.tipo;document.getElementById(pre+'-busca').value=F.busca}
function applyFrom(pre){F.dia=document.getElementById(pre+'-dia').value;F.uf=document.getElementById(pre+'-uf').value;F.ag=document.getElementById(pre+'-ag').value;F.tipo=document.getElementById(pre+'-tipo').value;F.busca=document.getElementById(pre+'-busca').value;renderAll()}
function limpar(){F.dia='';F.uf='';F.ag='';F.tipo='';F.busca='';writeBar('f');renderAll()}
function matchRow(r,ignoreDia){const b=F.busca.trim().toUpperCase();
 if(!ignoreDia&&F.dia&&r.d!==F.dia)return false;
 if(F.uf&&r.uf!==F.uf)return false;
 if(F.ag&&r.ag!==F.ag)return false;
 if(F.tipo&&r.t!==F.tipo)return false;
 if(b&&!(String(r.p).toUpperCase().includes(b)||String(r.l||'').toUpperCase().includes(b)||String(r.ro||'').toUpperCase().includes(b)))return false;
 return true}
function matchPed(p,ignoreDia){const b=F.busca.trim().toUpperCase();
 if(!ignoreDia&&F.dia&&p.d!==F.dia)return false;
 if(F.uf&&p.uf!==F.uf)return false;
 if(F.ag&&p.ag!==F.ag)return false;
 if(b&&!String(p.p).toUpperCase().includes(b))return false;
 return true}
function renderAll(){
 const rows=ROWS.filter(r=>matchRow(r,false));
 const peds=PEDIDOS.filter(p=>matchPed(p,false));
 const pend=rows.filter(r=>r.st==='P');
 const gru=rows.filter(r=>r.st==='G');
 const patio=rows.filter(r=>r.st==='E');
 const ent=rows.filter(r=>r.saiu==='S');
 const pedSet=new Set(peds.map(p=>p.p)).size;
 const pedPend=new Set(pend.map(r=>r.p)).size;
 const pct=ent.length?pend.length/ent.length*100:0;
 const hj=ROWS.filter(r=>r.d===HOJE&&matchRow(r,true));
 const hjPend=hj.filter(r=>r.st==='P').length;
 const hjPedPend=new Set(hj.filter(r=>r.st==='P').map(r=>r.p)).size;
 const hjGru=hj.filter(r=>r.st==='G').length;
 const hjPatio=hj.filter(r=>r.st==='E').length;
 const hjEnt=hj.filter(r=>r.saiu==='S').length;
 const hjPed=new Set(PEDIDOS.filter(p=>p.d===HOJE&&matchPed(p,true)).map(p=>p.p)).size;
 document.getElementById('kpis').innerHTML=
  '<div class="kpi"><div class="lbl">Pedidos entregues</div><div class="val">'+fmtInt(pedSet)+'</div><div class="sub">hoje: '+fmtInt(hjPed)+' &middot; com data de entrega</div></div>'
 +'<div class="kpi"><div class="lbl">Loggers ARES entregues</div><div class="val">'+fmtInt(ent.length)+'</div><div class="sub">hoje: '+fmtInt(hjEnt)+' &middot; com data de entrega</div></div>'
 +'<div class="kpi warn"><div class="lbl">Pendentes — cobrança agente</div><div class="val">'+fmtInt(pend.length)+'</div><div class="sub">hoje: '+fmtInt(hjPend)+' logger(s) &middot; '+fmtInt(hjPedPend)+' pedido(s) &middot; janela 20d: '+fmtInt(pedPend)+' pedido(s)</div></div>'
 +'<div class="kpi gru"><div class="lbl">Já retornou — cobrança GRU</div><div class="val">'+fmtInt(gru.length)+'</div><div class="sub">hoje: '+fmtInt(hjGru)+' &middot; Em GRU agora ou estoque depois da entrega</div></div>'
 +'<div class="kpi"><div class="lbl">Registro de entrega ausente</div><div class="val">'+fmtInt(patio.length)+'</div><div class="sub">hoje: '+fmtInt(hjPatio)+' &middot; pátio — não é entrega</div></div>'
 +'<div class="kpi '+(pct>=30?'danger':(pct>0?'warn':''))+'"><div class="lbl">% pendência agente</div><div class="val">'+fmtPct(pct)+'</div><div class="sub">cobrança agente / loggers entregues</div></div>';
 // serie diaria (ignora filtro de dia)
 const rowsS=ROWS.filter(r=>matchRow(r,true));
 const pedsS=PEDIDOS.filter(p=>matchPed(p,true));
 const serie=DIAS.map(d=>{const rr=rowsS.filter(r=>r.d===d);const entd=rr.filter(r=>r.saiu==='S');const pp=rr.filter(r=>r.st==='P').length;const gg=rr.filter(r=>r.st==='G').length;const ee=rr.filter(r=>r.st==='E').length;
  return {d:d,dia:diaBR(d).slice(0,5),ped:new Set(pedsS.filter(p=>p.d===d).map(p=>p.p)).size,log:entd.length,pend:pp,gru:gg,patio:ee,pct:entd.length?pp/entd.length*100:0}});
 if(window.Plotly) Plotly.react('g-serie',[
  {type:'bar',name:'Pedidos entregues',x:serie.map(s=>s.dia),y:serie.map(s=>s.ped),marker:{color:'#3d8bfd'},hovertemplate:'%{x}<br>Pedidos: %{y}<extra></extra>'},
  {type:'bar',name:'Loggers pendentes',x:serie.map(s=>s.dia),y:serie.map(s=>s.pend),marker:{color:'#fbbf24'},hovertemplate:'%{x}<br>Pendentes: %{y}<extra></extra>'},
  {type:'scatter',mode:'lines+markers',name:'% pendência',x:serie.map(s=>s.dia),y:serie.map(s=>s.pct),yaxis:'y2',line:{color:'#fb7185',width:2},marker:{size:5},hovertemplate:'%{x}<br>%{y:.1f}%<extra></extra>'}
 ],{paper_bgcolor:'rgba(0,0,0,0)',plot_bgcolor:'rgba(0,0,0,0)',font:{color:'#eaf2fb',family:'Inter,Segoe UI,sans-serif'},barmode:'group',bargap:.25,margin:{t:8,r:52,b:40,l:44},legend:{orientation:'h',y:-.2},xaxis:{gridcolor:'rgba(200,220,240,.10)',tickfont:{color:'#d7e5f4',size:11}},yaxis:{gridcolor:'rgba(200,220,240,.14)',tickfont:{color:'#d7e5f4'}},yaxis2:{overlaying:'y',side:'right',range:[0,100],ticksuffix:'%',showgrid:false,tickfont:{color:'#fda4af'}},hoverlabel:{bgcolor:'#132c46',font:{color:'#f1f5fb'}}},{displayModeBar:false,responsive:true});
 renderTop('ag',rows,'tb-ag');
 renderTop('uf',rows,'tb-uf');
 renderLat(rows,rowsS);
 DATA.dia=serie.map(s=>{const pp=rowsS.filter(r=>r.d===s.d&&r.st==='P');return {...s,sync:s.log-s.pend-s.gru,pped:new Set(pp.map(r=>r.p)).size}});
 DATA.det=rows.filter(r=>r.st==='P'||r.st==='G'||r.st==='E').slice();
 paintDia();paintDet();
 if(TAB==='cobranca'||TAB==='tempo'||TAB==='erros') showTab(TAB);
 paintOcultos();
}
const DATA={dia:[],det:[],lat:[]};
const SORT={dia:{k:'d',dir:-1},det:{k:'d',dir:-1},lat:{k:'th',dir:-1},erros:{k:'d',dir:-1}};
function parseTs(s){if(!s)return 0;const m=String(s).match(/(\d{2})\/(\d{2})\/(\d{4})(?: (\d{2}):(\d{2}))?/);return m?Date.UTC(+m[3],+m[2]-1,+m[1],+(m[4]||0),+(m[5]||0)):0}
function valSort(row,k){
 if(k==='d')return row.d||'';
 if(k==='e'||k==='ch'||k==='s'||k==='emb'||k==='t0'||k==='fs')return parseTs(row[k]);
 if(k==='th')return Number(row.th||0);
 const v=row[k];
 if(typeof v==='number')return v;
 return String(v==null?'':v).toUpperCase();
}
function applySort(list,st){const {k,dir}=st;return list.slice().sort((a,b)=>{const va=valSort(a,k),vb=valSort(b,k);if(va<vb)return -1*dir;if(va>vb)return 1*dir;return 0})}
function markSort(tblId,st){document.querySelectorAll('#'+tblId+' th.sort').forEach(th=>{const on=th.dataset.k===st.k;th.classList.toggle('on',on);const ar=th.querySelector('.ar');if(ar)ar.textContent=on?(st.dir<0?'▾':'▴'):'↕'})}
function sortTbl(which,k){const st=SORT[which];if(st.k===k)st.dir*=-1;else{st.k=k;st.dir=-1}if(which==='dia')paintDia();else if(which==='lat')paintLat();else if(which==='erros')paintOcultos();else paintDet()}
function paintLat(){const all=DATA.lat||[];const rows=latGridRows();const list=applySort(rows,SORT.lat);markSort('tbl-lat',SORT.lat);
 const sc=document.getElementById('lat-slow-count');
 const mid=' '+String.fromCharCode(183)+' ';
 if(sc) sc.textContent='('+fmtInt(rows.length)+(rows.length!==all.length?mid+'de '+fmtInt(all.length)+' filtrados':mid+'todos')+' — cabeçalho só ordena)';
 const tb=document.getElementById('tb-lat-slow');
 if(tb) tb.innerHTML=list.map((r,i)=>'<tr><td>'+(i+1)+'</td><td>'+diaBR(r.d)+'</td><td>'+esc(r.p)+'</td><td>'+esc(r.l)+'</td><td>'+esc(r.uf)+'</td><td title="'+esc(r.ag)+'">'+esc(r.ag)+'</td><td>'+esc(r.t0||r.emb||'')+'</td><td>'+esc(r.fs||'')+'</td><td class="num">'+fmtHoras(r.th)+'</td></tr>').join('')||'<tr><td colspan="9" style="color:#a9c1da">Sem 1º sync de agente para os filtros</td></tr>';
}
function paintDia(){const list=applySort(DATA.dia,SORT.dia);markSort('tbl-dia',SORT.dia);
 document.getElementById('tb-dia').innerHTML=list.map(s=>'<tr'+(s.d===HOJE?' class="hoje"':'')+'><td>'+diaBR(s.d)+(s.d===HOJE?' (hoje)':'')+'</td><td class="num">'+fmtInt(s.ped)+'</td><td class="num">'+fmtInt(s.log)+'</td><td class="num"><span class="pill-ok">'+fmtInt(s.sync)+'</span></td><td class="num">'+(s.pend?'<span class="pill-pend">'+fmtInt(s.pend)+'</span>':'0')+'</td><td class="num">'+(s.gru?'<span class="pill-gru">'+fmtInt(s.gru)+'</span>':'0')+'</td><td class="num">'+(s.patio?'<span class="pill-patio">'+fmtInt(s.patio)+'</span>':'0')+'</td><td class="num">'+fmtPct(s.pct)+'</td><td class="num">'+fmtInt(s.pped)+'</td></tr>').join('')}
function sitPill(r){if(r.st==='G')return '<span class="pill-gru">'+esc(r.mot||'já retornou')+'</span>';if(r.st==='E')return '<span class="pill-patio">registro de entrega ausente</span>';return '<span class="pill-pend">pendente</span>'}
function cobPill(r){if(r.cob==='GRU')return '<span class="pill-gru">GRU</span>';if(r.cob==='AGENTE')return '<span class="pill-pend">Agente</span>';return '—'}
function sitTxt(r){if(r.st==='G')return r.mot||'já retornou';if(r.st==='E')return 'registro de entrega ausente';return 'pendente'}
function detFiltroAtivo(){return !!(FD.uf||FD.ag||FD.busca.trim()||FD.cob||FD.ro)}
function markDl(){const n=detRows().length;const mid=' '+String.fromCharCode(183)+' ';const suf=detFiltroAtivo()?mid+fmtInt(n)+' filtrados':mid+'todos ('+fmtInt(n)+')';const c=document.getElementById('btn-csv-det');const x=document.getElementById('btn-xlsx-det');if(c)c.textContent=String.fromCharCode(8595)+' CSV'+suf;if(x)x.textContent=String.fromCharCode(8595)+' XLSX'+suf}
function paintDet(){const rows=detRows();const list=applySort(rows,SORT.det);markSort('tbl-det',SORT.det);
 let lab='('+fmtInt(rows.length)+' logger(s)';
 if(FD.cob==='PATIO')lab+=' registro de entrega ausente';
 else if(FD.cob==='GRU')lab+=' cobrança em GRU';
 else if(FD.cob==='PG')lab+=' agentes + GRU';
 else lab+=' cobrança de agentes';
 if(rows.length>800)lab+=' — tela 800, download leva todos';
 document.getElementById('det-count').textContent=lab+')';
 const tit=document.getElementById('det-title');
 if(tit)tit.textContent=FD.cob==='PATIO'?'Registro de entrega ausente':FD.cob==='GRU'?'Pendentes de cobrança em GRU':FD.cob==='PG'?'Pendências de cobrança':'Pendência de cobrança de agentes';
 document.getElementById('tb-det').innerHTML=list.slice(0,800).map((r,i)=>'<tr><td>'+(i+1)+'</td><td>'+diaBR(r.d)+'</td><td>'+esc(r.p)+'</td><td>'+esc(r.ro||'')+'</td><td>'+esc(r.l)+'</td><td>'+esc(r.t)+'</td><td>'+esc(r.uf)+'</td><td title="'+esc(r.ag)+'">'+esc(r.ag)+'</td><td>'+cobPill(r)+'</td><td title="'+esc(r.cli)+'">'+esc(r.cli)+'</td><td>'+esc(r.mo)+'</td><td>'+(r.emb?esc(r.emb):'<span class="pill-patio">registro de entrega ausente</span>')+'</td><td>'+(r.s?esc(r.s):'<span class="pill-pend">nunca</span>')+'</td><td class="num">'+fmtInt(r.ds)+'</td><td>'+sitPill(r)+'</td></tr>').join('')||'<tr><td colspan="15" style="color:#a9c1da">'+(FD.cob==='PATIO'?'Sem linhas de pátio para os filtros':'Sem pendências para os filtros')+'</td></tr>';
 markDl();
}
function csvEscape(v){const s=String(v==null?'':v);return /[;"]/.test(s)||s.indexOf(String.fromCharCode(10))>=0?'"'+s.replace(/"/g,'""')+'"':s}
function diaMatrix(){const h=['Dia','Pedidos entregues','Loggers ARES','Sincronizados','Pendentes agente','Cobranca GRU','Registro de entrega ausente','% pendencia','Pedidos c/ pendencia'];return [h].concat(applySort(DATA.dia,SORT.dia).map(s=>[diaBR(s.d),s.ped,s.log,s.sync,s.pend,s.gru,s.patio,s.pct,s.pped]))}
function detMatrix(){const h=['#','Dia','Pedido','Romaneio','Logger','Tipo','UF','Agente','Cobranca','Cliente','Modal','Data de entrega','Ultimo sync','Dias sem sync','Situacao'];return [h].concat(applySort(detRows(),SORT.det).map((r,i)=>[i+1,diaBR(r.d),r.p,r.ro||'',r.l,r.t,r.uf,r.ag,r.cob||'',r.cli,r.mo,r.emb||'registro de entrega ausente',r.s||'nunca',r.ds,sitTxt(r)]))}
function downloadCsv(which){const m=which==='dia'?diaMatrix():detMatrix();const nl=String.fromCharCode(13,10);const body=m.map(row=>row.map(csvEscape).join(';')).join(nl);const blob=new Blob([String.fromCharCode(65279)+body],{type:'text/csv;charset=utf-8'});baixarBlob(blob,which==='dia'?'entregas_vs_sincronismo_resumo.csv':'entregas_vs_sincronismo_pendentes.csv')}
function downloadXlsx(which){if(!window.XLSX||!XLSX.writeFile){alert('Biblioteca XLSX indisponivel. Use o CSV.');return}const m=which==='dia'?diaMatrix():detMatrix();const wb=XLSX.utils.book_new();XLSX.utils.book_append_sheet(wb,XLSX.utils.aoa_to_sheet(m),which==='dia'?'Resumo':'Pendentes');XLSX.writeFile(wb,which==='dia'?'entregas_vs_sincronismo_resumo.xlsx':'entregas_vs_sincronismo_pendentes.xlsx')}
function renderLat(rows,rowsS){
 const lat=latRows(rows);
 const hrs=lat.map(r=>Number(r.th));
 const media=hrs.length?hrs.reduce((a,b)=>a+b,0)/hrs.length:null;
 const med=quantile(hrs,0.5);
 const p90=quantile(hrs,0.9);
 const sem=rows.filter(r=>r.saiu==='S'&&r.lk==='P').length;
 const soGru=rows.filter(r=>r.lk==='G').length;
 const hjLat=latRows(rows.filter(r=>r.d===HOJE));
 const hjHrs=hjLat.map(r=>Number(r.th));
 const box=document.getElementById('kpis-lat');
 if(box) box.innerHTML=
  '<div class="kpi ok"><div class="lbl">Tempo médio até o 1º sync</div><div class="val">'+fmtHoras(media)+'</div><div class="sub">hoje: '+fmtHoras(hjHrs.length?hjHrs.reduce((a,b)=>a+b,0)/hjHrs.length:null)+' &middot; só quem sincronizou no agente</div></div>'
 +'<div class="kpi ok"><div class="lbl">Mediana</div><div class="val">'+fmtHoras(med)+'</div><div class="sub">P90: '+fmtHoras(p90)+' &middot; n='+fmtInt(hrs.length)+'</div></div>'
 +'<div class="kpi"><div class="lbl">Sincronizaram no agente</div><div class="val">'+fmtInt(hrs.length)+'</div><div class="sub">entram na média &middot; 1º log ARES após a entrega</div></div>'
 +'<div class="kpi warn"><div class="lbl">Ainda sem 1º sync</div><div class="val">'+fmtInt(sem)+'</div><div class="sub">fora da média &middot; pendentes de sincronismo</div></div>'
 +'<div class="kpi gru"><div class="lbl">Só sincronizou em GRU</div><div class="val">'+fmtInt(soGru)+'</div><div class="sub">1º log depois do estoque/restauração — fora da média do agente</div></div>';
 const serie=DIAS.map(d=>{const hh=latRows(rowsS.filter(r=>r.d===d)).map(r=>Number(r.th));return {dia:diaBR(d).slice(0,5),n:hh.length,media:hh.length?hh.reduce((a,b)=>a+b,0)/hh.length:null,med:quantile(hh,0.5)}});
 if(window.Plotly&&document.getElementById('g-lat')) Plotly.react('g-lat',[
  {type:'bar',name:'Mediana (h)',x:serie.map(s=>s.dia),y:serie.map(s=>s.med),marker:{color:'#4ade80'},hovertemplate:'%{x}<br>Mediana: %{y:.1f} h<extra></extra>'},
  {type:'scatter',mode:'lines+markers',name:'Média (h)',x:serie.map(s=>s.dia),y:serie.map(s=>s.media),line:{color:'#8fc0ff',width:2},marker:{size:5},hovertemplate:'%{x}<br>Média: %{y:.1f} h<extra></extra>'}
 ],{paper_bgcolor:'rgba(0,0,0,0)',plot_bgcolor:'rgba(0,0,0,0)',font:{color:'#eaf2fb',family:'Inter,Segoe UI,sans-serif'},barmode:'group',bargap:.28,margin:{t:8,r:16,b:40,l:44},legend:{orientation:'h',y:-.22},xaxis:{gridcolor:'rgba(200,220,240,.10)',tickfont:{color:'#d7e5f4',size:11}},yaxis:{title:'horas',gridcolor:'rgba(200,220,240,.14)',tickfont:{color:'#d7e5f4'}},hoverlabel:{bgcolor:'#132c46',font:{color:'#f1f5fb'}}},{displayModeBar:false,responsive:true});
 renderLatTop('ag',rows,'tb-lat-ag');
 renderLatTop('uf',rows,'tb-lat-uf');
 DATA.lat=lat.slice();
 fillLat();
 paintLat();
}
function renderLatTop(campo,rows,tbId){
 const g={};latRows(rows).forEach(r=>{const k=r[campo]||'—';g[k]=g[k]||[];g[k].push(Number(r.th))});
 const arr=Object.entries(g).map(([k,vs])=>({k:k,n:vs.length,med:quantile(vs,0.5),media:vs.reduce((a,b)=>a+b,0)/vs.length,p90:quantile(vs,0.9)})).filter(x=>x.n>=5).sort((a,b)=>b.med-a.med||b.media-a.media).slice(0,10);
 const el=document.getElementById(tbId);
 if(el) el.innerHTML=arr.map((x,i)=>'<tr><td>'+(i+1)+'</td><td>'+esc(x.k)+'</td><td class="num">'+fmtInt(x.n)+'</td><td class="num">'+fmtHoras(x.med)+'</td><td class="num">'+fmtHoras(x.media)+'</td><td class="num">'+fmtHoras(x.p90)+'</td></tr>').join('')||'<tr><td colspan="6" style="color:#a9c1da">Poucos sincronizados no agente para ranquear</td></tr>';
}
function renderTop(campo,rows,tbId){
 const g={};rows.forEach(r=>{const k=r[campo]||'—';g[k]=g[k]||{ent:0,pend:0};if(r.saiu==='S')g[k].ent++;if(r.st==='P')g[k].pend++});
 const arr=Object.entries(g).map(([k,v])=>({k:k,ent:v.ent,pend:v.pend,pct:v.ent?v.pend/v.ent*100:0})).filter(x=>x.pend>0).sort((a,b)=>b.pend-a.pend||b.pct-a.pct).slice(0,10);
 const max=arr.length?arr[0].pend:1;
 document.getElementById(tbId).innerHTML=arr.map((x,i)=>'<tr><td>'+(i+1)+'</td><td>'+esc(x.k)+'</td><td class="num">'+fmtInt(x.ent)+'</td><td class="num"><span class="pill-pend">'+fmtInt(x.pend)+'</span></td><td class="num">'+fmtPct(x.pct)+'</td><td class="bar-cell"><div class="bar" style="width:'+Math.round(x.pend/max*100)+'%"></div><span class="bar-txt">&nbsp;</span></td></tr>').join('')||'<tr><td colspan="6" style="color:#a9c1da">Sem pendências para os filtros</td></tr>';
}
function paintHoje(){
 const prev=DIAHOJE.filter(r=>r.ph===1);
 const ent=DIAHOJE.filter(r=>r.st==='E');
 const falta=DIAHOJE.filter(r=>r.st==='F');
 const outra=DIAHOJE.filter(r=>r.st==='O');
 const pct=prev.length?((prev.length-falta.length)/prev.length*100):0;
 const k=document.getElementById('kpis-hoje');
 if(k)k.innerHTML=
  '<div class="kpi"><div class="lbl">Previstas para hoje</div><div class="val">'+fmtInt(prev.length)+'</div><div class="sub">pedidos com previsão de entrega hoje</div></div>'
 +'<div class="kpi ok"><div class="lbl">Entregues hoje</div><div class="val">'+fmtInt(ent.length)+'</div><div class="sub">independente de sincronismo</div></div>'
 +'<div class="kpi warn"><div class="lbl">Faltam entregar</div><div class="val">'+fmtInt(falta.length)+'</div><div class="sub">previstas para hoje, sem entrega lançada</div></div>'
 +'<div class="kpi"><div class="lbl">% concluído</div><div class="val">'+fmtPct(pct)+'</div><div class="sub">das previstas p/ hoje (conta entrega em qualquer data)</div></div>';
 const fc=document.getElementById('hj-falta-count');if(fc)fc.textContent='('+fmtInt(falta.length)+')';
 const ec=document.getElementById('hj-ent-count');if(ec)ec.textContent='('+fmtInt(ent.length)+')';
 const oc=document.getElementById('hj-outra-count');if(oc)oc.textContent='('+fmtInt(outra.length)+')';
 const linha=(r,i,comEnt)=>'<tr><td>'+(i+1)+'</td><td>'+esc(r.p)+'</td><td>'+esc(r.uf)+'</td><td title="'+esc(r.cli)+'">'+esc(r.cli)+'</td><td>'+esc(r.mo)+'</td><td>'+(r.prev?esc(r.prev):'—')+'</td>'+(comEnt?'<td>'+(r.ent?esc(r.ent):'—')+'</td>':'')+'<td class="num">'+fmtInt(r.lg)+'</td></tr>';
 const tf=document.getElementById('tb-hj-falta');
 if(tf)tf.innerHTML=falta.slice().sort((a,b)=>(a.prev||'99').localeCompare(b.prev||'99')||a.uf.localeCompare(b.uf)).map((r,i)=>linha(r,i,false)).join('')||'<tr><td colspan="7" style="color:#a9c1da">Nenhum pedido previsto para hoje pendente de entrega</td></tr>';
 const te=document.getElementById('tb-hj-ent');
 if(te)te.innerHTML=ent.slice().sort((a,b)=>(b.ent||'').localeCompare(a.ent||'')).map((r,i)=>linha(r,i,true)).join('')||'<tr><td colspan="8" style="color:#a9c1da">Nenhuma entrega lançada hoje até agora</td></tr>';
 const to=document.getElementById('tb-hj-outra');
 if(to)to.innerHTML=outra.slice().sort((a,b)=>a.uf.localeCompare(b.uf)||String(a.p).localeCompare(String(b.p))).map((r,i)=>linha(r,i,true)).join('')||'<tr><td colspan="8" style="color:#a9c1da">Nenhum</td></tr>';
}
function copiarLink(btn,url){
 if(navigator.clipboard&&navigator.clipboard.writeText){navigator.clipboard.writeText(url).then(function(){const t=btn.textContent;btn.textContent='Copiado!';setTimeout(function(){btn.textContent=t},1500)}).catch(function(){window.prompt('Copie o link:',url)})}
 else{window.prompt('Copie o link:',url)}
}
function paintBases(){
 const tb=document.getElementById('tb-bases');if(!tb)return;
 const list=BASESLINKS.slice().sort((a,b)=>b.n-a.n||String(a.base).localeCompare(String(b.base)));
 tb.innerHTML=list.map((x,i)=>'<tr><td>'+(i+1)+'</td><td title="'+esc(x.base)+'">'+esc(x.base)+'</td><td class="num">'+(x.n?'<span class="pill-pend">'+fmtInt(x.n)+'</span>':'<span class="pill-ok">0</span>')+'</td><td><a class="csv" style="text-decoration:none;margin-right:6px" href="'+esc(x.url)+'" target="_blank" rel="noopener">Abrir</a><button class="csv" type="button" onclick="copiarLink(this,\\''+esc(x.url)+'\\')">Copiar link</button></td></tr>').join('')||'<tr><td colspan="4" style="color:#a9c1da">Nenhuma página de base gerada ainda</td></tr>';
 const sc=document.getElementById('bases-count');
 const mid=' '+String.fromCharCode(183)+' ';
 if(sc)sc.textContent='('+list.length+' base(s)'+mid+fmtInt(list.reduce((a,b)=>a+Number(b.n||0),0))+' logger(s) pendente(s) agora)';
}
renderAll();
paintHoje();
paintBases();
</script></body></html>
"""


BASE_PAGE_TEMPLATE = """<!DOCTYPE html><html lang="pt-BR"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow">
<meta name="theme-color" content="#081527">
<title>Pendências de Sincronismo — __BASE__</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800;900&display=swap" rel="stylesheet">
<style>
:root{
 --bg1:#081527;--bg2:#0e2a47;--panel:rgba(20,48,79,.66);--panel2:rgba(13,34,58,.72);
 --line:rgba(140,180,220,.18);--line2:rgba(140,180,220,.32);
 --txt:#eaf2fb;--soft:#9db8d6;--blue:#3d8bfd;--cyan:#67e8f9;
 --pend:#fb7185;--warn:#fbbf24;--ok:#34d399
}
*{margin:0;padding:0;box-sizing:border-box}
body{min-height:100vh;background:
 radial-gradient(900px 480px at 12% -8%,rgba(61,139,253,.22) 0%,transparent 55%),
 radial-gradient(700px 420px at 98% 108%,rgba(103,232,249,.10) 0%,transparent 55%),
 linear-gradient(160deg,var(--bg1) 0%,var(--bg2) 100%);
 color:var(--txt);font-family:'Inter','Segoe UI',system-ui,sans-serif;
 padding:clamp(14px,2.6vw,30px) clamp(12px,3vw,34px) 42px;-webkit-font-smoothing:antialiased}
.brand{display:inline-flex;align-items:center;gap:10px;color:#8fc0ff;font-size:.64rem;font-weight:800;letter-spacing:.22em;text-transform:uppercase;margin-bottom:10px}
.brand::before{content:"";width:26px;height:3px;border-radius:2px;background:linear-gradient(90deg,var(--blue),var(--cyan))}
.top{display:flex;justify-content:space-between;align-items:flex-end;gap:12px;flex-wrap:wrap;margin-bottom:16px}
h1{font-size:clamp(1.2rem,5vw,1.75rem);font-weight:900;letter-spacing:-.015em;line-height:1.12}
h1 small{display:block;font-size:.56em;font-weight:600;color:var(--soft);margin-top:5px;letter-spacing:0}
.stamp{display:inline-flex;align-items:center;gap:8px;background:var(--panel);border:1px solid var(--line2);border-radius:999px;padding:8px 15px;font-size:.72rem;color:var(--soft);backdrop-filter:blur(8px);white-space:nowrap}
.stamp .dot{width:8px;height:8px;border-radius:50%;background:var(--ok);box-shadow:0 0 0 0 rgba(52,211,153,.6);animation:pulse 2.2s infinite}
@keyframes pulse{0%{box-shadow:0 0 0 0 rgba(52,211,153,.45)}70%{box-shadow:0 0 0 7px rgba(52,211,153,0)}100%{box-shadow:0 0 0 0 rgba(52,211,153,0)}}
.stamp b{color:var(--txt);font-weight:700}
.kpis{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-bottom:16px}
.kpi{background:var(--panel);border:1px solid var(--line);border-radius:16px;padding:14px 15px 12px;backdrop-filter:blur(8px);position:relative;overflow:hidden;box-shadow:0 10px 26px rgba(3,10,20,.35);text-align:center}
.kpi::before{content:"";position:absolute;top:0;left:0;right:0;height:3px;background:linear-gradient(90deg,var(--blue),var(--cyan));opacity:.55}
.kpi.bad::before{background:linear-gradient(90deg,#f43f5e,#fb7185);opacity:.8}
.kpi.warn::before{background:linear-gradient(90deg,#d97706,#fbbf24);opacity:.8}
.kpi.cy::before{background:linear-gradient(90deg,#0ea5b7,#67e8f9);opacity:.8}
.kpi .lbl{display:flex;align-items:center;justify-content:center;gap:7px;font-size:.62rem;font-weight:700;text-transform:uppercase;letter-spacing:.1em;color:var(--soft)}
.kpi .val{font-size:clamp(1.5rem,4.2vw,1.9rem);font-weight:900;margin-top:4px;letter-spacing:-.02em;color:#f4f8fd}
.kpi.bad .val{color:var(--pend)}.kpi.warn .val{color:var(--warn)}
.panel{background:var(--panel);border:1px solid var(--line);border-radius:18px;padding:16px clamp(12px,2vw,20px) 14px;backdrop-filter:blur(8px);box-shadow:0 14px 34px rgba(3,10,20,.4)}
.panel-hd{display:flex;justify-content:space-between;align-items:center;gap:10px;flex-wrap:wrap;margin-bottom:12px}
.panel h2{font-size:.92rem;font-weight:800;display:flex;align-items:center;gap:8px}
.badge{display:inline-flex;align-items:center;justify-content:center;min-width:26px;height:22px;padding:0 9px;border-radius:999px;background:rgba(251,113,133,.16);border:1px solid rgba(251,113,133,.45);color:var(--pend);font-size:.72rem;font-weight:800}
.dl button{display:inline-flex;align-items:center;gap:7px;background:linear-gradient(135deg,#2563c4,#3d8bfd);color:#fff;border:1px solid rgba(157,197,254,.45);border-radius:999px;padding:8px 18px;font-weight:700;font-size:.76rem;cursor:pointer;font-family:inherit;box-shadow:0 6px 16px rgba(37,99,196,.35);transition:filter .15s}
.dl button:hover{filter:brightness(1.12)}
.callout{display:flex;gap:12px;align-items:flex-start;background:linear-gradient(135deg,rgba(61,139,253,.16),rgba(103,232,249,.07));border:1px solid var(--line2);border-left:4px solid var(--blue);border-radius:14px;padding:14px 16px;margin-bottom:16px;backdrop-filter:blur(8px);box-shadow:0 10px 26px rgba(3,10,20,.3)}
.callout .ic{font-size:1.2rem;line-height:1.2;flex-shrink:0}
.callout p{font-size:clamp(.82rem,2.6vw,.92rem);line-height:1.6;color:#dcE9f7}
.callout b{color:#fff;font-weight:900;white-space:nowrap;border-bottom:2px solid var(--cyan);padding-bottom:1px}
.callout .seq{color:#cfe2f6;font-weight:600}
.callout .tx{display:flex;flex-direction:column;gap:9px;flex:1}
.callout.mini{border-left-color:var(--ok);background:linear-gradient(135deg,rgba(52,211,153,.13),rgba(103,232,249,.05));padding:10px 14px;margin-top:0}
.flow{background:transparent;border:0;padding:6px 4px 8px;margin:-4px 0 10px;text-align:center}
.flow-title{font-size:.62rem;font-weight:800;letter-spacing:.18em;text-transform:uppercase;color:#8fc0ff;margin-bottom:11px}
.flow-steps{display:flex;align-items:center;justify-content:center;flex-wrap:wrap;gap:8px}
.fstp{display:inline-flex;align-items:center;gap:8px;background:rgba(8,20,36,.62);border:1px solid var(--line2);border-radius:999px;padding:8px 14px;font-size:.76rem;font-weight:700;color:#eaf2fb;text-align:left}
.fstp .n{display:inline-flex;align-items:center;justify-content:center;width:20px;height:20px;border-radius:50%;background:linear-gradient(135deg,#2563c4,#3d8bfd);font-size:.64rem;font-weight:900;color:#fff;flex-shrink:0}
.fstp b{color:#fff;font-weight:900;border-bottom:2px solid var(--cyan);padding-bottom:1px}
.farr{font-size:1.5rem;font-weight:900;color:var(--cyan);line-height:1;text-shadow:0 0 12px rgba(103,232,249,.5)}
.farr.eq{font-size:1rem}
.flow-ok{display:inline-flex;align-items:center;gap:7px;background:rgba(52,211,153,.14);border:1px solid rgba(52,211,153,.5);color:#86efac;border-radius:999px;padding:8px 16px;font-size:.74rem;font-weight:800}
.callout.mini .ic{font-size:1rem}
.callout.mini p{font-size:clamp(.74rem,2.3vw,.84rem);color:#cfeee0}
.fbar{display:grid;grid-template-columns:minmax(170px,1.8fr) minmax(115px,1fr) minmax(110px,1fr) minmax(90px,.8fr) minmax(130px,1fr) auto;gap:8px;margin-bottom:12px}
.fbar input,.fbar select{width:100%;background:rgba(8,20,36,.55);border:1px solid var(--line2);border-radius:10px;color:var(--txt);font-family:inherit;font-size:.8rem;padding:9px 12px;outline:none;transition:border-color .15s}
.fbar input:focus,.fbar select:focus{border-color:var(--blue)}
.fbar input::placeholder{color:#7d99b8}
.fbar select option{background:#0e2a47;color:#eaf2fb}
.fbar .limpar{background:transparent;border:1px solid var(--line2);border-radius:10px;color:var(--soft);font-family:inherit;font-size:.78rem;font-weight:600;padding:9px 14px;cursor:pointer;transition:color .15s,border-color .15s}
.fbar .limpar:hover{color:var(--txt);border-color:var(--blue)}
@media(max-width:860px){.fbar{grid-template-columns:1fr 1fr}.fbar input{grid-column:1/-1}}
th.dias{white-space:normal;max-width:64px;line-height:1.25}
@media(max-width:1280px){table{font-size:.73rem}th{padding:8px 8px;font-size:.6rem}td{padding:7px 8px}}
.wrap{overflow:auto;max-height:64vh;border:1px solid var(--line);border-radius:12px}
table{width:100%;border-collapse:collapse;font-size:.78rem;white-space:nowrap}
th{position:sticky;top:0;z-index:1;background:#14304f;color:#cfe2f6;text-align:left;padding:9px 11px;font-size:.64rem;text-transform:uppercase;letter-spacing:.08em}
td{padding:8px 11px;border-top:1px solid rgba(140,180,220,.10)}
tbody tr:hover td{background:rgba(61,139,253,.07)}
td.num{text-align:right}
.pill{display:inline-block;background:rgba(251,113,133,.14);border:1px solid rgba(251,113,133,.45);color:var(--pend);border-radius:999px;padding:2px 11px;font-weight:700;font-size:.7rem}
.mono{font-weight:800;letter-spacing:.02em}
.ok-big{padding:34px 16px;text-align:center;color:#86efac;font-weight:700;font-size:.95rem}
.cards{display:none;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:10px}
.card{background:var(--panel2);border:1px solid var(--line);border-radius:16px;padding:14px 15px;box-shadow:0 8px 20px rgba(3,10,20,.3);position:relative;overflow:hidden}
.card::before{content:"";position:absolute;top:0;left:0;right:0;height:3px;background:linear-gradient(90deg,#f43f5e,#fb7185);opacity:.7}
.card .hd{display:flex;justify-content:space-between;align-items:center;gap:8px;margin-bottom:11px;padding-bottom:9px;border-bottom:1px solid var(--line)}
.card .lg{font-size:1.05rem;font-weight:900;letter-spacing:.02em}
.card .lg small{display:block;font-size:.6rem;font-weight:700;color:var(--soft);text-transform:uppercase;letter-spacing:.1em;margin-bottom:2px}
.card .dias{flex-shrink:0;background:rgba(251,113,133,.14);border:1px solid rgba(251,113,133,.45);color:var(--pend);border-radius:999px;padding:4px 12px;font-weight:800;font-size:.72rem;text-align:center;line-height:1.2}
.card .gd{display:grid;grid-template-columns:1fr 1fr;gap:9px 14px}
.card .f .k{font-size:.58rem;font-weight:700;text-transform:uppercase;letter-spacing:.1em;color:var(--soft);margin-bottom:2px}
.card .f .v{font-size:.84rem;font-weight:600;word-break:break-word}
.card .f.full{grid-column:1/-1}
.ok-card{background:var(--panel2);border:1px solid rgba(52,211,153,.35);border-radius:14px;padding:26px 16px;text-align:center;color:#86efac;font-weight:700}
.obs{color:var(--soft);font-size:.73rem;margin-top:12px;line-height:1.55}
.obs b{color:#cfe2f6}
footer{color:var(--soft);font-size:.68rem;margin-top:18px;text-align:center;letter-spacing:.04em}
@media(max-width:860px){
 .wrap{display:none}
 .cards{display:grid}
 .kpis{grid-template-columns:repeat(2,1fr)}
 .top{align-items:flex-start}
 .stamp{font-size:.66rem;padding:7px 12px}
}
</style></head><body>
<div class="brand">VTC LOG &middot; Gerenciamento Térmico</div>
<div class="top">
  <h1>__BASE__<small>Pendências de sincronismo dos dataloggers</small></h1>
  <div class="stamp"><span class="dot"></span>Atualizado em <b>__GERADO__</b></div>
</div>
<div class="kpis" id="kpis"></div>
<div class="callout"><span class="ic">&#9888;&#65039;</span><div class="tx">
<p>Pendências por: Ausência de sincronismo. Datalogger descarregado. Lançamento incorreto no Aplicativo de entregas (após a entrega real) <span style="color:var(--cyan);font-weight:800">&rarr;</span> por erro operacional ou BUG no app de entregas.</p>
</div></div>
<div class="flow">
  <div class="flow-title">Sequência correta</div>
  <div class="flow-steps">
    <span class="fstp"><span class="n">1</span>Chegou no cliente</span>
    <span class="farr">&rarr;</span>
    <span class="fstp"><span class="n">2</span>Lançou no aplicativo de entregas</span>
    <span class="farr">&rarr;</span>
    <span class="fstp"><span class="n">3</span>Sincronizou, ainda no cliente</span>
    <span class="farr eq">=</span>
    <span class="flow-ok">&#10003;&nbsp;Não gera pendência</span>
  </div>
</div>
<div class="callout mini"><span class="ic">&#128260;</span><p>Sincronizou agora? A pendência sai sozinha na próxima atualização automática dessa página.</p></div>
<div class="panel">
<div class="panel-hd">
  <h2>Loggers pendentes <span class="badge" id="cnt"></span></h2>
  <div class="dl"><button type="button" onclick="baixar()">&#8595;&nbsp;Baixar CSV</button></div>
</div>
<div class="fbar">
  <input id="f-busca" placeholder="&#128269; Buscar pedido, logger ou romaneio..." oninput="apply()">
  <select id="f-pedido" onchange="apply()"><option value="">Todos os pedidos</option></select>
  <select id="f-rom" onchange="apply()"><option value="">Todos os romaneios</option></select>
  <select id="f-uf" onchange="apply()"><option value="">Todas UF</option></select>
  <select id="f-ord" onchange="apply()">
    <option value="rec">Mais recentes</option>
    <option value="ant">Mais antigos</option>
    <option value="dias">Mais dias sem sync</option>
  </select>
  <button type="button" class="limpar" onclick="limpar()">Limpar</button>
</div>
<div class="wrap"><table><thead><tr>
<th>#</th><th>Dia</th><th>Pedido</th><th>Romaneio</th><th>Logger</th><th>Tipo</th><th>UF</th><th>Cliente</th><th>Data de entrega</th><th>Último sync</th><th class="num dias">Dias sem sync</th>
</tr></thead><tbody id="tb"></tbody></table></div>
<div class="cards" id="cards"></div>
</div>
<footer>VTC LOG &middot; Gerenciamento Térmico &middot; página exclusiva desta base &middot; atualização automática</footer>
<script>
const ROWS=__ROWS__;
function esc(v){return String(v==null?'':v).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
function diaBR(iso){return iso?iso.slice(8,10)+'/'+iso.slice(5,7)+'/'+iso.slice(0,4):''}
function fmtInt(n){return Number(n||0).toLocaleString('pt-BR')}
const peds=new Set(ROWS.map(r=>r.p));const dias=new Set(ROWS.map(r=>r.d));
const maisAntigo=ROWS.length?ROWS.map(r=>r.d).sort()[0]:null;
document.getElementById('kpis').innerHTML=
 '<div class="kpi bad"><div class="lbl">Loggers pendentes</div><div class="val">'+fmtInt(ROWS.length)+'</div></div>'
+'<div class="kpi warn"><div class="lbl">Pedidos afetados</div><div class="val">'+fmtInt(peds.size)+'</div></div>'
+'<div class="kpi"><div class="lbl">Dias com pendência</div><div class="val">'+fmtInt(dias.size)+'</div></div>'
+'<div class="kpi cy"><div class="lbl">Mais antiga</div><div class="val" style="font-size:clamp(1rem,3.4vw,1.25rem)">'+(maisAntigo?diaBR(maisAntigo):'—')+'</div></div>';
const selPed=document.getElementById('f-pedido');
const selRom=document.getElementById('f-rom');
const selUf=document.getElementById('f-uf');
[...new Set(ROWS.map(r=>String(r.p)))].sort().forEach(function(p){const o=document.createElement('option');o.value=p;o.textContent=p;selPed.appendChild(o)});
[...new Set(ROWS.map(r=>String(r.ro||'')).filter(Boolean))].sort().forEach(function(v){const o=document.createElement('option');o.value=v;o.textContent=v;selRom.appendChild(o)});
[...new Set(ROWS.map(r=>String(r.uf||'')).filter(Boolean))].sort().forEach(function(v){const o=document.createElement('option');o.value=v;o.textContent=v;selUf.appendChild(o)});
let VIEW=ROWS.slice();
function apply(){
 const b=document.getElementById('f-busca').value.trim().toUpperCase();
 const ped=selPed.value;
 const rom=selRom.value;
 const uf=selUf.value;
 const ord=document.getElementById('f-ord').value;
 VIEW=ROWS.filter(function(r){
  if(ped&&String(r.p)!==ped)return false;
  if(rom&&String(r.ro||'')!==rom)return false;
  if(uf&&String(r.uf||'')!==uf)return false;
  if(b&&!(String(r.p).toUpperCase().includes(b)||String(r.l||'').toUpperCase().includes(b)||String(r.ro||'').toUpperCase().includes(b)))return false;
  return true});
 VIEW.sort(function(a,b2){
  if(ord==='dias')return Number(b2.ds||0)-Number(a.ds||0)||String(a.l).localeCompare(String(b2.l));
  const c=String(a.d||'').localeCompare(String(b2.d||''));
  return (ord==='ant'?c:-c)||String(a.l).localeCompare(String(b2.l))});
 render();
}
function limpar(){document.getElementById('f-busca').value='';selPed.value='';selRom.value='';selUf.value='';document.getElementById('f-ord').value='rec';apply()}
function render(){
 const filtrado=VIEW.length!==ROWS.length;
 document.getElementById('cnt').textContent=filtrado?fmtInt(VIEW.length)+' de '+fmtInt(ROWS.length):fmtInt(ROWS.length);
 document.getElementById('tb').innerHTML=VIEW.map((r,i)=>'<tr><td>'+(i+1)+'</td><td>'+diaBR(r.d)+'</td><td>'+esc(r.p)+'</td><td>'+esc(r.ro||'')+'</td><td class="mono">'+esc(r.l)+'</td><td>'+esc(r.t)+'</td><td>'+esc(r.uf)+'</td><td title="'+esc(r.cli)+'">'+esc(r.cli)+'</td><td>'+esc(r.emb||'')+'</td><td>'+(r.s?esc(r.s):'nunca')+'</td><td class="num">'+fmtInt(r.ds)+'</td></tr>').join('')||'<tr><td colspan="11" class="ok-big">'+(ROWS.length?'Nenhum resultado para os filtros.':'Sem pendências de sincronismo nesta base. Bom trabalho! &#10003;')+'</td></tr>';
 document.getElementById('cards').innerHTML=VIEW.map(function(r,i){return '<div class="card">'
 +'<div class="hd"><div class="lg"><small>Logger &middot; '+esc(r.t)+'</small>'+esc(r.l)+'</div><div class="dias">'+fmtInt(r.ds)+' dia(s)<br>sem sync</div></div>'
 +'<div class="gd">'
 +'<div class="f"><div class="k">Pedido</div><div class="v">'+esc(r.p)+'</div></div>'
 +'<div class="f"><div class="k">Romaneio</div><div class="v">'+esc(r.ro||'—')+'</div></div>'
 +'<div class="f"><div class="k">Dia</div><div class="v">'+diaBR(r.d)+'</div></div>'
 +'<div class="f"><div class="k">UF</div><div class="v">'+esc(r.uf)+'</div></div>'
 +'<div class="f"><div class="k">Data de entrega</div><div class="v">'+esc(r.emb||'—')+'</div></div>'
 +'<div class="f"><div class="k">Último sync</div><div class="v">'+(r.s?esc(r.s):'nunca')+'</div></div>'
 +'<div class="f full"><div class="k">Cliente</div><div class="v">'+esc(r.cli)+'</div></div>'
 +'</div></div>'}).join('')||'<div class="ok-card">'+(ROWS.length?'Nenhum resultado para os filtros.':'Sem pendências de sincronismo nesta base. Bom trabalho! &#10003;')+'</div>';
}
function csvEsc(v){const s=String(v==null?'':v);return /[;"]/.test(s)?'"'+s.replace(/"/g,'""')+'"':s}
function baixar(){const h=['#','Dia','Pedido','Romaneio','Logger','Tipo','UF','Cliente','Data de entrega','Ultimo sync','Dias sem sync'];const m=[h].concat(VIEW.map((r,i)=>[i+1,diaBR(r.d),r.p,r.ro||'',r.l,r.t,r.uf,r.cli,r.emb||'',r.s||'nunca',r.ds]));const nl=String.fromCharCode(13,10);const blob=new Blob([String.fromCharCode(65279)+m.map(x=>x.map(csvEsc).join(';')).join(nl)],{type:'text/csv;charset=utf-8'});const a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download='pendencias_sincronismo_base.csv';a.style.display='none';document.body.appendChild(a);a.click();setTimeout(function(){document.body.removeChild(a);URL.revokeObjectURL(a.href)},3000)}
apply();
</script></body></html>
"""


def _slug_base(nome: str) -> str:
    slug = re.sub(r"[^A-Z0-9]+", "_", str(nome or "").upper()).strip("_")
    return slug[:30] or "SEM_AGENTE"


def _esc_html(value: str) -> str:
    return (str(value or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def write_base_pages(model: dict) -> list[dict]:
    """Gera uma pagina isolada por base (so pendencias da propria base), mesma fonte."""
    pend = [r for r in model["ares_rows"] if r.get("st") == "P"]
    por_base: dict[str, list] = {}
    for rec in pend:
        por_base.setdefault(str(rec.get("ag") or "SEM AGENTE"), []).append(rec)
    try:
        tokens = json.loads(BASES_TOKENS_LOCAL.read_text(encoding="utf-8")) if BASES_TOKENS_LOCAL.exists() else {}
    except Exception:
        tokens = {}
    novos = False
    for base in por_base:
        if base not in tokens:
            tokens[base] = secrets.token_hex(4)
            novos = True
    if novos:
        BASES_TOKENS_LOCAL.write_text(json.dumps(tokens, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    if not tokens:
        print("Paginas por base: nenhuma base com pendencia ainda; nada gerado")
        return []
    BASES_DIR.mkdir(exist_ok=True)
    gerado = fmt_ts(model["pagina_em"])
    links: list[tuple[str, int, str]] = []
    for base in sorted(tokens):
        rows = sorted(
            por_base.get(base, []),
            key=lambda r: (r.get("d") or "", str(r.get("p") or ""), str(r.get("l") or "")),
        )
        fname = f"base_{_slug_base(base)}_{tokens[base]}.html"
        html = (
            BASE_PAGE_TEMPLATE
            .replace("__BASE__", _esc_html(base))
            .replace("__GERADO__", gerado)
            .replace("__ROWS__", json.dumps(rows, ensure_ascii=False))
        )
        (BASES_DIR / fname).write_text(html, encoding="utf-8")
        links.append((base, len(rows), BASES_URL + fname))
    try:
        with open(BASES_LINKS_CSV, "w", newline="", encoding="utf-8-sig") as fh:
            writer = csv.writer(fh, delimiter=";")
            writer.writerow(["base", "pendencias_agora", "url"])
            writer.writerows(links)
    except PermissionError:
        print(f"AVISO: {BASES_LINKS_CSV.name} aberto em outro programa; lista de links nao regravada")
    print(
        f"Paginas por base: {len(links)} arquivos em {BASES_DIR.name}/ | "
        f"bases com pendencia agora: {len(por_base)} | links em {BASES_LINKS_CSV.name}"
    )
    return [{"base": base, "n": n, "url": url} for base, n, url in links]


def write_outputs(model: dict) -> None:
    pagina_txt = fmt_ts(model["pagina_em"])
    janela_txt = f"{model['cutoff']:%d/%m/%Y} a {pagina_txt[:10]} ({WINDOW_DAYS} dias)"
    dias_iso = [d["dia_iso"] for d in model["daily"]]
    bases_links = write_base_pages(model)
    html = (
        TEMPLATE.replace("__PAGINA_EM__", pagina_txt)
        .replace("__JANELA__", janela_txt)
        .replace("__PEDIDOS__", json.dumps(model["pedidos_rows"], ensure_ascii=False))
        .replace("__ROWS__", json.dumps(model["ares_rows"], ensure_ascii=False))
        .replace("__OCULTOS__", json.dumps(model.get("ocultos_rows") or [], ensure_ascii=False))
        .replace("__HOJE__", json.dumps(model["hoje_iso"]))
        .replace("__DIAS__", json.dumps(dias_iso))
        .replace("__DIAHOJE__", json.dumps(model.get("dia_hoje") or [], ensure_ascii=False))
        .replace("__BASESLINKS__", json.dumps(bases_links, ensure_ascii=False))
    )
    OUT_HTML.write_text(html, encoding="utf-8")

    serie = pd.DataFrame(model["daily"])
    serie = serie.rename(
        columns={
            "dia_full": "Dia",
            "pedidos": "Pedidos entregues",
            "loggers": "Loggers ARES entregues",
            "sincronizados": "Sincronizados",
            "pendentes": "Pendentes agente",
            "cobranca_gru": "Cobranca GRU",
            "sem_embarque": "Registro de entrega ausente",
            "pct": "% pendência",
            "pedidos_pendentes": "Pedidos com pendência",
            "n_latencia": "N latencia agente",
            "media_horas": "Media horas ate 1o sync",
            "mediana_horas": "Mediana horas ate 1o sync",
            "p90_horas": "P90 horas ate 1o sync",
            "n_sync_so_gru": "So sincronizou em GRU",
            "n_sem_primeiro_sync": "Sem 1o sync",
        }
    )
    csv_cols = [
        "Dia",
        "Pedidos entregues",
        "Loggers ARES entregues",
        "Sincronizados",
        "Pendentes agente",
        "Cobranca GRU",
        "Registro de entrega ausente",
        "% pendência",
        "Pedidos com pendência",
        "N latencia agente",
        "Media horas ate 1o sync",
        "Mediana horas ate 1o sync",
        "P90 horas ate 1o sync",
        "So sincronizou em GRU",
        "Sem 1o sync",
    ]
    tmp_csv = OUT_CSV.with_name(OUT_CSV.name + ".__new__")
    last_err: Exception | None = None
    for _attempt in range(6):
        try:
            serie[csv_cols].to_csv(tmp_csv, index=False, encoding="utf-8-sig", sep=";")
            os.replace(tmp_csv, OUT_CSV)
            last_err = None
            break
        except PermissionError as exc:
            last_err = exc
            time.sleep(0.5)
    if last_err is not None:
        print(f"AVISO: CSV travado ({OUT_CSV.name}); HTML ja foi gerado. Feche o arquivo se estiver aberto no Excel.")
        if tmp_csv.exists():
            tmp_csv.unlink(missing_ok=True)

    manifesto = {
        "gerado_em": model["pagina_em"].strftime("%Y-%m-%dT%H:%M:%S"),
        "status": "VALIDADO" if not model["erros"] else "VALIDADO_COM_ERROS",
        "janela_dias": WINDOW_DAYS,
        "regras": {
            "entregue": "vtc_stage.documentos.dt_entregaefetivaembarque no dia (BRT), 1 linha por pedido+logger",
            "saiu_gru": "dt_entregaefetivaembarque preenchida E nr_romaneio preenchido. Sem romaneio e trava: nao saiu. Romaneio sozinho nao prova entrega",
            "pendente_sync": "ARES sem lastSyncDate >= entrega efetiva embarque - 15 min (nem >= chegada - 15 min, so se chegada < embarque)",
            "cobranca_agente": "pendente de sync + saiu + nao esta Em GRU agora + sem estoque/restauracao depois desta entrega efetiva embarque",
            "cobranca_gru": "pendente de sync + saiu + (Em GRU agora OU estoque/restauracao depois desta entrega efetiva embarque). Linha permanece visivel; nao some. Nao usa retornando nem restauracao no mesmo dia antes da entrega",
            "sem_embarque": "ARES sem dt_entregaefetivaembarque OU sem nr_romaneio fica fora da cobranca (patio / trava)",
            "escopo_sync": "somente ARES/ARES COM SONDA (tipo do cadastro dtbPortal ds_tipodatalogger; fallback padrao de tag A/TA/AS e numerico 07417…/V7417…); demais tipos nao avaliados",
            "agente": "dtbTransporte tbdMovimento.nr_Referencia/ds_Agente (view ESL); fallback Reversa por pedido",
            "chave_logger": "1 logger por dia (pedido+logger ja e unico na origem; varios produtos/pedidos na mesma caixa nao repetem o aparelho)",
            "romaneio": "nr_romaneio e trava de saida: sem romaneio o pedido nao saiu. Com romaneio ainda precisa de embarque para cobrar; romaneio sozinho nao prova entrega",
            "latencia_t0": "T0 = dt_chegadacliente so se for anterior a dt_entregaefetivaembarque; senao embarque (BRT). Cobre lancamento atrasado. So quem saiu. Patio fora",
            "latencia_primeiro_sync": "menor createdAt em ares-prod.beacon_devices_logs >= T0 (BRT). Sem tolerancia de 15 min (essa vale so na classificacao pendente). Nao usa lastSyncDate",
            "latencia_media": "media/mediana/P90 so de quem teve 1o sync no agente (antes de estoque/restauracao depois deste embarque). Pendente e so-GRU ficam fora da media",
            "ocultos_operacao": "pedido+logger+dia em ocultos_operacao_entregas_vs.json sai da cobranca, da serie e do tempo. Nao muda regra. Aba Erros operacionais",
        },
        "contagens": model["stats"],
        "serie_diaria": model["daily"],
        "erros_validacao": model["erros"],
        "fontes": [
            "vtc_stage.documentos",
            "mongo ares-prod lastSyncDate",
            "mongo ares-prod beacon_devices_logs (1o sync apos T0)",
            "dtbTransporte tbdMovimento (agente)",
            "dtbPortal vwTabelaMovDataloggers (Em GRU agora)",
            "dtbPortal tbddataloggerhistoricos (estoque/restauracao depois da entrega)",
            "REVERSA_DATALOGGERS.html (agente fallback)",
            "ocultos_operacao_entregas_vs.json (excecao pontual)",
        ],
        "arquivos": [OUT_HTML.name, OUT_CSV.name],
        "ocultos_operacao": [
            {"pedido": r.get("p"), "logger": r.get("l"), "dia": r.get("d"), "motivo": r.get("ox")}
            for r in (model.get("ocultos_rows") or [])
        ],
    }
    OUT_MANIFEST.write_text(json.dumps(manifesto, ensure_ascii=False, indent=2), encoding="utf-8")


def rebuild_daily_from_rows(
    ares_rows: list[dict], pedidos_by_dia: dict[str, int], cutoff, hoje
) -> list[dict]:
    dias = pd.date_range(cutoff, hoje, freq="D")
    by_dia: dict[str, list] = {}
    for rec in ares_rows:
        by_dia.setdefault(rec["d"], []).append(rec)
    daily = []
    for dia in dias:
        iso = dia.strftime("%Y-%m-%d")
        ares_dia = by_dia.get(iso, [])
        pend_dia = [r for r in ares_dia if r["st"] == "P"]
        gru_dia = [r for r in ares_dia if r["st"] == "G"]
        patio_n = sum(1 for r in ares_dia if r["st"] == "E")
        loggers_n = sum(1 for r in ares_dia if r["st"] != "E")
        pend_n = len(pend_dia)
        lat_dia = [float(r["th"]) for r in ares_dia if r.get("lk") == "A" and r.get("th") is not None]
        daily.append(
            {
                "dia_iso": iso,
                "dia": dia.strftime("%d/%m"),
                "dia_full": dia.strftime("%d/%m/%Y"),
                "pedidos": int(pedidos_by_dia.get(iso, 0)),
                "loggers": loggers_n,
                "sincronizados": sum(1 for r in ares_dia if r["st"] == "S"),
                "pendentes": pend_n,
                "sem_embarque": patio_n,
                "cobranca_gru": len(gru_dia),
                "pedidos_pendentes": len({r["p"] for r in pend_dia}),
                "pct": round(pend_n / loggers_n * 100, 1) if loggers_n else 0.0,
                "n_latencia": len(lat_dia),
                "media_horas": round(sum(lat_dia) / len(lat_dia), 2) if lat_dia else None,
                "mediana_horas": _quant(lat_dia, 0.5),
                "p90_horas": _quant(lat_dia, 0.9),
                "n_sync_so_gru": sum(1 for r in ares_dia if r.get("lk") == "G"),
                "n_sem_primeiro_sync": sum(1 for r in ares_dia if r.get("lk") == "P"),
            }
        )
    return daily


def rebuild_from_published() -> None:
    """Reaplica ocultos no HTML atual, sem extrair Mongo de novo."""
    html = OUT_HTML.read_text(encoding="utf-8")
    m = re.search(
        r"const PEDIDOS=(\[.*?\]);const ROWS=(\[.*?\]);(?:const OCULTOS=(\[.*?\]);)?(?:const OCORRENCIAS_SEED=(\[.*?\]);)?const HOJE=(\"[^\"]*\");const DIAS=(\[.*?\]);(?:const DIAHOJE=(\[.*?\]);)?",
        html,
    )
    if not m:
        raise RuntimeError("HTML publicado sem PEDIDOS/ROWS")
    pedidos_rows = json.loads(m.group(1))
    ares_rows = json.loads(m.group(2))
    ja_ocultos = json.loads(m.group(3) or "[]")
    hoje_iso = json.loads(m.group(5))
    if ja_ocultos:
        ares_rows = list(ares_rows) + list(ja_ocultos)
        for rec in ares_rows:
            rec.pop("ox", None)
    manifesto = json.loads(OUT_MANIFEST.read_text(encoding="utf-8")) if OUT_MANIFEST.exists() else {}
    hoje = pd.Timestamp(hoje_iso)
    cutoff = hoje - pd.Timedelta(days=WINDOW_DAYS - 1)
    ped_by_dia = {d["dia_iso"]: int(d["pedidos"]) for d in manifesto.get("serie_diaria") or []}
    if not ped_by_dia:
        tmp: dict[str, set] = {}
        for p in pedidos_rows:
            tmp.setdefault(p["d"], set()).add(p["p"])
        ped_by_dia = {k: len(v) for k, v in tmp.items()}
    ares_rows, ocultos_rows, oc_stats = aplicar_ocultos_operacao(ares_rows)
    daily = rebuild_daily_from_rows(ares_rows, ped_by_dia, cutoff, hoje)
    pend_rows = [r for r in ares_rows if r["st"] == "P"]
    gru_rows = [r for r in ares_rows if r["st"] == "G"]
    base = dict(manifesto.get("contagens") or {})
    base["loggers_ares_janela"] = len(ares_rows)
    base["pendentes_janela"] = len(pend_rows)
    base["cobranca_gru_janela"] = len(gru_rows)
    base["ocultos_operacao"] = len(ocultos_rows)
    base["ocultos_lista"] = oc_stats["lista"]
    base["ocultos_ausentes"] = oc_stats["ausentes"]
    base["top10_agentes_pendencias"] = dict(Counter(r["ag"] for r in pend_rows).most_common(10))
    base["top10_ufs_pendencias"] = dict(Counter(r["uf"] for r in pend_rows).most_common(10))
    lat = [float(r["th"]) for r in ares_rows if r.get("lk") == "A" and r.get("th") is not None]
    base["latencia"] = {
        "n_agente": sum(1 for r in ares_rows if r.get("lk") == "A"),
        "n_so_gru": sum(1 for r in ares_rows if r.get("lk") == "G"),
        "n_sem_primeiro_sync": sum(1 for r in ares_rows if r.get("lk") == "P"),
        "media_horas": round(sum(lat) / len(lat), 2) if lat else None,
        "mediana_horas": _quant(lat, 0.5),
        "p90_horas": _quant(lat, 0.9),
    }
    write_outputs(
        {
            "pagina_em": now_brt(),
            "hoje_iso": hoje_iso,
            "cutoff": cutoff,
            "daily": daily,
            "pedidos_rows": pedidos_rows,
            "ares_rows": ares_rows,
            "ocultos_rows": ocultos_rows,
            "dia_hoje": json.loads(m.group(7) or "[]"),
            "erros": {},
            "stats": base,
        }
    )
    print(
        f"REBUILD ocultos={len(ocultos_rows)} pendentes={len(pend_rows)} "
        f"ausentes={oc_stats['ausentes']}"
    )


def main() -> None:
    model = build()
    write_outputs(model)
    hoje = next((d for d in model["daily"] if d["dia_iso"] == model["hoje_iso"]), None)
    if hoje:
        print(
            f"HOJE {hoje['dia_full']}: pedidos={hoje['pedidos']} loggersARES={hoje['loggers']} "
            f"pendentes_agente={hoje['pendentes']} cobranca_gru={hoje.get('cobranca_gru', 0)} "
            f"ocultos={model['stats'].get('ocultos_operacao', 0)} "
            f"patio={hoje.get('sem_embarque', 0)} ({hoje['pct']}%) "
            f"latencia_mediana={hoje.get('mediana_horas')}h n={hoje.get('n_latencia')}"
        )
    print(f"STATUS {'VALIDADO' if not model['erros'] else 'VALIDADO_COM_ERROS'}")
    print(f"HTML: {OUT_HTML}")
    print(f"CSV: {OUT_CSV}")


if __name__ == "__main__":
    main()
