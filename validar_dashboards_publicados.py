from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import re
import subprocess
import sys
import traceback
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from urllib.error import URLError
from urllib.request import Request, urlopen

from env_utils import load_env_file


ROOT = Path(__file__).resolve().parent

PYTHON_SCRIPTS = [
    "gerar_html_estoque.py",
    "gerar_html_controle_entregas.py",
    "HTMLACOMPANHAMENTO.py",
    "gerar_dashboard_entregas.py",
    "gerar_html_reversa.py",
    "gerar_html_rastreio_caixas_sem_datalogger.py",
    "env_utils.py",
    "validar_gestao_dispositivos_db.py",
    "validar_dashboards_publicados.py",
]

GENERATED_HTML = {
    "ESTOQUE_DATALOGGERS.html": {
        "min_size": 100_000,
        "markers": ["Gerado em", "const STATES", "const DETAIL_ROWS"],
    },
    "CONTROLE_ENTREGAS_20D.html": {
        "min_size": 100_000,
        "markers": ["Gerado em", "const RAW_DATA"],
    },
    "HTMLACOMPANHAMENTO.html": {
        "min_size": 100_000,
        "markers": ["Gerado em", "const payload", "generated_at"],
    },
    "REVERSA_DATALOGGERS.html": {
        "min_size": 100_000,
        "markers": ["Gerado em", "const ALL_ROWS", "const DEFAULT_PERIOD"],
    },
    "RASTREIO_CAIXAS_SEM_DATALOGGER.html": {
        "min_size": 20_000,
        "markers": ["Atualizado em", "const SUMMARY", "const TABLE_ROWS", "const DS_TIPO_VALUES"],
    },
    "GESTAO_DISPOSITIVOS.html": {
        "min_size": 20_000,
        "markers": ["GESTAO_DISPOSITIVOS_STAGE_DATA.js", "Fonte ativa", "buildPedidoLoggerKey"],
    },
}

STATIC_HTML = {
    "gerenciamento_termico.html": {
        "min_size": 20_000,
        "markers": [
            "Gerenciamento T",
            "INDICADOR_VTCBOX.html",
            "GESTAO_DISPOSITIVOS.html",
            "RASTREIO_CAIXAS_SEM_DATALOGGER.html",
        ],
    },
}

HTML_SCOPE_FILES = {
    "estoque": "ESTOQUE_DATALOGGERS.html",
    "controle": "CONTROLE_ENTREGAS_20D.html",
    "acompanhamento": "HTMLACOMPANHAMENTO.html",
    "reversa": "REVERSA_DATALOGGERS.html",
    "rastreio": "RASTREIO_CAIXAS_SEM_DATALOGGER.html",
    "gestao": "GESTAO_DISPOSITIVOS.html",
    "gerenciamento_termico": "gerenciamento_termico.html",
}

GENERATED_DATA = [
    "CONTROLE_ENTREGAS_20D.csv",
    "CONTROLE_ENTREGAS_20D_SLA_PENDENTES.csv",
    "GESTAO_DISPOSITIVOS_PLANILHA_DATA.js",
    "GESTAO_DISPOSITIVOS_STAGE_DATA.js",
]

CODE_FILES = [
    "ATUALIZAR_TUDO_10_MIN.bat",
    "ATUALIZAR_TUDO_10_MIN.ps1",
    "VALIDAR_DASHBOARDS_10_MIN.bat",
    "ATUALIZAR_REVERSA.bat",
    "HTMLACOMPANHAMENTO.py",
    "gerar_dashboard_entregas.py",
    "gerar_html_estoque.py",
    "gerar_html_controle_entregas.py",
    "gerar_html_reversa.py",
    "gerar_html_rastreio_caixas_sem_datalogger.py",
    "env_utils.py",
    "validar_gestao_dispositivos_db.py",
    "validar_dashboards_publicados.py",
    ".env.example",
    ".gitignore",
    "README.md",
]

PUBLIC_PAGES = {
    "gerenciamento_termico.html": "https://luan9753.github.io/banco-aura-dashboard/gerenciamento_termico.html",
    "ESTOQUE_DATALOGGERS.html": "https://luan9753.github.io/banco-aura-dashboard/ESTOQUE_DATALOGGERS.html",
    "CONTROLE_ENTREGAS_20D.html": "https://luan9753.github.io/banco-aura-dashboard/CONTROLE_ENTREGAS_20D.html",
    "HTMLACOMPANHAMENTO.html": "https://luan9753.github.io/banco-aura-dashboard/HTMLACOMPANHAMENTO.html",
    "RASTREIO_CAIXAS_SEM_DATALOGGER.html": "https://luan9753.github.io/banco-aura-dashboard/RASTREIO_CAIXAS_SEM_DATALOGGER.html",
    "GESTAO_DISPOSITIVOS.html": "https://luan9753.github.io/banco-aura-dashboard/GESTAO_DISPOSITIVOS.html",
}

REQUIRED_PRESENT = [
    "AURA_DB_HOST",
    "AURA_DB_NAME",
    "AURA_DB_USER",
    "AURA_DB_PASSWORD",
    "AURA_DB_PORT",
    "AURA_START_DATE",
    "AURA_END_DATE",
    "AURA_POSTGRES_HOST",
    "AURA_POSTGRES_NAME",
    "AURA_POSTGRES_USER",
    "AURA_POSTGRES_PASSWORD",
    "AURA_POSTGRES_PORT",
]

REQUIRED_NONEMPTY = [
    "AURA_DB_HOST",
    "AURA_DB_NAME",
    "AURA_DB_USER",
    "AURA_DB_PASSWORD",
    "AURA_DB_PORT",
    "AURA_START_DATE",
    "AURA_POSTGRES_HOST",
    "AURA_POSTGRES_NAME",
    "AURA_POSTGRES_USER",
    "AURA_POSTGRES_PASSWORD",
    "AURA_POSTGRES_PORT",
]


