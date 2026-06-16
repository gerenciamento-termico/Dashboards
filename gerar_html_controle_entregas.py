from __future__ import annotations

import os
import json
from datetime import datetime
from html import escape
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import plotly.io as pio
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL

from env_utils import load_env_file


WORKSPACE = Path(__file__).resolve().parent
SNAPSHOT_DIR = WORKSPACE.parent / "snapshot_reversa"
MODEL_FILE = SNAPSHOT_DIR / "modelo_final.pkl"
RECEBIMENTO_RESUMO_FILE = SNAPSHOT_DIR / "recebimento_resumo.pkl"
OUTPUT_HTML = WORKSPACE / "CONTROLE_ENTREGAS_20D.html"
OUTPUT_CSV = WORKSPACE / "CONTROLE_ENTREGAS_20D.csv"
OUTPUT_SLA_PENDING_CSV = WORKSPACE / "CONTROLE_ENTREGAS_20D_SLA_PENDENTES.csv"

WINDOW_DAYS = 20
TABLE_MAX_ROWS = 50
SECTION_TABLE_ROWS = 25
SLA_START = pd.Timestamp("2026-04-09")

DESTINO_ESTOQUE = "25d5c356-32a8-4221-84ae-8230061b9163"
FINALIDADE_SALDO_ESTOQUE = "e8031b09-2d30-414d-af5b-16e43a41618b"

load_env_file()

PG_CFG = {
    "host": os.getenv("AURA_POSTGRES_HOST", ""),
    "port": int(os.getenv("AURA_POSTGRES_PORT", "5432")),
    "database": os.getenv("AURA_POSTGRES_NAME", ""),
    "user": os.getenv("AURA_POSTGRES_USER", ""),
    "password": os.getenv("AURA_POSTGRES_PASSWORD", ""),
}


def fmt_int(value: int) -> str:
    return f"{int(value):,}".replace(",", ".")


def fmt_pct(num: int, den: int) -> str:
    if den <= 0:
        return "0,0%"
    return f"{(num / den) * 100:.1f}%".replace(".", ",")


def clean_text(series: pd.Series) -> pd.Series:
    return series.fillna("").astype(str).str.strip()


def normalize_key(series: pd.Series) -> pd.Series:
    return series.fillna("").astype(str).str.strip().str.upper()


