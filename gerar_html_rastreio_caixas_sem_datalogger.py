from __future__ import annotations

import json
import os
import unicodedata
from datetime import datetime
from html import escape
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL

from env_utils import load_env_file


WORKSPACE = Path(__file__).resolve().parent
OUTPUT_HTML = WORKSPACE / "RASTREIO_CAIXAS_SEM_DATALOGGER.html"
BRASILIA_TZ = ZoneInfo("America/Sao_Paulo")

load_env_file()

def fmt_int(value: int) -> str:
    return f"{int(value):,}".replace(",", ".")


def normalize_text(value: object) -> str:
    raw = "" if value is None else str(value)
    no_accent = "".join(
        ch for ch in unicodedata.normalize("NFD", raw)
        if unicodedata.category(ch) != "Mn"
    )
    return " ".join(no_accent.strip().upper().split())


def clean_text(series: pd.Series) -> pd.Series:
    return series.fillna("").astype(str).str.strip()


def _pg_configs() -> list[tuple[str, dict]]:
    return [
        (
            "AURA_POSTGRES",
            {
                "host": os.getenv("AURA_POSTGRES_HOST", ""),
                "port": int(os.getenv("AURA_POSTGRES_PORT", "5432")),
                "database": os.getenv("AURA_POSTGRES_NAME", ""),
                "user": os.getenv("AURA_POSTGRES_USER", ""),
                "password": os.getenv("AURA_POSTGRES_PASSWORD", ""),
            },
        ),
        (
            "AURA_DB",
            {
                "host": os.getenv("AURA_DB_HOST", ""),
                "port": int(os.getenv("AURA_DB_PORT", "5432")),
                "database": os.getenv("AURA_DB_NAME", ""),
                "user": os.getenv("AURA_DB_USER", ""),
                "password": os.getenv("AURA_DB_PASSWORD", ""),
            },
        ),
    ]


def _build_pg_url(cfg: dict) -> URL:
    return URL.create(
        "postgresql+psycopg2",
        username=cfg["user"],
        password=cfg["password"],
        host=cfg["host"],
        port=cfg["port"],
        database=cfg["database"],
    )


DB_SUCCESS_INFO = {"fonte": "", "horario": ""}

def _read_pg(sql: str) -> pd.DataFrame:
    global DB_SUCCESS_INFO
    errors: list[str] = []
    for label, cfg in _pg_configs():
        if not all([cfg.get("host"), cfg.get("database"), cfg.get("user"), cfg.get("password")]):
            errors.append(f"{label}: configuracao incompleta")
            continue
        try:
            print(f"[rastreio] Tentando conexao {label}...")
            engine = create_engine(
                _build_pg_url(cfg),
                pool_pre_ping=True,
                connect_args={"connect_timeout": 12},
            )
            with engine.connect() as conn:
                df = pd.read_sql(text(sql), conn)
                DB_SUCCESS_INFO["fonte"] = label
                DB_SUCCESS_INFO["horario"] = datetime.now().strftime("%d/%m/%Y %H:%M")
                return df
        except Exception as exc:
            errors.append(f"{label}: {type(exc).__name__}")
            continue
    raise RuntimeError("Nao foi possivel consultar o PostgreSQL. Tentativas: " + "; ".join(errors))


def load_tipo_distribution() -> pd.DataFrame:
    sql = """
    SELECT
        COALESCE(NULLIF(TRIM(ds_tipo), ''), '<VAZIO>') AS ds_tipo,
        COUNT(*) AS linhas,
        COUNT(DISTINCT cd_lpn) AS caixas
    FROM vtc_stage.documentos
    WHERE dt_coletaefetiva IS NOT NULL
      AND cd_lpn IS NOT NULL
      AND TRIM(cd_lpn) <> ''
    GROUP BY 1
    ORDER BY linhas DESC
    """
    return _read_pg(sql)


def load_raw_data() -> pd.DataFrame:
    sql = """
    SELECT
        nr_pedido::text AS nr_pedido,
        cd_uf::text AS cd_uf,
        dt_coletaefetiva,
        cd_lpn::text AS cd_lpn,
        cd_referencia::text AS cd_referencia,
        ds_tipo::text AS ds_tipo,
        ds_tag::text AS ds_tag,
        ds_descricaocliente::text AS ds_descricaocliente
    FROM vtc_stage.documentos
    WHERE dt_coletaefetiva IS NOT NULL
      AND cd_lpn IS NOT NULL
      AND TRIM(cd_lpn) <> ''
      AND (
          cd_referencia IS NULL
          OR TRIM(cd_referencia) = ''
      )
      AND ds_tipo IS NOT NULL
      AND TRIM(ds_tipo) <> ''
      AND UPPER(TRIM(ds_tipo)) LIKE '%CAIXA%'
      AND UPPER(TRIM(ds_tipo)) NOT LIKE '%PALLET%'
    ORDER BY dt_coletaefetiva DESC
    """
    return _read_pg(sql)