def _print(msg: str) -> None:
    print(msg, flush=True)


def _fail(msg: str) -> int:
    _print(f"[ERRO] {msg}")
    return 1


def _as_int(value: object) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _parse_date(value: str, var_name: str) -> None:
    if not value:
        return
    datetime.strptime(value, "%Y-%m-%d")


def _postgres_cfg(prefix: str) -> dict:
    if prefix == "AURA_DB":
        return {
            "host": os.getenv("AURA_DB_HOST", ""),
            "database": os.getenv("AURA_DB_NAME", ""),
            "user": os.getenv("AURA_DB_USER", ""),
            "password": os.getenv("AURA_DB_PASSWORD", ""),
            "port": int(os.getenv("AURA_DB_PORT", "5432")),
        }
    return {
        "host": os.getenv("AURA_POSTGRES_HOST", ""),
        "database": os.getenv("AURA_POSTGRES_NAME", ""),
        "user": os.getenv("AURA_POSTGRES_USER", ""),
        "password": os.getenv("AURA_POSTGRES_PASSWORD", ""),
        "port": int(os.getenv("AURA_POSTGRES_PORT", "5432")),
    }


def _check_postgres_connection(label: str, cfg: dict) -> None:
    import psycopg2

    _print(f"[check] Testando conexao {label}...")
    with psycopg2.connect(connect_timeout=15, **cfg) as conn:
        with conn.cursor() as cur:
            cur.execute("select 1")
            row = cur.fetchone()
    if not row or row[0] != 1:
        raise RuntimeError(f"select 1 inesperado em {label}")
    _print(f"[check] Conexao OK: {label}")


def validate_acompanhamento_payload(payload: dict) -> dict:
    """Raise if HTMLACOMPANHAMENTO would be generated with empty KPIs."""
    totals = payload.get("totals") or {}
    daily = payload.get("daily") or []
    generated_at = str(payload.get("generated_at") or "").strip()

    main_values = {
        "pedidos_entregues_total": _as_int(totals.get("pedidos_entregues_total")),
        "pedidos_inseridos_total": _as_int(totals.get("pedidos_inseridos_total")),
        "loggers_entregues_total": _as_int(totals.get("loggers_entregues_total")),
        "loggers_inseridos_total": _as_int(totals.get("loggers_inseridos_total")),
    }
    if not generated_at:
        raise ValueError("payload_sem_generated_at")
    if not daily:
        raise ValueError("payload_sem_series_diaria")
    if max(main_values.values() or [0]) <= 0:
        raise ValueError("payload_sem_kpi_principal_real")

    summary = {
        "dias": len(daily),
        **main_values,
    }
    return summary


def build_acompanhamento_payload() -> dict:
    from gerar_dashboard_entregas import (
        DEFAULT_DATABASE,
        DEFAULT_HOST,
        DEFAULT_PASSWORD,
        DEFAULT_PORT,
        DEFAULT_USER,
        build_payload,
        get_connection,
        query_data,
    )

    args = SimpleNamespace(
        host=os.getenv("AURA_DB_HOST", DEFAULT_HOST),
        database=os.getenv("AURA_DB_NAME", DEFAULT_DATABASE),
        user=os.getenv("AURA_DB_USER", DEFAULT_USER),
        password=os.getenv("AURA_DB_PASSWORD", DEFAULT_PASSWORD),
        port=int(os.getenv("AURA_DB_PORT", DEFAULT_PORT)),
    )
    start_date = os.getenv("AURA_START_DATE", "2025-12-04")
    end_date = (os.getenv("AURA_END_DATE", "") or "").strip() or datetime.now().strftime("%Y-%m-%d")

    with get_connection(args) as conn:
        (
            rows,
            sensor_rows,
            sensor_daily_rows,
            sensor_daily_stats_rows,
            order_daily_stats_rows,
            latency_row,
            delivery_launch,
        ) = query_data(conn, start_date, end_date)

    return build_payload(
        rows,
        start_date,
        end_date,
        sensor_rows,
        sensor_daily_rows,
        sensor_daily_stats_rows,
        order_daily_stats_rows,
        latency_row,
        delivery_launch,
    )


def command_check_env(args: argparse.Namespace) -> int:
    try:
        env_path = load_env_file()
        if env_path:
            _print(f"[check] .env carregado: {env_path}")
        else:
            _print("[check] .env nao encontrado; usando somente variaveis do processo.")

        missing_present = [key for key in REQUIRED_PRESENT if key not in os.environ]
        missing_value = [key for key in REQUIRED_NONEMPTY if not os.getenv(key, "").strip()]
        if missing_present:
            return _fail("Variaveis ausentes: " + ", ".join(missing_present))
        if missing_value:
            return _fail("Variaveis sem valor: " + ", ".join(missing_value))

        for var_name in ["AURA_DB_PORT", "AURA_POSTGRES_PORT"]:
            int(os.getenv(var_name, ""))
        _parse_date(os.getenv("AURA_START_DATE", ""), "AURA_START_DATE")
        _parse_date(os.getenv("AURA_END_DATE", ""), "AURA_END_DATE")

        _print("[check] Variaveis AURA_DB_* OK.")
        _print("[check] Variaveis AURA_POSTGRES_* OK.")
        if os.getenv("AURA_END_DATE", ""):
            _print("[check] AURA_END_DATE definida; o acompanhamento usara esta data final.")
        else:
            _print("[check] AURA_END_DATE vazia; o acompanhamento usara a data atual.")

        if not args.no_connection:
            _check_postgres_connection("AURA_DB", _postgres_cfg("AURA_DB"))
            _check_postgres_connection("AURA_POSTGRES", _postgres_cfg("AURA_POSTGRES"))

        if not args.no_payload:
            _print("[check] Consultando payload real do HTMLACOMPANHAMENTO...")
            payload = build_acompanhamento_payload()
            summary = validate_acompanhamento_payload(payload)
            _print(
                "[check] HTMLACOMPANHAMENTO payload OK: "
                f"dias={summary['dias']} "
                f"pedidos_entregues={summary['pedidos_entregues_total']} "
                f"pedidos_inseridos={summary['pedidos_inseridos_total']} "
                f"loggers_entregues={summary['loggers_entregues_total']} "
                f"loggers_inseridos={summary['loggers_inseridos_total']}"
            )

        return 0
    except Exception:
        traceback.print_exc()
        return 1


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore")


