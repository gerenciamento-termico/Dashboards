"""Gera REVERSA_DATALOGGERS.html replicando fielmente o dasboard_reversa_loggers.py"""
from __future__ import annotations
from pathlib import Path
from html import escape
import unicodedata
import json
from datetime import datetime

import pandas as pd

WORKSPACE    = Path(__file__).resolve().parents[1]
SNAPSHOT_DIR = WORKSPACE / "snapshot_reversa"
OUTPUT_FILE  = Path(__file__).resolve().parent / "REVERSA_DATALOGGERS.html"
PERIODOS     = [7, 30, 60, 90]
PERIODO_PAD  = 30
PENDING_AGENT_LABEL = "AGENTE PENDENTE (SEM DADOS)"
TABLE_MAX_ROWS = 50
TABLE_HEADERS = [
    "Pedido", "Logger", "Tipo Datalogger", "UF", "Data de Entrega",
    "Ultimo Historico", "Agente", "Status Retorno", "UF Destino",
    "Cidade Destino", "Destinatario", "Motorista",
]

TIPO_DATALOGGER_BY_CODE = {
    1: "ARES", 2: "ARES COM SONDA", 3: "SHIELD", 4: "SENSOR WEB", 5: "SYOS",
}


# ── helpers ───────────────────────────────────────────────────────────────────

def normalize_key(s: pd.Series) -> pd.Series:
    return s.fillna("").astype(str).str.strip().str.upper()

def normalize_text_value(v) -> str:
    s = str(v) if v is not None else ""
    s = "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")
    return s.strip().upper()

def first_non_blank(s: pd.Series) -> str:
    vals = s.dropna().astype(str).str.strip()
    vals = vals[vals != ""]
    return vals.iloc[0] if not vals.empty else ""

def join_unique_non_blank(s: pd.Series) -> str:
    vals = s.dropna().astype(str).str.strip()
    vals = [v for v in vals if v != ""]
    uniq = list(dict.fromkeys(vals))
    return " | ".join(uniq)

def fmt_int(n) -> str:
    return f"{int(n):,}".replace(",", ".")

def fmt_pct(v: float) -> str:
    return f"{v:.1f}%".replace(".", ",")

def safe_pct(num: int, den: int) -> float:
    return 0.0 if den <= 0 else (num / den) * 100.0

def _pedido_logger_key(df: pd.DataFrame) -> pd.Series:
    return normalize_key(df["Pedido"]) + "|" + normalize_key(df["Logger"])