def refresh_return_status(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty or not RECEBIMENTO_RESUMO_FILE.exists() or "Logger" not in df.columns:
        return df

    resumo = pd.read_pickle(RECEBIMENTO_RESUMO_FILE)
    if "Ultimo_Historico" in resumo.columns and "dt_historico" not in resumo.columns:
        resumo = resumo.rename(columns={"Ultimo_Historico": "dt_historico"})
    if not {"ds_tag", "dt_historico"}.issubset(resumo.columns):
        return df

    retorno = resumo[["ds_tag", "dt_historico"]].copy()
    retorno["_logger_key"] = normalize_key(retorno["ds_tag"])
    retorno["dt_historico"] = pd.to_datetime(retorno["dt_historico"], errors="coerce")
    retorno = (
        retorno[retorno["_logger_key"].ne("") & retorno["dt_historico"].notna()]
        .groupby("_logger_key", as_index=False)["dt_historico"]
        .max()
    )

    out = df.copy()
    out["_logger_key"] = normalize_key(out["Logger"])
    out = out.merge(retorno, how="left", on="_logger_key")
    historico_atual = (
        pd.to_datetime(out["Ultimo_Historico"], errors="coerce")
        if "Ultimo_Historico" in out.columns
        else pd.Series(pd.NaT, index=out.index)
    )
    out["Ultimo_Historico"] = out["dt_historico"].combine_first(historico_atual)
    entrega_dt = pd.to_datetime(out.get("Data de Entrega"), errors="coerce")
    out["Status Retorno"] = "Pendente de Retorno"
    out.loc[out["Ultimo_Historico"].notna() & entrega_dt.notna() & (out["Ultimo_Historico"] > entrega_dt), "Status Retorno"] = "Retornado"
    return out.drop(columns=["_logger_key", "dt_historico"], errors="ignore")


def load_data() -> pd.DataFrame:
    if not MODEL_FILE.exists():
        raise FileNotFoundError(
            f"Nao encontrei o snapshot necessario: {MODEL_FILE}"
        )
    df = pd.read_pickle(MODEL_FILE).copy()
    return refresh_return_status(df)


def _build_pg_url() -> URL:
    return URL.create(
        "postgresql+psycopg2",
        username=PG_CFG["user"],
        password=PG_CFG["password"],
        host=PG_CFG["host"],
        port=PG_CFG["port"],
        database=PG_CFG["database"],
    )


def _read_pg(sql: str, params: dict | None = None) -> pd.DataFrame:
    engine = create_engine(
        _build_pg_url(),
        pool_pre_ping=True,
    )
    with engine.connect() as conn:
        return pd.read_sql(text(sql), conn, params=params)


def prepare_data(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()

    required = [
        "Logger",
        "Pedido",
        "Agente",
        "UF",
        "Data de Entrega",
        "Tipo Datalogger",
        "Status Retorno",
    ]
    for col in required:
        if col not in out.columns:
            out[col] = ""

    for col in ["Logger", "Pedido", "Agente", "UF", "Tipo Datalogger", "Status Retorno"]:
        out[col] = clean_text(out[col])

    for col in ["UF Destino", "Cidade Destino", "Destinatario", "Ultimo_Historico"]:
        if col in out.columns:
            out[col] = out[col]
        else:
            out[col] = ""

    out["Data de Entrega"] = pd.to_datetime(out["Data de Entrega"], errors="coerce")
    out = out[out["Logger"].ne("")].copy()
    out = out[out["Data de Entrega"].notna()].copy()

    today = pd.Timestamp.now().normalize()
    cutoff = today - pd.Timedelta(days=WINDOW_DAYS)
    out = out[out["Data de Entrega"] >= cutoff].copy()

    out["_UltimoHistoricoDT"] = pd.to_datetime(out["Ultimo_Historico"], errors="coerce")
    out = (
        out.sort_values(
            ["Data de Entrega", "_UltimoHistoricoDT", "Pedido", "Logger"],
            ascending=[False, False, True, True],
        )
        .drop_duplicates(subset=["Pedido", "Logger"], keep="first")
        .copy()
    )

    out["Dia"] = out["Data de Entrega"].dt.normalize()
    out["_data_entrega_txt"] = out["Data de Entrega"].dt.strftime("%d/%m/%Y %H:%M:%S")
    out["_dia_txt"] = out["Dia"].dt.strftime("%d/%m/%Y")
    out["_is_today"] = out["Dia"].eq(today)
    out["_is_yesterday"] = out["Dia"].eq(today - pd.Timedelta(days=1))

    if "UF Destino" in out.columns:
        out["UF Destino"] = clean_text(out["UF Destino"]).replace("", "SEM UF")
    if "Cidade Destino" in out.columns:
        out["Cidade Destino"] = clean_text(out["Cidade Destino"]).replace("", "SEM CIDADE")
    if "Destinatario" in out.columns:
        out["Destinatario"] = clean_text(out["Destinatario"]).replace("", "SEM DESTINATARIO")

    out["UF"] = out["UF"].replace("", "SEM UF")
    out["Tipo Datalogger"] = out["Tipo Datalogger"].replace("", "SEM TIPO")
    if "Status Retorno" in out.columns:
        out["Status Retorno"] = out["Status Retorno"].replace("", "SEM STATUS")

    out = out.sort_values(["Data de Entrega", "Logger"], ascending=[False, True]).reset_index(drop=True)
    out = out.drop(columns=["_UltimoHistoricoDT"], errors="ignore")
    return out


def prepare_sla_deliveries(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()

    required = [
        "Logger",
        "Pedido",
        "Agente",
        "Motorista",
        "UF",
        "Data de Entrega",
        "Tipo Datalogger",
        "Status Retorno",
    ]
    for col in required:
        if col not in out.columns:
            out[col] = ""

    for col in ["Logger", "Pedido", "Agente", "Motorista", "UF", "Tipo Datalogger", "Status Retorno"]:
        out[col] = clean_text(out[col])

    for col in ["UF Destino", "Cidade Destino", "Destinatario", "Ultimo_Historico"]:
        if col not in out.columns:
            out[col] = ""

    out["Data de Entrega"] = pd.to_datetime(out["Data de Entrega"], errors="coerce")
    out = out[out["Logger"].ne("")].copy()
    out = out[out["Data de Entrega"].notna()].copy()
    out = out[out["Data de Entrega"] >= SLA_START].copy()

    out["_UltimoHistoricoDT"] = pd.to_datetime(out["Ultimo_Historico"], errors="coerce")
    out = out.sort_values(
        ["Logger", "Data de Entrega", "_UltimoHistoricoDT", "Pedido"],
        ascending=[True, True, False, True],
    ).copy()
    dedupe_cols = [
        col
        for col in [
            "Pedido",
            "Logger",
            "Agente",
            "Motorista",
            "UF",
            "Data de Entrega",
            "Tipo Datalogger",
            "Status Retorno",
            "Ultimo_Historico",
            "UF Destino",
            "Cidade Destino",
            "Destinatario",
        ]
        if col in out.columns
    ]
    out = out.drop_duplicates(subset=dedupe_cols, keep="first").copy()

    out["DiaEntrega"] = out["Data de Entrega"].dt.normalize()
    out = out.sort_values(["Logger", "Data de Entrega", "Pedido"], ascending=[True, True, True]).reset_index(drop=True)
    out = out.drop(columns=["_UltimoHistoricoDT"], errors="ignore")
    return out


def load_sla_returns() -> pd.DataFrame:
    sql = """
    SELECT
        upper(trim(d.ds_tag)) AS logger,
        dh.dt_inclusao AS dt_retorno
    FROM public.tbddataloggerhistoricos dh
    INNER JOIN vwtipos tam
        ON ds_tipo = 'tipoacaomovimentacao' AND dh.tp_acaomovimentacao = tam.id
    INNER JOIN public.tbdcaddataloggerdestinos dd
        ON dh.id_destino = dd.id_destino
    INNER JOIN public.tbdcaddataloggerfinalidades df
        ON dh.id_finalidade = df.id_finalidade
    INNER JOIN tbdcaddataloggers d
        ON dh.id_datalogger = d.id_datalogger
    WHERE dh.dt_inclusao >= :cutoff
      AND dh.tp_acaomovimentacao IN (2, 12)
      AND dh.id_destino = :destino_estoque
      AND dh.id_finalidade = :finalidade_saldo_estoque
    """
    try:
        df = _read_pg(
            sql,
            {
                "cutoff": SLA_START.to_pydatetime(),
                "destino_estoque": DESTINO_ESTOQUE,
                "finalidade_saldo_estoque": FINALIDADE_SALDO_ESTOQUE,
            },
        )
    except Exception:
        return pd.DataFrame(columns=["Logger", "dt_retorno"])

    if df.empty:
        return pd.DataFrame(columns=["Logger", "dt_retorno"])

    df.columns = [str(col).strip() for col in df.columns]
    df = df.rename(columns={col: "Logger" for col in df.columns if str(col).lower() == "logger"})
    df["Logger"] = clean_text(df["Logger"]).str.upper()
    df["dt_retorno"] = pd.to_datetime(df["dt_retorno"], errors="coerce")
    df = df[df["Logger"].ne("") & df["dt_retorno"].notna()].copy()
    df = df.sort_values(["Logger", "dt_retorno"], ascending=[True, True]).reset_index(drop=True)
    return df


def build_sla_pairs(deliveries: pd.DataFrame, returns: pd.DataFrame) -> pd.DataFrame:
    if deliveries.empty:
        return pd.DataFrame(columns=[
            "Pedido", "Logger", "Agente", "Motorista", "UF", "Data de Entrega",
            "Data de Retorno", "SLA Horas", "SLA Dias", "Status SLA"
        ])

    deliv = deliveries.copy()
    deliv["Logger"] = clean_text(deliv["Logger"]).str.upper()
    deliv["Pedido"] = clean_text(deliv["Pedido"])
    deliv["Data de Entrega"] = pd.to_datetime(deliv["Data de Entrega"], errors="coerce")
    deliv = deliv[deliv["Logger"].ne("") & deliv["Data de Entrega"].notna()].copy()
    deliv = deliv.sort_values(["Logger", "Data de Entrega", "Pedido"], ascending=[True, True, True]).reset_index(drop=True)

    ret_map: dict[str, list[pd.Timestamp]] = {}
    if not returns.empty:
        ret_base = returns.copy()
        ret_base["Logger"] = clean_text(ret_base["Logger"]).str.upper()
        ret_base["dt_retorno"] = pd.to_datetime(ret_base["dt_retorno"], errors="coerce")
        ret_base = ret_base[ret_base["Logger"].ne("") & ret_base["dt_retorno"].notna()].copy()
        ret_base = ret_base.sort_values(["Logger", "dt_retorno"], ascending=[True, True])
        ret_map = ret_base.groupby("Logger")["dt_retorno"].apply(list).to_dict()

    rows = []
    for logger, grp in deliv.groupby("Logger", sort=False):
        retorno_list = ret_map.get(logger, [])
        r_idx = 0
        for _, row in grp.iterrows():
            entrega = row["Data de Entrega"]
            while r_idx < len(retorno_list) and retorno_list[r_idx] <= entrega:
                r_idx += 1
            retorno = retorno_list[r_idx] if r_idx < len(retorno_list) else pd.NaT
            if pd.notna(retorno):
                sla_hours = (retorno - entrega).total_seconds() / 3600
                rows.append({
                    "Pedido": row.get("Pedido", ""),
                    "Logger": logger,
                    "Agente": row.get("Agente", ""),
                    "Motorista": row.get("Motorista", ""),
                    "UF": row.get("UF", ""),
                    "Data de Entrega": entrega,
                    "Data de Retorno": retorno,
                    "SLA Horas": sla_hours,
                    "SLA Dias": sla_hours / 24,
                    "Status SLA": "Retornado",
                })
                r_idx += 1
            else:
                rows.append({
                    "Pedido": row.get("Pedido", ""),
                    "Logger": logger,
                    "Agente": row.get("Agente", ""),
                    "Motorista": row.get("Motorista", ""),
                    "UF": row.get("UF", ""),
                    "Data de Entrega": entrega,
                    "Data de Retorno": pd.NaT,
                    "SLA Horas": None,
                    "SLA Dias": None,
                    "Status SLA": "Pendente",
                })

    out = pd.DataFrame(rows)
    if out.empty:
        return out
    out["DiaEntrega"] = pd.to_datetime(out["Data de Entrega"], errors="coerce").dt.normalize()
    out = out.sort_values(["Data de Entrega", "Logger", "Pedido"], ascending=[False, True, True]).reset_index(drop=True)
    return out


def fmt_duration_hours(hours: float) -> str:
    if pd.isna(hours):
        return "--"
    total_minutes = int(round(float(hours) * 60))
    days, rem = divmod(total_minutes, 24 * 60)
    hrs, mins = divmod(rem, 60)
    parts = []
    if days:
        parts.append(f"{days}d")
    if hrs:
        parts.append(f"{hrs}h")
    if mins or not parts:
        parts.append(f"{mins}m")
    return " ".join(parts)




def build_sla_section() -> str:
    try:
        deliveries_raw = load_data()
        deliveries = prepare_sla_deliveries(deliveries_raw)
        returns = load_sla_returns()
        pairs = build_sla_pairs(deliveries, returns)
    except Exception as exc:
        msg = escape(f"Nao foi possivel montar o SLA de retorno agora: {type(exc).__name__}")
        return f"""
        <div class="section">
          <div class="section-head">
            <div>
              <h2>SLA de Retorno</h2>
              <p>Base desde 09/04/2026</p>
            </div>
          </div>
          <div class="empty-box">{msg}</div>
        </div>
        """

    total = len(pairs)
    returned = pairs.loc[pairs["Status SLA"].eq("Retornado")].copy()
    pending = pairs.loc[pairs["Status SLA"].eq("Pendente")].copy()

    retornados = len(returned)
    pendentes = len(pending)

    ret_hours = returned["SLA Horas"].dropna() if not returned.empty else pd.Series(dtype=float)
    avg_hours = float(ret_hours.mean()) if not ret_hours.empty else float("nan")
    pct_48_count = int((ret_hours <= 48).sum()) if not ret_hours.empty else 0
    pct_7d_count = int((ret_hours <= 24 * 7).sum()) if not ret_hours.empty else 0
    pct_14d_count = int((ret_hours <= 24 * 14).sum()) if not ret_hours.empty else 0
    pct_20d_count = int((ret_hours <= 24 * 20).sum()) if not ret_hours.empty else 0

    now = pd.Timestamp.now()
    pending_view = pending.copy()
    if pending_view.empty:
        pending_view["Dias sem retorno"] = pd.Series(dtype=float)
    else:
        entrega_dt = pd.to_datetime(pending_view["Data de Entrega"], errors="coerce")
        pending_view["Dias sem retorno"] = (now - entrega_dt).dt.total_seconds() / 86400
        pending_view = pending_view.loc[pending_view["Dias sem retorno"] > 10].copy()
        pending_view = pending_view.sort_values(["Dias sem retorno", "Data de Entrega"], ascending=[False, True]).copy()

    bucket_df = pd.DataFrame(columns=["Faixa", "Pendentes"])
    if not pending_view.empty:
        bucket_source = pending_view.copy()
        bucket_source["Faixa"] = pd.cut(
            bucket_source["Dias sem retorno"],
            bins=[10, 14, 20, 30, float("inf")],
            labels=["11-14 dias", "15-20 dias", "21-30 dias", "31+ dias"],
            include_lowest=False,
            right=True,
        )
        bucket_df = (
            bucket_source.groupby("Faixa", as_index=False, observed=False)
            .size()
            .rename(columns={"size": "Pendentes"})
        )
        bucket_df["Faixa"] = pd.Categorical(
            bucket_df["Faixa"],
            categories=["11-14 dias", "15-20 dias", "21-30 dias", "31+ dias"],
            ordered=True,
        )
        bucket_df = bucket_df.sort_values("Faixa")

        rank_df = pd.DataFrame(columns=["Agente", "Pendentes", "MediaDias", "MaiorDia"])
        if not pending_view.empty:
            rank_source = pending_view.copy()
            rank_source["Agente"] = rank_source["Agente"].fillna("").astype(str).str.strip()
            rank_source = rank_source[rank_source["Agente"].ne("")].copy()
        else:
            rank_source = pending_view
        if not rank_source.empty:
            rank_df = (
                rank_source.groupby("Agente", as_index=False)
                .agg(
                    Pendentes=("Logger", "count"),
                    MediaDias=("Dias sem retorno", "mean"),
                    MaiorDia=("Dias sem retorno", "max"),
                )
                .sort_values(["Pendentes", "MaiorDia"], ascending=[False, False])
                .head(10)
                .copy()
            )
            rank_df["Agente"] = rank_df["Agente"].fillna("").astype(str).str.strip()
            rank_df["PendentesTxt"] = rank_df["Pendentes"].map(lambda v: fmt_int(int(v)))

    pending_export = pending_view.copy()
    pending_export["Data de Entrega"] = pd.to_datetime(pending_export["Data de Entrega"], errors="coerce").dt.strftime("%d/%m/%Y %H:%M:%S").fillna("")
    if "Ultimo_Historico" in pending_export.columns:
        pending_export["Ultimo_Historico"] = pd.to_datetime(pending_export["Ultimo_Historico"], errors="coerce").dt.strftime("%d/%m/%Y %H:%M:%S").fillna("")
    pending_export["Dias sem retorno"] = pd.to_numeric(pending_export["Dias sem retorno"], errors="coerce").round(1)
    pending_export["Dias sem retorno"] = pending_export["Dias sem retorno"].map(lambda v: str(v).replace(".", ",") if pd.notna(v) else "")
    pending_export["Status SLA"] = "Pendente"

    pending_cols = [
        "Pedido",
        "Logger",
        "Tipo Datalogger",
        "Agente",
        "Motorista",
        "UF",
        "UF Destino",
        "Cidade Destino",
        "Destinatario",
        "Data de Entrega",
        "Ultimo_Historico",
        "Dias sem retorno",
        "Status SLA",
    ]
    pending_export = pending_export[[c for c in pending_cols if c in pending_export.columns]].copy()
    pending_export.to_csv(OUTPUT_SLA_PENDING_CSV, index=False, sep=";", encoding="utf-8-sig")

    if pending_export.empty:
        pending_html = '<div class="empty-box">Nenhum logger pendente ha mais de 10 dias.</div>'
    else:
        pending_html = pending_export.head(50).to_html(index=False, escape=True, classes="data-table", border=0)

    bucket_chart_html = '<div class="empty-box">Sem faixas de atraso para mostrar.</div>'
    if not bucket_df.empty:
        bucket_fig = px.bar(
            bucket_df,
            x="Faixa",
            y="Pendentes",
            text="Pendentes",
            title="Faixas de atraso",
            color_discrete_sequence=["#6f9eff"],
        )
        bucket_fig.update_traces(textposition="outside", cliponaxis=False)
        bucket_fig.update_layout(
            template="plotly_dark",
            paper_bgcolor="#0b1020",
            plot_bgcolor="#0b1020",
            margin=dict(l=18, r=18, t=48, b=36),
            height=320,
            font=dict(color="#e5eefc"),
            xaxis=dict(gridcolor="#25304a", tickangle=-15),
            yaxis=dict(gridcolor="#25304a", title="Pendentes"),
            title=dict(x=0.02, font=dict(size=15)),
            bargap=0.25,
        )
        bucket_chart_html = fig_div(bucket_fig, "sla-bucket-chart")

    rank_chart_html = '<div class="empty-box">Sem ranking de atraso para mostrar.</div>'
    if not rank_df.empty:
        rank_fig = px.bar(
            rank_df.sort_values("Pendentes", ascending=True),
            x="Pendentes",
            y="Agente",
            orientation="h",
            text="PendentesTxt",
            title="Agentes com mais pendencias acima de 10 dias",
            color_discrete_sequence=["#2f6fd6"],
        )
        rank_fig.update_traces(textposition="inside", cliponaxis=False)
        rank_fig.update_layout(
            template="plotly_dark",
            paper_bgcolor="#0b1020",
            plot_bgcolor="#0b1020",
            margin=dict(l=18, r=18, t=48, b=36),
            height=320,
            font=dict(color="#e5eefc"),
            xaxis=dict(gridcolor="#25304a", title="Pendencias"),
            yaxis=dict(gridcolor="#25304a", autorange="reversed"),
            title=dict(x=0.02, font=dict(size=15)),
        )
        rank_chart_html = fig_div(rank_fig, "sla-rank-chart")

    return f"""
    <div class="section" id="sla-retorno">
      <div class="section-head">
        <div>
          <h2>SLA de Retorno</h2>
          <p>Base desde 09/04/2026. Cada entrega e pareada com o primeiro retorno posterior do mesmo logger.</p>
        </div>
        <div style="display:flex; gap:10px; flex-wrap:wrap;">
          <a class="btn" href="CONTROLE_ENTREGAS_20D_SLA_PENDENTES.csv" download>Baixar pendentes SLA</a>
          <a class="btn" href="#top" onclick="scrollToSection('top'); return false;">Voltar ao topo</a>
        </div>
      </div>
      <div class="kpis">
        <div class="kpi"><div class="label">Entregas no SLA</div><div class="value">{fmt_int(total)}</div><div class="foot">Entregas consideradas desde 09/04</div></div>
        <div class="kpi"><div class="label">Retornados</div><div class="value">{fmt_int(retornados)}</div><div class="foot">Com retorno pareado</div></div>
        <div class="kpi"><div class="label">Pendentes</div><div class="value">{fmt_int(pendentes)}</div><div class="foot">Sem retorno ainda</div></div>
        <div class="kpi"><div class="label">SLA medio</div><div class="value">{fmt_duration_hours(avg_hours)}</div><div class="foot">Media entre entrega e retorno</div></div>
      </div>
      <div class="kpis" style="margin-top:12px;">
        <div class="kpi"><div class="label">% retorno em 48h</div><div class="value">{fmt_pct(pct_48_count, retornados)}</div><div class="foot">{fmt_int(pct_48_count)} de {fmt_int(retornados)} retornos</div></div>
        <div class="kpi"><div class="label">% retorno em 7 dias</div><div class="value">{fmt_pct(pct_7d_count, retornados)}</div><div class="foot">{fmt_int(pct_7d_count)} de {fmt_int(retornados)} retornos</div></div>
        <div class="kpi"><div class="label">% retorno em 14 dias</div><div class="value">{fmt_pct(pct_14d_count, retornados)}</div><div class="foot">{fmt_int(pct_14d_count)} de {fmt_int(retornados)} retornos</div></div>
        <div class="kpi"><div class="label">% retorno em 20 dias</div><div class="value">{fmt_pct(pct_20d_count, retornados)}</div><div class="foot">{fmt_int(pct_20d_count)} de {fmt_int(retornados)} retornos</div></div>
      </div>
      <div class="two-col">
        <div class="section" style="margin-top:16px;">
          <div class="section-head">
            <div>
              <h3 class="section-title">Faixas de atraso</h3>
              <p class="section-note">Somente pendentes com mais de 10 dias</p>
            </div>
          </div>
          {bucket_chart_html}
        </div>
        <div class="section" style="margin-top:16px;">
          <div class="section-head">
            <div>
              <h3 class="section-title">Agentes responsaveis</h3>
              <p class="section-note">Top 10 por quantidade de pendencias acima de 10 dias</p>
            </div>
          </div>
          {rank_chart_html}
        </div>
      </div>
      <div class="section" style="margin-top:16px;">
        <div class="section-head">
          <div>
            <h3 class="section-title">Lista de pendencias</h3>
            <p class="section-note">Somente loggers pendentes ha mais de 10 dias</p>
          </div>
        </div>
        <div class="meta-strip" style="margin-bottom:12px;">
          <div><strong>Media de retorno:</strong> {fmt_duration_hours(avg_hours)}</div>
          <div><strong>Pendentes &gt; 10 dias:</strong> {fmt_int(len(pending_export))}</div>
          <div><strong>Base SLA:</strong> desde 09/04/2026</div>
        </div>
        {pending_html}
      </div>
    </div>
    """


def _series_by_day(df: pd.DataFrame) -> pd.DataFrame:
    today = pd.Timestamp.now().normalize()
    start = today - pd.Timedelta(days=WINDOW_DAYS)
    days = pd.DataFrame({"Dia": pd.date_range(start=start, end=today, freq="D")})
    if df.empty:
        days["Loggers"] = 0
        return days
    agg = (
        df.groupby("Dia", as_index=False)["Logger"]
        .nunique()
        .rename(columns={"Logger": "Loggers"})
    )
    out = days.merge(agg, how="left", on="Dia")
    out["Loggers"] = out["Loggers"].fillna(0).astype(int)
    return out


def _daily_return_stack(df: pd.DataFrame) -> pd.DataFrame:
    today = pd.Timestamp.now().normalize()
    start = today - pd.Timedelta(days=WINDOW_DAYS)
    days = pd.DataFrame({"Dia": pd.date_range(start=start, end=today, freq="D")})
    if df.empty:
        days["Retornado"] = 0
        days["Pendente"] = 0
        days["Total"] = 0
        days["PctRetornado"] = 0.0
        return days

    base = df.copy()
    base["Dia"] = pd.to_datetime(base["Dia"], errors="coerce").dt.normalize()
    base = base[base["Dia"].notna()].copy()
    base["Status Retorno"] = base["Status Retorno"].fillna("").astype(str).str.strip()

    agg = (
        base.assign(
            Retornado=base["Status Retorno"].eq("Retornado").astype(int),
            Pendente=base["Status Retorno"].eq("Pendente de Retorno").astype(int),
        )
        .groupby("Dia", as_index=False)[["Retornado", "Pendente"]]
        .sum()
    )
    out = days.merge(agg, how="left", on="Dia")
    out[["Retornado", "Pendente"]] = out[["Retornado", "Pendente"]].fillna(0).astype(int)
    out["Total"] = out["Retornado"] + out["Pendente"]
    out["PctRetornado"] = out["Retornado"].where(out["Total"].gt(0), 0) / out["Total"].where(out["Total"].gt(0), 1) * 100
    return out


def _agent_return_stack(df: pd.DataFrame, limit: int = 12) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(columns=["Agente", "Retornado", "Pendente", "Total", "PctRetornado"])

    base = df.copy()
    base["Agente"] = base["Agente"].fillna("").astype(str).str.strip()
    base = base[base["Agente"].ne("")].copy()
    if base.empty:
        return pd.DataFrame(columns=["Agente", "Retornado", "Pendente", "Total", "PctRetornado"])
    base["Status Retorno"] = base["Status Retorno"].fillna("").astype(str).str.strip()

    agg = (
        base.assign(
            Retornado=base["Status Retorno"].eq("Retornado").astype(int),
            Pendente=base["Status Retorno"].eq("Pendente de Retorno").astype(int),
        )
        .groupby("Agente", as_index=False)[["Retornado", "Pendente"]]
        .sum()
    )
    agg["Total"] = agg["Retornado"] + agg["Pendente"]
    agg["PctRetornado"] = agg["Retornado"].where(agg["Total"].gt(0), 0) / agg["Total"].where(agg["Total"].gt(0), 1) * 100
    agg = agg.sort_values("Total", ascending=False).head(limit)
    agg = agg.reset_index(drop=True)
    return agg


def _top_series(df: pd.DataFrame, col: str, label: str, limit: int = 8) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(columns=[label, "Loggers"])
    out = (
        df.groupby(col, as_index=False)["Logger"]
        .nunique()
        .rename(columns={col: label, "Logger": "Loggers"})
        .sort_values("Loggers", ascending=False)
        .head(limit)
    )
    return out


DAY_COLOR = "#6f9eff"
AGENT_COLOR = "#2f6fd6"
UF_COLOR = "#9bc7ff"


def make_bar_chart(
    df: pd.DataFrame,
    x: str,
    y: str,
    title: str,
    color: str = "#7aa2ff",
    orientation: str = "v",
    height: int = 320,
    show_text: bool = True,
):
    if df.empty:
        fig = px.bar(title=title)
        fig.add_annotation(
            text="Sem dados para exibir",
            x=0.5,
            y=0.5,
            xref="paper",
            yref="paper",
            showarrow=False,
            font=dict(color="#cbd5e1", size=16),
        )
        fig.update_layout(template="plotly_dark", paper_bgcolor="#0b1020", plot_bgcolor="#0b1020")
        return fig
    chart_df = df.copy()
    if orientation == "h" and y in chart_df.columns:
        chart_df = chart_df.sort_values(x, ascending=False)
    fig = px.bar(chart_df, x=x, y=y, title=title, color_discrete_sequence=[color], orientation=orientation)
    trace_kwargs = dict(
        cliponaxis=False,
        marker_line_width=0,
    )
    if show_text:
        text_template = "%{x}" if orientation == "h" else "%{y}"
        trace_kwargs["texttemplate"] = text_template
        trace_kwargs["textposition"] = "outside"
        if orientation == "h":
            trace_kwargs["textposition"] = "inside"
            trace_kwargs["insidetextanchor"] = "end"
            trace_kwargs["textfont"] = dict(color="#000000")
    fig.update_traces(**trace_kwargs)
    fig.update_layout(
        template="plotly_dark",
        paper_bgcolor="#0b1020",
        plot_bgcolor="#0b1020",
        margin=dict(l=20, r=20, t=52, b=48),
        height=height,
        font=dict(color="#e5eefc"),
        xaxis=dict(gridcolor="#25304a"),
        yaxis=dict(
            gridcolor="#25304a",
            categoryorder="array" if orientation == "h" else "trace",
            categoryarray=chart_df[y].tolist() if orientation == "h" else None,
            autorange="reversed" if orientation == "h" else True,
        ),
        title=dict(x=0.02, font=dict(size=15)),
        bargap=0.28,
    )
    return fig


def make_line_chart(df: pd.DataFrame):
    if df.empty:
        fig = go.Figure()
        fig.add_annotation(
            text="Sem dados para exibir",
            x=0.5,
            y=0.5,
            xref="paper",
            yref="paper",
            showarrow=False,
            font=dict(color="#cbd5e1", size=16),
        )
        fig.update_layout(template="plotly_dark", paper_bgcolor="#0b1020", plot_bgcolor="#0b1020")
        return fig
    chart_df = _daily_return_stack(df).copy()
    chart_df["DiaTxt"] = chart_df["Dia"].dt.strftime("%d/%m")
    fig = go.Figure()
    fig.add_bar(
        x=chart_df["DiaTxt"],
        y=chart_df["Retornado"],
        name="Retornado ao estoque",
        marker=dict(color="#2f6fd6", line=dict(width=0)),
        text=chart_df["PctRetornado"].map(lambda v: f"{v:.0f}%"),
        textposition="inside",
        insidetextanchor="middle",
        textfont=dict(color="#ffffff"),
        cliponaxis=False,
        hovertemplate="<b>%{x}</b><br>Retornado: %{y}<br>% retornado: %{customdata[0]:.0f}%<extra></extra>",
        customdata=chart_df[["PctRetornado"]],
    )
    fig.add_bar(
        x=chart_df["DiaTxt"],
        y=chart_df["Pendente"],
        name="Pendente de retorno",
        marker=dict(color="#9bc7ff", line=dict(width=0)),
        hovertemplate="<b>%{x}</b><br>Pendente: %{y}<extra></extra>",
    )
    for _, row in chart_df.iterrows():
        total = int(row["Total"])
        if total > 0:
            fig.add_annotation(
                x=row["DiaTxt"],
                y=total,
                text=fmt_int(total),
                showarrow=False,
                yshift=10,
                font=dict(color="#ffffff", size=11, family="Segoe UI, Tahoma, Arial, sans-serif"),
            )
    fig.update_layout(
        barmode="stack",
        template="plotly_dark",
        paper_bgcolor="#0b1020",
        plot_bgcolor="#0b1020",
        margin=dict(l=24, r=20, t=52, b=52),
        height=380,
        font=dict(color="#e5eefc"),
        xaxis=dict(gridcolor="#25304a"),
        yaxis=dict(gridcolor="#25304a"),
        title=dict(x=0.02, font=dict(size=15)),
        bargap=0.22,
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.02,
            xanchor="left",
            x=0.0,
        ),
    )
    return fig


def make_rank_chart(
    df: pd.DataFrame,
    x: str,
    y: str,
    title: str,
    color: str,
    height: int = 430,
):
    if df.empty:
        fig = px.bar(title=title)
        fig.add_annotation(
            text="Sem dados para exibir",
            x=0.5,
            y=0.5,
            xref="paper",
            yref="paper",
            showarrow=False,
            font=dict(color="#cbd5e1", size=16),
        )
        fig.update_layout(template="plotly_dark", paper_bgcolor="#0b1020", plot_bgcolor="#0b1020")
        return fig
    chart_df = df.copy().sort_values(y, ascending=True)
    fig = px.bar(
        chart_df,
        x=y,
        y=x,
        orientation="h",
        title=title,
        color_discrete_sequence=[color],
    )
    fig.update_traces(
        cliponaxis=False,
        texttemplate="%{x}",
        textposition="outside",
        marker_line_width=0,
        marker=dict(line=dict(color="rgba(255,255,255,0.05)", width=1)),
    )
    fig.update_layout(
        template="plotly_dark",
        paper_bgcolor="#0b1020",
        plot_bgcolor="#0b1020",
        margin=dict(l=22, r=24, t=56, b=36),
        height=height,
        font=dict(color="#e5eefc"),
        xaxis=dict(gridcolor="#25304a", zeroline=False),
        yaxis=dict(gridcolor="#25304a", automargin=True, categoryorder="total ascending"),
        title=dict(x=0.02, font=dict(size=15)),
        showlegend=False,
        bargap=0.22,
    )
    return fig


def make_agent_return_chart(df: pd.DataFrame) -> go.Figure:
    if df.empty:
        fig = go.Figure()
        fig.add_annotation(
            text="Sem dados para exibir",
            x=0.5,
            y=0.5,
            xref="paper",
            yref="paper",
            showarrow=False,
            font=dict(color="#cbd5e1", size=16),
        )
        fig.update_layout(template="plotly_dark", paper_bgcolor="#0b1020", plot_bgcolor="#0b1020")
        return fig

    chart_df = _agent_return_stack(df, limit=12)
    fig = go.Figure()
    fig.add_bar(
        y=chart_df["Agente"],
        x=chart_df["Retornado"],
        orientation="h",
        name="Retornado ao estoque",
        marker=dict(color="#2f6fd6", line=dict(width=0)),
        text=chart_df["PctRetornado"].map(lambda v: f"{v:.0f}%"),
        textposition="inside",
        insidetextanchor="middle",
        textfont=dict(color="#ffffff"),
        cliponaxis=False,
        customdata=chart_df[["PctRetornado"]],
        hovertemplate="<b>%{y}</b><br>Retornado: %{x}<br>% retornado: %{customdata[0]:.0f}%<extra></extra>",
    )
    fig.add_bar(
        y=chart_df["Agente"],
        x=chart_df["Pendente"],
        orientation="h",
        name="Pendente de retorno",
        marker=dict(color="#9bc7ff", line=dict(width=0)),
        cliponaxis=False,
        hovertemplate="<b>%{y}</b><br>Pendente: %{x}<extra></extra>",
    )
    for _, row in chart_df.iterrows():
        total = int(row["Total"])
        if total > 0:
            fig.add_annotation(
                x=total,
                y=row["Agente"],
                text=fmt_int(total),
                showarrow=False,
                xshift=14,
                font=dict(color="#e5eefc", size=11, family="Segoe UI, Tahoma, Arial, sans-serif"),
            )
    fig.update_layout(
        barmode="stack",
        template="plotly_dark",
        paper_bgcolor="#0b1020",
        plot_bgcolor="#0b1020",
        margin=dict(l=22, r=44, t=56, b=36),
        height=620,
        font=dict(color="#e5eefc"),
        xaxis=dict(
            gridcolor="#25304a",
            zeroline=False,
            range=[0, float(chart_df["Total"].max()) * 1.12 if not chart_df.empty else 1],
        ),
        yaxis=dict(
            gridcolor="#25304a",
            categoryorder="array",
            categoryarray=chart_df["Agente"].tolist(),
            autorange="reversed",
            automargin=True,
        ),
        title=dict(x=0.02, font=dict(size=15)),
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.02,
            xanchor="left",
            x=0.0,
        ),
        bargap=0.25,
    )
    return fig


def fig_div(fig, div_id: str) -> str:
    return pio.to_html(
        fig,
        include_plotlyjs=False,
        full_html=False,
        div_id=div_id,
        config={"displayModeBar": False, "responsive": True},
    )


def table_html(df: pd.DataFrame, columns: list[str], max_rows: int) -> tuple[str, int]:
    if df.empty:
        empty = '<div class="empty-box">Sem registros neste recorte.</div>'
        return empty, 0
    view = df.copy()
    cols = [c for c in columns if c in view.columns]
    view = view[cols].head(max_rows).copy()
    if "Data de Entrega" in view.columns:
        view["Data de Entrega"] = pd.to_datetime(view["Data de Entrega"], errors="coerce").dt.strftime("%d/%m/%Y %H:%M:%S").fillna("")
    if "Ultimo_Historico" in view.columns:
        view["Ultimo_Historico"] = pd.to_datetime(view["Ultimo_Historico"], errors="coerce").dt.strftime("%d/%m/%Y %H:%M:%S").fillna("")
    html = view.to_html(index=False, escape=True, classes="data-table", border=0)
    return html, len(view)


def _csv_frame(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    cols = [
        "Pedido",
        "Logger",
        "Tipo Datalogger",
        "Agente",
        "Motorista",
        "UF",
        "Data de Entrega",
        "Status Retorno",
        "UF Destino",
        "Cidade Destino",
        "Destinatario",
        "Ultimo_Historico",
    ]
    cols = [c for c in cols if c in out.columns]
    out = out[cols].copy()
    for col in ["Data de Entrega", "Ultimo_Historico"]:
        if col in out.columns:
            out[col] = pd.to_datetime(out[col], errors="coerce").dt.strftime("%d/%m/%Y %H:%M:%S").fillna("")
    return out


def build_page(df: pd.DataFrame) -> str:
    today = pd.Timestamp.now().normalize()
    yesterday = today - pd.Timedelta(days=1)

    total_loggers = int(df["Logger"].nunique()) if not df.empty else 0
    total_pedidos = int(df["Pedido"].nunique()) if not df.empty and "Pedido" in df.columns else 0
    total_agentes = int(df["Agente"].nunique()) if not df.empty and "Agente" in df.columns else 0
    total_retorno = int(df.loc[df["Status Retorno"].eq("Retornado"), "Logger"].nunique()) if not df.empty and "Status Retorno" in df.columns else 0
    total_hoje = int(df.loc[df["Dia"].eq(today), "Logger"].nunique()) if not df.empty else 0
    total_ontem = int(df.loc[df["Dia"].eq(yesterday), "Logger"].nunique()) if not df.empty else 0

    ult_entrega = ""
    if not df.empty:
        ult_dt = pd.to_datetime(df["Data de Entrega"], errors="coerce").max()
        if pd.notna(ult_dt):
            ult_entrega = ult_dt.strftime("%d/%m/%Y %H:%M:%S")

    daily_fig = '<div id="chart-daily" class="chart-box"></div>'
    top_agente_fig = '<div id="chart-agentes" class="chart-box"></div>'
    top_uf_fig = '<div id="chart-ufs" class="chart-box"></div>'
    today_table = '<div id="today-table" class="table-wrap"></div>'
    yest_table = '<div id="yesterday-table" class="table-wrap"></div>'
    detail_table = '<div id="detail-table" class="table-wrap"></div>'
    sla_section = build_sla_section()

    csv_frame = _csv_frame(df)
    csv_frame.to_csv(OUTPUT_CSV, index=False, sep=";", encoding="utf-8-sig")

    data_cols = [
        "Pedido",
        "Logger",
        "Agente",
        "Motorista",
        "UF",
        "Tipo Datalogger",
        "Status Retorno",
        "Data de Entrega",
        "Ultimo_Historico",
        "UF Destino",
        "Cidade Destino",
        "Destinatario",
        "Dia",
    ]
    data_json_df = df.copy()
    if "Dia" in data_json_df.columns:
        data_json_df["DiaTxt"] = pd.to_datetime(data_json_df["Dia"], errors="coerce").dt.strftime("%d/%m").fillna("")
    else:
        data_json_df["DiaTxt"] = ""
    for col in ["Data de Entrega", "Ultimo_Historico"]:
        if col in data_json_df.columns:
            data_json_df[col] = pd.to_datetime(data_json_df[col], errors="coerce").dt.strftime("%d/%m/%Y %H:%M:%S").fillna("")
    data_json_df = data_json_df[[c for c in data_cols if c in data_json_df.columns] + ["DiaTxt"]].copy()
    data_json_df = data_json_df.rename(columns={
        "Tipo Datalogger": "TipoDatalogger",
        "Status Retorno": "StatusRetorno",
        "Data de Entrega": "DataEntrega",
        "Ultimo_Historico": "UltimoHistorico",
        "UF Destino": "UFDestino",
        "Cidade Destino": "CidadeDestino",
        "Destinatario": "Destinatario",
        "Agente": "Agente",
        "UF": "UF",
        "Pedido": "Pedido",
        "Logger": "Logger",
        "DiaTxt": "DiaTxt",
        "Motorista": "Motorista",
    })
    data_json = json.dumps(data_json_df.to_dict(orient="records"), ensure_ascii=False, default=str)
    day_labels = [d.strftime("%d/%m") for d in pd.date_range(start=today - pd.Timedelta(days=WINDOW_DAYS), end=today, freq="D")]
    day_labels_json = json.dumps(day_labels, ensure_ascii=False)

    html = f"""<!doctype html>
<html lang="pt-BR">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <meta http-equiv="X-UA-Compatible" content="IE=edge" />
  <title>Controle de Entregas - Loggers</title>
  <script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>
  <style>
    :root {{
      --bg: #07101d;
      --panel: rgba(14, 22, 38, 0.96);
      --panel-2: rgba(10, 16, 29, 0.96);
      --line: rgba(122,162,255,0.16);
      --line-strong: rgba(122,162,255,0.28);
      --text: #e8eefb;
      --muted: #93a2b8;
      --accent: #8fb8ff;
      --accent-2: #4f8cff;
      --accent-3: #2dd4bf;
      --warn: #f59e0b;
      --danger: #fb7185;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: "Segoe UI", Tahoma, Arial, sans-serif;
      background:
        radial-gradient(circle at top left, rgba(122,162,255,0.13), transparent 26%),
        radial-gradient(circle at top right, rgba(79,140,255,0.12), transparent 22%),
        linear-gradient(180deg, #09111e 0%, #050911 100%);
      color: var(--text);
    }}
    .wrap {{ max-width: 1620px; margin: 0 auto; padding: 24px 18px 40px; }}
    .hero {{
      background:
        linear-gradient(135deg, rgba(17,26,44,0.98), rgba(10,16,29,0.98)),
        radial-gradient(circle at top right, rgba(122,162,255,0.10), transparent 30%);
      border: 1px solid var(--line);
      border-radius: 22px;
      padding: 24px 24px 20px;
      box-shadow: 0 18px 44px rgba(0,0,0,0.30);
      position: relative;
      overflow: hidden;
    }}
    .hero::before {{
      content: "";
      position: absolute;
      inset: 0 auto 0 0;
      width: 5px;
      background: linear-gradient(180deg, var(--accent), var(--accent-2));
    }}
    .eyebrow {{
      display: inline-flex;
      align-items: center;
      gap: 8px;
      color: var(--accent);
      font-weight: 800;
      letter-spacing: 0.10em;
      text-transform: uppercase;
      font-size: 12px;
    }}
    h1 {{
      margin: 10px 0 8px;
      font-size: 31px;
      line-height: 1.1;
    }}
    .sub {{
      color: var(--muted);
      font-size: 14px;
      line-height: 1.6;
      max-width: 1120px;
    }}
    .pill-row {{
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      margin-top: 16px;
    }}
    .filter-row {{
      display: grid;
      grid-template-columns: minmax(0, 1.2fr) minmax(0, 1.2fr) auto;
      gap: 12px;
      margin-top: 16px;
    }}
    .filter-box {{
      display: flex;
      flex-direction: column;
      gap: 6px;
    }}
    .filter-label {{
      color: var(--muted);
      font-size: 12px;
      text-transform: uppercase;
      letter-spacing: 0.06em;
      font-weight: 700;
    }}
    .filter-select {{
      width: 100%;
      border-radius: 12px;
      border: 1px solid rgba(122,162,255,0.26);
      background: rgba(9,14,25,0.92);
      color: #e8eefb;
      padding: 10px 12px;
      font-size: 13px;
      outline: none;
    }}
    .filter-select:focus {{
      border-color: rgba(122,162,255,0.48);
      box-shadow: 0 0 0 3px rgba(79,140,255,0.16);
    }}
    .pill {{
      border: 1px solid rgba(148,163,184,0.22);
      border-radius: 999px;
      padding: 8px 12px;
      background: rgba(255,255,255,0.03);
      color: var(--text);
      font-size: 13px;
    }}
    .kpis {{
      display: grid;
      grid-template-columns: repeat(6, minmax(0, 1fr));
      gap: 12px;
      margin: 16px 0 18px;
    }}
    .kpi {{
      background:
        linear-gradient(180deg, rgba(17,26,44,0.98), rgba(11,18,32,0.98));
      border: 1px solid var(--line);
      border-radius: 18px;
      padding: 16px 16px 14px;
      min-height: 108px;
      box-shadow: inset 0 1px 0 rgba(255,255,255,0.03);
    }}
    .kpi .label {{
      color: var(--muted);
      font-size: 12px;
      text-transform: uppercase;
      letter-spacing: 0.04em;
      margin-bottom: 10px;
    }}
    .kpi .value {{
      font-size: 30px;
      font-weight: 800;
      line-height: 1;
      margin-bottom: 8px;
    }}
    .kpi .foot {{
      font-size: 12px;
      color: var(--muted);
    }}
    .chart-stack {{
      display: flex;
      flex-direction: column;
      gap: 14px;
      margin: 14px 0 18px;
    }}
    .grid2 {{
      display: grid;
      grid-template-columns: minmax(0, 2.15fr) minmax(300px, 0.95fr);
      gap: 14px;
    }}
    .panel {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 20px;
      padding: 14px 14px 10px;
      overflow: hidden;
      box-shadow: inset 0 1px 0 rgba(255,255,255,0.03);
    }}
    .panel-wide {{
      padding-bottom: 8px;
      border-color: var(--line-strong);
    }}
    .panel-title {{
      font-size: 15px;
      font-weight: 800;
      margin: 2px 0 12px;
      color: #f6f8ff;
    }}
    .chart-box {{
      min-height: 320px;
      width: 100%;
      max-height: 620px;
      overflow-y: auto;
      padding-right: 6px;
    }}
    .section {{
      margin-top: 18px;
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 20px;
      padding: 16px;
    }}
    .section h2 {{
      margin: 0 0 8px;
      font-size: 20px;
    }}
    .section p {{
      margin: 0 0 14px;
      color: var(--muted);
      font-size: 13px;
    }}
    .section-head {{
      display: flex;
      justify-content: space-between;
      gap: 10px;
      align-items: center;
      flex-wrap: wrap;
      margin-bottom: 12px;
    }}
    .btn {{
      display: inline-flex;
      align-items: center;
      gap: 8px;
      border: 1px solid rgba(122,162,255,0.26);
      background: linear-gradient(180deg, rgba(255,255,255,0.06), rgba(255,255,255,0.03));
      color: var(--text);
      text-decoration: none;
      border-radius: 12px;
      padding: 10px 14px;
      font-weight: 700;
      font-size: 13px;
    }}
    .btn:hover {{ background: rgba(255,255,255,0.08); }}
    .meta {{
      color: var(--muted);
      font-size: 13px;
    }}
    .table-wrap {{
      overflow-x: auto;
      border-radius: 14px;
      border: 1px solid rgba(148,163,184,0.16);
    }}
    table.data-table {{
      width: 100%;
      border-collapse: collapse;
      background: rgba(9,14,25,0.95);
      min-width: 1050px;
    }}
    .data-table th,
    .data-table td {{
      padding: 11px 10px;
      border-bottom: 1px solid rgba(148,163,184,0.12);
      border-right: 1px solid rgba(148,163,184,0.10);
      font-size: 12px;
      text-align: left;
      white-space: nowrap;
    }}
    .data-table th {{
      position: sticky;
      top: 0;
      background: linear-gradient(180deg, rgba(30,37,54,0.98), rgba(25,31,46,0.98));
      color: #e6efff;
      z-index: 1;
    }}
    .data-table tbody tr:nth-child(even) {{ background: rgba(255,255,255,0.015); }}
    .data-table tbody tr:hover {{ background: rgba(122,162,255,0.07); }}
    .two-col {{
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 14px;
      margin-top: 14px;
    }}
    .empty-box {{
      padding: 18px;
      color: var(--muted);
      background: rgba(10,16,29,0.92);
      border-radius: 14px;
      border: 1px dashed rgba(148,163,184,0.24);
    }}
    .footer {{
      margin-top: 18px;
      color: var(--muted);
      font-size: 12px;
    }}
    @media (max-width: 1280px) {{
      .kpis {{ grid-template-columns: repeat(3, minmax(0, 1fr)); }}
      .grid2, .two-col {{ grid-template-columns: 1fr; }}
      .filter-row {{ grid-template-columns: 1fr; }}
    }}
    @media (max-width: 720px) {{
      .wrap {{ padding: 14px 10px 24px; }}
      h1 {{ font-size: 24px; }}
      .kpis {{ grid-template-columns: 1fr 1fr; }}
    }}
  </style>
</head>
<body>
  <div class="wrap">
      <div class="hero" id="top">
      <div class="eyebrow">Controle operacional</div>
      <h1>Controle de Entregas dos Loggers</h1>
      <div class="sub">
        Visao separada para acompanhar apenas os loggers entregues nos ultimos {WINDOW_DAYS} dias.
        A pagina destaca o que foi entregue hoje, ontem e no periodo completo, com tabela limitada na visualizacao
        e exportacao completa em CSV.
      </div>
      <div class="filter-row">
        <div class="filter-box">
          <div class="filter-label">Filtro por AGENTE</div>
          <select id="filter-agente" class="filter-select"></select>
        </div>
        <div class="filter-box">
          <div class="filter-label">Filtro por UF</div>
          <select id="filter-uf" class="filter-select"></select>
        </div>
        <div class="filter-box" style="justify-content:flex-end;">
          <div class="filter-label">&nbsp;</div>
          <button id="btn-clear-filters" class="btn" type="button">Limpar filtros</button>
        </div>
      </div>
      <div class="pill-row">
        <div class="pill"><strong>Janela:</strong> ultimos {WINDOW_DAYS} dias</div>
        <div class="pill"><strong>Ultima entrega:</strong> {escape(ult_entrega) if ult_entrega else "--"}</div>
        <div class="pill"><strong>Gerado em:</strong> {datetime.now().strftime("%d/%m/%Y %H:%M:%S")}</div>
      </div>
      <div style="display:flex; justify-content:flex-end; margin-top: 12px;">
        <a class="btn" href="CONTROLE_ENTREGAS_20D.csv" download>Baixar CSV completo</a>
      </div>
      <div style="display:flex; gap:10px; flex-wrap:wrap; margin-top: 12px;">
        <a class="btn" href="#painel-operacional" onclick="scrollToSection('painel-operacional'); return false;">Ir para o painel operacional</a>
        <a class="btn" href="#sla-retorno" onclick="scrollToSection('sla-retorno'); return false;">Ir para SLA de retorno</a>
      </div>
    </div>

    <div class="kpis">
      <div class="kpi"><div class="label">Loggers no periodo</div><div class="value" id="kpi-loggers">{fmt_int(total_loggers)}</div><div class="foot">Entregas com data valida</div></div>
      <div class="kpi"><div class="label">Entregues hoje</div><div class="value" id="kpi-hoje">{fmt_int(total_hoje)}</div><div class="foot">Data de entrega igual a hoje</div></div>
      <div class="kpi"><div class="label">Entregues ontem</div><div class="value" id="kpi-ontem">{fmt_int(total_ontem)}</div><div class="foot">Referencia para o turno anterior</div></div>
      <div class="kpi"><div class="label">Pedidos unicos</div><div class="value" id="kpi-pedidos">{fmt_int(total_pedidos)}</div><div class="foot">Pedidos com logger entregue</div></div>
      <div class="kpi"><div class="label">Agentes unicos</div><div class="value" id="kpi-agentes">{fmt_int(total_agentes)}</div><div class="foot">Responsaveis da entrega</div></div>
      <div class="kpi"><div class="label">Entregues que retornaram</div><div class="value" id="kpi-retornados">{fmt_int(total_retorno)}</div><div class="foot">Da janela de entrega, com retorno ao estoque</div></div>
    </div>

    <div class="chart-stack" id="painel-operacional">
      <div class="panel panel-wide">
        <div class="panel-title">Entregas por dia</div>
        {daily_fig}
      </div>
      <div class="grid2">
        <div class="panel">
          <div class="panel-title">Top agentes</div>
          {top_agente_fig}
        </div>
        <div class="panel">
          <div class="panel-title">Top UFs</div>
          {top_uf_fig}
        </div>
      </div>
    </div>

    <div class="section">
      <div class="section-head">
        <div>
          <h2>Entregas de hoje</h2>
          <p id="today-summary">{fmt_int(total_hoje)} registro(s) encontrados para a data atual.</p>
        </div>
      </div>
      {today_table}
    </div>

    <div class="section">
      <div class="section-head">
        <div>
          <h2>Entregas de ontem</h2>
          <p id="yesterday-summary">{fmt_int(total_ontem)} registro(s) encontrados para o dia anterior.</p>
        </div>
      </div>
      {yest_table}
    </div>

    <div class="section">
      <div class="section-head">
        <div>
          <h2>Detalhe do periodo</h2>
          <p id="detail-summary">{fmt_int(len(df))} linhas exibidas na tabela; o CSV completo sai em <code>CONTROLE_ENTREGAS_20D.csv</code>.</p>
        </div>
      </div>
      {detail_table}
    </div>

    {sla_section}

    <div class="footer">
      Fonte: snapshot_reversa/modelo_final.pkl. O recorte considera apenas loggers com data de entrega valida e janela dos ultimos {WINDOW_DAYS} dias.
    </div>
    <script>
      function scrollToSection(id) {{
        const target = document.getElementById(id);
        if (target) {{
          target.scrollIntoView({{ behavior: "smooth", block: "start" }});
          if (history.replaceState) {{
            history.replaceState(null, "", "#" + id);
          }}
        }}
      }}

      const RAW_DATA = {data_json};
      const DAY_LABELS = {day_labels_json};
      const TABLE_COLUMNS = [
        {{ key: "Pedido", label: "Pedido" }},
        {{ key: "Logger", label: "Logger" }},
        {{ key: "TipoDatalogger", label: "Tipo Datalogger" }},
        {{ key: "Agente", label: "Agente" }},
        {{ key: "UF", label: "UF" }},
        {{ key: "DataEntrega", label: "Data de Entrega" }},
        {{ key: "StatusRetorno", label: "Status Retorno" }},
        {{ key: "UFDestino", label: "UF Destino" }},
        {{ key: "CidadeDestino", label: "Cidade Destino" }},
        {{ key: "Destinatario", label: "Destinatario" }}
      ];

      const state = {{ agent: "", uf: "" }};
      const els = {{
        agent: document.getElementById("filter-agente"),
        uf: document.getElementById("filter-uf"),
        clear: document.getElementById("btn-clear-filters"),
        kpiLoggers: document.getElementById("kpi-loggers"),
        kpiHoje: document.getElementById("kpi-hoje"),
        kpiOntem: document.getElementById("kpi-ontem"),
        kpiPedidos: document.getElementById("kpi-pedidos"),
        kpiAgentes: document.getElementById("kpi-agentes"),
        kpiRetornados: document.getElementById("kpi-retornados"),
        todaySummary: document.getElementById("today-summary"),
        yesterdaySummary: document.getElementById("yesterday-summary"),
        detailSummary: document.getElementById("detail-summary"),
        todayTable: document.getElementById("today-table"),
        yesterdayTable: document.getElementById("yesterday-table"),
        detailTable: document.getElementById("detail-table")
      }};

      function clean(value) {{
        return (value ?? "").toString().trim();
      }}

      function formatInt(value) {{
        return new Intl.NumberFormat("pt-BR").format(Number(value) || 0);
      }}

      function escapeHtml(value) {{
        return clean(value)
          .replaceAll("&", "&amp;")
          .replaceAll("<", "&lt;")
          .replaceAll(">", "&gt;")
          .replaceAll('"', "&quot;")
          .replaceAll("'", "&#39;");
      }}

      function uniqueSorted(values) {{
        return [...new Set(values.map(clean).filter(Boolean))].sort((a, b) => a.localeCompare(b, "pt-BR"));
      }}

      function parseBrDateTime(value) {{
        const txt = clean(value);
        if (!txt) return 0;
        const parts = txt.split(" ");
        const datePart = parts[0] || "";
        const timePart = parts[1] || "00:00:00";
        const d = datePart.split("/");
        const t = timePart.split(":");
        if (d.length !== 3) return 0;
        const day = parseInt(d[0], 10) || 1;
        const month = (parseInt(d[1], 10) || 1) - 1;
        const year = parseInt(d[2], 10) || 1970;
        const hour = parseInt(t[0], 10) || 0;
        const minute = parseInt(t[1], 10) || 0;
        const second = parseInt(t[2], 10) || 0;
        return new Date(year, month, day, hour, minute, second).getTime();
      }}

      function cloneRow(row) {{
        return Object.assign({{}}, row);
      }}

      function filterRows() {{
        return RAW_DATA.filter(function(row) {{
          const agentOk = !state.agent || clean(row.Agente) === state.agent;
          const ufOk = !state.uf || clean(row.UF) === state.uf;
          return agentOk && ufOk;
        }});
      }}

      function setupFilters() {{
        const agents = uniqueSorted(RAW_DATA.map((r) => r.Agente));
        const ufs = uniqueSorted(RAW_DATA.map((r) => r.UF));

        let agentHtml = '<option value="">Todos os agentes</option>';
        agents.forEach(function(item) {{
          agentHtml += '<option value="' + escapeHtml(item) + '">' + escapeHtml(item) + '</option>';
        }});
        els.agent.innerHTML = agentHtml;

        let ufHtml = '<option value="">Todas as UFs</option>';
        ufs.forEach(function(item) {{
          ufHtml += '<option value="' + escapeHtml(item) + '">' + escapeHtml(item) + '</option>';
        }});
        els.uf.innerHTML = ufHtml;

        els.agent.addEventListener("change", function(ev) {{
          state.agent = ev.target.value;
          renderAll();
        }});
        els.uf.addEventListener("change", function(ev) {{
          state.uf = ev.target.value;
          renderAll();
        }});
        els.clear.addEventListener("click", function() {{
          state.agent = "";
          state.uf = "";
          els.agent.value = "";
          els.uf.value = "";
          renderAll();
        }});
      }}

      function countUnique(rows, key) {{
        return new Set(rows.map((row) => clean(row[key])).filter(Boolean)).size;
      }}

      function countByStatus(rows, status) {{
        return new Set(rows.filter((row) => clean(row.StatusRetorno) === status).map((row) => clean(row.Logger)).filter(Boolean)).size;
      }}

      function countUniqueByDate(rows, targetDate) {{
        return new Set(
          rows
            .filter(function(row) {{ return clean(row.DataEntrega).startsWith(targetDate); }})
            .map(function(row) {{ return clean(row.Logger); }})
            .filter(Boolean)
        ).size;
      }}

      function todayKey() {{
        return new Date().toLocaleDateString("pt-BR");
      }}

      function yesterdayKey() {{
        const d = new Date();
        d.setDate(d.getDate() - 1);
        return d.toLocaleDateString("pt-BR");
      }}

      function buildDailySeries(rows) {{
        const buckets = Object.fromEntries(DAY_LABELS.map((day) => [day, {{ ret: new Set(), pend: new Set() }}]));
        rows.forEach(function(row) {{
          const day = clean(row.DiaTxt);
          const logger = clean(row.Logger);
          if (!buckets[day] || !logger) return;
          if (clean(row.StatusRetorno) === "Retornado") buckets[day].ret.add(logger);
          if (clean(row.StatusRetorno) === "Pendente de Retorno") buckets[day].pend.add(logger);
        }});
        return DAY_LABELS.map(function(day) {{
          const ret = buckets[day].ret.size;
          const pend = buckets[day].pend.size;
          const total = ret + pend;
          return {{
            day: day,
            ret: ret,
            pend: pend,
            total: total,
            pct: total > 0 ? (ret / total) * 100 : 0
          }};
        }});
      }}

      function buildAgentSeries(rows) {{
        const map = new Map();
        rows.forEach(function(row) {{
          const agent = clean(row.Agente);
          const logger = clean(row.Logger);
          if (!agent) return;
          if (!logger) return;
          if (!map.has(agent)) map.set(agent, {{ ret: new Set(), pend: new Set() }});
          const bucket = map.get(agent);
          const status = clean(row.StatusRetorno);
          if (status === "Retornado") bucket.ret.add(logger);
          if (status === "Pendente de Retorno") bucket.pend.add(logger);
        }});
        const arr = Array.from(map.entries()).map(function(entry) {{
          const agent = entry[0];
          const bucket = entry[1];
          const ret = bucket.ret.size;
          const pend = bucket.pend.size;
          const total = ret + pend;
          return {{ agent: agent, ret: ret, pend: pend, total: total, pct: total > 0 ? (ret / total) * 100 : 0 }};
        }});
        arr.sort(function(a, b) {{ return b.total - a.total; }});
        return arr;
      }}

      function buildUfSeries(rows) {{
        const map = new Map();
        rows.forEach(function(row) {{
          const uf = clean(row.UF) || "SEM UF";
          const logger = clean(row.Logger);
          if (!logger) return;
          if (!map.has(uf)) map.set(uf, new Set());
          map.get(uf).add(logger);
        }});
        const arr = Array.from(map.entries()).map(function(entry) {{
          return {{ uf: entry[0], total: entry[1].size }};
        }});
        arr.sort(function(a, b) {{ return b.total - a.total; }});
        return arr;
      }}

      function renderKpis(rows) {{
        const loggers = countUnique(rows, "Logger");
        const pedidos = countUnique(rows, "Pedido");
        const agentes = countUnique(rows, "Agente");
        const retornados = countByStatus(rows, "Retornado");
        const hoje = countUniqueByDate(rows, todayKey());
        const ontem = countUniqueByDate(rows, yesterdayKey());

        els.kpiLoggers.textContent = formatInt(loggers);
        els.kpiHoje.textContent = formatInt(hoje);
        els.kpiOntem.textContent = formatInt(ontem);
        els.kpiPedidos.textContent = formatInt(pedidos);
        els.kpiAgentes.textContent = formatInt(agentes);
        els.kpiRetornados.textContent = formatInt(retornados);

        els.todaySummary.textContent = formatInt(hoje) + " registro(s) encontrados para a data atual.";
        els.yesterdaySummary.textContent = formatInt(ontem) + " registro(s) encontrados para o dia anterior.";
        els.detailSummary.innerHTML = formatInt(rows.length) + ' linhas exibidas na tabela; o CSV completo sai em <code>CONTROLE_ENTREGAS_20D.csv</code>.';
      }}

      function renderDailyChart(rows) {{
        const series = buildDailySeries(rows);
        const traceRet = {{
          x: series.map((d) => d.day),
          y: series.map((d) => d.ret),
          type: "bar",
          name: "Retornado ao estoque",
          marker: {{ color: "#2f6fd6" }},
          text: series.map((d) => d.ret > 0 ? Math.round(d.pct) + "%" : ""),
          textposition: "inside",
          insidetextanchor: "middle",
          textfont: {{ color: "#ffffff", size: 15 }},
          cliponaxis: false,
          customdata: series.map((d) => [d.pct]),
          hovertemplate: "<b>%{{x}}</b><br>Retornado: %{{y}}<br>% retornado: %{{customdata[0]:.0f}}%<extra></extra>"
        }};
        const tracePend = {{
          x: series.map((d) => d.day),
          y: series.map((d) => d.pend),
          type: "bar",
          name: "Pendente de retorno",
          marker: {{ color: "#9bc7ff" }},
          cliponaxis: false,
          hovertemplate: "<b>%{{x}}</b><br>Pendente: %{{y}}<extra></extra>"
        }};
        const annotations = series
          .filter((d) => d.total > 0)
          .map((d) => ({{ x: d.day, y: d.total, text: formatInt(d.total), showarrow: false, yshift: 10, font: {{ color: "#ffffff", size: 13 }} }}));
        Plotly.react("chart-daily", [traceRet, tracePend], {{
          barmode: "stack",
          template: "plotly_dark",
          paper_bgcolor: "#0b1020",
          plot_bgcolor: "#0b1020",
          margin: {{ l: 24, r: 20, t: 52, b: 52 }},
          height: 390,
          font: {{ color: "#e5eefc" }},
          xaxis: {{ gridcolor: "#25304a", tickfont: {{ size: 11 }}, automargin: true }},
          yaxis: {{ gridcolor: "#25304a", showticklabels: false, ticks: "", zeroline: false }},
          title: {{ x: 0.02, font: {{ size: 15 }} }},
          bargap: 0.22,
          legend: {{
            orientation: "h",
            yanchor: "bottom",
            y: 1.02,
            xanchor: "left",
            x: 0.0
          }},
          annotations: annotations
        }}, {{ displayModeBar: false, responsive: true }});
      }}

      function renderAgentChart(rows) {{
        const series = buildAgentSeries(rows);
        const chartHeight = Math.max(620, 110 + (series.length * 34));
        const traceRet = {{
          y: series.map((d) => d.agent),
          x: series.map((d) => d.ret),
          orientation: "h",
          type: "bar",
          name: "Retornado ao estoque",
          marker: {{ color: "#2f6fd6" }},
          text: series.map((d) => d.ret > 0 ? Math.round(d.pct) + "%" : ""),
          textposition: "inside",
          insidetextanchor: "middle",
          textfont: {{ color: "#ffffff", size: 15 }},
          cliponaxis: false,
          customdata: series.map((d) => [d.pct]),
          hovertemplate: "<b>%{{y}}</b><br>Retornado: %{{x}}<br>% retornado: %{{customdata[0]:.0f}}%<extra></extra>"
        }};
        const tracePend = {{
          y: series.map((d) => d.agent),
          x: series.map((d) => d.pend),
          orientation: "h",
          type: "bar",
          name: "Pendente de retorno",
          marker: {{ color: "#9bc7ff" }},
          cliponaxis: false,
          hovertemplate: "<b>%{{y}}</b><br>Pendente: %{{x}}<extra></extra>"
        }};
        const maxTotal = series.length ? Math.max.apply(null, series.map((d) => d.total)) : 1;
        const annotations = series
          .filter((d) => d.total > 0)
          .map((d) => ({{ x: d.total, y: d.agent, text: formatInt(d.total), showarrow: false, xshift: 14, font: {{ color: "#e5eefc", size: 13 }} }}));
        Plotly.react("chart-agentes", [traceRet, tracePend], {{
          barmode: "stack",
          template: "plotly_dark",
          paper_bgcolor: "#0b1020",
          plot_bgcolor: "#0b1020",
          margin: {{ l: 22, r: 44, t: 56, b: 36 }},
          height: chartHeight,
          font: {{ color: "#e5eefc" }},
          xaxis: {{ gridcolor: "#25304a", zeroline: false, showticklabels: false, range: [0, maxTotal * 1.15] }},
          yaxis: {{
            gridcolor: "#25304a",
            automargin: true,
            categoryorder: "array",
            categoryarray: series.map((d) => d.agent),
            autorange: "reversed"
          }},
          title: {{ x: 0.02, font: {{ size: 15 }} }},
          legend: {{
            orientation: "h",
            yanchor: "bottom",
            y: 1.02,
            xanchor: "left",
            x: 0.0
          }},
          bargap: 0.25,
          annotations: annotations
        }}, {{ displayModeBar: false, responsive: true }});
      }}

      function renderUfChart(rows) {{
        const series = buildUfSeries(rows);
        const chartHeight = Math.max(620, 110 + (series.length * 34));
        const maxTotal = series.length ? Math.max.apply(null, series.map((d) => d.total)) : 1;
        const trace = {{
          y: series.map((d) => d.uf),
          x: series.map((d) => d.total),
          orientation: "h",
          type: "bar",
          name: "Loggers",
          marker: {{ color: "#9bc7ff" }},
          cliponaxis: false,
          hovertemplate: "<b>%{{y}}</b><br>Loggers: %{{x}}<extra></extra>"
        }};
        const annotations = series
          .filter((d) => d.total > 0)
          .map((d) => ({{ x: d.total, y: d.uf, text: formatInt(d.total), showarrow: false, xshift: 14, font: {{ color: "#e5eefc", size: 13 }} }}));
        Plotly.react("chart-ufs", [trace], {{
          template: "plotly_dark",
          paper_bgcolor: "#0b1020",
          plot_bgcolor: "#0b1020",
          margin: {{ l: 22, r: 44, t: 56, b: 36 }},
          height: chartHeight,
          font: {{ color: "#e5eefc" }},
          xaxis: {{ gridcolor: "#25304a", zeroline: false, showticklabels: false, range: [0, maxTotal * 1.15] }},
          yaxis: {{
            gridcolor: "#25304a",
            automargin: true,
            categoryorder: "array",
            categoryarray: series.map((d) => d.uf),
            autorange: "reversed"
          }},
          title: {{ x: 0.02, font: {{ size: 15 }} }},
          showlegend: false,
          bargap: 0.22,
          annotations: annotations
        }}, {{ displayModeBar: false, responsive: true }});
      }}

      function renderTable(container, rows) {{
        if (!rows.length) {{
          container.innerHTML = '<div class="empty-box">Sem registros neste recorte.</div>';
          return;
        }}
        const view = rows.slice().sort(function(a, b) {{ return parseBrDateTime(b.DataEntrega) - parseBrDateTime(a.DataEntrega); }});
        let html = '<table class="data-table"><thead><tr>';
        TABLE_COLUMNS.forEach(function(col) {{
          html += '<th>' + col.label + '</th>';
        }});
        html += '</tr></thead><tbody>';
        view.forEach(function(row) {{
          html += '<tr>';
          TABLE_COLUMNS.forEach(function(col) {{
            html += '<td>' + escapeHtml(row[col.key] || "") + '</td>';
          }});
          html += '</tr>';
        }});
        html += '</tbody></table>';
        container.innerHTML = html;
      }}

      function renderTables(rows) {{
        const today = todayKey();
        const yesterday = yesterdayKey();
        const todayRows = rows.filter(function(row) {{ return clean(row.DataEntrega).startsWith(today); }});
        const yesterdayRows = rows.filter(function(row) {{ return clean(row.DataEntrega).startsWith(yesterday); }});
        renderTable(els.todayTable, todayRows.slice(0, {SECTION_TABLE_ROWS}));
        renderTable(els.yesterdayTable, yesterdayRows.slice(0, {SECTION_TABLE_ROWS}));
        renderTable(els.detailTable, rows.slice(0, {TABLE_MAX_ROWS}));
      }}

      function renderAll() {{
        const rows = filterRows();
        renderKpis(rows);
        renderDailyChart(rows);
        renderAgentChart(rows);
        renderUfChart(rows);
        renderTables(rows);
      }}

      let resizeTimer = null;
      function resizeCharts() {{
        clearTimeout(resizeTimer);
        resizeTimer = setTimeout(function() {{
          ["chart-daily", "chart-agentes", "chart-ufs"].forEach(function(id) {{
            const el = document.getElementById(id);
            if (el) {{
              Plotly.Plots.resize(el);
            }}
          }});
        }}, 120);
      }}

      setupFilters();
      renderAll();
      window.addEventListener("resize", resizeCharts);
    </script>
  </div>
</body>
</html>"""
    return html


def main() -> None:
    print("[controle] Carregando snapshot consolidado...")
    raw = load_data()
    df = prepare_data(raw)
    print(f"[controle] Registros no recorte: {len(df)}")
    print(f"[controle] Loggers unicos: {df['Logger'].nunique() if not df.empty else 0}")
    print(f"[controle] Pedidos unicos: {df['Pedido'].nunique() if not df.empty else 0}")
    html = build_page(df)
    OUTPUT_HTML.write_text(html, encoding="utf-8")
    print(f"[controle] HTML salvo: {OUTPUT_HTML}")
    print(f"[controle] CSV salvo: {OUTPUT_CSV}")


if __name__ == "__main__":
    main()