def _extract_js_json(text: str, var_name: str):
    prefix = f"const {var_name} = "
    start = text.find(prefix)
    if start < 0:
        raise ValueError(f"variavel_js_nao_encontrada:{var_name}")
    fragment = text[start + len(prefix) :].lstrip()
    value, _ = json.JSONDecoder().raw_decode(fragment)
    return value


def _extract_window_json(text: str, var_name: str):
    prefix = f"window.{var_name} = "
    start = text.find(prefix)
    if start < 0:
        raise ValueError(f"variavel_window_nao_encontrada:{var_name}")
    fragment = text[start + len(prefix) :].lstrip()
    value, _ = json.JSONDecoder().raw_decode(fragment)
    return value


def _validate_common_html(name: str, cycle_start: float | None) -> str:
    spec = GENERATED_HTML[name]
    path = ROOT / name
    if not path.exists():
        raise FileNotFoundError(f"{name} nao existe")
    stat = path.stat()
    if cycle_start and stat.st_mtime < cycle_start - 5:
        raise RuntimeError(f"{name} nao foi modificado neste ciclo")
    if stat.st_size < spec["min_size"]:
        raise RuntimeError(f"{name} pequeno demais: {stat.st_size} bytes")

    text = _read_text(path)
    for marker in spec["markers"]:
        if marker not in text:
            raise RuntimeError(f"{name} sem marcador obrigatorio: {marker}")
    return text


def _validate_static_html(name: str) -> str:
    spec = STATIC_HTML[name]
    path = ROOT / name
    if not path.exists():
        raise FileNotFoundError(f"{name} nao existe")
    stat = path.stat()
    if stat.st_size < spec["min_size"]:
        raise RuntimeError(f"{name} pequeno demais: {stat.st_size} bytes")

    text = _read_text(path)
    for marker in spec["markers"]:
        if marker not in text:
            raise RuntimeError(f"{name} sem marcador obrigatorio: {marker}")
    _print(f"[html] {name} OK: pagina estatica sem script gerador proprio; validada para publicacao")
    return text


def _validate_estoque(cycle_start: float | None) -> None:
    text = _validate_common_html("ESTOQUE_DATALOGGERS.html", cycle_start)
    states = _extract_js_json(text, "STATES")
    details = _extract_js_json(text, "DETAIL_ROWS")
    all_state = states.get("ALL") if isinstance(states, dict) else None
    if not isinstance(all_state, dict):
        raise RuntimeError("ESTOQUE_DATALOGGERS.html sem estado ALL")
    kpis = [
        _as_int(all_state.get("total_estoque")),
        _as_int(all_state.get("apto_uso")),
        _as_int(all_state.get("resumo_cf")),
        _as_int(all_state.get("total_mov_cf")),
        _as_int(all_state.get("total_rec_est")),
    ]
    if max(kpis or [0]) <= 0:
        raise RuntimeError("ESTOQUE_DATALOGGERS.html sem KPIs reais")
    if not isinstance(details, list) or len(details) <= 0:
        raise RuntimeError("ESTOQUE_DATALOGGERS.html sem detalhe de registros")
    _print(
        "[html] ESTOQUE_DATALOGGERS.html OK: "
        f"detalhes={len(details)} total_estoque={_as_int(all_state.get('total_estoque'))}"
    )


def _csv_rows(path: Path) -> int:
    if not path.exists():
        raise FileNotFoundError(f"{path.name} nao existe")
    lines = path.read_text(encoding="utf-8-sig", errors="ignore").splitlines()
    if not lines:
        return 0
    return max(0, len([line for line in lines[1:] if line.strip()]))


def _validate_controle(cycle_start: float | None) -> None:
    text = _validate_common_html("CONTROLE_ENTREGAS_20D.html", cycle_start)
    data = _extract_js_json(text, "RAW_DATA")
    if not isinstance(data, list) or len(data) <= 0:
        raise RuntimeError("CONTROLE_ENTREGAS_20D.html sem RAW_DATA real")
    main_rows = _csv_rows(ROOT / "CONTROLE_ENTREGAS_20D.csv")
    if main_rows <= 0:
        raise RuntimeError("CONTROLE_ENTREGAS_20D.csv sem linhas")
    sla_rows = _csv_rows(ROOT / "CONTROLE_ENTREGAS_20D_SLA_PENDENTES.csv")
    _print(
        "[html] CONTROLE_ENTREGAS_20D.html OK: "
        f"raw_data={len(data)} csv_linhas={main_rows} sla_pendentes={sla_rows}"
    )