def prepare_data(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    for col in ["nr_pedido", "cd_uf", "cd_lpn", "cd_referencia", "ds_tipo", "ds_tag", "ds_descricaocliente"]:
        if col not in out.columns:
            out[col] = ""
        out[col] = clean_text(out[col])

    out["dt_coletaefetiva"] = pd.to_datetime(out.get("dt_coletaefetiva"), errors="coerce")
    out["_tipo_norm"] = out["ds_tipo"].map(normalize_text)
    out = out[
        out["dt_coletaefetiva"].notna()
        & out["cd_lpn"].ne("")
        & out["cd_referencia"].eq("")
        & out["_tipo_norm"].str.contains("CAIXA", regex=False, na=False)
        & ~out["_tipo_norm"].str.contains("PALLET", regex=False, na=False)
    ].copy()

    out = (
        out.sort_values(["dt_coletaefetiva", "nr_pedido", "cd_lpn"], ascending=[False, True, True])
        .drop_duplicates(subset=["cd_lpn"], keep="first")
        .reset_index(drop=True)
    )
    out["Status do logger"] = "Sem datalogger"
    out["Data da coleta"] = out["dt_coletaefetiva"].dt.strftime("%d/%m/%Y %H:%M:%S").fillna("")
    out["_coleta_ts"] = out["dt_coletaefetiva"].apply(lambda dt: int(dt.timestamp() * 1000) if pd.notna(dt) else 0)
    return out


def build_summary(df: pd.DataFrame, generated_at: pd.Timestamp) -> dict:
    cutoff_24 = generated_at - pd.Timedelta(hours=24)
    cutoff_48 = generated_at - pd.Timedelta(hours=48)
    last_24 = df.loc[df["dt_coletaefetiva"].ge(cutoff_24)].copy()
    last_48 = df.loc[df["dt_coletaefetiva"].ge(cutoff_48)].copy()

    by_uf = (
        df.assign(cd_uf=df["cd_uf"].replace("", "SEM UF"))
        .groupby("cd_uf", as_index=False)["cd_lpn"]
        .nunique()
        .rename(columns={"cd_uf": "UF", "cd_lpn": "Caixas"})
        .sort_values(["Caixas", "UF"], ascending=[False, True])
    )
    by_tipo = (
        df.assign(ds_tipo=df["ds_tipo"].replace("", "SEM TIPO"))
        .groupby("ds_tipo", as_index=False)["cd_lpn"]
        .nunique()
        .rename(columns={"ds_tipo": "Tipo de caixa", "cd_lpn": "Caixas"})
        .sort_values(["Caixas", "Tipo de caixa"], ascending=[False, True])
    )

    return {
        "total_caixas": int(df["cd_lpn"].nunique()),
        "total_pedidos": int(df.loc[df["nr_pedido"].ne(""), "nr_pedido"].nunique()),
        "caixas_24h": int(last_24["cd_lpn"].nunique()),
        "caixas_48h": int(last_48["cd_lpn"].nunique()),
        "ufs_afetadas": int(df.loc[df["cd_uf"].ne(""), "cd_uf"].nunique()),
        "linhas_tabela": int(len(df)),
        "total_por_uf": by_uf.to_dict(orient="records"),
        "total_por_tipo": by_tipo.to_dict(orient="records"),
    }


def build_rows(df: pd.DataFrame) -> list[dict]:
    view = df.copy()
    view["Pedido"] = view["nr_pedido"]
    view["UF"] = view["cd_uf"]
    view["Tipo de caixa"] = view["ds_tipo"]
    view["LPN"] = view["cd_lpn"]
    view["Logger/Datalogger"] = view["cd_referencia"]
    cols = [
        "Pedido",
        "UF",
        "Data da coleta",
        "Tipo de caixa",
        "LPN",
        "Logger/Datalogger",
        "Status do logger",
        "_coleta_ts",
    ]
    return view[cols].to_dict(orient="records")


def now_for_coleta(df: pd.DataFrame) -> pd.Timestamp:
    now_brasilia = pd.Timestamp.now(tz=BRASILIA_TZ)
    if df.empty or "dt_coletaefetiva" not in df.columns:
        return now_brasilia
    tz = getattr(df["dt_coletaefetiva"].dt, "tz", None)
    if tz is not None:
        return now_brasilia.tz_convert(tz)
    return now_brasilia.tz_localize(None)


def format_generated_at(value: pd.Timestamp) -> str:
    if value.tzinfo is not None:
        value = value.tz_convert(BRASILIA_TZ)
    return value.strftime("%d/%m/%Y %H:%M")


def validate_business_rules(df: pd.DataFrame, summary: dict) -> None:
    if df["cd_lpn"].duplicated().any():
        raise RuntimeError("Tabela contem LPN duplicado; a pagina deve exibir uma linha por caixa.")
    if df["cd_lpn"].eq("").any():
        raise RuntimeError("Tabela contem caixa sem cd_lpn.")
    if df["dt_coletaefetiva"].isna().any():
        raise RuntimeError("Tabela contem registro sem dt_coletaefetiva.")
    if df["cd_referencia"].ne("").any():
        raise RuntimeError("Indicador contem registro com cd_referencia preenchido.")
    tipo_norm = df["ds_tipo"].map(normalize_text)
    if tipo_norm.str.contains("PALLET", regex=False, na=False).any():
        raise RuntimeError("Filtro de ds_tipo falhou: pallet entrou no indicador.")
    if not tipo_norm.str.contains("CAIXA", regex=False, na=False).all() and not df.empty:
        raise RuntimeError("Filtro de ds_tipo falhou: ha registro que nao representa caixa.")
    if int(summary["total_caixas"]) != int(len(df)):
        raise RuntimeError("Card Total de caixas sem datalogger nao bate com a tabela deduplicada por LPN.")


def _top_summary(rows: list[dict], first_col: str, limit: int = 3) -> str:
    if not rows:
        return ""
    items = []
    for row in rows[:limit]:
        label = str(row.get(first_col, ""))
        total = int(row.get("Caixas", 0) or 0)
        items.append(
            f'<span class="summary-chip"><strong>{escape(label)}</strong>{fmt_int(total)}</span>'
        )
    return "".join(items)


def build_page(df: pd.DataFrame, tipo_distribution: pd.DataFrame) -> str:
    generated_at = now_for_coleta(df)
    gerado = format_generated_at(generated_at)
    consultado_em = DB_SUCCESS_INFO.get("horario", "")
    fonte = DB_SUCCESS_INFO.get("fonte", "")

    raw_max = pd.to_datetime(df['dt_coletaefetiva'], errors='coerce').max()
    disponivel_ate = raw_max.strftime("%d/%m/%Y %H:%M") if pd.notna(raw_max) else "--"

    summary = build_summary(df, generated_at)
    validate_business_rules(df, summary)

    rows = build_rows(df)
    summary_json = json.dumps(summary, ensure_ascii=False)
    rows_json = json.dumps(rows, ensure_ascii=False)
    tipo_values_json = json.dumps(
        tipo_distribution.fillna("").to_dict(orient="records"),
        ensure_ascii=False,
    )
    uf_top = _top_summary(summary["total_por_uf"], "UF", 5)
    tipo_top = _top_summary(summary["total_por_tipo"], "Tipo de caixa", 3)

    return f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Rastreio de Caixas sem Datalogger</title>
  <style>
    :root {{
      --bg-main: #0b1020;
      --bg-panel: #121b2d;
      --bg-panel-2: #0f1728;
      --line: #24344d;
      --line-soft: #2a3e5e;
      --text: #e8eefb;
      --muted: #9fb7d4;
      --muted-2: #7f95b2;
      --blue: #3f7bc3;
      --blue-2: #75b5ff;
      --danger: #ffb3b3;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      min-height: 100vh;
      font-family: "Segoe UI", "Trebuchet MS", Arial, sans-serif;
      background:
        radial-gradient(900px 260px at 0% -10%, #13233f 0%, rgba(19,35,63,0) 60%),
        radial-gradient(700px 220px at 100% -20%, #1a2d4d 0%, rgba(26,45,77,0) 58%),
        var(--bg-main);
      color: var(--text);
    }}
    .wrap {{ max-width: 1360px; margin: 0 auto; padding: 18px; }}
    .hero {{
      background: linear-gradient(120deg, #0f2344 0%, #173463 52%, #1e4178 100%);
      border: 1px solid #2b4a76;
      border-radius: 16px;
      padding: 16px 18px;
      margin-bottom: 14px;
      box-shadow: inset 0 1px 0 rgba(125,173,230,.08);
    }}
    .eyebrow {{
      color: #bdd0ec;
      font-weight: 800;
      letter-spacing: 0.08em;
      text-transform: uppercase;
      font-size: 12px;
    }}
    h1 {{ margin: 8px 0 6px; font-size: 2rem; line-height: 1.1; font-weight: 800; letter-spacing: 0; }}
    .sub {{ color: #bdd0ec; font-size: .95rem; line-height: 1.45; max-width: 1120px; }}
    .pill-row {{ display: flex; flex-wrap: wrap; gap: 8px; margin-top: 10px; }}
    .pill {{
      display: inline-flex;
      align-items: center;
      border: 1px solid rgba(173,200,232,.2);
      border-radius: 999px;
      padding: 5px 11px;
      background: rgba(9,15,28,.34);
      color: #dfeaf8;
      font-size: .78rem;
      font-weight: 700;
    }}
    .filter-row {{
      display: grid;
      grid-template-columns: minmax(180px, 0.9fr) minmax(180px, 1fr) minmax(180px, 1fr) auto;
      gap: 12px;
      margin: 14px 0;
    }}
    .filter-box {{ display: flex; flex-direction: column; gap: 6px; }}
    .filter-label {{
      color: var(--muted);
      font-size: 12px;
      text-transform: uppercase;
      letter-spacing: 0.06em;
      font-weight: 700;
    }}
    .filter-select, .search-input {{
      width: 100%;
      border-radius: 10px;
      border: 1px solid #2b466b;
      background: #13243c;
      color: #e8eefb;
      padding: 10px 12px;
      font-size: 13px;
      outline: none;
      min-height: 40px;
    }}
    .btn {{
      border: 1px solid #4f94da;
      background: linear-gradient(135deg, #173158 0%, #1d4f8f 100%);
      color: var(--text);
      border-radius: 10px;
      padding: 10px 14px;
      font-weight: 700;
      font-size: 13px;
      min-height: 40px;
      cursor: pointer;
    }}
    .kpis {{
      display: grid;
      grid-template-columns: repeat(5, minmax(170px, 1fr));
      gap: 12px;
      margin: 8px 0 14px;
    }}
    .kpi {{
      position: relative;
      overflow: hidden;
      background: linear-gradient(155deg, #15233a 0%, #121b2d 62%, #101829 100%);
      border: 1px solid #365379;
      border-radius: 12px;
      padding: 14px 14px 13px;
      min-height: 118px;
      text-align: center;
      box-shadow: inset 0 1px 0 rgba(125,173,230,.06);
    }}
    .kpi::before {{
      content: "";
      position: absolute;
      top: 0;
      left: 0;
      right: 0;
      height: 3px;
      background: linear-gradient(90deg, var(--blue) 0%, var(--blue-2) 100%);
      opacity: .9;
    }}
    .kpi .label {{
      color: #b9d1ee;
      font-size: .9rem;
      font-weight: 800;
      line-height: 1.25;
      margin: 7px 0 10px;
      min-height: 34px;
      display: flex;
      align-items: center;
      justify-content: center;
    }}
    .kpi .value {{ color: #f5f9ff; font-size: 2.15rem; font-weight: 800; line-height: 1.05; margin-bottom: 7px; }}
    .kpi .foot {{ font-size: .74rem; color: #89a9cf; line-height: 1.35; }}
    .insight-grid {{
      display: grid;
      grid-template-columns: 1fr;
      gap: 10px;
      margin: 10px 0 12px;
      align-items: start;
    }}
    .section {{
      margin-top: 14px;
      background: linear-gradient(170deg, #121f34 0%, #0f1828 100%);
      border: 1px solid var(--line-soft);
      border-radius: 14px;
      padding: 14px;
      box-shadow: inset 0 1px 0 rgba(125,173,230,.05);
    }}
    .summary-panel {{
      display: flex;
      flex-direction: column;
      min-height: 0;
      margin-top: 0;
      width: 100%;
      padding: 10px;
    }}
    .section-head {{
      display: flex;
      justify-content: space-between;
      gap: 8px;
      align-items: center;
      flex-wrap: wrap;
      margin-bottom: 8px;
    }}
    .summary-panel .section-head {{ align-items: flex-start; }}
    .summary-panel h2 {{ font-size: .92rem; }}
    .summary-panel p {{ font-size: .72rem; line-height: 1.25; }}
    .section h2 {{ margin: 0 0 5px; font-size: 1.02rem; font-weight: 800; color: #dceafe; }}
    .section p {{ margin: 0; color: #96afcf; font-size: .78rem; line-height: 1.4; }}
    .summary-count {{
      padding: 4px 8px;
      border-radius: 999px;
      background: #13243c;
      border: 1px solid #2b466b;
      color: #dceafe;
      font-size: .7rem;
      font-weight: 800;
      white-space: nowrap;
    }}
    .summary-chip-row {{
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin: 0 0 10px;
    }}
    .quick-filter-panel {{
      display: flex;
      flex-direction: column;
      gap: 7px;
    }}
    .quick-filter-toolbar,
    .summary-controls {{
      display: grid;
      grid-template-columns: minmax(130px, 170px);
      gap: 7px;
      margin: 0 0 7px;
    }}
    .quick-filter-actions {{
      display: flex;
      align-items: center;
      flex-wrap: wrap;
      gap: 6px;
    }}
    .summary-input,
    .summary-select {{
      width: 100%;
      min-height: 30px;
      border-radius: 8px;
      border: 1px solid #2b466b;
      background: #13243c;
      color: #e8eefb;
      padding: 6px 9px;
      font-size: 12px;
      outline: none;
    }}
    .view-toggle {{
      min-height: 29px;
      border: 1px solid #2b466b;
      background: #13243c;
      color: #dceafe;
      border-radius: 999px;
      padding: 5px 9px;
      font-size: .7rem;
      font-weight: 800;
      cursor: pointer;
    }}
    .view-toggle.active {{
      border-color: #75b5ff;
      background: linear-gradient(135deg, #1d3f72 0%, #2560ab 100%);
      color: #f5f9ff;
      box-shadow: 0 0 0 2px rgba(117,181,255,.12);
    }}
    .quick-filter-list {{
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
    }}
    .quick-filter-chip {{
      display: inline-flex;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
      min-width: 82px;
      border: 1px solid rgba(117,181,255,.22);
      background: rgba(63,123,195,.13);
      color: #dceafe;
      border-radius: 999px;
      padding: 6px 9px;
      font-size: .74rem;
      font-weight: 800;
      cursor: pointer;
      transition: background .15s, border-color .15s, transform .15s;
    }}
    .quick-filter-chip:hover,
    .type-filter-card:hover {{
      transform: translateY(-1px);
      border-color: rgba(117,181,255,.46);
    }}
    .quick-filter-chip strong {{
      color: #f6fbff;
      font-size: .82rem;
    }}
    .quick-filter-chip.active {{
      background: linear-gradient(135deg, #173158 0%, #1d4f8f 100%);
      border-color: #75b5ff;
      box-shadow: 0 0 0 2px rgba(117,181,255,.13);
    }}
    .summary-chip {{
      display: inline-flex;
      align-items: center;
      gap: 8px;
      padding: 7px 10px;
      border-radius: 999px;
      background: rgba(63,123,195,.14);
      border: 1px solid rgba(117,181,255,.22);
      color: #dceafe;
      font-size: .78rem;
      font-weight: 800;
      max-width: 100%;
    }}
    .summary-chip strong {{
      color: #9fb7d4;
      font-weight: 800;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }}
    .type-filter-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(190px, 1fr));
      gap: 7px;
    }}
    .type-filter-card {{
      width: 100%;
      text-align: left;
      border: 1px solid rgba(117,181,255,.20);
      background: linear-gradient(155deg, rgba(21,35,58,.96) 0%, rgba(18,27,45,.96) 70%, rgba(16,24,41,.96) 100%);
      color: #dceafe;
      border-radius: 10px;
      padding: 8px 9px;
      cursor: pointer;
      box-shadow: inset 0 1px 0 rgba(125,173,230,.05);
      transition: background .15s, border-color .15s, transform .15s;
    }}
    .type-filter-card.active {{
      border-color: #75b5ff;
      background: linear-gradient(155deg, #173158 0%, #132c50 72%, #10213d 100%);
      box-shadow: 0 0 0 2px rgba(117,181,255,.13), inset 0 1px 0 rgba(125,173,230,.08);
    }}
    .type-filter-card.type-all {{
      grid-column: auto;
    }}
    .type-card-head {{
      display: flex;
      align-items: flex-start;
      justify-content: space-between;
      gap: 8px;
      margin-bottom: 5px;
    }}
    .type-card-name {{
      color: #cfe0f6;
      font-size: .73rem;
      line-height: 1.25;
      font-weight: 800;
    }}
    .type-card-value {{
      color: #f6fbff;
      font-size: .98rem;
      line-height: 1;
      font-weight: 800;
      white-space: nowrap;
    }}
    .type-card-meta {{
      color: #89a9cf;
      font-size: .66rem;
      font-weight: 700;
      margin-bottom: 6px;
    }}
    .progress-bar {{
      width: 100%;
      height: 5px;
      overflow: hidden;
      border-radius: 999px;
      background: rgba(9,15,28,.72);
      border: 1px solid rgba(117,181,255,.14);
    }}
    .progress-fill {{
      height: 100%;
      border-radius: 999px;
      background: linear-gradient(90deg, #3f7bc3 0%, #75b5ff 100%);
      min-width: 3px;
    }}
    .filter-applied-text {{
      margin-top: 7px;
      color: #9fb7d4;
      font-size: .7rem;
      font-weight: 700;
    }}
    .table-wrap {{
      overflow-x: auto;
      border-radius: 12px;
      border: 1px solid rgba(148,163,184,0.16);
      max-height: 690px;
    }}
    .detail-list {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(330px, 1fr));
      gap: 12px;
      margin-top: 4px;
    }}
    .detail-card {{
      border: 1px solid rgba(117,181,255,.18);
      background: linear-gradient(160deg, rgba(18,31,52,.98) 0%, rgba(15,24,40,.98) 100%);
      border-radius: 14px;
      padding: 13px;
      box-shadow: inset 0 1px 0 rgba(125,173,230,.05);
    }}
    .detail-card-head {{
      display: flex;
      align-items: flex-start;
      justify-content: space-between;
      gap: 12px;
      margin-bottom: 11px;
    }}
    .detail-pedido {{
      display: flex;
      flex-direction: column;
      gap: 3px;
    }}
    .detail-pedido span {{
      color: #89a9cf;
      font-size: .72rem;
      font-weight: 800;
      text-transform: uppercase;
      letter-spacing: .04em;
    }}
    .detail-pedido strong {{
      color: #f6fbff;
      font-size: 1.1rem;
      line-height: 1;
    }}
    .detail-badges {{
      display: flex;
      align-items: center;
      justify-content: flex-end;
      flex-wrap: wrap;
      gap: 6px;
    }}
    .badge {{
      display: inline-flex;
      align-items: center;
      border-radius: 999px;
      padding: 5px 8px;
      font-size: .72rem;
      font-weight: 800;
      line-height: 1;
      white-space: nowrap;
      border: 1px solid rgba(117,181,255,.24);
      background: rgba(63,123,195,.13);
      color: #dceafe;
    }}
    .badge.uf {{
      min-width: 34px;
      justify-content: center;
      color: #f6fbff;
      background: linear-gradient(135deg, #173158 0%, #1d4f8f 100%);
      border-color: #75b5ff;
    }}
    .detail-grid {{
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 9px;
    }}
    .detail-field {{
      border: 1px solid rgba(148,163,184,.12);
      background: rgba(9,15,28,.34);
      border-radius: 10px;
      padding: 9px 10px;
      min-width: 0;
    }}
    .detail-field.wide {{ grid-column: 1 / -1; }}
    .detail-label {{
      display: block;
      color: #89a9cf;
      font-size: .7rem;
      font-weight: 800;
      text-transform: uppercase;
      letter-spacing: .04em;
      margin-bottom: 4px;
    }}
    .detail-value {{
      display: block;
      color: #e8eefb;
      font-size: .84rem;
      font-weight: 700;
      line-height: 1.3;
      overflow-wrap: anywhere;
    }}
    .detail-empty {{
      grid-column: 1 / -1;
    }}
    table.data-table {{
      width: 100%;
      border-collapse: collapse;
      background: rgba(9,14,25,0.95);
    }}
    table.data-table {{ min-width: 1060px; }}
    .data-table th, .data-table td {{
      padding: 10px 10px;
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
    .status-badge {{
      display: inline-flex;
      border: 1px solid rgba(251,113,133,0.35);
      color: #fecdd3;
      background: rgba(251,113,133,0.12);
      border-radius: 999px;
      padding: 5px 9px;
      font-weight: 800;
    }}
    .empty-box {{
      padding: 18px;
      color: var(--muted);
      background: rgba(10,16,29,0.92);
      border-radius: 14px;
      border: 1px dashed rgba(148,163,184,0.24);
    }}
    .footer {{ margin-top: 18px; color: var(--muted); font-size: 12px; }}
    @media (max-width: 1280px) {{
      .kpis {{ grid-template-columns: repeat(3, minmax(0, 1fr)); }}
      .filter-row {{ grid-template-columns: 1fr; }}
    }}
    @media (max-width: 720px) {{
      .wrap {{ padding: 14px 10px 24px; }}
      h1 {{ font-size: 24px; }}
      .kpis {{ grid-template-columns: 1fr 1fr; }}
      .insight-grid {{ grid-template-columns: 1fr; }}
      .quick-filter-toolbar,
      .summary-controls {{ grid-template-columns: 1fr; }}
      .summary-chip {{ width: 100%; justify-content: space-between; }}
      .quick-filter-chip {{ width: 100%; }}
    }}
  </style>
</head>
<body>
  <div class="wrap">
    <div class="hero">
      <div class="eyebrow">Banco Aura</div>
      <h1>Rastreio de Caixas sem Datalogger</h1>
      <div class="sub">Caixas coletadas em vtc_stage.documentos com LPN preenchido, data de coleta valida, sem logger vinculado e ds_tipo filtrado para caixa, ignorando pallets.</div>
      <div class="pill-row">
        <span class="pill">Gerado em: {gerado}</span>
        <span class="pill">Conex&atilde;o com banco: {fonte} OK &agrave;s {consultado_em}</span>
        <span class="pill">Dados disponiveis ate: {disponivel_ate}</span>
        <span class="pill">Fonte: {fonte}</span>
        <span class="pill">Caixa = cd_lpn unico</span>
        <span class="pill">Logger = cd_referencia</span>
        <span class="pill">Periodo 24h/48h por dt_coletaefetiva</span>
      </div>
    </div>

    <div class="kpis">
      <div class="kpi"><div class="label">Total de caixas sem datalogger</div><div class="value" id="kpi-total">{fmt_int(summary["total_caixas"])}</div><div class="foot">COUNT(DISTINCT cd_lpn)</div></div>
      <div class="kpi"><div class="label">Pedidos com caixas sem datalogger</div><div class="value" id="kpi-pedidos">{fmt_int(summary["total_pedidos"])}</div><div class="foot">COUNT(DISTINCT nr_pedido)</div></div>
      <div class="kpi"><div class="label">Caixas sem datalogger 24h</div><div class="value" id="kpi-24">{fmt_int(summary["caixas_24h"])}</div><div class="foot">Base: dt_coletaefetiva</div></div>
      <div class="kpi"><div class="label">Caixas sem datalogger 48h</div><div class="value" id="kpi-48">{fmt_int(summary["caixas_48h"])}</div><div class="foot">Base: dt_coletaefetiva</div></div>
      <div class="kpi"><div class="label">UFs afetadas</div><div class="value" id="kpi-ufs">{fmt_int(summary["ufs_afetadas"])}</div><div class="foot">COUNT(DISTINCT cd_uf)</div></div>
    </div>

    <div class="insight-grid">
      <div class="section summary-panel">
        <div class="section-head">
          <div><h2>Total por tipo de caixa</h2><p>Contagem por ds_tipo apos excluir pallets.</p></div>
          <span class="summary-count">{fmt_int(len(summary["total_por_tipo"]))} tipos</span>
        </div>
        <div class="summary-controls">
          <select id="tipo-summary-sort" class="summary-select">
            <option value="desc">Maior volume</option>
            <option value="asc">Menor volume</option>
            <option value="az">Tipo A-Z</option>
          </select>
        </div>
        <div class="type-filter-grid" id="type-filter-grid"></div>
        <div class="filter-applied-text" id="tipo-filter-text">Todos os tipos selecionados.</div>
      </div>
    </div>

    <div class="section">
      <div class="section-head">
        <div>
          <h2>Detalhe das caixas</h2>
          <p id="detail-summary">{fmt_int(len(rows))} caixa(s) exibidas. A tabela usa uma linha por LPN.</p>
        </div>
      </div>
      <div class="filter-row">
        <div class="filter-box">
          <div class="filter-label">Periodo</div>
          <select id="filter-periodo" class="filter-select">
            <option value="all">Todas</option>
            <option value="24">Ultimas 24h</option>
            <option value="48">Ultimas 48h</option>
          </select>
        </div>
        <div class="filter-box">
          <div class="filter-label">UF</div>
          <select id="filter-uf" class="filter-select"></select>
        </div>
        <div class="filter-box">
          <div class="filter-label">Busca</div>
          <input id="filter-search" class="search-input" type="search" placeholder="Pedido, LPN, UF ou tipo">
        </div>
        <div class="filter-box">
          <div class="filter-label">&nbsp;</div>
          <button id="btn-clear" class="btn" type="button">Limpar filtros</button>
        </div>
        <div class="filter-box">
          <div class="filter-label">&nbsp;</div>
          <button id="btn-export-xlsx" class="btn" type="button">Exportar .xlsx</button>
        </div>
      </div>
      <div class="table-wrap">
        <table class="data-table">
          <thead>
            <tr>
              <th>Pedido</th>
              <th>UF</th>
              <th>Data da coleta</th>
              <th>Tipo de caixa</th>
              <th>LPN</th>
              <th>Logger/Datalogger</th>
              <th>Status do logger</th>
            </tr>
          </thead>
          <tbody id="detail-tbody"></tbody>
        </table>
      </div>
    </div>

    <div class="footer">Fonte: vtc_stage.documentos. Regras: cd_lpn como caixa unica, cd_referencia vazio/nulo como sem datalogger, dt_coletaefetiva obrigatoria e ds_tipo usado para considerar somente caixas e ignorar pallets.</div>
  </div>

  <script>
    const SUMMARY = {summary_json};
    const TABLE_ROWS = {rows_json};
    const DS_TIPO_VALUES = {tipo_values_json};
    const GENERATED_AT_TS = {int(generated_at.timestamp() * 1000)};

    const els = {{
      periodo: document.getElementById("filter-periodo"),
      uf: document.getElementById("filter-uf"),
      search: document.getElementById("filter-search"),
      clear: document.getElementById("btn-clear"),
      exportXlsx: document.getElementById("btn-export-xlsx"),
      tbody: document.getElementById("detail-tbody"),
      summary: document.getElementById("detail-summary"),
      ufSummarySearch: document.getElementById("uf-summary-search"),
      ufQuickList: document.getElementById("uf-quick-list"),
      ufLimitActions: document.getElementById("uf-limit-actions"),
      ufFilterText: document.getElementById("uf-filter-text"),
      tipoSummarySearch: document.getElementById("tipo-summary-search"),
      tipoSummarySort: document.getElementById("tipo-summary-sort"),
      typeFilterGrid: document.getElementById("type-filter-grid"),
      tipoFilterText: document.getElementById("tipo-filter-text")
    }};
    const quickState = {{ ufLimit: "all", tipo: "" }};

    function clean(value) {{ return (value ?? "").toString().trim(); }}
    function formatInt(value) {{ return new Intl.NumberFormat("pt-BR").format(Number(value) || 0); }}
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
    function sortSummaryRows(rows, labelKey, sortMode) {{
      const view = rows.slice();
      if (sortMode === "asc") {{
        view.sort((a, b) => (Number(a.Caixas) || 0) - (Number(b.Caixas) || 0) || clean(a[labelKey]).localeCompare(clean(b[labelKey]), "pt-BR"));
      }} else if (sortMode === "az") {{
        view.sort((a, b) => clean(a[labelKey]).localeCompare(clean(b[labelKey]), "pt-BR"));
      }} else {{
        view.sort((a, b) => (Number(b.Caixas) || 0) - (Number(a.Caixas) || 0) || clean(a[labelKey]).localeCompare(clean(b[labelKey]), "pt-BR"));
      }}
      return view;
    }}
    function renderUfSummary() {{
      if (!els.ufQuickList) return;
      let rows = sortSummaryRows(SUMMARY.total_por_uf || [], "UF", "desc");
      const selectedUf = clean(els.uf.value);
      let html = '<button class="quick-filter-chip ' + (selectedUf ? "" : "active") + '" type="button" data-uf=""><span>Todas as UFs</span><strong>' + formatInt(SUMMARY.total_caixas) + '</strong></button>';
      rows.forEach((row) => {{
        const uf = clean(row.UF);
        html += '<button class="quick-filter-chip ' + (selectedUf === uf ? "active" : "") + '" type="button" data-uf="' + escapeHtml(uf) + '"><span>' + escapeHtml(uf) + '</span><strong>' + formatInt(row.Caixas) + '</strong></button>';
      }});
      if (!rows.length) html += '<div class="empty-box">Sem UFs para o filtro informado.</div>';
      els.ufQuickList.innerHTML = html;
      if (els.ufFilterText) {{
        els.ufFilterText.textContent = selectedUf ? "Filtro rapido aplicado: UF " + selectedUf + "." : "Todas as UFs selecionadas.";
      }}
      if (els.ufLimitActions) {{
        Array.from(els.ufLimitActions.querySelectorAll(".view-toggle")).forEach((btn) => {{
          btn.classList.toggle("active", clean(btn.dataset.limit) === quickState.ufLimit);
        }});
      }}
    }}
    function renderTipoSummary() {{
      const sortMode = clean(els.tipoSummarySort && els.tipoSummarySort.value) || "desc";
      const rows = SUMMARY.total_por_tipo || [];
      const sorted = sortSummaryRows(rows, "Tipo de caixa", sortMode);
      const maxValue = Math.max(1, ...(SUMMARY.total_por_tipo || []).map((row) => Number(row.Caixas) || 0));
      let html = '<button class="type-filter-card type-all ' + (quickState.tipo ? "" : "active") + '" type="button" data-tipo=""><div class="type-card-head"><span class="type-card-name">Todos os tipos</span><strong class="type-card-value">' + formatInt(SUMMARY.total_caixas) + '</strong></div><div class="type-card-meta">Limpa o filtro por tipo de caixa</div><div class="progress-bar"><div class="progress-fill" style="width: 100%"></div></div></button>';
      sorted.forEach((row) => {{
        const tipo = clean(row["Tipo de caixa"]);
        const value = Number(row.Caixas) || 0;
        const pct = Math.max(1, Math.round((value / maxValue) * 100));
        html += '<button class="type-filter-card ' + (quickState.tipo === tipo ? "active" : "") + '" type="button" data-tipo="' + escapeHtml(tipo) + '"><div class="type-card-head"><span class="type-card-name">' + escapeHtml(tipo) + '</span><strong class="type-card-value">' + formatInt(value) + '</strong></div><div class="type-card-meta">' + formatInt(value) + ' caixa(s)</div><div class="progress-bar"><div class="progress-fill" style="width: ' + pct + '%"></div></div></button>';
      }});
      if (!sorted.length) html += '<div class="empty-box">Sem tipos para o filtro informado.</div>';
      els.typeFilterGrid.innerHTML = html;
      if (els.tipoFilterText) {{
        els.tipoFilterText.textContent = quickState.tipo ? "Filtro rapido aplicado: " + quickState.tipo + "." : "Todos os tipos selecionados.";
      }}
    }}
    function setupUfFilter() {{
      const ufs = uniqueSorted(TABLE_ROWS.map((row) => row.UF));
      let html = '<option value="">Todas as UFs</option>';
      ufs.forEach((uf) => {{
        html += '<option value="' + escapeHtml(uf) + '">' + escapeHtml(uf) + '</option>';
      }});
      els.uf.innerHTML = html;
    }}
    function rowMatchesPeriod(row) {{
      const period = els.periodo.value;
      if (period === "all") return true;
      const hours = Number(period) || 0;
      const cutoff = GENERATED_AT_TS - (hours * 60 * 60 * 1000);
      return Number(row._coleta_ts || 0) >= cutoff;
    }}
    function filterRows() {{
      const uf = clean(els.uf.value);
      const tipo = clean(quickState.tipo);
      const q = clean(els.search.value).toLocaleLowerCase("pt-BR");
      return TABLE_ROWS.filter((row) => {{
        if (!rowMatchesPeriod(row)) return false;
        if (uf && clean(row.UF) !== uf) return false;
        if (tipo && clean(row["Tipo de caixa"]) !== tipo) return false;
        if (!q) return true;
        const haystack = [row.Pedido, row.UF, row["Data da coleta"], row["Tipo de caixa"], row.LPN, row["Status do logger"]]
          .map(clean)
          .join(" ")
          .toLocaleLowerCase("pt-BR");
        return haystack.includes(q);
      }});
    }}
    function renderTable() {{
      const rows = filterRows().sort((a, b) => Number(b._coleta_ts || 0) - Number(a._coleta_ts || 0));
      const applied = [];
      if (clean(els.uf.value)) applied.push("UF " + clean(els.uf.value));
      if (clean(quickState.tipo)) applied.push(clean(quickState.tipo));
      els.summary.textContent = formatInt(rows.length) + " caixa(s) exibidas" + (applied.length ? " para " + applied.join(" + ") : "") + ". A tabela usa uma linha por LPN.";
      if (!rows.length) {{
        els.tbody.innerHTML = '<tr><td colspan="7">Sem registros neste recorte.</td></tr>';
        return;
      }}
      let html = "";
      rows.forEach((row) => {{
        html += "<tr>";
        html += "<td>" + escapeHtml(row.Pedido) + "</td>";
        html += "<td>" + escapeHtml(row.UF) + "</td>";
        html += "<td>" + escapeHtml(row["Data da coleta"]) + "</td>";
        html += "<td>" + escapeHtml(row["Tipo de caixa"]) + "</td>";
        html += "<td>" + escapeHtml(row.LPN) + "</td>";
        html += "<td>" + escapeHtml(row["Logger/Datalogger"]) + "</td>";
        html += '<td><span class="status-badge">' + escapeHtml(row["Status do logger"]) + "</span></td>";
        html += "</tr>";
      }});
      els.tbody.innerHTML = html;
    }}
    function xmlEscape(value) {{
      return clean(value)
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&apos;");
    }}
    function utf8Bytes(text) {{
      return new TextEncoder().encode(text);
    }}
    const CRC_TABLE = (() => {{
      const table = new Uint32Array(256);
      for (let i = 0; i < 256; i++) {{
        let c = i;
        for (let k = 0; k < 8; k++) c = (c & 1) ? (0xedb88320 ^ (c >>> 1)) : (c >>> 1);
        table[i] = c >>> 0;
      }}
      return table;
    }})();
    function crc32(bytes) {{
      let crc = 0xffffffff;
      for (let i = 0; i < bytes.length; i++) {{
        crc = CRC_TABLE[(crc ^ bytes[i]) & 0xff] ^ (crc >>> 8);
      }}
      return (crc ^ 0xffffffff) >>> 0;
    }}
    function writeUint16(out, value) {{
      out.push(value & 0xff, (value >>> 8) & 0xff);
    }}
    function writeUint32(out, value) {{
      out.push(value & 0xff, (value >>> 8) & 0xff, (value >>> 16) & 0xff, (value >>> 24) & 0xff);
    }}
    function dosDateTime(date) {{
      const year = Math.max(1980, date.getFullYear());
      const dosTime = (date.getHours() << 11) | (date.getMinutes() << 5) | Math.floor(date.getSeconds() / 2);
      const dosDate = ((year - 1980) << 9) | ((date.getMonth() + 1) << 5) | date.getDate();
      return {{ dosTime, dosDate }};
    }}
    function createZip(files) {{
      const chunks = [];
      const central = [];
      let offset = 0;
      const now = new Date();
      const dt = dosDateTime(now);
      files.forEach((file) => {{
        const nameBytes = utf8Bytes(file.name);
        const data = utf8Bytes(file.content);
        const crc = crc32(data);
        const local = [];
        writeUint32(local, 0x04034b50);
        writeUint16(local, 20);
        writeUint16(local, 0x0800);
        writeUint16(local, 0);
        writeUint16(local, dt.dosTime);
        writeUint16(local, dt.dosDate);
        writeUint32(local, crc);
        writeUint32(local, data.length);
        writeUint32(local, data.length);
        writeUint16(local, nameBytes.length);
        writeUint16(local, 0);
        local.push(...nameBytes);
        chunks.push(new Uint8Array(local), data);
        const entry = [];
        writeUint32(entry, 0x02014b50);
        writeUint16(entry, 20);
        writeUint16(entry, 20);
        writeUint16(entry, 0x0800);
        writeUint16(entry, 0);
        writeUint16(entry, dt.dosTime);
        writeUint16(entry, dt.dosDate);
        writeUint32(entry, crc);
        writeUint32(entry, data.length);
        writeUint32(entry, data.length);
        writeUint16(entry, nameBytes.length);
        writeUint16(entry, 0);
        writeUint16(entry, 0);
        writeUint16(entry, 0);
        writeUint16(entry, 0);
        writeUint32(entry, 0);
        writeUint32(entry, offset);
        entry.push(...nameBytes);
        central.push(new Uint8Array(entry));
        offset += local.length + data.length;
      }});
      const centralOffset = offset;
      let centralSize = 0;
      central.forEach((entry) => {{
        chunks.push(entry);
        centralSize += entry.length;
      }});
      const end = [];
      writeUint32(end, 0x06054b50);
      writeUint16(end, 0);
      writeUint16(end, 0);
      writeUint16(end, files.length);
      writeUint16(end, files.length);
      writeUint32(end, centralSize);
      writeUint32(end, centralOffset);
      writeUint16(end, 0);
      chunks.push(new Uint8Array(end));
      return new Blob(chunks, {{ type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" }});
    }}
    function buildWorksheetXml(rows) {{
      const headers = ["Pedido", "UF", "Data da coleta", "Tipo de caixa", "LPN", "Logger/Datalogger", "Status do logger"];
      const data = [headers, ...rows.map((row) => headers.map((header) => clean(row[header])))];
      const sheetRows = data.map((cells, rowIndex) => {{
        const cellXml = cells.map((value, colIndex) => {{
          const ref = String.fromCharCode(65 + colIndex) + (rowIndex + 1);
          return '<c r="' + ref + '" t="inlineStr"><is><t>' + xmlEscape(value) + '</t></is></c>';
        }}).join("");
        return '<row r="' + (rowIndex + 1) + '">' + cellXml + '</row>';
      }}).join("");
      return '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>' +
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">' +
        '<sheetData>' + sheetRows + '</sheetData></worksheet>';
    }}
    function exportCurrentRowsXlsx() {{
      const rows = filterRows().sort((a, b) => Number(b._coleta_ts || 0) - Number(a._coleta_ts || 0));
      const files = [
        {{ name: "[Content_Types].xml", content: '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>' }},
        {{ name: "_rels/.rels", content: '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>' }},
        {{ name: "xl/workbook.xml", content: '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Detalhe das caixas" sheetId="1" r:id="rId1"/></sheets></workbook>' }},
        {{ name: "xl/_rels/workbook.xml.rels", content: '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>' }},
        {{ name: "xl/worksheets/sheet1.xml", content: buildWorksheetXml(rows) }},
      ];
      const blob = createZip(files);
      const stamp = new Date().toISOString().slice(0, 19).replace(/[-:T]/g, "");
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = "rastreio_caixas_sem_datalogger_" + stamp + ".xlsx";
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
    }}
    function refreshAll() {{
      renderTipoSummary();
      renderTable();
    }}
    els.periodo.addEventListener("change", renderTable);
    els.uf.addEventListener("change", () => {{
      renderTable();
    }});
    els.search.addEventListener("input", renderTable);
    els.clear.addEventListener("click", () => {{
      els.periodo.value = "all";
      els.uf.value = "";
      els.search.value = "";
      quickState.tipo = "";
      if (els.ufSummarySearch) els.ufSummarySearch.value = "";
      if (els.tipoSummarySearch) els.tipoSummarySearch.value = "";
      if (els.tipoSummarySort) els.tipoSummarySort.value = "desc";
      refreshAll();
    }});
    if (els.exportXlsx) els.exportXlsx.addEventListener("click", exportCurrentRowsXlsx);
    if (els.ufSummarySearch) els.ufSummarySearch.addEventListener("input", renderUfSummary);
    if (els.ufLimitActions) els.ufLimitActions.addEventListener("click", (ev) => {{
      const btn = ev.target.closest(".view-toggle");
      if (!btn) return;
      quickState.ufLimit = clean(btn.dataset.limit) || "10";
      renderUfSummary();
    }});
    if (els.ufQuickList) els.ufQuickList.addEventListener("click", (ev) => {{
      const btn = ev.target.closest(".quick-filter-chip");
      if (!btn) return;
      els.uf.value = clean(btn.dataset.uf);
      renderUfSummary();
      renderTable();
    }});
    if (els.tipoSummarySearch) els.tipoSummarySearch.addEventListener("input", renderTipoSummary);
    if (els.tipoSummarySort) els.tipoSummarySort.addEventListener("change", renderTipoSummary);
    if (els.typeFilterGrid) els.typeFilterGrid.addEventListener("click", (ev) => {{
      const card = ev.target.closest(".type-filter-card");
      if (!card) return;
      quickState.tipo = clean(card.dataset.tipo);
      renderTipoSummary();
      renderTable();
    }});
    setupUfFilter();
    refreshAll();
  </script>
</body>
</html>"""


def main() -> None:
    print("[rastreio] Consultando distribuicao de ds_tipo...")
    tipo_distribution = load_tipo_distribution()
    if tipo_distribution.empty:
        print("[rastreio] ds_tipo sem valores para registros com coleta e LPN.")
    else:
        for _, row in tipo_distribution.head(20).iterrows():
            print(
                "[rastreio] ds_tipo="
                f"{row.get('ds_tipo')} linhas={int(row.get('linhas') or 0)} "
                f"caixas={int(row.get('caixas') or 0)}"
            )

    print("[rastreio] Carregando caixas sem datalogger...")
    raw = load_raw_data()
    df = prepare_data(raw)
    summary = build_summary(df, now_for_coleta(df))
    print(f"[rastreio] Caixas sem datalogger: {summary['total_caixas']}")
    print(f"[rastreio] Pedidos afetados: {summary['total_pedidos']}")
    print(f"[rastreio] Ultimas 24h: {summary['caixas_24h']}")
    print(f"[rastreio] Ultimas 48h: {summary['caixas_48h']}")
    print("[rastreio] Validando cards contra tabela e filtro ds_tipo...")
    validate_business_rules(df, summary)

    html = build_page(df, tipo_distribution)
    OUTPUT_HTML.write_text(html, encoding="utf-8")
    print(f"[rastreio] HTML salvo: {OUTPUT_HTML.name}")
    print("[rastreio] Validacao OK: cards batem com a tabela; ds_tipo contem CAIXA e exclui PALLET.")


if __name__ == "__main__":
    main()