def dedupe_pedido_logger(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df.copy()
    out = df.copy()
    out["Pedido"] = out["Pedido"].fillna("").astype(str).str.strip()
    out["Logger"] = out["Logger"].fillna("").astype(str).str.strip()
    out = out[out["Pedido"].ne("") & out["Logger"].ne("")].copy()
    out["_pedido_logger_key"] = _pedido_logger_key(out)
    out["_status_rank"] = out["Status Retorno"].eq("Retornado").astype(int)
    out["_hist_sort"] = pd.to_datetime(out.get("Ultimo_Historico"), errors="coerce")
    out["_entrega_sort"] = pd.to_datetime(out.get("_data_entrega_dt", out.get("Data de Entrega")), errors="coerce")
    out = out.sort_values(
        ["_pedido_logger_key", "_status_rank", "_hist_sort", "_entrega_sort"],
        ascending=[True, False, False, False],
        kind="mergesort",
    )
    out = out.drop_duplicates("_pedido_logger_key", keep="first")
    return out.drop(columns=["_pedido_logger_key", "_status_rank", "_hist_sort", "_entrega_sort"])

def infer_tipo_datalogger(series: pd.Series) -> pd.Series:
    logger = series.fillna("").astype(str).str.strip().str.upper()
    tipo = pd.Series("", index=series.index, dtype="object")
    tipo.loc[logger.str.startswith("AS")] = "ARES COM SONDA"
    tipo.loc[logger.str.startswith(("TA", "A"))] = "ARES"
    tipo.loc[logger.str.startswith("S")] = "SYOS"
    tipo.loc[logger.str.startswith(("V", "B"))] = "SHIELD"
    tipo.loc[logger.str.match(r"^\d", na=False)] = "SENSOR WEB"
    return tipo

def resolve_tipo_datalogger(df: pd.DataFrame, logger_col: str, code_col: str = "tp_datalogger") -> pd.Series:
    tipo = pd.Series("", index=df.index, dtype="object")
    if code_col in df.columns:
        code = pd.to_numeric(df[code_col], errors="coerce")
        tipo = code.map(TIPO_DATALOGGER_BY_CODE).fillna("").astype(str)
    if logger_col in df.columns:
        inferred = infer_tipo_datalogger(df[logger_col])
        tipo = tipo.mask(tipo.eq(""), inferred)
    return tipo


# ── model ─────────────────────────────────────────────────────────────────────

def build_model(base_loggers, base_agentes, recebimento, base_destinatarios) -> pd.DataFrame:
    for col in ["nr_pedido", "cd_referencia"]:
        base_loggers[col] = normalize_key(base_loggers[col])
    base_loggers = base_loggers[
        base_loggers["nr_pedido"].ne("") & base_loggers["cd_referencia"].ne("")
    ].copy()
    base_agentes["PEDIDO"]       = normalize_key(base_agentes["PEDIDO"])
    base_destinatarios["PEDIDO"] = normalize_key(base_destinatarios["PEDIDO"])
    recebimento["ds_tag"]        = normalize_key(recebimento["ds_tag"])
    if "MOTORISTA" not in base_agentes.columns:
        base_agentes["MOTORISTA"] = ""

    if "ds_acaomovimentacao" in recebimento.columns:
        acao = recebimento["ds_acaomovimentacao"].fillna("").astype(str).str.lower()
        recebimento = recebimento[
            acao.str.contains("receb", na=False) | acao.str.contains("restaur", na=False)
        ].copy()
    if "ds_destino" in recebimento.columns:
        recebimento = recebimento[recebimento["ds_destino"].map(normalize_text_value).eq("EM ESTOQUE")].copy()
    if "ds_finalidade" in recebimento.columns:
        recebimento = recebimento[
            recebimento["ds_finalidade"].map(normalize_text_value).isin(["SALDO ESTOQUE", "SALDO DE ESTOQUE"])
        ].copy()

    base_loggers["dt_embalagem"] = pd.to_datetime(base_loggers["dt_embalagem"], errors="coerce")
    base_loggers["Tipo Datalogger"] = resolve_tipo_datalogger(base_loggers, logger_col="cd_referencia")
    base_agentes["DATA_ENTREGA"] = pd.to_datetime(base_agentes["DATA_ENTREGA"], errors="coerce")
    base_agentes = base_agentes[base_agentes["DATA_ENTREGA"].notna()].copy()
    recebimento["dt_historico"]  = pd.to_datetime(recebimento["dt_historico"], errors="coerce")

    agentes_agg = base_agentes.groupby("PEDIDO", as_index=False).agg({
        "AGENTE": first_non_blank,
        "MOTORISTA": join_unique_non_blank,
        "UF Destinatario": first_non_blank,
        "DATA_ENTREGA": "min",
    })
    dest_agg = base_destinatarios.groupby("PEDIDO", as_index=False).agg({
        "CIDADE_DESTINO": first_non_blank, "UF_DESTINO": first_non_blank, "DESTINATARIO": first_non_blank,
    })
    ultimo_hist = (
        recebimento.groupby("ds_tag", as_index=False)["dt_historico"]
        .max().rename(columns={"dt_historico": "Ultimo_Historico"})
    )

    df = base_loggers.merge(agentes_agg, how="left", left_on="nr_pedido", right_on="PEDIDO")
    df = df.merge(dest_agg, how="left", left_on="nr_pedido", right_on="PEDIDO", suffixes=("", "_dest"))
    df = df.merge(ultimo_hist, how="left", left_on="cd_referencia", right_on="ds_tag")

    df["Status Retorno"] = "Pendente de Retorno"
    mask = (
        df["Ultimo_Historico"].notna() & df["DATA_ENTREGA"].notna()
        & (df["Ultimo_Historico"] > df["DATA_ENTREGA"])
    )
    df.loc[mask, "Status Retorno"] = "Retornado"

    df = df.rename(columns={
        "nr_pedido": "Pedido", "cd_referencia": "Logger",
        "UF Destinatario": "UF", "DATA_ENTREGA": "Data de Entrega", "AGENTE": "Agente",
        "UF_DESTINO": "UF Destino", "CIDADE_DESTINO": "Cidade Destino", "DESTINATARIO": "Destinatario",
    })

    df["Tipo Datalogger"] = df["Tipo Datalogger"].fillna("").astype(str).str.strip()
    df["_data_entrega_dt"] = pd.to_datetime(df["Data de Entrega"], errors="coerce")
    df = df[df["Logger"].fillna("").astype(str).str.strip().ne("")].copy()
    df = df[df["Data de Entrega"].notna()].copy()

    # Agente vazio → ENTREGA VTC se tem data de entrega
    df["Agente"] = df["Agente"].fillna("").astype(str).str.strip()
    agente_vazio = df["Agente"] == ""
    entrega_ok   = df["_data_entrega_dt"].notna()
    df.loc[agente_vazio & entrega_ok,  "Agente"] = "ENTREGA VTC"
    df.loc[agente_vazio & ~entrega_ok, "Agente"] = PENDING_AGENT_LABEL

    return df


def _prefilter(base_loggers, base_agentes, recebimento, base_destinatarios, days):
    cutoff = pd.Timestamp.now().normalize() - pd.Timedelta(days=max(days, 7))
    bl = base_loggers.copy()
    bl["dt_embalagem"] = pd.to_datetime(bl["dt_embalagem"], errors="coerce")
    bl = bl[bl["dt_embalagem"] >= cutoff]
    bl["nr_pedido"] = normalize_key(bl["nr_pedido"])
    bl["cd_referencia"] = normalize_key(bl["cd_referencia"])
    bl = bl[bl["nr_pedido"].ne("") & bl["cd_referencia"].ne("")].copy()
    if bl.empty:
        return bl, base_agentes.head(0), recebimento.head(0), base_destinatarios.head(0)
    pedidos_set = set(normalize_key(bl["nr_pedido"]).loc[lambda s: s != ""])
    tags_set    = set(normalize_key(bl["cd_referencia"]).loc[lambda s: s != ""])
    ba = base_agentes.copy()
    ba["_k"] = normalize_key(ba["PEDIDO"])
    ba = ba[ba["_k"].isin(pedidos_set)].drop(columns=["_k"])
    if "DATA_ENTREGA" in ba.columns:
        ba["DATA_ENTREGA"] = pd.to_datetime(ba["DATA_ENTREGA"], errors="coerce")
        ba = ba[ba["DATA_ENTREGA"].notna()].copy()
    bd = base_destinatarios.copy()
    bd["_k"] = normalize_key(bd["PEDIDO"])
    bd = bd[bd["_k"].isin(pedidos_set)].drop(columns=["_k"])
    rc = recebimento.copy()
    rc["_k"] = normalize_key(rc["ds_tag"])
    rc["dt_historico"] = pd.to_datetime(rc["dt_historico"], errors="coerce")
    rc = rc[(rc["_k"].isin(tags_set)) & (rc["dt_historico"] >= cutoff - pd.Timedelta(days=30))].drop(columns=["_k"])
    return bl, ba, rc, bd


def _load_recebimento_snapshot() -> pd.DataFrame:
    resumo_path = SNAPSHOT_DIR / "recebimento_resumo.pkl"
    if resumo_path.exists():
        recebimento = pd.read_pickle(resumo_path)
        if "Ultimo_Historico" in recebimento.columns and "dt_historico" not in recebimento.columns:
            recebimento = recebimento.rename(columns={"Ultimo_Historico": "dt_historico"})
        return recebimento
    return pd.read_pickle(SNAPSHOT_DIR / "recebimento.pkl")


# ── per-period computation ─────────────────────────────────────────────────────

def _compute_period_data(df: pd.DataFrame, days: int) -> dict:
    df = df.copy()
    df["Logger"] = df["Logger"].fillna("").astype(str).str.strip()
    df = df[df["Logger"] != ""].copy()
    df = df[df["Data de Entrega"].notna()].copy()
    df = dedupe_pedido_logger(df)

    pedidos    = int(df["Pedido"].nunique())
    loggers    = int(len(df))
    retornados = int((df["Status Retorno"] == "Retornado").sum())
    pendentes  = int((df["Status Retorno"] == "Pendente de Retorno").sum())
    taxa_ret   = safe_pct(retornados, loggers)
    taxa_pend  = safe_pct(pendentes, loggers)

    min_dt = df["_data_entrega_dt"].min()
    max_dt = df["_data_entrega_dt"].max()
    period_txt = (
        f"{pd.Timestamp(min_dt).strftime('%d/%m/%Y')} ate {pd.Timestamp(max_dt).strftime('%d/%m/%Y')}"
        if pd.notna(min_dt) and pd.notna(max_dt) else f"Ultimos {days} dias"
    )

    # Chart: Loggers Expedidos por Agente (top 12)
    ag_s = (df.groupby("Agente")["Logger"].count()
              .sort_values(ascending=True).tail(12))

    # Chart: Loggers Pendentes de Retorno por Agente (top 12)
    pend_s = (df[df["Status Retorno"] == "Pendente de Retorno"]
               .groupby("Agente")["Logger"].count()
               .sort_values(ascending=True).tail(12))

    # Chart: Tendencia Semanal
    trend_df = (df.dropna(subset=["_data_entrega_dt"])
                  .assign(Semana=lambda d: d["_data_entrega_dt"].dt.to_period("W").dt.start_time)
                  .groupby("Semana")["Logger"].count()
                  .reset_index(name="Loggers")
                  .sort_values("Semana"))
    trend_x = [d.strftime("%d/%m/%Y") for d in pd.to_datetime(trend_df["Semana"])]
    trend_y = [int(v) for v in trend_df["Loggers"]]

    # Chart: UF Destino (top 10)
    uf_s = (df.groupby("UF Destino")["Logger"].count()
              .sort_values(ascending=True).tail(10))

    # Chart: Top 10 Agentes com Maior Pendencia (full width)
    risk_s = (df[df["Status Retorno"] == "Pendente de Retorno"]
               .groupby("Agente", as_index=False)["Logger"].count()
               .rename(columns={"Logger": "Pendentes"})
               .sort_values("Pendentes", ascending=True)
               .tail(10))

    # Chart: Dispositivos Recebidos por Dia (Ultimos 7 dias) — fixo em 7d
    m7 = df.copy()
    m7["Ultimo_Historico"] = pd.to_datetime(m7["Ultimo_Historico"], errors="coerce")
    start7 = pd.Timestamp.now().normalize() - pd.Timedelta(days=6)
    end7   = pd.Timestamp.now().normalize() + pd.Timedelta(days=1) - pd.Timedelta(seconds=1)
    m7 = m7[m7["Ultimo_Historico"].between(start7, end7)].copy()
    all_days_7 = [(pd.Timestamp.now().normalize() - pd.Timedelta(days=i)) for i in range(6, -1, -1)]
    if not m7.empty:
        m7["Dia"] = m7["Ultimo_Historico"].dt.normalize()
        rec7_s = m7.groupby("Dia")["Logger"].nunique()
        rec7_s = rec7_s.reindex(all_days_7, fill_value=0)
    else:
        rec7_s = pd.Series(0, index=all_days_7)
    rec7_x = [d.strftime("%d/%m") for d in rec7_s.index]
    rec7_y = [int(v) for v in rec7_s.values]

    # Table: Visao Detalhada
    detail_cols = [
        "Pedido", "Logger", "Tipo Datalogger", "UF", "Data de Entrega",
        "Ultimo_Historico", "Agente", "Status Retorno", "UF Destino",
        "Cidade Destino", "Destinatario", "MOTORISTA",
    ]
    tbl = df.copy()
    if "Ultimo_Historico" not in tbl.columns:
        tbl["Ultimo_Historico"] = pd.NaT
    tbl = tbl.sort_values("_data_entrega_dt", ascending=False)
    tbl["Data de Entrega"] = pd.to_datetime(tbl["Data de Entrega"], errors="coerce").dt.strftime("%d/%m/%Y %H:%M:%S").fillna("")
    tbl["Ultimo_Historico"] = pd.to_datetime(tbl["Ultimo_Historico"], errors="coerce").dt.strftime("%d/%m/%Y %H:%M:%S").fillna("")
    tbl["_data_entrega_iso"] = pd.to_datetime(tbl["_data_entrega_dt"], errors="coerce").dt.strftime("%Y-%m-%dT%H:%M:%S").fillna("")
    all_rows = tbl.reindex(columns=detail_cols + ["_data_entrega_iso"]).fillna("").values.tolist()
    all_rows = [[str(c) if c is not None else "" for c in row] for row in all_rows]
    table_rows = all_rows[:TABLE_MAX_ROWS]
    csv_rows = table_rows

    return {
        "period_txt": period_txt,
        "kpi": {
            "pedidos":    fmt_int(pedidos),
            "loggers":    fmt_int(loggers),
            "retornados": fmt_int(retornados),
            "pendentes":  fmt_int(pendentes),
            "taxa_ret":   fmt_pct(taxa_ret),
            "taxa_pend":  fmt_pct(taxa_pend),
        },
        "charts": {
            "ag":    {"y": ag_s.index.tolist(),       "x": [int(v) for v in ag_s.values]},
            "pend":  {"y": pend_s.index.tolist(),     "x": [int(v) for v in pend_s.values]},
            "trend": {"x": trend_x,                    "y": trend_y},
            "uf":    {"y": uf_s.index.tolist(),        "x": [int(v) for v in uf_s.values]},
            "risk":  {"y": risk_s["Agente"].tolist(),  "x": risk_s["Pendentes"].tolist()},
            "rec7":  {"x": rec7_x,                     "y": rec7_y},
        },
        "table": {
            "headers": ["Pedido", "Logger", "Tipo Datalogger", "UF", "Data de Entrega",
                        "Ultimo Historico", "Agente", "Status Retorno", "UF Destino",
                        "Cidade Destino", "Destinatario", "Motorista"],
            "rows": table_rows,
        },
        "csv_rows": csv_rows,
    }


def _build_all_rows(df: pd.DataFrame) -> list[list[str]]:
    detail_cols = [
        "Pedido", "Logger", "Tipo Datalogger", "UF", "Data de Entrega",
        "Ultimo_Historico", "Agente", "Status Retorno", "UF Destino",
        "Cidade Destino", "Destinatario", "Motorista", "_data_entrega_iso",
    ]
    tbl = df.copy()
    tbl["Logger"] = tbl["Logger"].fillna("").astype(str).str.strip()
    tbl = tbl[tbl["Logger"] != ""].copy()
    tbl["Data de Entrega"] = pd.to_datetime(tbl["Data de Entrega"], errors="coerce")
    tbl = tbl[tbl["Data de Entrega"].notna()].copy()
    tbl = dedupe_pedido_logger(tbl)
    tbl = tbl.sort_values("Data de Entrega", ascending=False)
    tbl["_data_entrega_iso"] = tbl["Data de Entrega"].dt.strftime("%Y-%m-%dT%H:%M:%S").fillna("")
    tbl["Data de Entrega"] = tbl["Data de Entrega"].dt.strftime("%d/%m/%Y %H:%M:%S").fillna("")
    tbl["Ultimo_Historico"] = pd.to_datetime(tbl["Ultimo_Historico"], errors="coerce").dt.strftime("%d/%m/%Y %H:%M:%S").fillna("")
    rows = tbl.reindex(columns=detail_cols).fillna("").values.tolist()
    return [[str(c) if c is not None else "" for c in row] for row in rows]


# ── CSS ───────────────────────────────────────────────────────────────────────

CSS = """
<style>
*{box-sizing:border-box;margin:0;padding:0;}
:root{--bg-main:#0b1020;--bg-panel:#121b2d;--bg-panel-2:#0f1728;--line:#24344d;
      --txt:#e8edf5;--txt-soft:#9fb0c8;--brand:#2f80d0;--brand-2:#1d4f8f;}
html,body{min-height:100%;}
body{background:
  radial-gradient(900px 260px at 0% -10%,#13233f 0%,rgba(19,35,63,0) 60%),
  radial-gradient(700px 220px at 100% -20%,#1a2d4d 0%,rgba(26,45,77,0) 58%),
  var(--bg-main);
  color:var(--txt);font-family:"Segoe UI","Trebuchet MS",sans-serif;padding:18px 20px 32px;}
.hero-wrap{background:linear-gradient(120deg,#0f2344 0%,#173463 52%,#1e4178 100%);
  border:1px solid #2b4a76;border-radius:16px;padding:14px 18px;
  box-shadow:0 10px 24px rgba(0,0,0,.32);margin-bottom:12px;}
.hero-title{margin:0;font-size:2.1rem;font-weight:800;letter-spacing:.2px;color:#e8edf5;}
.period-section{margin-bottom:8px;}
.period-label-sm{font-size:.82rem;color:#9fb7d4;display:block;margin-bottom:5px;}
.period-btns-wrap{background:linear-gradient(180deg,var(--bg-panel) 0%,var(--bg-panel-2) 100%);
  border:1px solid var(--line);border-radius:12px;padding:8px 10px;display:inline-flex;gap:8px;align-items:center;}
.period-btn{background:#13243c;border:1px solid #2b466b;border-radius:8px;
  color:#9fb7d4;cursor:pointer;font-size:.88rem;font-weight:600;padding:6px 18px;
  transition:background .15s,color .15s,border-color .15s;}
.period-btn:hover{background:#1a3358;color:#dceafe;border-color:#3a6090;}
.period-btn.active{background:linear-gradient(135deg,#1d4f8f 0%,#2f80d0 100%);
  border-color:#4f94da;color:#fff;}
.meta-strip{background:linear-gradient(180deg,var(--bg-panel) 0%,var(--bg-panel-2) 100%);
  border:1px solid var(--line);border-radius:12px;padding:8px 12px;margin:8px 0 10px;
  color:var(--txt-soft);font-size:.95rem;line-height:1.6;}
.meta-strip strong{color:#d7e6fb;font-weight:700;}
.kpi-grid{display:grid;grid-template-columns:repeat(6,minmax(0,1fr));gap:12px;margin:6px 0 14px;}
.kpi-card{background:linear-gradient(155deg,#15233a 0%,#121b2d 62%,#101829 100%);
  border:1px solid #2a3e5e;border-radius:14px;padding:16px 18px;min-height:108px;
  box-shadow:0 8px 18px rgba(0,0,0,.28);}
.kpi-title{font-size:1.08rem;font-weight:700;color:#9fb7d4;margin-bottom:12px;}
.kpi-value{font-size:2.65rem;font-weight:800;color:#f6fbff;letter-spacing:-.03em;line-height:1;}
.tipo-row{display:flex;align-items:center;gap:10px;margin:8px 0 0;flex-wrap:wrap;}
.tipo-label-sm{font-size:.85rem;color:#9fb7d4;font-weight:600;white-space:nowrap;}
.tipo-btn{background:#13243c;border:1px solid #2b466b;border-radius:8px;
  color:#9fb7d4;cursor:pointer;font-size:.82rem;font-weight:600;padding:5px 14px;
  transition:background .15s,color .15s,border-color .15s;}
.tipo-btn:hover{background:#1a3358;color:#dceafe;border-color:#3a6090;}
.tipo-btn.active{background:linear-gradient(135deg,#1d4f8f 0%,#2f80d0 100%);
  border-color:#4f94da;color:#fff;}
.filter-row{display:flex;align-items:center;gap:10px;margin:10px 0 0;flex-wrap:wrap;}
.uf-select{background:#13243c;border:1px solid #2b466b;border-radius:8px;
  color:#dceafe;cursor:pointer;font-size:.88rem;font-weight:600;padding:6px 12px;
  min-width:220px;outline:none;}
.uf-select:hover{background:#1a3358;border-color:#3a6090;}
.uf-select:focus{border-color:#4f94da;box-shadow:0 0 0 2px rgba(47,128,208,.18);}
.agent-input{background:#13243c;border:1px solid #2b466b;border-radius:8px;
  color:#dceafe;font-size:.88rem;font-weight:600;padding:6px 12px;
  min-width:320px;outline:none;}
.agent-input:hover{background:#1a3358;border-color:#3a6090;}
.agent-input:focus{border-color:#4f94da;box-shadow:0 0 0 2px rgba(47,128,208,.18);}
.clear-btn{background:#13243c;border:1px solid #2b466b;border-radius:8px;
  color:#9fb7d4;cursor:pointer;font-size:.82rem;font-weight:600;padding:6px 12px;
  transition:background .15s,color .15s,border-color .15s;}
.clear-btn:hover{background:#1a3358;color:#dceafe;border-color:#3a6090;}
.tabs-bar{display:flex;gap:6px;margin:6px 0 0;border-bottom:1px solid #24344d;padding-bottom:0;}
.tab-btn{background:#131d30;border:1px solid var(--line);border-bottom:none;border-radius:10px 10px 0 0;
  padding:9px 18px;color:#ced9ea;cursor:pointer;font-size:.95rem;font-weight:600;
  transition:background .15s,color .15s,border-color .15s;}
.tab-btn:hover{background:#1a2b45;color:#fff;}
.tab-btn.active{background:#173158;color:#fff;border-color:#2f5ea3;}
.tab-content{padding-top:14px;}
.section-title{font-size:1.35rem;font-weight:800;color:#dceafe;margin:14px 2px 8px;}
.charts-2{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px;margin-bottom:14px;}
.chart-box{background:linear-gradient(170deg,#121f34 0%,#0f1828 100%);
  border:1px solid #2a3e5e;border-radius:14px;padding:10px 10px 2px;
  box-shadow:0 8px 18px rgba(0,0,0,.24);margin-bottom:14px;}
.chart-box.fullwidth{width:100%;}
.chart-title{font-size:1.05rem;font-weight:700;color:#dceafe;padding:4px 4px 6px;}
.chart-empty{display:flex;align-items:center;justify-content:center;min-height:200px;
  border:1px dashed #2b466b;border-radius:10px;color:#7f93ad;
  background:rgba(19,36,60,.35);font-size:.9rem;text-align:center;padding:12px;}
/* Visao Detalhada */
.detail-wrap{overflow-x:auto;}
table.detail-tbl{width:100%;border-collapse:collapse;font-size:.82rem;color:#c8d8ef;}
table.detail-tbl thead tr{background:#0f1f37;border-bottom:2px solid #2a3e5e;}
table.detail-tbl thead th{padding:9px 10px;font-weight:700;color:#9ec2ea;text-align:left;
  white-space:nowrap;position:sticky;top:0;background:#0f1f37;z-index:1;}
table.detail-tbl tbody tr:nth-child(even){background:rgba(19,45,77,.25);}
table.detail-tbl tbody tr:hover{background:rgba(47,128,208,.12);}
table.detail-tbl tbody td{padding:7px 10px;border-bottom:1px solid #1e2e47;white-space:nowrap;max-width:220px;overflow:hidden;text-overflow:ellipsis;}
td.status-retornado{color:#4ecb71;font-weight:600;}
td.status-pendente{color:#f0a04b;font-weight:600;}
.csv-link{display:inline-block;margin:10px 0 4px;background:linear-gradient(135deg,#1d4f8f 0%,#2f80d0 100%);
  color:#fff;border:1px solid #4f94da;border-radius:10px;padding:8px 20px;font-weight:700;
  font-size:.9rem;text-decoration:none;}
.csv-link:hover{background:linear-gradient(135deg,#2561ab 0%,#3b8de0 100%);}
footer{margin-top:24px;font-size:.75rem;color:#556070;text-align:right;}
@media(max-width:1200px){
  .kpi-grid{grid-template-columns:repeat(3,minmax(0,1fr));}
  .charts-2{grid-template-columns:1fr;}
}
@media(max-width:720px){
  .kpi-grid{grid-template-columns:repeat(2,minmax(0,1fr));}
}
</style>
"""

# ── HTML template ─────────────────────────────────────────────────────────────

def generate_html(all_rows: list[list[str]], tipos: list[str],
                  ufs: list[str], agentes: list[str], gerado: str, hist_last: str, snapshot_time: str) -> str:
    all_rows_json = json.dumps(all_rows, ensure_ascii=False, default=str)
    headers_json = json.dumps(TABLE_HEADERS, ensure_ascii=False)
    btn_labels = {
        7: "Ultimos 7 dias",
        30: "Ultimos 30 dias",
        60: "Ultimos 60 dias",
        90: "Ultimos 90 dias",
    }
    btns_html = "".join(
        f'<button class="period-btn{"  active" if d == PERIODO_PAD else ""}" '
        f'id="btn-{d}" onclick="switchPeriod({d})">{btn_labels[d]}</button>'
        for d in PERIODOS
    )
    tipo_btns_html = (
        '<button class="tipo-btn active" id="tipo-btn-" onclick="switchTipo(\'\')">Todos os tipos</button>'
        + "".join(
            f"<button class=\"tipo-btn\" id=\"tipo-btn-{t}\" onclick=\"switchTipo('{t}')\">{t}</button>"
            for t in tipos
        )
    )
    uf_options_html = "".join(
        f'<option value="{escape(uf)}">{escape(uf)}</option>'
        for uf in ufs
    )
    agente_options_html = "".join(
        f'<option value="{escape(ag)}">{escape(ag)}</option>'
        for ag in agentes
    )
    tbl_headers = "".join(f"<th>{h}</th>" for h in TABLE_HEADERS)

    return f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Controle de Reversa — Dataloggers</title>
<script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>
{CSS}
</head>
<body>

<div class="hero-wrap">
  <h1 class="hero-title">Controle de Reversa de Dataloggers</h1>
</div>

<div class="period-section">
  <h2 style="font-size:1.25rem;font-weight:700;color:#e8edf5;margin:10px 0 6px;">Periodo</h2>
  <div class="period-btns-wrap">
    <span class="period-label-sm">Filtro rapido</span>
    {btns_html}
  </div>
</div>

<div class="tipo-row">
  <span class="tipo-label-sm">Filtro rapido por tipo</span>
  {tipo_btns_html}
</div>

<div class="meta-strip">
  <strong>Periodo aplicado:</strong> <span id="meta-period">--</span><br>
  <strong>Agente aplicado:</strong> <span id="meta-agente">Todos os agentes</span> &nbsp;|&nbsp;
  <strong>UF aplicada:</strong> <span id="meta-uf">Todas as UFs</span> &nbsp;|&nbsp;
  <strong>Ultima atualizacao do historico:</strong> {hist_last}<br>
  <strong>Snapshot atualizado em:</strong> {snapshot_time} &nbsp;|&nbsp; <strong>Ultima atualizacao da tela:</strong> {gerado}
</div>

<div class="filter-row">
  <span class="tipo-label-sm">Filtro por Agente</span>
  <input id="agente-input" class="agent-input" type="text" list="agente-options"
         placeholder="Digite o agente" oninput="switchAgente(this.value)">
  <datalist id="agente-options">
    {agente_options_html}
  </datalist>
  <button class="clear-btn" type="button" onclick="clearAgente()">Limpar</button>
</div>

<div class="filter-row">
  <span class="tipo-label-sm">Filtro por UF</span>
  <select id="uf-select" class="uf-select" onchange="switchUf(this.value)">
    <option value="">Todas as UFs</option>
    {uf_options_html}
  </select>
</div>

<div class="kpi-grid">
  <div class="kpi-card"><div class="kpi-title">Pedidos Entregues</div><div class="kpi-value" id="kpi-pedidos">--</div></div>
  <div class="kpi-card"><div class="kpi-title">Loggers Entregues</div><div class="kpi-value" id="kpi-loggers">--</div></div>
  <div class="kpi-card"><div class="kpi-title">Loggers Retornados</div><div class="kpi-value" id="kpi-retornados">--</div></div>
  <div class="kpi-card"><div class="kpi-title">Loggers Pendentes</div><div class="kpi-value" id="kpi-pendentes">--</div></div>
  <div class="kpi-card"><div class="kpi-title">Taxa de Retorno</div><div class="kpi-value" id="kpi-taxa-ret">--</div></div>
  <div class="kpi-card"><div class="kpi-title">Taxa de Pendencia</div><div class="kpi-value" id="kpi-taxa-pend">--</div></div>
</div>

<div class="tabs-bar">
  <button class="tab-btn active" id="tab-exec-btn" onclick="switchTab('exec')">Visao Executiva</button>
  <button class="tab-btn" id="tab-detalhe-btn" onclick="switchTab('detalhe')">Visao Detalhada</button>
</div>

<!-- ===== VISAO EXECUTIVA ===== -->
<div id="tab-exec" class="tab-content">

  <div class="charts-2">
    <div class="chart-box">
      <div class="chart-title">Loggers Expedidos por Agente</div>
      <div id="chart-ag" style="height:420px"></div>
    </div>
    <div class="chart-box">
      <div class="chart-title">Loggers Pendentes de Retorno por Agente</div>
      <div id="chart-pend" style="height:420px"></div>
    </div>
  </div>

  <div class="charts-2">
    <div class="chart-box">
      <div class="chart-title">Tendencia Semanal de Loggers Entregues</div>
      <div id="chart-trend" style="height:360px"></div>
    </div>
    <div class="chart-box">
      <div class="chart-title">Loggers por UF Destino</div>
      <div id="chart-uf" style="height:360px"></div>
    </div>
  </div>

  <h3 class="section-title">Top 10 Agentes com Maior Pendencia</h3>
  <div class="chart-box fullwidth">
    <div id="chart-risk" style="height:360px"></div>
  </div>

  <h3 class="section-title">Dispositivos Recebidos por Dia (Ultimos 7 dias)</h3>
  <div class="chart-box fullwidth">
    <div id="chart-rec7" style="height:320px"></div>
  </div>

</div>

<!-- ===== VISAO DETALHADA ===== -->
<div id="tab-detalhe" class="tab-content" style="display:none">
  <h3 class="section-title">Detalhamento</h3>
  <a id="csv-download" class="csv-link" href="#" download="dataloggers_filtrado.csv">&#8595; Baixar CSV</a>
  <div class="detail-wrap" style="max-height:600px;overflow-y:auto;margin-top:8px;">
    <table class="detail-tbl">
      <thead><tr>{tbl_headers}</tr></thead>
      <tbody id="detail-tbody"></tbody>
    </table>
  </div>
</div>

<footer>Gerado em {gerado} &nbsp;&middot;&nbsp; VTC LOG &mdash; BI Qualidade</footer>

<script>
const ALL_ROWS = {all_rows_json};
const TABLE_HEADERS = {headers_json};
const DEFAULT_PERIOD = {PERIODO_PAD};
const TABLE_MAX_ROWS = {TABLE_MAX_ROWS};
const cfg = {{displayModeBar:false, responsive:true}};

const LAYOUT_BASE = {{
  paper_bgcolor:"#141b26", plot_bgcolor:"#141b26",
  font:{{color:"#e8edf5",family:'"Segoe UI","Trebuchet MS",sans-serif'}},
  xaxis:{{gridcolor:"#2b3a4d",zerolinecolor:"#2b3a4d",linecolor:"#2b3a4d",tickfont:{{color:"#d3dceb"}}}},
  yaxis:{{gridcolor:"#2b3a4d",zerolinecolor:"#2b3a4d",linecolor:"#2b3a4d",tickfont:{{color:"#d3dceb",size:14}}}},
  hoverlabel:{{bgcolor:"#1b2635",font:{{color:"#f1f5fb"}}}},
  showlegend:false,
}};

function layoutBarH(data, height){{
  // Margem esquerda dinamica para nao cortar os nomes dos agentes
  const maxLen = data && data.y ? Math.max(1, ...data.y.map(s => String(s||"").length)) : 20;
  const lMargin = Math.min(Math.max(80, Math.round(maxLen * 7.5)), 300);
  return Object.assign({{}}, LAYOUT_BASE, {{
    height:height,
    margin:{{l:lMargin,r:55,t:20,b:10}},
    xaxis:Object.assign({{}},LAYOUT_BASE.xaxis,{{visible:false}}),
    yaxis:Object.assign({{}},LAYOUT_BASE.yaxis,{{autorange:"reversed",tickfont:{{color:"#d3dceb",size:13}}}}),
  }});
}}
function layoutBarV(height){{
  return Object.assign({{}}, LAYOUT_BASE, {{
    height:height,
    margin:{{l:50,r:20,t:50,b:60}},
    xaxis:Object.assign({{}},LAYOUT_BASE.xaxis,{{tickangle:-30,automargin:true}}),
    yaxis:Object.assign({{}},LAYOUT_BASE.yaxis,{{visible:false}}),
  }});
}}
function layoutLine(height){{
  return Object.assign({{}}, LAYOUT_BASE, {{
    height:height,
    margin:{{l:50,r:30,t:50,b:70}},
    xaxis:Object.assign({{}},LAYOUT_BASE.xaxis,{{tickangle:-30,automargin:true}}),
    yaxis:Object.assign({{}},LAYOUT_BASE.yaxis,{{visible:false}}),
  }});
}}

function traceBarH(d, color){{
  return [{{
    type:"bar", x:d.x, y:d.y, orientation:"h",
    text:d.x, textposition:"inside", insidetextanchor:"middle",
    textangle:0, cliponaxis:false,
    textfont:{{size:18,color:"#f8fbff",weight:"bold"}},
    marker:{{color:color}},
  }}];
}}
function traceBarV(d, color){{
  return [{{
    type:"bar", x:d.x, y:d.y,
    text:d.y, textposition:"outside", cliponaxis:false,
    textfont:{{size:14,color:"#f8fbff",weight:"bold"}},
    marker:{{color:color}},
  }}];
}}
function traceLine(d){{
  return [{{
    type:"scatter", x:d.x, y:d.y, mode:"lines+markers+text",
    text:d.y, textposition:"top center",
    textfont:{{size:13,color:"#e8edf5"}},
    line:{{color:"#2f80d0",width:4}},
    marker:{{color:"#2f80d0",size:10}},
  }}];
}}

function renderChart(id, traces, layout){{
  const el = document.getElementById(id);
  if (!el) return;
  const hasData = traces && traces.length && traces[0] &&
    ((traces[0].x||[]).length > 0 || (traces[0].y||[]).length > 0);
  if (el._fullLayout) Plotly.purge(el);
  if (!hasData) {{
    el.innerHTML = '<div class="chart-empty">Sem dados no periodo selecionado</div>';
    return;
  }}
  // Copia profunda do layout para evitar que Plotly mute o objeto e
  // quebre renders subsequentes (causa do bug d="M0,0Z").
  const lay = JSON.parse(JSON.stringify(layout));
  Plotly.newPlot(el, traces, lay, cfg)
    .catch(() => {{ el.innerHTML = '<div class="chart-empty">Erro ao carregar grafico</div>'; }});
}}

function buildTableRows(rows){{
  return rows.map(r => {{
    const statusIdx = 7;
    const visible = r.slice(0, TABLE_HEADERS.length);
    const statusClass = (visible[statusIdx] || "").includes("Retornado") ? "status-retornado" : "status-pendente";
    const cells = visible.map((c,i) =>
      i === statusIdx
        ? `<td class="${{statusClass}}">${{escapeHtml(c)}}</td>`
        : `<td title="${{escapeHtml(c)}}">${{escapeHtml(c)}}</td>`
    ).join("");
    return `<tr>${{cells}}</tr>`;
  }}).join("");
}}

function buildCsvHref(headers, rows){{
  const escape = v => '"' + String(v).replace(/"/g,'""') + '"';
  const lines = [headers.map(escape).join(",")].concat(rows.map(r => r.slice(0, headers.length).map(escape).join(",")));
  const blob = new Blob([lines.join("\\n")], {{type:"text/csv;charset=utf-8;"}});
  return URL.createObjectURL(blob);
}}

let _currentDays = DEFAULT_PERIOD;
let _currentTipo = "";
let _currentUf = "";
let _currentAgente = "";

function escapeHtml(value){{
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}}

function normalizeUf(value){{
  return String(value ?? "").trim().toUpperCase();
}}

function normalizeText(value){{
  return String(value ?? "")
    .normalize("NFD")
    .replace(/[\\u0300-\\u036f]/g, "")
    .trim()
    .toUpperCase();
}}

function parseIsoDate(value){{
  const s = String(value ?? "").trim();
  if (!s) return null;
  const d = new Date(s);
  return Number.isNaN(d.getTime()) ? null : d;
}}

function matchesAgente(rowAgente, filtro){{
  const f = normalizeText(filtro);
  if (!f) return true;
  return normalizeText(rowAgente).includes(f);
}}

function parseBrDate(value){{
  const s = String(value ?? "").trim();
  if (!s) return null;
  const m = s.match(/^(\d{{2}})\/(\d{{2}})\/(\d{{4}})(?:\s+(\d{{2}}):(\d{{2}}):(\d{{2}}))?$/);
  if (!m) return null;
  return new Date(Number(m[3]), Number(m[2]) - 1, Number(m[1]), Number(m[4] || 0), Number(m[5] || 0), Number(m[6] || 0));
}}

function formatBrDate(date){{
  if (!(date instanceof Date) || Number.isNaN(date.getTime())) return "";
  const dd = String(date.getDate()).padStart(2, "0");
  const mm = String(date.getMonth() + 1).padStart(2, "0");
  const yyyy = date.getFullYear();
  return `${{dd}}/${{mm}}/${{yyyy}}`;
}}

function startOfWeek(date){{
  const d = new Date(date);
  d.setHours(0, 0, 0, 0);
  const day = (d.getDay() + 6) % 7;
  d.setDate(d.getDate() - day);
  return d;
}}

function topEntries(map, limit){{
  return Array.from(map.entries())
    .sort((a, b) => a[1] - b[1])
    .slice(-limit);
}}

function pedidoLoggerKey(row){{
  const pedido = normalizeText(row[0]);
  const logger = normalizeText(row[1]);
  return pedido && logger ? `${{pedido}}|${{logger}}` : "";
}}

function rowRank(row){{
  const status = String(row[7] ?? "");
  const hist = parseBrDate(row[5]);
  const entrega = parseBrDate(row[4]);
  return {{
    status: status.includes("Retornado") ? 1 : 0,
    hist: hist ? hist.getTime() : 0,
    entrega: entrega ? entrega.getTime() : 0,
  }};
}}

function isBetterRow(candidate, current){{
  const a = rowRank(candidate);
  const b = rowRank(current);
  if (a.status !== b.status) return a.status > b.status;
  if (a.hist !== b.hist) return a.hist > b.hist;
  return a.entrega > b.entrega;
}}

function uniquePedidoLoggerRows(rows){{
  const byKey = new Map();
  for (const row of rows) {{
    const key = pedidoLoggerKey(row);
    if (!key) continue;
    const current = byKey.get(key);
    if (!current || isBetterRow(row, current)) byKey.set(key, row);
  }}
  return Array.from(byKey.values());
}}

function computePeriodData(rows, days, periodStart, periodEnd){{
  rows = uniquePedidoLoggerRows(rows);
  const pedidos = new Set();
  let loggers = 0;
  let retornados = 0;
  let pendentes = 0;

  const agCounts = new Map();
  const pendCounts = new Map();
  const ufCounts = new Map();
  const trendCounts = new Map();
  const rec7ByDay = new Map();

  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const start7 = new Date(today);
  start7.setDate(start7.getDate() - 6);
  const end7 = new Date(today);
  end7.setHours(23, 59, 59, 999);

  for (const row of rows) {{
    const pedido = String(row[0] ?? "").trim();
    const logger = String(row[1] ?? "").trim();
    const agente = String(row[6] ?? "").trim();
    const status = String(row[7] ?? "").trim();
    const ufDestino = String(row[8] ?? "").trim();
    const dtEntrega = parseBrDate(row[4]);
    const dtHistorico = parseBrDate(row[5]);

    if (pedido) pedidos.add(pedido);
    if (logger) loggers += 1;
    if (status.includes("Retornado")) retornados += 1;
    else pendentes += 1;

    if (agente) agCounts.set(agente, (agCounts.get(agente) || 0) + 1);
    if (status.includes("Pendente de Retorno") && agente) {{
      pendCounts.set(agente, (pendCounts.get(agente) || 0) + 1);
    }}
    if (ufDestino) ufCounts.set(ufDestino, (ufCounts.get(ufDestino) || 0) + 1);

    if (dtEntrega) {{
      const wk = startOfWeek(dtEntrega);
      const key = `${{wk.getFullYear()}}-${{String(wk.getMonth() + 1).padStart(2, "0")}}-${{String(wk.getDate()).padStart(2, "0")}}`;
      trendCounts.set(key, (trendCounts.get(key) || 0) + 1);
    }}

    if (dtHistorico && dtHistorico >= start7 && dtHistorico <= end7 && logger) {{
      const dayKey = `${{dtHistorico.getFullYear()}}-${{String(dtHistorico.getMonth() + 1).padStart(2, "0")}}-${{String(dtHistorico.getDate()).padStart(2, "0")}}`;
      if (!rec7ByDay.has(dayKey)) rec7ByDay.set(dayKey, new Set());
      rec7ByDay.get(dayKey).add(logger);
    }}
  }}

  const periodTxt = periodStart && periodEnd
    ? `${{formatBrDate(periodStart)}} ate ${{formatBrDate(periodEnd)}}`
    : `Ultimos ${{days}} dias`;

  const trendEntries = Array.from(trendCounts.entries())
    .sort((a, b) => a[0].localeCompare(b[0]));
  const trendX = trendEntries.map(([k]) => {{
    const [y, m, d] = k.split("-").map(Number);
    return `${{String(d).padStart(2, "0")}}/${{String(m).padStart(2, "0")}}/${{y}}`;
  }});
  const trendY = trendEntries.map(([, v]) => v);

  const rec7X = [];
  const rec7Y = [];
  for (let i = 6; i >= 0; i--) {{
    const d = new Date(today);
    d.setDate(d.getDate() - i);
    const key = `${{d.getFullYear()}}-${{String(d.getMonth() + 1).padStart(2, "0")}}-${{String(d.getDate()).padStart(2, "0")}}`;
    rec7X.push(`${{String(d.getDate()).padStart(2, "0")}}/${{String(d.getMonth() + 1).padStart(2, "0")}}`);
    rec7Y.push((rec7ByDay.get(key) || new Set()).size);
  }}

  const toChartData = (map, limit) => {{
    const entries = topEntries(map, limit);
    return {{
      y: entries.map(([label]) => label),
      x: entries.map(([, value]) => value),
    }};
  }};

  return {{
    period_txt: periodTxt,
    kpi: {{
      pedidos: pedidos.size.toLocaleString("pt-BR"),
      loggers: loggers.toLocaleString("pt-BR"),
      retornados: retornados.toLocaleString("pt-BR"),
      pendentes: pendentes.toLocaleString("pt-BR"),
      taxa_ret: loggers > 0 ? `${{((retornados / loggers) * 100).toFixed(1).replace(".", ",")}}%` : "0,0%",
      taxa_pend: loggers > 0 ? `${{((pendentes / loggers) * 100).toFixed(1).replace(".", ",")}}%` : "0,0%",
    }},
    charts: {{
      ag: toChartData(agCounts, 12),
      pend: toChartData(pendCounts, 12),
      trend: {{ x: trendX, y: trendY }},
      uf: toChartData(ufCounts, 10),
      risk: toChartData(pendCounts, 10),
      rec7: {{ x: rec7X, y: rec7Y }},
    }},
    table: {{
      headers: TABLE_HEADERS,
      rows: rows.slice(0, TABLE_MAX_ROWS),
    }},
    csv_rows: rows,
  }};
}}

function renderAll(days, tipo){{
  const today = new Date();
  today.setHours(23, 59, 59, 999);
  const cutoff = new Date(today);
  cutoff.setHours(0, 0, 0, 0);
  cutoff.setDate(cutoff.getDate() - (days - 1));
  const rows = ALL_ROWS.filter(r => {{
    const logger = String(r[1] ?? "").trim();
    const dt = parseBrDate(r[4]);
    return logger && dt && dt >= cutoff && dt <= today &&
      (!tipo || String(r[2] ?? "") === String(tipo)) &&
      (!_currentUf || normalizeUf(r[3]) === _currentUf) &&
      matchesAgente(r[6], _currentAgente);
  }});
  const computed = computePeriodData(rows, days, cutoff, today);

  document.getElementById("meta-period").textContent     = computed.period_txt;
  document.getElementById("meta-agente").textContent     = _currentAgente || "Todos os agentes";
  document.getElementById("meta-uf").textContent         = _currentUf || "Todas as UFs";
  document.getElementById("kpi-pedidos").textContent     = computed.kpi.pedidos;
  document.getElementById("kpi-loggers").textContent     = computed.kpi.loggers;
  document.getElementById("kpi-retornados").textContent  = computed.kpi.retornados;
  document.getElementById("kpi-pendentes").textContent   = computed.kpi.pendentes;
  document.getElementById("kpi-taxa-ret").textContent    = computed.kpi.taxa_ret;
  document.getElementById("kpi-taxa-pend").textContent   = computed.kpi.taxa_pend;

  renderChart("chart-ag",    traceBarH(computed.charts.ag,   "#144b8b"), layoutBarH(computed.charts.ag,   420));
  renderChart("chart-pend",  traceBarH(computed.charts.pend, "#1f6cb8"), layoutBarH(computed.charts.pend, 420));
  renderChart("chart-trend", traceLine(computed.charts.trend),           layoutLine(360));
  renderChart("chart-uf",    traceBarH(computed.charts.uf,   "#2f80d0"), layoutBarH(computed.charts.uf,   360));
  renderChart("chart-risk",  traceBarH(computed.charts.risk, "#0f3f73"), layoutBarH(computed.charts.risk, 360));
  renderChart("chart-rec7",  traceBarV(computed.charts.rec7, "#2f80d0"), layoutBarV(320));

  const tbody = document.getElementById("detail-tbody");
  if (tbody) tbody.innerHTML = computed.table.rows.length ? buildTableRows(computed.table.rows) : '<tr><td colspan="12" style="padding:16px;text-align:center;color:#9fb0c8;">Sem dados no filtro selecionado</td></tr>';
  const csvLink = document.getElementById("csv-download");
  if (csvLink) csvLink.href = buildCsvHref(computed.table.headers, rows);
}}

function switchPeriod(days){{
  _currentDays = days;
  document.querySelectorAll(".period-btn").forEach(b => b.classList.remove("active"));
  const btn = document.getElementById("btn-"+days);
  if (btn) btn.classList.add("active");
  renderAll(days, _currentTipo);
}}

function switchTipo(tipo){{
  _currentTipo = tipo;
  document.querySelectorAll(".tipo-btn").forEach(b => b.classList.remove("active"));
  const btn = document.getElementById("tipo-btn-"+tipo);
  if (btn) btn.classList.add("active");
  renderAll(_currentDays, tipo);
}}

function switchUf(uf){{
  _currentUf = uf;
  renderAll(_currentDays, _currentTipo);
}}

function switchAgente(agente){{
  _currentAgente = String(agente || "").trim();
  renderAll(_currentDays, _currentTipo);
}}

function clearAgente(){{
  const input = document.getElementById("agente-input");
  if (input) input.value = "";
  _currentAgente = "";
  renderAll(_currentDays, _currentTipo);
}}

function switchTab(tab){{
  document.getElementById("tab-exec").style.display    = tab === "exec"    ? "" : "none";
  document.getElementById("tab-detalhe").style.display = tab === "detalhe" ? "" : "none";
  document.getElementById("tab-exec-btn").classList.toggle("active",    tab === "exec");
  document.getElementById("tab-detalhe-btn").classList.toggle("active", tab === "detalhe");
  if (tab === "exec") {{
    // Refit apos mostrar o container (que estava display:none)
    requestAnimationFrame(() => {{
      ["chart-ag","chart-pend","chart-trend","chart-uf","chart-risk","chart-rec7"].forEach(cid => {{
        const cel = document.getElementById(cid);
        if (cel && cel._fullLayout) Plotly.Plots.resize(cel);
      }});
    }});
  }}
}}

window.addEventListener("resize", () => {{
  ["chart-ag","chart-pend","chart-trend","chart-uf","chart-risk","chart-rec7"].forEach(id => {{
    const el = document.getElementById(id);
    if (el && Plotly.Plots && Plotly.Plots.resize) Plotly.Plots.resize(el);
  }});
}});

renderAll(DEFAULT_PERIOD, "");
</script>
</body>
</html>"""


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    print("[reversa] Carregando snapshots...")
    max_days = max(PERIODOS)
    try:
        base_loggers       = pd.read_pickle(SNAPSHOT_DIR / "base_loggers.pkl")
        base_agentes       = pd.read_pickle(SNAPSHOT_DIR / "base_agentes.pkl")
        recebimento        = _load_recebimento_snapshot()
        base_destinatarios = pd.read_pickle(SNAPSHOT_DIR / "base_destinatarios.pkl")

        base_loggers, base_agentes, recebimento, base_destinatarios = _prefilter(
            base_loggers, base_agentes, recebimento, base_destinatarios, max_days
        )
        print(f"[reversa] Pos-filtro ({max_days}d): loggers={len(base_loggers)} agentes={len(base_agentes)} receb={len(recebimento)}")

        model_full = build_model(base_loggers, base_agentes, recebimento, base_destinatarios)
        cutoff_max = pd.Timestamp.now() - pd.Timedelta(days=max_days)
        model_full = model_full[model_full["_data_entrega_dt"] >= cutoff_max].copy()
        before_dedupe = len(model_full)
        model_full = dedupe_pedido_logger(model_full)
        print(f"[reversa] Deduplicacao Pedido+Logger: {before_dedupe} -> {len(model_full)} registros")
        print(f"[reversa] Modelo completo: {len(model_full)} registros")
    except MemoryError:
        raise RuntimeError(
            "[reversa] Memoria insuficiente ao carregar snapshots atualizados; abortando para nao usar fallback local."
        )

    gerado = datetime.now().strftime("%d/%m/%Y %H:%M:%S")

    # Obter a data do snapshot consumido
    try:
        mod_ts = (SNAPSHOT_DIR / "modelo_final.pkl").stat().st_mtime
    except FileNotFoundError:
        try:
            mod_ts = (SNAPSHOT_DIR / "base_loggers.pkl").stat().st_mtime
        except FileNotFoundError:
            mod_ts = datetime.now().timestamp()
    snapshot_time = datetime.fromtimestamp(mod_ts).strftime("%d/%m/%Y %H:%M:%S")

    # Ultima atualizacao do historico = max(Ultimo_Historico) do modelo completo
    hist_ts = pd.to_datetime(model_full.get("Ultimo_Historico"), errors="coerce").max()
    hist_last = hist_ts.strftime("%d/%m/%Y %H:%M:%S") if pd.notna(hist_ts) else "Sem historico"

    tipos = sorted(t for t in model_full["Tipo Datalogger"].dropna().unique() if str(t).strip())
    ufs = sorted(
        {
            normalize_text_value(v)
            for v in model_full.get("UF", pd.Series(dtype="object")).dropna().unique()
            if normalize_text_value(v)
        }
    )
    agentes = sorted(
        {
            normalize_text_value(v)
            for v in model_full.get("Agente", pd.Series(dtype="object")).dropna().unique()
            if normalize_text_value(v)
        }
    )
    print(f"[reversa] Tipos disponiveis: {tipos}")
    print(f"[reversa] UFs disponiveis: {ufs}")
    print(f"[reversa] Agentes disponiveis: {len(agentes)}")

    periods_data: dict[int, dict[str, dict]] = {}
    for days in PERIODOS:
        cutoff = pd.Timestamp.now() - pd.Timedelta(days=days)
        df_p = model_full[model_full["_data_entrega_dt"] >= cutoff].copy()
        df_p["Data de Entrega"] = pd.to_datetime(df_p.get("Data de Entrega"), errors="coerce")
        df_p = df_p[df_p["Data de Entrega"].notna()].copy()

        periods_data[days] = {}
        periods_data[days][""] = _compute_period_data(df_p, days)
        for tipo in tipos:
            df_t = df_p[df_p["Tipo Datalogger"] == tipo].copy()
            periods_data[days][tipo] = _compute_period_data(df_t, days)

        n_all = len(df_p)
        print(f"[reversa] Periodo {days}d: {n_all} registros | "
              + " | ".join(f"{t}:{len(df_p[df_p['Tipo Datalogger']==t])}" for t in tipos))

    all_rows = _build_all_rows(model_full)
    print(f"[reversa] Dataset unico para navegação: {len(all_rows)} registros")

    html = generate_html(all_rows, tipos, ufs, agentes, gerado, hist_last, snapshot_time)
    OUTPUT_FILE.write_text(html, encoding="utf-8")
    print(f"[reversa] HTML salvo: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