def _validate_reversa(cycle_start: float | None) -> None:
    text = _validate_common_html("REVERSA_DATALOGGERS.html", cycle_start)
    rows = _extract_js_json(text, "ALL_ROWS")
    if not isinstance(rows, list) or len(rows) <= 0:
        raise RuntimeError("REVERSA_DATALOGGERS.html sem ALL_ROWS real")
    keys: set[str] = set()
    duplicates = 0
    pending = 0
    for row in rows:
        if not isinstance(row, list) or len(row) < 8:
            continue
        pedido = str(row[0]).strip().upper()
        logger = str(row[1]).strip().upper()
        if not pedido or not logger:
            continue
        key = f"{pedido}|{logger}"
        if key in keys:
            duplicates += 1
        else:
            keys.add(key)
            if str(row[7]).strip() == "Pendente de Retorno":
                pending += 1
    if duplicates:
        raise RuntimeError(
            "REVERSA_DATALOGGERS.html com chaves Pedido+Logger duplicadas "
            f"em ALL_ROWS: {duplicates}"
        )
    _print(
        "[html] REVERSA_DATALOGGERS.html OK: "
        f"registros={len(rows)} chaves_unicas={len(keys)} pendentes={pending}"
    )


def _validate_acompanhamento_html(cycle_start: float | None) -> None:
    text = _validate_common_html("HTMLACOMPANHAMENTO.html", cycle_start)
    payload = _extract_js_json(text, "payload")
    summary = validate_acompanhamento_payload(payload)
    _print(
        "[html] HTMLACOMPANHAMENTO.html OK: "
        f"dias={summary['dias']} "
        f"pedidos_entregues={summary['pedidos_entregues_total']} "
        f"pedidos_inseridos={summary['pedidos_inseridos_total']} "
        f"loggers_entregues={summary['loggers_entregues_total']} "
        f"loggers_inseridos={summary['loggers_inseridos_total']}"
    )


def _validate_rastreio(cycle_start: float | None) -> None:
    text = _validate_common_html("RASTREIO_CAIXAS_SEM_DATALOGGER.html", cycle_start)
    summary = _extract_js_json(text, "SUMMARY")
    rows = _extract_js_json(text, "TABLE_ROWS")
    tipo_values = _extract_js_json(text, "DS_TIPO_VALUES")
    if not isinstance(summary, dict):
        raise RuntimeError("RASTREIO_CAIXAS_SEM_DATALOGGER.html sem SUMMARY valido")
    if not isinstance(rows, list):
        raise RuntimeError("RASTREIO_CAIXAS_SEM_DATALOGGER.html sem TABLE_ROWS valido")
    if not isinstance(tipo_values, list):
        raise RuntimeError("RASTREIO_CAIXAS_SEM_DATALOGGER.html sem DS_TIPO_VALUES valido")

    caixa_keys: set[tuple[str, str]] = set()
    pedidos: set[str] = set()
    ufs: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            raise RuntimeError("RASTREIO_CAIXAS_SEM_DATALOGGER.html contem linha invalida")
        lpn = str(row.get("LPN") or "").strip()
        romaneio = str(row.get("Romaneio") or "").strip()
        pedido = str(row.get("Pedido") or "").strip()
        uf = str(row.get("UF") or "").strip()
        logger = str(row.get("Logger/Datalogger") or "").strip()
        tipo = str(row.get("Tipo de caixa") or "").strip().upper()
        data = str(row.get("Data da coleta") or "").strip()
        data_embarque = str(row.get("Data Coleta Embarque") or "").strip()
        status = str(row.get("Status do logger") or "").strip()
        if not lpn:
            raise RuntimeError("RASTREIO_CAIXAS_SEM_DATALOGGER.html contem linha sem LPN")
        if not romaneio:
            raise RuntimeError(f"RASTREIO_CAIXAS_SEM_DATALOGGER.html contem linha sem Romaneio para LPN {lpn}")
        caixa_key = (romaneio, lpn)
        if caixa_key in caixa_keys:
            raise RuntimeError(f"RASTREIO_CAIXAS_SEM_DATALOGGER.html contem par Romaneio + LPN duplicado: {romaneio} + {lpn}")
        if logger:
            raise RuntimeError(f"RASTREIO_CAIXAS_SEM_DATALOGGER.html contem logger preenchido para LPN {lpn}")
        if not data:
            raise RuntimeError(f"RASTREIO_CAIXAS_SEM_DATALOGGER.html contem coleta vazia para LPN {lpn}")
        if data_embarque and not re.fullmatch(r"\d{2}/\d{2}/\d{4} \d{2}:\d{2}:\d{2}", data_embarque):
            raise RuntimeError(f"RASTREIO_CAIXAS_SEM_DATALOGGER.html contem Data Coleta Embarque em formato inesperado para LPN {lpn}")
        if "PALLET" in tipo:
            raise RuntimeError(f"RASTREIO_CAIXAS_SEM_DATALOGGER.html contem pallet em ds_tipo para LPN {lpn}")
        if "CAIXA" not in tipo:
            raise RuntimeError(f"RASTREIO_CAIXAS_SEM_DATALOGGER.html contem ds_tipo que nao representa caixa para LPN {lpn}")
        if status != "Sem datalogger":
            raise RuntimeError(f"RASTREIO_CAIXAS_SEM_DATALOGGER.html contem status inesperado para LPN {lpn}")
        caixa_keys.add(caixa_key)
        if pedido:
            pedidos.add(pedido)
        if uf:
            ufs.add(uf)

    if _as_int(summary.get("total_caixas")) != len(caixa_keys):
        raise RuntimeError("RASTREIO_CAIXAS_SEM_DATALOGGER.html: card total de caixas nao bate com tabela")
    if _as_int(summary.get("linhas_tabela")) != len(rows):
        raise RuntimeError("RASTREIO_CAIXAS_SEM_DATALOGGER.html: linhas_tabela nao bate com TABLE_ROWS")
    if _as_int(summary.get("total_pedidos")) != len(pedidos):
        raise RuntimeError("RASTREIO_CAIXAS_SEM_DATALOGGER.html: card total de pedidos nao bate com tabela")
    if _as_int(summary.get("ufs_afetadas")) != len(ufs):
        raise RuntimeError("RASTREIO_CAIXAS_SEM_DATALOGGER.html: card UFs afetadas nao bate com tabela")
    if _as_int(summary.get("caixas_24h")) > _as_int(summary.get("caixas_48h")):
        raise RuntimeError("RASTREIO_CAIXAS_SEM_DATALOGGER.html: 24h maior que 48h")
    if _as_int(summary.get("caixas_48h")) > _as_int(summary.get("total_caixas")):
        raise RuntimeError("RASTREIO_CAIXAS_SEM_DATALOGGER.html: 48h maior que total")

    _print(
        "[html] RASTREIO_CAIXAS_SEM_DATALOGGER.html OK: "
        f"caixas={len(caixa_keys)} pedidos={len(pedidos)} ufs={len(ufs)} "
        "ds_tipo=CAIXA sem PALLET"
    )


def _validate_no_conflict_markers(name: str, text: str) -> None:
    if "<<<<<<<" in text or "=======" in text or ">>>>>>>" in text:
        raise RuntimeError(f"{name} contem marcador de conflito Git")


def _validate_gestao(cycle_start: float | None) -> None:
    text = _validate_common_html("GESTAO_DISPOSITIVOS.html", cycle_start)
    _validate_no_conflict_markers("GESTAO_DISPOSITIVOS.html", text)
    if "../data/estoque.json" in text or "../data/entregas.json" in text:
        raise RuntimeError("GESTAO_DISPOSITIVOS.html ainda referencia JSON fora do projeto")
    if "SEM_LOGGER" in text or "SEM_PEDIDO" in text or "buildPedidoLoggerKey(row, fieldMap, index)" in text:
        raise RuntimeError("GESTAO_DISPOSITIVOS.html voltou a aceitar pedido/logger vazio na chave de deduplicacao")
    required_markers = [
        "estoqueDashboard: \"ESTOQUE_DATALOGGERS.html\"",
        "await carregarEstoqueDashboard(resultado)",
        "numberOrNull(base.totalEstoque)",
        "function buildPedidoLoggerKey(row, fieldMap)",
    ]
    for marker in required_markers:
        if marker not in text:
            raise RuntimeError(f"GESTAO_DISPOSITIVOS.html sem regra/fonte corrigida: {marker}")

    planilha_text = _read_text(ROOT / "GESTAO_DISPOSITIVOS_PLANILHA_DATA.js")
    _validate_no_conflict_markers("GESTAO_DISPOSITIVOS_PLANILHA_DATA.js", planilha_text)
    if "GESTAO_DISPOSITIVOS_STAGE_DATA = window.GESTAO_DISPOSITIVOS_PLANILHA_DATA" in planilha_text:
        raise RuntimeError("GESTAO_DISPOSITIVOS_PLANILHA_DATA.js nao pode sobrescrever STAGE_DATA preservado")

    stage_text = _read_text(ROOT / "GESTAO_DISPOSITIVOS_STAGE_DATA.js")
    _validate_no_conflict_markers("GESTAO_DISPOSITIVOS_STAGE_DATA.js", stage_text)
    stage_data = _extract_window_json(stage_text, "GESTAO_DISPOSITIVOS_STAGE_DATA")
    summary = stage_data.get("summary") if isinstance(stage_data, dict) else None
    all_summary = summary.get("ALL") if isinstance(summary, dict) else None
    if not isinstance(all_summary, dict):
        raise RuntimeError("GESTAO_DISPOSITIVOS_STAGE_DATA.js sem summary.ALL")
    if _as_int(all_summary.get("registrosEstoque")) == 0 and all_summary.get("totalEstoque") not in (None, ""):
        raise RuntimeError("GESTAO_DISPOSITIVOS_STAGE_DATA.js tem totalEstoque com registrosEstoque=0")
    if _as_int(all_summary.get("loggersEntregues")) < _as_int(all_summary.get("loggersRetornados")):
        raise RuntimeError("GESTAO_DISPOSITIVOS_STAGE_DATA.js tem retornados maior que entregues")
    campos = stage_data.get("campos") if isinstance(stage_data, dict) else {}
    if campos.get("pedido") != "documentos.nr_pedido" or campos.get("logger") != "documentos.ds_tag":
        raise RuntimeError("GESTAO_DISPOSITIVOS_STAGE_DATA.js sem amarracao nr_pedido + ds_tag")

    estoque_text = _read_text(ROOT / "ESTOQUE_DATALOGGERS.html")
    states = _extract_js_json(estoque_text, "STATES")
    details = _extract_js_json(estoque_text, "DETAIL_ROWS")
    all_state = states.get("ALL") if isinstance(states, dict) else None
    if not isinstance(all_state, dict):
        raise RuntimeError("ESTOQUE_DATALOGGERS.html sem STATES.ALL para gestao")
    if _as_int(all_state.get("total_estoque")) <= 0:
        raise RuntimeError("ESTOQUE_DATALOGGERS.html sem total_estoque valido para gestao")
    if not isinstance(details, list) or len(details) <= 0:
        raise RuntimeError("ESTOQUE_DATALOGGERS.html sem DETAIL_ROWS para gestao")

    _print(
        "[html] GESTAO_DISPOSITIVOS.html OK: "
        f"stage_entregas={_as_int(all_summary.get('registrosEntregas'))} "
        f"estoque_detalhes={len(details)} "
        f"estoque_total={_as_int(all_state.get('total_estoque'))}"
    )


def command_validate_html(args: argparse.Namespace) -> int:
    cycle_start = float(args.cycle_start) if args.cycle_start else None
    validators = {
        "estoque": _validate_estoque,
        "controle": _validate_controle,
        "reversa": _validate_reversa,
        "acompanhamento": _validate_acompanhamento_html,
        "rastreio": _validate_rastreio,
        "gestao": _validate_gestao,
        "gerenciamento_termico": lambda _cycle_start: _validate_static_html("gerenciamento_termico.html"),
    }
    selected = list(validators)
    if args.only:
        selected = [item.strip().lower() for item in args.only.split(",") if item.strip()]
        invalid = [item for item in selected if item not in validators]
        if invalid:
            return _fail(
                "Escopo invalido em --only: "
                + ", ".join(invalid)
                + ". Use: "
                + ", ".join(validators)
            )
    try:
        for name in selected:
            validators[name](cycle_start)
        return 0
    except Exception:
        traceback.print_exc()
        return 1


def _git(args: list[str], *, check: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=ROOT,
        text=False,
        capture_output=True,
        check=check,
    )


def _git_text(args: list[str]) -> str:
    proc = _git(args)
    if proc.returncode != 0:
        return ""
    return proc.stdout.decode("utf-8", errors="ignore")


def _git_status_for(path: str) -> str:
    return _git_text(["status", "--porcelain", "--", path]).strip()


def _git_head_bytes(path: str) -> bytes | None:
    proc = _git(["show", f"HEAD:{path}"])
    if proc.returncode != 0:
        return None
    return proc.stdout


def _normalize_html_for_meaningful_diff(path: str, data: bytes) -> str:
    text = data.decode("utf-8", errors="ignore").replace("\r\n", "\n")
    text = re.sub(r'("generated_at"\s*:\s*")[^"]+(")', r"\1<TIMESTAMP>\2", text)
    text = re.sub(
        r"(<span id=\"meta-gerado\">)[^<]+(</span>)",
        r"\1<TIMESTAMP>\2",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"(Gerado em(?::)?(?:</strong>)?(?:\s|&nbsp;)*)\d{2}/\d{2}/\d{4} \d{2}:\d{2}(?::\d{2})?",
        r"\1<TIMESTAMP>",
        text,
        flags=re.IGNORECASE,
    )
    if path == "CONTROLE_ENTREGAS_20D.html":
        # Plotly used to generate random div ids on every render; those ids are
        # not data and must not force a publish by themselves.
        text = re.sub(
            r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
            "<PLOTLY_DIV_ID>",
            text,
            flags=re.IGNORECASE,
        )
    return text


def _restore_worktree_path(path: str) -> None:
    subprocess.run(
        ["git", "restore", "--worktree", "--staged", "--", path],
        cwd=ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )


def command_changed_files(args: argparse.Namespace) -> int:
    try:
        stage_paths: list[str] = []
        timestamp_only: list[str] = []

        html_paths = list(GENERATED_HTML) + list(STATIC_HTML)
        if args.only:
            selected = [item.strip().lower() for item in args.only.split(",") if item.strip()]
            invalid = [item for item in selected if item not in HTML_SCOPE_FILES]
            if invalid:
                raise ValueError(f"Escopo invalido para changed-files: {', '.join(invalid)}")
            html_paths = [HTML_SCOPE_FILES[item] for item in selected]

        for path in html_paths:
            if not _git_status_for(path):
                continue
            current_path = ROOT / path
            if not current_path.exists():
                stage_paths.append(path)
                continue
            head = _git_head_bytes(path)
            current = current_path.read_bytes()
            if head is None:
                stage_paths.append(path)
                continue
            if _normalize_html_for_meaningful_diff(path, current) != _normalize_html_for_meaningful_diff(path, head):
                stage_paths.append(path)
            elif current != head:
                timestamp_only.append(path)

        if not args.html_only:
            for path in GENERATED_DATA + CODE_FILES:
                if _git_status_for(path) and path not in stage_paths:
                    stage_paths.append(path)

        if args.publish_timestamp_only:
            for path in timestamp_only:
                if path not in stage_paths:
                    stage_paths.append(path)
        elif args.restore_timestamp_only:
            for path in timestamp_only:
                _restore_worktree_path(path)

        if args.out:
            out_path = Path(args.out)
            out_path.write_text("\n".join(stage_paths) + ("\n" if stage_paths else ""), encoding="utf-8")

        for path in stage_paths:
            _print(path)
        if timestamp_only:
            if args.publish_timestamp_only:
                _print("[stage] Publicados por carimbo de atualizacao: " + ", ".join(timestamp_only))
            else:
                _print("[stage] Ignorados por alteracao apenas de horario: " + ", ".join(timestamp_only))
            if args.restore_timestamp_only and not args.publish_timestamp_only:
                _print("[stage] Arquivos restaurados para evitar commit somente de horario.")
        if not stage_paths:
            _print("[stage] Nenhum arquivo com alteracao real para commit.")
        return 0
    except Exception:
        traceback.print_exc()
        return 1


def _cmd_text(args: list[str]) -> tuple[int, str]:
    proc = subprocess.run(
        args,
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
        encoding="utf-8",
        errors="ignore",
    )
    output = (proc.stdout or "") + (proc.stderr or "")
    return proc.returncode, output.strip()


def _extract_generated_value(name: str, text: str) -> str:
    if name == "HTMLACOMPANHAMENTO.html":
        try:
            payload = _extract_js_json(text, "payload")
            value = str(payload.get("generated_at") or "").strip()
            if value:
                return value
        except Exception:
            pass
    match = re.search(
        r"Gerado em\s*:?\s*(?:</strong>)?\s*([^<\n]+)",
        text,
        flags=re.IGNORECASE,
    )
    if match:
        return re.sub(r"\s+", " ", match.group(1).replace("&nbsp;", " ")).strip()
    return ""


def _parse_generated_datetime(value: str) -> datetime | None:
    cleaned = re.sub(r"<[^>]+>", " ", value or "")
    cleaned = cleaned.replace("&nbsp;", " ").replace("\xa0", " ")
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M"):
        try:
            return datetime.strptime(cleaned, fmt)
        except ValueError:
            continue
    return None


def _fetch_public_page(url: str) -> str:
    sep = "&" if "?" in url else "?"
    cache_buster = datetime.now().strftime("%Y%m%d%H%M%S")
    request = Request(
        f"{url}{sep}verificacao={cache_buster}",
        headers={
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
            "User-Agent": "AuraDailyDashboardCheck/1.0",
        },
    )
    with urlopen(request, timeout=30) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        return response.read().decode(charset, errors="ignore")


def _last_success_from_log(log_path: Path) -> datetime | None:
    if not log_path.exists():
        return None
    text = log_path.read_text(encoding="utf-8", errors="ignore")
    matches = list(
        re.finditer(
            r"\[(\d{2}/\d{2}/\d{4})\s+(\d{2}:\d{2}:\d{2}),\d+\]\s+\[OK\]\s+Ciclo concluido com sucesso\.",
            text,
        )
    )
    if not matches:
        return None
    last = matches[-1]
    return datetime.strptime(" ".join(last.groups()), "%d/%m/%Y %H:%M:%S")


def command_daily_check(args: argparse.Namespace) -> int:
    log_dir = ROOT / "logs"
    log_dir.mkdir(exist_ok=True)
    log_path = log_dir / "verificacao_diaria.txt"
    today = datetime.now().date()
    errors: list[str] = []
    alerts: list[str] = []

    def emit(status: str, message: str) -> None:
        line = f"{status} - {message}"
        print(line, flush=True)
        with log_path.open("a", encoding="utf-8") as fh:
            fh.write(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {line}\n")

    def ok(message: str) -> None:
        emit("OK", message)

    def alert(message: str) -> None:
        alerts.append(message)
        emit("ALERTA", message)

    def error(message: str) -> None:
        errors.append(message)
        emit("ERRO", message)

    emit("INFO", "=" * 72)
    emit("INFO", "VERIFICACAO DIARIA DOS DASHBOARDS AURA")
    emit("INFO", f"Pasta: {ROOT}")

    for html_name in GENERATED_HTML:
        path = ROOT / html_name
        if not path.exists():
            error(f"Dashboard nao encontrado: {html_name}")
            continue
        stat = path.stat()
        modified_at = datetime.fromtimestamp(stat.st_mtime)
        if stat.st_size < GENERATED_HTML[html_name]["min_size"]:
            error(f"{html_name} esta pequeno demais ({stat.st_size} bytes)")
        if modified_at.date() == today:
            ok(f"Dashboard atualizado hoje: {html_name} ({modified_at.strftime('%Y-%m-%d %H:%M:%S')})")
        else:
            alert(f"Arquivo nao atualizado hoje: {html_name} ({modified_at.strftime('%Y-%m-%d %H:%M:%S')})")
        text = _read_text(path)
        generated = _extract_generated_value(html_name, text)
        generated_dt = _parse_generated_datetime(generated)
        if generated_dt and generated_dt.date() == today:
            ok(f"Data exibida no HTML confere com hoje: {html_name} ({generated})")
        elif generated:
            alert(f"Data exibida no HTML pode estar antiga: {html_name} ({generated})")
        else:
            alert(f"Nao foi possivel identificar a data exibida em {html_name}")

    for html_name in STATIC_HTML:
        try:
            _validate_static_html(html_name)
            path = ROOT / html_name
            stat = path.stat()
            ok(
                "Pagina estatica sem script gerador proprio validada: "
                f"{html_name} ({stat.st_size} bytes)"
            )
        except Exception as exc:
            error(f"Validacao da pagina estatica falhou: {html_name}: {exc}")

    buffer = io.StringIO()
    validation_error: Exception | None = None
    with contextlib.redirect_stdout(buffer):
        try:
            _validate_estoque(None)
            _validate_controle(None)
            _validate_acompanhamento_html(None)
        except Exception as exc:
            validation_error = exc
    captured = buffer.getvalue().strip()
    if captured:
        for line in captured.splitlines():
            emit("INFO", line)
    if validation_error:
        error(f"Validacao interna dos HTMLs falhou: {validation_error}")
    else:
        ok("Payloads e indicadores principais dos HTMLs foram validados")

    rc, remote_url = _cmd_text(["git", "remote", "get-url", "origin"])
    if rc == 0 and remote_url:
        ok(f"Repositorio remoto configurado: {remote_url}")
    else:
        error("Repositorio remoto origin nao configurado")

    branch_rc, branch = _cmd_text(["git", "branch", "--show-current"])
    if branch_rc == 0 and branch:
        ok(f"Branch atual: {branch}")
    else:
        alert("Nao foi possivel identificar o branch atual")

    fetch_rc, fetch_out = _cmd_text(["git", "fetch", "--quiet", "origin", "main"])
    if fetch_rc == 0:
        ok("Fetch do origin/main concluido")
    else:
        error(f"Falha ao buscar origin/main: {fetch_out}")

    rc, local_head = _cmd_text(["git", "rev-parse", "HEAD"])
    rc_remote, remote_head = _cmd_text(["git", "rev-parse", "origin/main"])
    if rc == 0 and rc_remote == 0 and local_head == remote_head:
        ok(f"Commit local confere com origin/main: {local_head[:7]}")
    elif rc == 0 and rc_remote == 0:
        error(f"Falha no push ou repositorio divergente: local={local_head[:7]} origin/main={remote_head[:7]}")
    else:
        error("Nao foi possivel comparar HEAD local com origin/main")

    rc, counts = _cmd_text(["git", "rev-list", "--left-right", "--count", "HEAD...origin/main"])
    if rc == 0 and counts:
        parts = counts.split()
        ahead = int(parts[0]) if len(parts) > 0 and parts[0].isdigit() else 0
        behind = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
        if ahead == 0 and behind == 0:
            ok("Sem commits locais pendentes e sem atraso em relacao ao origin/main")
        if ahead > 0:
            error(f"Existem alteracoes nao enviadas ao GitHub: {ahead} commit(s) local(is)")
        if behind > 0:
            alert(f"Repositorio local esta atrasado em relacao ao origin/main: {behind} commit(s)")

    rc, status = _cmd_text(["git", "status", "--porcelain"])
    if rc == 0:
        status_lines = [line for line in status.splitlines() if line.strip()]
        tracked = [line for line in status_lines if not line.startswith("??")]
        untracked = [line for line in status_lines if line.startswith("??")]
        if tracked:
            error("Existem alteracoes rastreadas pendentes no Git: " + "; ".join(tracked[:10]))
        else:
            ok("Nao existem alteracoes rastreadas pendentes")
        if untracked:
            alert(f"Existem {len(untracked)} arquivo(s) nao rastreado(s); eles nao sao enviados pelo push automatico")
            for line in untracked[:10]:
                emit("INFO", line)
    else:
        error("Falha ao consultar git status")

    rc, commit_info = _cmd_text(["git", "log", "-1", "--date=iso-strict", "--pretty=format:%h %cd %s"])
    if rc == 0 and commit_info:
        ok(f"Ultimo commit: {commit_info}")
        date_match = re.search(r"\d{4}-\d{2}-\d{2}", commit_info)
        if date_match and date_match.group(0) != today.isoformat():
            alert(f"Ultimo commit nao e de hoje: {commit_info}")
    else:
        alert("Nao foi possivel ler o ultimo commit")

    today_log = ROOT / "logs" / f"atualizacao_{today.isoformat()}.log"
    last_success = _last_success_from_log(today_log)
    if last_success:
        minutes = (datetime.now() - last_success).total_seconds() / 60
        ok(f"Ultimo ciclo automatico com sucesso: {last_success.strftime('%Y-%m-%d %H:%M:%S')}")
        if minutes > 30:
            alert(f"Ultimo ciclo automatico ocorreu ha {minutes:.1f} minutos")
    else:
        alert("Nao encontrei ciclo automatico concluido com sucesso no log de hoje")

    for html_name, url in PUBLIC_PAGES.items():
        try:
            public_text = _fetch_public_page(url)
            if html_name in STATIC_HTML:
                missing = [marker for marker in STATIC_HTML[html_name]["markers"] if marker not in public_text]
                if missing:
                    error(f"GitHub Pages respondeu sem marcador da pagina estatica {html_name}: {', '.join(missing)}")
                else:
                    ok(f"GitHub Pages validado para pagina estatica: {html_name}")
                continue
            generated = _extract_generated_value(html_name, public_text)
            generated_dt = _parse_generated_datetime(generated)
            if generated_dt and generated_dt.date() == today:
                ok(f"GitHub Pages atualizado: {html_name} ({generated})")
            elif generated:
                alert(f"GitHub Pages pode estar desatualizado: {html_name} ({generated})")
            else:
                alert(f"GitHub Pages respondeu, mas nao identifiquei a data: {html_name}")
        except (URLError, TimeoutError, OSError) as exc:
            alert(f"Nao foi possivel verificar GitHub Pages para {html_name}: {exc}")

    if errors:
        emit("ERRO", f"Verificacao diaria finalizada com {len(errors)} erro(s) e {len(alerts)} alerta(s)")
        return 1
    if alerts:
        emit("ALERTA", f"Verificacao diaria finalizada sem erros criticos, com {len(alerts)} alerta(s)")
        return 0
    ok("Verificacao diaria finalizada sem erros ou alertas")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validacoes do atualizador Aura.")
    sub = parser.add_subparsers(dest="command", required=True)

    p_check = sub.add_parser("check-env", help="Valida .env, variaveis e conexoes.")
    p_check.add_argument("--no-connection", action="store_true")
    p_check.add_argument("--no-payload", action="store_true")
    p_check.set_defaults(func=command_check_env)

    p_html = sub.add_parser("validate-html", help="Valida HTMLs gerados no ciclo.")
    p_html.add_argument("--cycle-start", default="")
    p_html.add_argument(
        "--only",
        default="",
        help="Escopo separado por virgula: estoque,controle,reversa,acompanhamento,rastreio,gestao,gerenciamento_termico.",
    )
    p_html.set_defaults(func=command_validate_html)

    p_changed = sub.add_parser("changed-files", help="Lista arquivos que merecem git add.")
    p_changed.add_argument("--out", default="")
    p_changed.add_argument("--restore-timestamp-only", action="store_true")
    p_changed.add_argument("--publish-timestamp-only", action="store_true")
    p_changed.add_argument("--html-only", action="store_true")
    p_changed.add_argument(
        "--only",
        default="",
        help="Escopo separado por virgula: estoque,controle,reversa,acompanhamento,rastreio,gestao,gerenciamento_termico.",
    )
    p_changed.set_defaults(func=command_changed_files)

    p_daily = sub.add_parser("daily-check", help="Executa verificacao diaria dos dashboards, Git e GitHub Pages.")
    p_daily.set_defaults(func=command_daily_check)

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
