import argparse
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

import psycopg2

from env_utils import load_env_file


load_env_file()

DEFAULT_HOST = "db.lwfiljyxrlahuhjddfnp.supabase.co"
DEFAULT_DATABASE = "postgres"
DEFAULT_USER = "readonly_user"
DEFAULT_PASSWORD = os.getenv("AURA_DB_PASSWORD", "")
DEFAULT_PORT = 5432
DEFAULT_START_DATE = "2026-04-10"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Gera dashboard HTML com proporção de pedidos/loggers inseridos "
            "e séries diárias desde uma data."
        )
    )
    parser.add_argument("--start-date", default=DEFAULT_START_DATE)
    parser.add_argument("--end-date", default=os.getenv("AURA_END_DATE", ""))
    parser.add_argument("--output", default="")
    parser.add_argument("--host", default=os.getenv("AURA_DB_HOST", DEFAULT_HOST))
    parser.add_argument("--database", default=os.getenv("AURA_DB_NAME", DEFAULT_DATABASE))
    parser.add_argument("--user", default=os.getenv("AURA_DB_USER", DEFAULT_USER))
    parser.add_argument("--password", default=os.getenv("AURA_DB_PASSWORD", DEFAULT_PASSWORD))
    parser.add_argument("--port", type=int, default=int(os.getenv("AURA_DB_PORT", DEFAULT_PORT)))
    return parser.parse_args()


def get_connection(args: argparse.Namespace):
    return psycopg2.connect(
        host=args.host,
        database=args.database,
        user=args.user,
        password=args.password,
        port=args.port,
    )


def build_output_path(start_date: str, end_date: str | None, output: str) -> str:
    if output:
        return output
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    if end_date:
        return os.path.join(
            "Banco_Aura",
            f"dashboard_entregas_{start_date}_a_{end_date}_{stamp}.html",
        )
    return os.path.join(
        "Banco_Aura",
        f"dashboard_entregas_desde_{start_date}_{stamp}.html",
    )


def _load_sqlserver_conn_string() -> str:
    env_cs = os.getenv("AURA_SQLSERVER_CONN_STRING", "").strip()
    if env_cs:
        return env_cs

    workspace_root = Path(__file__).resolve().parent.parent
    odc_candidates = [
        workspace_root / "10.141.0.111_Entregas_Dashboard.odc",
        workspace_root / "GRUAG_02_Entregas_Dashboard.odc",
    ]
    for odc_path in odc_candidates:
        if not odc_path.exists():
            continue
        txt = odc_path.read_text(encoding="utf-8", errors="ignore")
        match = re.search(
            r"DRIVER=\{ODBC Driver 18 for SQL Server\};SERVER=.*?TrustServerCertificate=yes;",
            txt,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if match:
            return re.sub(r"\s+", " ", match.group(0)).strip()
    return ""


def query_delivery_launch_metrics(start_date: str, end_date: str | None = None) -> dict:
    metrics = {
        "available": False,
        "pedidos_com_entrega_e_lancamento": 0,
        "pedidos_validos": 0,
        "pedidos_negativos": 0,
        "media_horas": None,
        "media_dias": None,
        "daily": [],
        "error": "",
    }

    conn_str = _load_sqlserver_conn_string()
    if not conn_str:
        metrics["error"] = "connection_string_sqlserver_nao_encontrada"
        return metrics

    try:
        import pyodbc
    except Exception:
        metrics["error"] = "pyodbc_nao_disponivel"
        return metrics

    try:
        start_sql = datetime.strptime(start_date, "%Y-%m-%d").strftime("%Y%m%d")
    except ValueError:
        metrics["error"] = f"start_date_invalida:{start_date}"
        return metrics
    end_sql = None
    if end_date:
        try:
            end_sql = datetime.strptime(end_date, "%Y-%m-%d").strftime("%Y%m%d")
        except ValueError:
            metrics["error"] = f"end_date_invalida:{end_date}"
            return metrics

    summary_sql = """
WITH entregas AS (
    SELECT
        mov.nr_PedidoCliente AS Pedido,
        CASE
            WHEN ocn32.dt_PrazoFechamento IS NOT NULL
             AND TRY_CAST(LEFT(ocn32.hr_PrazoFechamento, 5) AS time(0)) IS NOT NULL
            THEN DATETIMEFROMPARTS(
                YEAR(ocn32.dt_PrazoFechamento),
                MONTH(ocn32.dt_PrazoFechamento),
                DAY(ocn32.dt_PrazoFechamento),
                DATEPART(hour, TRY_CAST(LEFT(ocn32.hr_PrazoFechamento, 5) AS time(0))),
                DATEPART(minute, TRY_CAST(LEFT(ocn32.hr_PrazoFechamento, 5) AS time(0))),
                0, 0
            )
            ELSE NULL
        END AS Data_Entrega,
        CASE
            WHEN ocn32.dt_Abertura IS NOT NULL
             AND TRY_CAST(LEFT(ocn32.hr_Abertura, 5) AS time(0)) IS NOT NULL
            THEN DATETIMEFROMPARTS(
                YEAR(ocn32.dt_Abertura),
                MONTH(ocn32.dt_Abertura),
                DAY(ocn32.dt_Abertura),
                DATEPART(hour, TRY_CAST(LEFT(ocn32.hr_Abertura, 5) AS time(0))),
                DATEPART(minute, TRY_CAST(LEFT(ocn32.hr_Abertura, 5) AS time(0))),
                0, 0
            )
            ELSE NULL
        END AS Data_Lancamento,
        ROW_NUMBER() OVER (
            PARTITION BY mov.nr_PedidoCliente, mov.id_Movimento
            ORDER BY ocn32.dt_Abertura DESC, ocn32.hr_Abertura DESC, ocn32.id_OcorrenciaNota DESC
        ) AS rn
    FROM dbo.tbdMovimento mov
    INNER JOIN dbo.tbdOcorrenciaNota ocn32
        ON ocn32.id_Movimento = mov.id_Movimento
       AND ocn32.id_Ocorrencia = 32
    WHERE ocn32.dt_PrazoFechamento >= CONVERT(date, ?, 112)
      AND (? IS NULL OR ocn32.dt_PrazoFechamento <= CONVERT(date, ?, 112))
),
base AS (
    SELECT
        Pedido,
        Data_Entrega,
        Data_Lancamento,
        DATEDIFF_BIG(MINUTE, Data_Entrega, Data_Lancamento) AS Minutos_Lancamento
    FROM entregas
    WHERE rn = 1
      AND Data_Entrega IS NOT NULL
      AND Data_Lancamento IS NOT NULL
),
validos AS (
    SELECT * FROM base WHERE Minutos_Lancamento >= 0
)
SELECT
    (SELECT COUNT(*) FROM base) AS pedidos_com_entrega_e_lancamento,
    (SELECT COUNT(*) FROM validos) AS pedidos_validos,
    (SELECT COUNT(*) FROM base WHERE Minutos_Lancamento < 0) AS pedidos_negativos,
    CAST(AVG(CAST(Minutos_Lancamento AS float))/60.0 AS decimal(18,2)) AS media_horas,
    CAST(AVG(CAST(Minutos_Lancamento AS float))/1440.0 AS decimal(18,2)) AS media_dias
FROM validos;
"""

    daily_sql = """
WITH entregas AS (
    SELECT
        mov.nr_PedidoCliente AS Pedido,
        CASE
            WHEN ocn32.dt_PrazoFechamento IS NOT NULL
             AND TRY_CAST(LEFT(ocn32.hr_PrazoFechamento, 5) AS time(0)) IS NOT NULL
            THEN DATETIMEFROMPARTS(
                YEAR(ocn32.dt_PrazoFechamento),
                MONTH(ocn32.dt_PrazoFechamento),
                DAY(ocn32.dt_PrazoFechamento),
                DATEPART(hour, TRY_CAST(LEFT(ocn32.hr_PrazoFechamento, 5) AS time(0))),
                DATEPART(minute, TRY_CAST(LEFT(ocn32.hr_PrazoFechamento, 5) AS time(0))),
                0, 0
            )
            ELSE NULL
        END AS Data_Entrega,
        CASE
            WHEN ocn32.dt_Abertura IS NOT NULL
             AND TRY_CAST(LEFT(ocn32.hr_Abertura, 5) AS time(0)) IS NOT NULL
            THEN DATETIMEFROMPARTS(
                YEAR(ocn32.dt_Abertura),
                MONTH(ocn32.dt_Abertura),
                DAY(ocn32.dt_Abertura),
                DATEPART(hour, TRY_CAST(LEFT(ocn32.hr_Abertura, 5) AS time(0))),
                DATEPART(minute, TRY_CAST(LEFT(ocn32.hr_Abertura, 5) AS time(0))),
                0, 0
            )
            ELSE NULL
        END AS Data_Lancamento,
        ROW_NUMBER() OVER (
            PARTITION BY mov.nr_PedidoCliente, mov.id_Movimento
            ORDER BY ocn32.dt_Abertura DESC, ocn32.hr_Abertura DESC, ocn32.id_OcorrenciaNota DESC
        ) AS rn
    FROM dbo.tbdMovimento mov
    INNER JOIN dbo.tbdOcorrenciaNota ocn32
        ON ocn32.id_Movimento = mov.id_Movimento
       AND ocn32.id_Ocorrencia = 32
    WHERE ocn32.dt_PrazoFechamento >= CONVERT(date, ?, 112)
      AND (? IS NULL OR ocn32.dt_PrazoFechamento <= CONVERT(date, ?, 112))
),
base AS (
    SELECT
        Data_Entrega,
        DATEDIFF_BIG(MINUTE, Data_Entrega, Data_Lancamento) AS Minutos_Lancamento
    FROM entregas
    WHERE rn = 1
      AND Data_Entrega IS NOT NULL
      AND Data_Lancamento IS NOT NULL
      AND DATEDIFF_BIG(MINUTE, Data_Entrega, Data_Lancamento) >= 0
)
SELECT
    CAST(Data_Entrega AS date) AS dia_entrega,
    COUNT(*) AS pedidos_validos,
    CAST(AVG(CAST(Minutos_Lancamento AS float))/60.0 AS decimal(18,2)) AS media_horas
FROM base
GROUP BY CAST(Data_Entrega AS date)
ORDER BY dia_entrega;
"""

    try:
        with pyodbc.connect(conn_str, timeout=30) as conn:
            cur = conn.cursor()
            cur.execute(summary_sql, (start_sql, end_sql, end_sql))
            s_row = cur.fetchone()
            cur.execute(daily_sql, (start_sql, end_sql, end_sql))
            d_rows = cur.fetchall()
    except Exception as exc:
        metrics["error"] = str(exc)
        return metrics

    if s_row:
        metrics["pedidos_com_entrega_e_lancamento"] = int(s_row[0] or 0)
        metrics["pedidos_validos"] = int(s_row[1] or 0)
        metrics["pedidos_negativos"] = int(s_row[2] or 0)
        metrics["media_horas"] = float(s_row[3]) if s_row[3] is not None else None
        metrics["media_dias"] = float(s_row[4]) if s_row[4] is not None else None

    daily = []
    for dia, pedidos_validos, media_horas in d_rows:
        dia_iso = dia.isoformat() if hasattr(dia, "isoformat") else str(dia)
        daily.append(
            {
                "dia": dia_iso,
                "pedidos_validos": int(pedidos_validos or 0),
                "media_horas": float(media_horas) if media_horas is not None else 0.0,
            }
        )
    metrics["daily"] = daily
    metrics["available"] = metrics["media_horas"] is not None
    return metrics


def query_data(conn, start_date: str, end_date: str | None = None):
    sql = """
WITH so AS (
  SELECT
    so.id,
    so.order_code,
    so.delivery_date,
    (so.delivery_date AT TIME ZONE 'America/Sao_Paulo')::date AS dia
  FROM public.sync_orders so
  WHERE (so.delivery_date AT TIME ZONE 'America/Sao_Paulo')::date >= %s::date
    AND (%s::date IS NULL OR (so.delivery_date AT TIME ZONE 'America/Sao_Paulo')::date <= %s::date)
),
so_orders AS (
  SELECT DISTINCT
    so.order_code,
    so.delivery_date,
    so.dia
  FROM so
  WHERE so.order_code IS NOT NULL AND btrim(so.order_code) <> ''
),
order_keys_all AS (
  SELECT DISTINCT
    regexp_replace(
      upper(coalesce(o.order_code, '') || coalesce(oi.item_label, '')),
      '[^A-Z0-9]',
      '',
      'g'
    ) AS k
  FROM public.orders o
  JOIN public.order_items oi ON oi.fk_order = o.id
),
daily_orders AS (
  SELECT
    so_o.dia,
    count(DISTINCT so_o.order_code) AS pedidos_entregues,
    count(DISTINCT CASE WHEN o.id IS NOT NULL THEN so_o.order_code END) AS pedidos_inseridos
  FROM so_orders so_o
  LEFT JOIN public.orders o ON o.order_code = so_o.order_code
  GROUP BY so_o.dia
),
daily_latency AS (
  SELECT
    so_o.dia,
    avg(extract(epoch FROM (o.created_at - so_o.delivery_date)) / 3600.0)
      FILTER (WHERE o.created_at IS NOT NULL AND so_o.delivery_date IS NOT NULL) AS avg_horas_pedidos
  FROM so_orders so_o
  JOIN public.orders o ON o.order_code = so_o.order_code
  GROUP BY so_o.dia
),
sensor_sync_items AS (
  SELECT
    (coalesce(si.delivery_date, so.delivery_date) AT TIME ZONE 'America/Sao_Paulo')::date AS dia,
    coalesce(si.delivery_date, so.delivery_date) AS delivery_date_item,
    CASE
      WHEN upper(coalesce(si.device_serial, '')) LIKE 'TA%%' THEN 'ARES'
      WHEN upper(coalesce(si.device_serial, '')) LIKE 'A%%' THEN 'ARES'
      WHEN upper(coalesce(si.device_serial, '')) LIKE 'S%%' THEN 'SYOS'
      WHEN upper(coalesce(si.device_serial, '')) LIKE 'V%%' OR upper(coalesce(si.device_serial, '')) LIKE 'B%%' THEN 'Shield'
      ELSE 'Sensor web'
    END AS sensor,
    regexp_replace(
      upper(coalesce(so.order_code, '') || coalesce(si.device_serial, '')),
      '[^A-Z0-9]',
      '',
      'g'
    ) AS k
  FROM so
  JOIN public.sync_items si ON si.sync_order_id = so.id
),
aura_item_created AS (
  SELECT
    regexp_replace(
      upper(coalesce(o.order_code, '') || coalesce(oi.item_label, '')),
      '[^A-Z0-9]',
      '',
      'g'
    ) AS k,
    min(o.created_at) AS order_created_at
  FROM public.orders o
  JOIN public.order_items oi ON oi.fk_order = o.id
  WHERE o.created_at IS NOT NULL
  GROUP BY 1
),
daily_latency_sensor AS (
  SELECT
    ssi.dia,
    avg(extract(epoch FROM (aic.order_created_at - ssi.delivery_date_item)) / 3600.0)
      FILTER (
        WHERE ssi.sensor = 'ARES'
          AND aic.order_created_at IS NOT NULL
          AND ssi.delivery_date_item IS NOT NULL
      ) AS avg_horas_itens_ares,
    avg(extract(epoch FROM (aic.order_created_at - ssi.delivery_date_item)) / 3600.0)
      FILTER (
        WHERE ssi.sensor = 'SYOS'
          AND aic.order_created_at IS NOT NULL
          AND ssi.delivery_date_item IS NOT NULL
      ) AS avg_horas_itens_syos,
    avg(extract(epoch FROM (aic.order_created_at - ssi.delivery_date_item)) / 3600.0)
      FILTER (
        WHERE ssi.sensor = 'Shield'
          AND aic.order_created_at IS NOT NULL
          AND ssi.delivery_date_item IS NOT NULL
      ) AS avg_horas_itens_shield,
    avg(extract(epoch FROM (aic.order_created_at - ssi.delivery_date_item)) / 3600.0)
      FILTER (
        WHERE ssi.sensor = 'Sensor web'
          AND aic.order_created_at IS NOT NULL
          AND ssi.delivery_date_item IS NOT NULL
      ) AS avg_horas_itens_sensor_web
  FROM sensor_sync_items ssi
  JOIN aura_item_created aic ON aic.k = ssi.k
  WHERE ssi.k <> ''
  GROUP BY ssi.dia
),
daily_loggers AS (
  SELECT
    so.dia,
    count(*) FILTER (WHERE k.k <> '') AS loggers_entregues,
    count(*) FILTER (WHERE k.k <> '' AND ok.k IS NOT NULL) AS loggers_inseridos
  FROM so
  JOIN public.sync_items si ON si.sync_order_id = so.id
  CROSS JOIN LATERAL (
    SELECT regexp_replace(
      upper(coalesce(so.order_code, '') || coalesce(si.device_serial, '')),
      '[^A-Z0-9]',
      '',
      'g'
    ) AS k
  ) k
  LEFT JOIN order_keys_all ok ON ok.k = k.k
  GROUP BY so.dia
),
daily AS (
  SELECT
    d.dia,
    coalesce(o.pedidos_entregues, 0) AS pedidos_entregues,
    coalesce(o.pedidos_inseridos, 0) AS pedidos_inseridos,
    coalesce(l.loggers_entregues, 0) AS loggers_entregues,
    coalesce(l.loggers_inseridos, 0) AS loggers_inseridos,
    lat.avg_horas_pedidos AS avg_horas_pedidos,
    lat_s.avg_horas_itens_ares AS avg_horas_itens_ares,
    lat_s.avg_horas_itens_syos AS avg_horas_itens_syos,
    lat_s.avg_horas_itens_shield AS avg_horas_itens_shield,
    lat_s.avg_horas_itens_sensor_web AS avg_horas_itens_sensor_web
  FROM (
    SELECT dia FROM daily_orders
    UNION
    SELECT dia FROM daily_loggers
  ) d
  LEFT JOIN daily_orders o ON o.dia = d.dia
  LEFT JOIN daily_loggers l ON l.dia = d.dia
  LEFT JOIN daily_latency lat ON lat.dia = d.dia
  LEFT JOIN daily_latency_sensor lat_s ON lat_s.dia = d.dia
),
totals AS (
  SELECT
    sum(pedidos_entregues) AS pedidos_entregues_total,
    sum(pedidos_inseridos) AS pedidos_inseridos_total,
    sum(loggers_entregues) AS loggers_entregues_total,
    sum(loggers_inseridos) AS loggers_inseridos_total
  FROM daily
)
SELECT
  to_char(dia, 'YYYY-MM-DD') AS dia,
  pedidos_entregues,
  pedidos_inseridos,
  loggers_entregues,
  loggers_inseridos,
  avg_horas_pedidos,
  avg_horas_itens_ares,
  avg_horas_itens_syos,
  avg_horas_itens_shield,
  avg_horas_itens_sensor_web,
  (SELECT pedidos_entregues_total FROM totals) AS pedidos_entregues_total,
  (SELECT pedidos_inseridos_total FROM totals) AS pedidos_inseridos_total,
  (SELECT loggers_entregues_total FROM totals) AS loggers_entregues_total,
  (SELECT loggers_inseridos_total FROM totals) AS loggers_inseridos_total
FROM daily
ORDER BY dia;
"""
    sensor_sql = """
WITH so AS (
  SELECT
    so.id,
    so.order_code
  FROM public.sync_orders so
  WHERE (so.delivery_date AT TIME ZONE 'America/Sao_Paulo')::date >= %s::date
    AND (%s::date IS NULL OR (so.delivery_date AT TIME ZONE 'America/Sao_Paulo')::date <= %s::date)
),
order_keys_all AS (
  SELECT DISTINCT
    regexp_replace(
      upper(coalesce(o.order_code, '') || coalesce(oi.item_label, '')),
      '[^A-Z0-9]',
      '',
      'g'
    ) AS k
  FROM public.orders o
  JOIN public.order_items oi ON oi.fk_order = o.id
),
base AS (
  SELECT
    CASE
      WHEN upper(coalesce(si.device_serial,'')) LIKE 'TA%%' THEN 'ARES'
      WHEN upper(coalesce(si.device_serial,'')) LIKE 'A%%' THEN 'ARES'
      WHEN upper(coalesce(si.device_serial,'')) LIKE 'S%%' THEN 'SYOS'
      WHEN upper(coalesce(si.device_serial,'')) LIKE 'V%%' OR upper(coalesce(si.device_serial,'')) LIKE 'B%%' THEN 'Shield'
      ELSE 'Sensor web'
    END AS sensor,
    regexp_replace(
      upper(coalesce(so.order_code, '') || coalesce(si.device_serial, '')),
      '[^A-Z0-9]',
      '',
      'g'
    ) AS k
  FROM so
  JOIN public.sync_items si ON si.sync_order_id = so.id
),
pend AS (
  SELECT b.sensor
  FROM base b
  LEFT JOIN order_keys_all ok ON ok.k = b.k
  WHERE b.k <> '' AND ok.k IS NULL
)
SELECT sensor, count(*) AS pendentes
FROM pend
GROUP BY sensor;
"""
    sensor_daily_sql = """
WITH so AS (
  SELECT
    so.id,
    so.order_code,
    (so.delivery_date AT TIME ZONE 'America/Sao_Paulo')::date AS dia
  FROM public.sync_orders so
  WHERE (so.delivery_date AT TIME ZONE 'America/Sao_Paulo')::date >= %s::date
    AND (%s::date IS NULL OR (so.delivery_date AT TIME ZONE 'America/Sao_Paulo')::date <= %s::date)
),
order_keys_all AS (
  SELECT DISTINCT
    regexp_replace(
      upper(coalesce(o.order_code, '') || coalesce(oi.item_label, '')),
      '[^A-Z0-9]',
      '',
      'g'
    ) AS k
  FROM public.orders o
  JOIN public.order_items oi ON oi.fk_order = o.id
),
base AS (
  SELECT
    so.dia,
    CASE
      WHEN upper(coalesce(si.device_serial,'')) LIKE 'TA%%' THEN 'ARES'
      WHEN upper(coalesce(si.device_serial,'')) LIKE 'A%%' THEN 'ARES'
      WHEN upper(coalesce(si.device_serial,'')) LIKE 'S%%' THEN 'SYOS'
      WHEN upper(coalesce(si.device_serial,'')) LIKE 'V%%' OR upper(coalesce(si.device_serial,'')) LIKE 'B%%' THEN 'Shield'
      ELSE 'Sensor web'
    END AS sensor,
    CASE
      WHEN coalesce(
        replace(
          substring(
            coalesce(
              si.metadata #>> '{product_info,thermal_type}',
              si.metadata ->> 'thermal_type',
              ''
            ) from '(-?[0-9]+(?:[.,][0-9]+)?)'
          ),
          ',',
          '.'
        )::numeric,
        si.expected_temp_min
      ) >= 0 THEN 'refrigerado'
      WHEN coalesce(
        replace(
          substring(
            coalesce(
              si.metadata #>> '{product_info,thermal_type}',
              si.metadata ->> 'thermal_type',
              ''
            ) from '(-?[0-9]+(?:[.,][0-9]+)?)'
          ),
          ',',
          '.'
        )::numeric,
        si.expected_temp_min
      ) < 0 THEN 'congelado'
      ELSE 'nao_classificado'
    END AS thermal_class,
    regexp_replace(
      upper(coalesce(so.order_code, '') || coalesce(si.device_serial, '')),
      '[^A-Z0-9]',
      '',
      'g'
    ) AS k
  FROM so
  JOIN public.sync_items si ON si.sync_order_id = so.id
),
pend AS (
  SELECT b.dia, b.sensor
  FROM base b
  LEFT JOIN order_keys_all ok ON ok.k = b.k
  WHERE b.k <> '' AND ok.k IS NULL
)
SELECT to_char(dia, 'YYYY-MM-DD') AS dia, sensor, count(*) AS pendentes
FROM pend
GROUP BY dia, sensor
ORDER BY dia, sensor;
"""
    sensor_daily_stats_sql = """
WITH so AS (
  SELECT
    so.id,
    so.order_code,
    (so.delivery_date AT TIME ZONE 'America/Sao_Paulo')::date AS dia
  FROM public.sync_orders so
  WHERE (so.delivery_date AT TIME ZONE 'America/Sao_Paulo')::date >= %s::date
    AND (%s::date IS NULL OR (so.delivery_date AT TIME ZONE 'America/Sao_Paulo')::date <= %s::date)
),
order_keys_all AS (
  SELECT DISTINCT
    regexp_replace(
      upper(coalesce(o.order_code, '') || coalesce(oi.item_label, '')),
      '[^A-Z0-9]',
      '',
      'g'
    ) AS k
  FROM public.orders o
  JOIN public.order_items oi ON oi.fk_order = o.id
),
base AS (
  SELECT
    so.dia,
    CASE
      WHEN upper(coalesce(si.device_serial,'')) LIKE 'TA%%' THEN 'ARES'
      WHEN upper(coalesce(si.device_serial,'')) LIKE 'A%%' THEN 'ARES'
      WHEN upper(coalesce(si.device_serial,'')) LIKE 'S%%' THEN 'SYOS'
      WHEN upper(coalesce(si.device_serial,'')) LIKE 'V%%' OR upper(coalesce(si.device_serial,'')) LIKE 'B%%' THEN 'Shield'
      ELSE 'Sensor web'
    END AS sensor,
    CASE
      WHEN coalesce(
        replace(
          substring(
            coalesce(
              si.metadata #>> '{product_info,thermal_type}',
              si.metadata ->> 'thermal_type',
              ''
            ) from '(-?[0-9]+(?:[.,][0-9]+)?)'
          ),
          ',',
          '.'
        )::numeric,
        si.expected_temp_min
      ) >= 0 THEN 'refrigerado'
      WHEN coalesce(
        replace(
          substring(
            coalesce(
              si.metadata #>> '{product_info,thermal_type}',
              si.metadata ->> 'thermal_type',
              ''
            ) from '(-?[0-9]+(?:[.,][0-9]+)?)'
          ),
          ',',
          '.'
        )::numeric,
        si.expected_temp_min
      ) < 0 THEN 'congelado'
      ELSE 'nao_classificado'
    END AS thermal_class,
    regexp_replace(
      upper(coalesce(so.order_code, '') || coalesce(si.device_serial, '')),
      '[^A-Z0-9]',
      '',
      'g'
    ) AS k
  FROM so
  JOIN public.sync_items si ON si.sync_order_id = so.id
)
SELECT
  to_char(b.dia, 'YYYY-MM-DD') AS dia,
  b.sensor,
  b.thermal_class,
  count(*) FILTER (WHERE b.k <> '') AS loggers_entregues,
  count(*) FILTER (WHERE b.k <> '' AND ok.k IS NOT NULL) AS loggers_inseridos,
  count(*) FILTER (WHERE b.k <> '' AND ok.k IS NULL) AS loggers_pendentes
FROM base b
LEFT JOIN order_keys_all ok ON ok.k = b.k
GROUP BY b.dia, b.sensor, b.thermal_class
ORDER BY b.dia, b.sensor, b.thermal_class;
"""
    order_daily_stats_sql = """
WITH so AS (
  SELECT
    so.id,
    so.order_code,
    (so.delivery_date AT TIME ZONE 'America/Sao_Paulo')::date AS dia
  FROM public.sync_orders so
  WHERE (so.delivery_date AT TIME ZONE 'America/Sao_Paulo')::date >= %s::date
    AND (%s::date IS NULL OR (so.delivery_date AT TIME ZONE 'America/Sao_Paulo')::date <= %s::date)
    AND so.order_code IS NOT NULL
    AND btrim(so.order_code) <> ''
),
base AS (
  SELECT
    so.dia,
    so.order_code,
    CASE
      WHEN upper(coalesce(si.device_serial,'')) LIKE 'TA%%' THEN 'ARES'
      WHEN upper(coalesce(si.device_serial,'')) LIKE 'A%%' THEN 'ARES'
      WHEN upper(coalesce(si.device_serial,'')) LIKE 'S%%' THEN 'SYOS'
      WHEN upper(coalesce(si.device_serial,'')) LIKE 'V%%' OR upper(coalesce(si.device_serial,'')) LIKE 'B%%' THEN 'Shield'
      ELSE 'Sensor web'
    END AS sensor,
    CASE
      WHEN coalesce(
        replace(
          substring(
            coalesce(
              si.metadata #>> '{product_info,thermal_type}',
              si.metadata ->> 'thermal_type',
              ''
            ) from '(-?[0-9]+(?:[.,][0-9]+)?)'
          ),
          ',',
          '.'
        )::numeric,
        si.expected_temp_min
      ) >= 0 THEN 'refrigerado'
      WHEN coalesce(
        replace(
          substring(
            coalesce(
              si.metadata #>> '{product_info,thermal_type}',
              si.metadata ->> 'thermal_type',
              ''
            ) from '(-?[0-9]+(?:[.,][0-9]+)?)'
          ),
          ',',
          '.'
        )::numeric,
        si.expected_temp_min
      ) < 0 THEN 'congelado'
      ELSE 'nao_classificado'
    END AS thermal_class
  FROM so
  JOIN public.sync_items si ON si.sync_order_id = so.id
),
agg AS (
  SELECT
    b.dia,
    b.sensor,
    b.thermal_class,
    count(DISTINCT b.order_code) AS pedidos_entregues,
    count(DISTINCT CASE WHEN o.id IS NOT NULL THEN b.order_code END) AS pedidos_inseridos
  FROM base b
  LEFT JOIN public.orders o ON o.order_code = b.order_code
  GROUP BY b.dia, b.sensor, b.thermal_class
)
SELECT
  to_char(dia, 'YYYY-MM-DD') AS dia,
  sensor,
  thermal_class,
  pedidos_entregues,
  pedidos_inseridos
FROM agg
ORDER BY dia, sensor, thermal_class;
"""
    latency_sql = """
WITH so_base AS (
  SELECT
    so.order_code,
    max(so.delivery_date) AS delivery_date
  FROM public.sync_orders so
  WHERE (so.delivery_date AT TIME ZONE 'America/Sao_Paulo')::date >= %s::date
    AND (%s::date IS NULL OR (so.delivery_date AT TIME ZONE 'America/Sao_Paulo')::date <= %s::date)
    AND so.order_code IS NOT NULL
    AND btrim(so.order_code) <> ''
  GROUP BY so.order_code
),
matched AS (
  SELECT
    o.order_code,
    o.created_at AS aura_created_at,
    s.delivery_date,
    extract(epoch FROM (o.created_at - s.delivery_date)) / 3600.0 AS hrs_after_delivery
  FROM public.orders o
  JOIN so_base s ON s.order_code = o.order_code
  WHERE o.created_at IS NOT NULL
    AND s.delivery_date IS NOT NULL
)
SELECT
  count(*) AS pedidos_comparados,
  count(*) FILTER (WHERE hrs_after_delivery >= 0) AS pedidos_validos,
  avg(hrs_after_delivery) FILTER (WHERE hrs_after_delivery >= 0) AS avg_horas,
  percentile_cont(0.5) WITHIN GROUP (ORDER BY hrs_after_delivery) FILTER (WHERE hrs_after_delivery >= 0) AS p50_horas,
  count(*) FILTER (WHERE hrs_after_delivery < 0) AS pedidos_negativos
FROM matched;
"""
    with conn.cursor() as cur:
        params = (start_date, end_date, end_date)
        cur.execute(sql, params)
        rows = cur.fetchall()
        cur.execute(sensor_sql, params)
        sensor_rows = cur.fetchall()
        cur.execute(sensor_daily_sql, params)
        sensor_daily_rows = cur.fetchall()
        cur.execute(sensor_daily_stats_sql, params)
        sensor_daily_stats_rows = cur.fetchall()
        cur.execute(order_daily_stats_sql, params)
        order_daily_stats_rows = cur.fetchall()
        cur.execute(latency_sql, params)
        latency_row = cur.fetchone()
    delivery_launch = query_delivery_launch_metrics(start_date, end_date)
    return (
        rows,
        sensor_rows,
        sensor_daily_rows,
        sensor_daily_stats_rows,
        order_daily_stats_rows,
        latency_row,
        delivery_launch,
    )


def build_payload(
    rows,
    start_date: str,
    end_date: str | None,
    sensor_rows,
    sensor_daily_rows,
    sensor_daily_stats_rows,
    order_daily_stats_rows,
    latency_row,
    delivery_launch: dict,
) -> dict:
    daily = []
    totals = {
        "pedidos_entregues_total": 0,
        "pedidos_inseridos_total": 0,
        "loggers_entregues_total": 0,
        "loggers_inseridos_total": 0,
    }
    for row in rows:
        (
            dia,
            p_ent,
            p_ins,
            l_ent,
            l_ins,
            avg_h_ped,
            avg_h_it_ares,
            avg_h_it_syos,
            avg_h_it_shield,
            avg_h_it_web,
            t_pe,
            t_pi,
            t_le,
            t_li,
        ) = row
        daily.append(
            {
                "dia": dia,
                "pedidos_entregues": int(p_ent or 0),
                "pedidos_inseridos": int(p_ins or 0),
                "loggers_entregues": int(l_ent or 0),
                "loggers_inseridos": int(l_ins or 0),
                "avg_horas_pedidos": float(avg_h_ped) if avg_h_ped is not None else None,
                "avg_horas_itens_ares": float(avg_h_it_ares) if avg_h_it_ares is not None else None,
                "avg_horas_itens_syos": float(avg_h_it_syos) if avg_h_it_syos is not None else None,
                "avg_horas_itens_shield": float(avg_h_it_shield) if avg_h_it_shield is not None else None,
                "avg_horas_itens_sensor_web": float(avg_h_it_web) if avg_h_it_web is not None else None,
            }
        )
        totals = {
            "pedidos_entregues_total": int(t_pe or 0),
            "pedidos_inseridos_total": int(t_pi or 0),
            "loggers_entregues_total": int(t_le or 0),
            "loggers_inseridos_total": int(t_li or 0),
        }

    p_total = totals["pedidos_entregues_total"]
    l_total = totals["loggers_entregues_total"]
    totals["pedidos_pct"] = (totals["pedidos_inseridos_total"] / p_total * 100) if p_total else 0
    totals["loggers_pct"] = (totals["loggers_inseridos_total"] / l_total * 100) if l_total else 0
    totals["pedidos_pendentes_total"] = p_total - totals["pedidos_inseridos_total"]
    totals["loggers_pendentes_total"] = l_total - totals["loggers_inseridos_total"]

    sensor_pending = {"ARES": 0, "SYOS": 0, "Shield": 0, "Sensor web": 0}
    for sensor, pend in sensor_rows:
        sensor_pending[str(sensor)] = int(pend or 0)
    sensor_pending_daily = []
    for dia, sensor, pend in sensor_daily_rows:
        sensor_pending_daily.append(
            {
                "dia": str(dia),
                "sensor": str(sensor),
                "pendentes": int(pend or 0),
            }
        )
    sensor_daily_stats = []
    for dia, sensor, thermal_class, l_ent, l_ins, l_pen in sensor_daily_stats_rows:
        sensor_daily_stats.append(
            {
                "dia": str(dia),
                "sensor": str(sensor),
                "thermal_class": str(thermal_class),
                "loggers_entregues": int(l_ent or 0),
                "loggers_inseridos": int(l_ins or 0),
                "loggers_pendentes": int(l_pen or 0),
            }
        )
    order_daily_stats = []
    for dia, sensor, thermal_class, p_ent, p_ins in order_daily_stats_rows:
        order_daily_stats.append(
            {
                "dia": str(dia),
                "sensor": str(sensor),
                "thermal_class": str(thermal_class),
                "pedidos_entregues": int(p_ent or 0),
                "pedidos_inseridos": int(p_ins or 0),
            }
        )

    pedidos_comparados, pedidos_validos, avg_horas, p50_horas, pedidos_negativos = latency_row
    avg_h = float(avg_horas or 0.0)
    p50_h = float(p50_horas or 0.0)
    latency = {
        "pedidos_comparados": int(pedidos_comparados or 0),
        "pedidos_validos": int(pedidos_validos or 0),
        "pedidos_negativos": int(pedidos_negativos or 0),
        "avg_horas": avg_h,
        "avg_dias": avg_h / 24.0,
        "p50_horas": p50_h,
        "p50_dias": p50_h / 24.0,
    }

    return {
        "start_date": start_date,
        "end_date": end_date,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "totals": totals,
        "daily": daily,
        "sensor_pending": sensor_pending,
        "sensor_pending_daily": sensor_pending_daily,
        "sensor_daily_stats": sensor_daily_stats,
        "order_daily_stats": order_daily_stats,
        "latency": latency,
        "delivery_launch": delivery_launch,
    }


def render_html(payload: dict) -> str:
    data_json = json.dumps(payload, ensure_ascii=False)
    return f"""<!doctype html>
<html lang="pt-BR">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Painel Operacional Aura - Entregas e Inserções</title>
  <style>
    :root {{
      --bg: #eef3f8;
      --bg-soft: #f7faff;
      --card: #ffffff;
      --ink: #122033;
      --muted: #637184;
      --muted-2: #8a98a8;
      --line: #d8e2ec;
      --line-soft: #eaf0f6;
      --primary: #185c9d;
      --primary-2: #2476c7;
      --primary-soft: #e8f2ff;
      --ok: #1f9d7a;
      --warn: #ce4d4d;
      --a: #2563eb;
      --b: #0ea5e9;
      --shadow-soft: 0 10px 28px rgba(23, 35, 50, .07);
      --shadow-card: 0 8px 20px rgba(23, 35, 50, .06);
      --radius: 12px;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: "Segoe UI", Tahoma, sans-serif;
      color: var(--ink);
      background: linear-gradient(180deg, #e8f0f8 0%, #f8fafc 36%, #f4f7fb 100%);
    }}
    .wrap {{
      max-width: 1280px;
      margin: 0 auto;
      padding: 14px 14px 22px;
    }}
    .header {{
      background: linear-gradient(135deg, #0b2745 0%, #14528d 58%, #1e78bd 100%);
      color: #fff;
      border: 1px solid rgba(255, 255, 255, .20);
      border-radius: 16px;
      padding: 18px;
      box-shadow: 0 14px 32px rgba(13, 36, 66, .22);
      overflow: hidden;
    }}
    .header-top {{
      display: flex;
      justify-content: space-between;
      gap: 18px;
      align-items: center;
    }}
    .brand-row {{
      display: flex;
      align-items: center;
      gap: 14px;
      min-width: 0;
    }}
    .brand-mark {{
      width: 46px;
      height: 46px;
      border-radius: 12px;
      display: grid;
      place-items: center;
      flex: 0 0 auto;
      background: rgba(255, 255, 255, .16);
      border: 1px solid rgba(255, 255, 255, .24);
      color: #fff;
      font-weight: 900;
      font-size: 1.05rem;
      box-shadow: inset 0 1px 0 rgba(255, 255, 255, .20);
    }}
    .header-copy {{ min-width: 0; }}
    .eyebrow {{
      color: #bfe1ff;
      font-size: .72rem;
      font-weight: 800;
      text-transform: uppercase;
      margin-bottom: 3px;
    }}
    .header h1 {{
      margin: 0 0 5px;
      font-size: clamp(1.28rem, 2vw, 1.85rem);
      line-height: 1.08;
      font-weight: 850;
    }}
    .sub {{
      color: #d9ecff;
      font-size: .88rem;
      line-height: 1.35;
      max-width: 760px;
    }}
    .header-badges {{
      display: flex;
      flex-wrap: wrap;
      justify-content: flex-end;
      gap: 8px;
      max-width: 520px;
    }}
    .header-badge {{
      min-width: 142px;
      border: 1px solid rgba(255, 255, 255, .20);
      border-radius: 12px;
      padding: 8px 10px;
      background: rgba(255, 255, 255, .12);
      box-shadow: inset 0 1px 0 rgba(255, 255, 255, .16);
    }}
    .badge-label {{
      color: #bfe1ff;
      display: block;
      font-size: .68rem;
      font-weight: 800;
      text-transform: uppercase;
      margin-bottom: 2px;
    }}
    .badge-value {{
      display: block;
      color: #fff;
      font-size: .8rem;
      font-weight: 800;
      line-height: 1.22;
    }}
    .section-head {{
      display: flex;
      justify-content: space-between;
      gap: 12px;
      align-items: flex-start;
      margin-bottom: 10px;
    }}
    .section-title {{
      margin: 0;
      color: #172033;
      font-size: .96rem;
      font-weight: 850;
    }}
    .section-copy {{
      margin: 3px 0 0;
      color: var(--muted);
      font-size: .78rem;
      line-height: 1.35;
    }}
    .filters {{
      display: grid;
      grid-template-columns: repeat(7, minmax(0, 1fr));
      gap: 8px;
      align-items: end;
    }}
    .filter-panel {{
      margin-top: 12px;
    }}
    .fgroup {{
      display: flex;
      flex-direction: column;
      gap: 4px;
    }}
    .flabel {{
      color: var(--muted);
      font-size: .74rem;
      font-weight: 750;
    }}
    .finput {{
      width: 100%;
      border: 1px solid var(--line);
      border-radius: 9px;
      padding: 8px 9px;
      font-size: .82rem;
      color: var(--ink);
      background: #fff;
      min-height: 36px;
      outline: none;
      transition: border-color .14s ease, box-shadow .14s ease, background .14s ease;
    }}
    .finput:focus {{
      border-color: #7fb3e5;
      box-shadow: 0 0 0 3px rgba(36, 118, 199, .14);
      background: #fbfdff;
    }}
    .fbtn {{
      border: 1px solid #1b5f9f;
      background: linear-gradient(180deg, #257bcc, #1b66ac);
      color: #fff;
      border-radius: 9px;
      padding: 8px 10px;
      min-height: 36px;
      font-size: .82rem;
      cursor: pointer;
      font-weight: 800;
      box-shadow: 0 7px 16px rgba(27, 102, 172, .16);
      transition: transform .12s ease, box-shadow .12s ease, border-color .12s ease, background .12s ease;
    }}
    .fbtn:hover {{
      border-color: #134f86;
      box-shadow: 0 9px 18px rgba(27, 102, 172, .22);
      transform: translateY(-1px);
    }}
    .fbtn:active {{
      transform: translateY(0);
      box-shadow: 0 4px 10px rgba(27, 102, 172, .18);
    }}
    .fbtn.secondary {{
      border-color: #9fb3c7;
      background: #f3f7fb;
      color: #33485c;
      box-shadow: 0 5px 12px rgba(23, 35, 50, .06);
    }}
    .fbtn.secondary:hover {{
      border-color: #7f99b3;
      background: #eaf1f8;
    }}
    .filter-state {{
      margin-top: 10px;
      color: #41566d;
      font-size: .78rem;
      font-weight: 750;
      border: 1px solid #d7e3ef;
      background: #f7fbff;
      border-radius: 10px;
      padding: 8px 10px;
    }}
    .dashboard-section-title {{
      margin: 14px 2px 8px;
    }}
    .dashboard-section-title span {{
      color: #172033;
      display: block;
      font-size: .98rem;
      font-weight: 850;
    }}
    .dashboard-section-title p {{
      margin: 3px 0 0;
      color: var(--muted);
      font-size: .78rem;
    }}
    .grid {{
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 8px;
      margin-top: 8px;
    }}
    .card {{
      background: var(--card);
      border: 1px solid var(--line);
      border-radius: 12px;
      padding: 12px;
      box-shadow: var(--shadow-card);
    }}
    .kpi-card {{
      position: relative;
      overflow: hidden;
      min-height: 104px;
    }}
    .kpi-card::before {{
      content: "";
      position: absolute;
      inset: 0 auto 0 0;
      width: 4px;
      background: var(--accent, var(--primary-2));
    }}
    .kpi-top {{
      display: flex;
      justify-content: space-between;
      gap: 8px;
      align-items: flex-start;
      margin-bottom: 8px;
    }}
    .kpi-icon {{
      width: 34px;
      height: 34px;
      display: grid;
      place-items: center;
      flex: 0 0 auto;
      border-radius: 10px;
      color: var(--accent, var(--primary-2));
      background: var(--accent-bg, var(--primary-soft));
      font-size: .72rem;
      font-weight: 900;
    }}
    .accent-blue {{ --accent: #2563eb; --accent-bg: #eaf2ff; }}
    .accent-sky {{ --accent: #0ea5e9; --accent-bg: #e8f7ff; }}
    .accent-green {{ --accent: #1f9d7a; --accent-bg: #e8f8f3; }}
    .accent-teal {{ --accent: #13a8a3; --accent-bg: #e7fbfa; }}
    .accent-amber {{ --accent: #c17a12; --accent-bg: #fff5df; }}
    .accent-red {{ --accent: #c85050; --accent-bg: #fff0f0; }}
    .k {{
      color: var(--muted);
      font-size: .75rem;
      font-weight: 800;
      margin-bottom: 2px;
    }}
    .v {{
      color: #11243a;
      font-size: clamp(1.3rem, 2.1vw, 1.78rem);
      font-weight: 850;
      line-height: 1.1;
    }}
    .progress {{
      margin-top: 10px;
      height: 6px;
      border-radius: 999px;
      background: #e7edf4;
      overflow: hidden;
    }}
    .fill {{
      height: 100%;
      border-radius: 999px;
      background: linear-gradient(90deg, var(--ok), #36bb96);
      width: 0%;
    }}
    .section {{
      margin-top: 10px;
      background: var(--card);
      border: 1px solid var(--line);
      border-radius: 14px;
      padding: 14px;
      box-shadow: var(--shadow-soft);
    }}
    .section h2 {{
      margin: 0 0 8px;
      color: #16243a;
      font-size: .98rem;
      font-weight: 850;
    }}
    .chart-wrap {{
      width: 100%;
      min-height: 270px;
      position: relative;
      overflow: hidden;
      background: #fff;
      border: 1px solid #e4ebf3;
      border-radius: 12px;
      padding: 10px 10px 2px;
      box-shadow: inset 0 1px 0 rgba(255,255,255,.85);
    }}
    .chart-wrap svg {{
      width: 100%;
      min-width: 0;
      height: 300px;
      display: block;
      background: #fff;
      border: 0;
      border-radius: 0;
      overflow: visible;
    }}
    .legend {{
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      margin-bottom: 10px;
      color: var(--muted);
      font-size: .76rem;
      align-items: center;
      font-weight: 700;
    }}
    .chart-period-card {{
      margin-top: 10px;
      background: linear-gradient(180deg, #ffffff 0%, #f9fbfe 100%);
      border: 1px solid var(--line);
      border-radius: 14px;
      padding: 12px 14px;
      box-shadow: var(--shadow-soft);
    }}
    .chart-period-head {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
    }}
    .chart-period-title {{
      color: #172033;
      font-size: .92rem;
      font-weight: 800;
    }}
    .chart-period-sub {{
      color: var(--muted);
      font-size: .74rem;
      font-weight: 600;
      margin-top: 2px;
    }}
    .chart-period-segment {{
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
      justify-content: flex-end;
    }}
    .chart-period-btn {{
      appearance: none;
      border: 1px solid #cbd8e7;
      background: #f8fbff;
      color: #1f3349;
      border-radius: 999px;
      padding: 8px 13px;
      min-height: 34px;
      font: inherit;
      font-size: .78rem;
      font-weight: 800;
      cursor: pointer;
      box-shadow: 0 1px 2px rgba(15, 23, 42, .04);
      transition: background .14s ease, border-color .14s ease, color .14s ease, box-shadow .14s ease;
    }}
    .chart-period-btn:hover {{
      border-color: #8fb1d8;
      background: #eef6ff;
    }}
    .chart-period-btn.active {{
      color: #fff;
      border-color: #1f6fb8;
      background: linear-gradient(180deg, #2b7fd0, #1f67aa);
      box-shadow: 0 6px 14px rgba(31, 103, 170, .22);
    }}
    .chart-axis-label {{
      fill: #64748b;
      font-size: 11px;
      font-weight: 600;
    }}
    .chart-y-label {{
      fill: #64748b;
      font-size: 11px;
    }}
    .chart-grid {{
      stroke: #edf2f7;
      stroke-width: 1;
      shape-rendering: crispEdges;
    }}
    .chart-axis {{
      stroke: #cbd5e1;
      stroke-width: 1;
      shape-rendering: crispEdges;
    }}
    .chart-bar {{
      opacity: .92;
      transition: opacity .16s ease, filter .16s ease, stroke-width .16s ease;
      shape-rendering: geometricPrecision;
    }}
    .chart-bar.is-hover {{
      opacity: 1;
      filter: drop-shadow(0 4px 8px rgba(15, 23, 42, .18));
      stroke: #0f172a;
      stroke-width: 1;
    }}
    .chart-bar-hit {{
      fill: transparent;
      cursor: pointer;
      pointer-events: all;
    }}
    .chart-value-label {{
      fill: #334155;
      font-size: 11px;
      font-weight: 700;
    }}
    .chart-tooltip {{
      position: fixed;
      left: 0;
      top: 0;
      z-index: 50;
      max-width: min(280px, calc(100vw - 24px));
      padding: 9px 11px;
      border: 1px solid #d8e2ee;
      border-radius: 9px;
      background: rgba(255, 255, 255, .97);
      box-shadow: 0 12px 28px rgba(15, 23, 42, .16);
      color: #172033;
      font-size: .78rem;
      line-height: 1.35;
      opacity: 0;
      pointer-events: none;
      transform: translate(-9999px, -9999px);
      transition: opacity .12s ease;
    }}
    .chart-tooltip .tooltip-date {{
      color: #64748b;
      font-weight: 700;
      margin-bottom: 4px;
    }}
    .chart-tooltip .tooltip-row {{
      display: flex;
      align-items: center;
      gap: 6px;
      white-space: nowrap;
    }}
    .chart-tooltip .tooltip-swatch {{
      width: 9px;
      height: 9px;
      border-radius: 50%;
      display: inline-block;
      flex: 0 0 auto;
    }}
    .dot {{
      width: 10px;
      height: 10px;
      border-radius: 50%;
      display: inline-block;
      margin-right: 6px;
      transform: translateY(1px);
    }}
    .t {{
      margin-top: 8px;
      color: var(--muted);
      font-size: .74rem;
      line-height: 1.35;
    }}
    .sensor-grid {{
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 8px;
      margin-top: 6px;
    }}
    .sensor-card {{
      border: 1px solid var(--line);
      border-radius: 12px;
      padding: 12px;
      background: #fbfdff;
      box-shadow: 0 5px 14px rgba(23, 35, 50, .04);
    }}
    .sensor-name {{
      font-size: .78rem;
      color: var(--muted);
      margin-bottom: 2px;
    }}
    .sensor-val {{
      font-size: 1.2rem;
      font-weight: 850;
      color: #1f2e3b;
      line-height: 1.1;
    }}
    .latency-grid {{
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 280px));
      gap: 8px;
      margin-top: 6px;
    }}
    @media (max-width: 950px) {{
      .header-top {{ align-items: flex-start; flex-direction: column; }}
      .header-badges {{ justify-content: flex-start; max-width: none; width: 100%; }}
      .header-badge {{ flex: 1 1 160px; }}
      .grid {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
      .sensor-grid {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
      .latency-grid {{ grid-template-columns: repeat(1, minmax(0, 1fr)); }}
      .filters {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
      .chart-period-head {{ align-items: flex-start; flex-direction: column; }}
      .chart-period-segment {{ justify-content: flex-start; width: 100%; }}
    }}
    @media (max-width: 520px) {{
      .wrap {{ padding: 10px 8px 16px; }}
      .header {{ padding: 14px; border-radius: 14px; }}
      .brand-row {{ align-items: flex-start; }}
      .brand-mark {{ width: 40px; height: 40px; border-radius: 10px; }}
      .header-badge {{ min-width: 0; flex-basis: 100%; }}
      .filters {{ grid-template-columns: 1fr; }}
      .grid {{ grid-template-columns: 1fr; }}
      .chart-period-card {{ padding: 10px; }}
      .chart-period-btn {{ flex: 1 1 calc(50% - 6px); padding-inline: 8px; }}
    }}
  </style>
</head>
<body>
  <div class="wrap">
    <div class="header">
      <div class="header-top">
        <div class="brand-row">
          <div class="brand-mark" aria-hidden="true">A</div>
          <div class="header-copy">
            <div class="eyebrow">Painel operacional</div>
            <h1>Painel Operacional Aura | Entregas, Inserções e Loggers</h1>
            <div class="sub" id="subtitle"></div>
          </div>
        </div>
        <div class="header-badges" aria-label="Informações da base">
          <div class="header-badge">
            <span class="badge-label">Período analisado</span>
            <span class="badge-value" id="header_period">-</span>
          </div>
          <div class="header-badge">
            <span class="badge-label">Última atualização</span>
            <span class="badge-value" id="header_generated">-</span>
          </div>
          <div class="header-badge">
            <span class="badge-label">Base</span>
            <span class="badge-value">Aura</span>
          </div>
        </div>
      </div>
    </div>
    <div class="section filter-panel">
      <div class="section-head">
        <div>
          <h2 class="section-title">Filtros operacionais</h2>
          <p class="section-copy">Ajuste o recorte de entrega, sensor e faixa térmica sem alterar a base consolidada.</p>
        </div>
      </div>
      <div class="filters">
        <div class="fgroup">
          <div class="flabel">Data inicial (entrega)</div>
          <input class="finput" type="date" id="flt_start" />
        </div>
        <div class="fgroup">
          <div class="flabel">Data final (entrega)</div>
          <input class="finput" type="date" id="flt_end" />
        </div>
        <div class="fgroup">
          <div class="flabel">Sensor (gráficos por sensor)</div>
          <select class="finput" id="flt_sensor">
            <option value="all">Todos</option>
            <option value="ares">ARES</option>
            <option value="syos">SYOS</option>
            <option value="shield">Shield</option>
            <option value="web">Sensor web</option>
          </select>
        </div>
        <div class="fgroup">
          <div class="flabel">Faixa térmica</div>
          <select class="finput" id="flt_thermal">
            <option value="all">Todos</option>
            <option value="refrigerado">Refrigerado (>= 0°C)</option>
            <option value="congelado">Congelado (< 0°C)</option>
          </select>
        </div>
        <div class="fgroup">
          <div class="flabel">Ações</div>
          <button class="fbtn" id="btn_apply" type="button">Aplicar filtro</button>
        </div>
        <div class="fgroup">
          <div class="flabel">Ações</div>
          <button class="fbtn secondary" id="btn_reset" type="button">Limpar</button>
        </div>
        <div class="fgroup">
          <div class="flabel">Atalho</div>
          <button class="fbtn secondary" id="btn_today" type="button">Somente hoje</button>
        </div>
      </div>
      <div class="filter-state" id="filter_state"></div>
    </div>

    <div class="dashboard-section-title">
      <span>Resumo operacional</span>
      <p>Indicadores consolidados do recorte selecionado.</p>
    </div>
    <div class="grid kpi-grid">
      <div class="card kpi-card accent-blue">
        <div class="kpi-top">
          <div class="k">Pedidos entregues</div>
          <div class="kpi-icon" aria-hidden="true">PE</div>
        </div>
        <div class="v" id="p_ent">-</div>
      </div>
      <div class="card kpi-card accent-sky">
        <div class="kpi-top">
          <div class="k">Pedidos inseridos</div>
          <div class="kpi-icon" aria-hidden="true">PI</div>
        </div>
        <div class="v" id="p_ins">-</div>
      </div>
      <div class="card kpi-card accent-green">
        <div class="kpi-top">
          <div class="k">Loggers entregues</div>
          <div class="kpi-icon" aria-hidden="true">LE</div>
        </div>
        <div class="v" id="l_ent">-</div>
      </div>
      <div class="card kpi-card accent-teal">
        <div class="kpi-top">
          <div class="k">Loggers inseridos</div>
          <div class="kpi-icon" aria-hidden="true">LI</div>
        </div>
        <div class="v" id="l_ins">-</div>
      </div>
    </div>

    <div class="grid kpi-grid">
      <div class="card kpi-card accent-blue">
        <div class="kpi-top">
          <div class="k">Proporção de pedidos inseridos</div>
          <div class="kpi-icon" aria-hidden="true">%</div>
        </div>
        <div class="v" id="p_pct">-</div>
        <div class="progress"><div class="fill" id="p_fill"></div></div>
      </div>
      <div class="card kpi-card accent-red">
        <div class="kpi-top">
          <div class="k">Pedidos pendentes</div>
          <div class="kpi-icon" aria-hidden="true">PP</div>
        </div>
        <div class="v" id="p_pen">-</div>
      </div>
      <div class="card kpi-card accent-green">
        <div class="kpi-top">
          <div class="k">Proporção de loggers inseridos</div>
          <div class="kpi-icon" aria-hidden="true">%</div>
        </div>
        <div class="v" id="l_pct">-</div>
        <div class="progress"><div class="fill" id="l_fill"></div></div>
      </div>
      <div class="card kpi-card accent-amber">
        <div class="kpi-top">
          <div class="k">Loggers pendentes</div>
          <div class="kpi-icon" aria-hidden="true">LP</div>
        </div>
        <div class="v" id="l_pen">-</div>
      </div>
    </div>

    <div class="dashboard-section-title">
      <span>Análise diária</span>
      <p>Visualização dos gráficos com segmentação de período para leitura executiva.</p>
    </div>
    <div class="chart-period-card">
      <div class="chart-period-head">
        <div>
          <div class="chart-period-title">Período dos gráficos</div>
          <div class="chart-period-sub" id="chart_period_state"></div>
        </div>
        <div class="chart-period-segment" role="group" aria-label="Período dos gráficos">
          <button class="chart-period-btn" type="button" data-chart-period="7d">7 dias</button>
          <button class="chart-period-btn" type="button" data-chart-period="15d">15 dias</button>
          <button class="chart-period-btn" type="button" data-chart-period="30d">30 dias</button>
          <button class="chart-period-btn" type="button" data-chart-period="month">Mês atual</button>
          <button class="chart-period-btn" type="button" data-chart-period="all">Tudo</button>
        </div>
      </div>
    </div>

    <div class="section chart-section">
      <h2>Pedidos por dia | entregues x inseridos</h2>
      <div class="legend">
        <div><span class="dot" style="background:#2563eb"></span>Entregues</div>
        <div><span class="dot" style="background:#0ea5e9"></span>Inseridos</div>
      </div>
      <div class="chart-wrap"><svg id="chartPedidos"></svg></div>
    </div>

    <div class="section chart-section">
      <h2 id="h2_loggers">Loggers por dia | entregues x inseridos</h2>
      <div class="legend">
        <div><span class="dot" style="background:#1f9d7a"></span>Entregues</div>
        <div><span class="dot" style="background:#65c7ae"></span>Inseridos</div>
      </div>
      <div class="chart-wrap"><svg id="chartLoggers"></svg></div>
      <div class="t">Gerado em <span id="gen_at"></span></div>
    </div>

    <div class="dashboard-section-title">
      <span>Pendências por tipo de sensor</span>
      <p>Distribuição dos loggers pendentes por tecnologia no recorte selecionado.</p>
    </div>
    <div class="section">
      <h2>Loggers pendentes por tipo</h2>
      <div class="sensor-grid">
        <div class="sensor-card">
          <div class="sensor-name">ARES</div>
          <div class="sensor-val" id="pend_ares">-</div>
        </div>
        <div class="sensor-card">
          <div class="sensor-name">SYOS</div>
          <div class="sensor-val" id="pend_syos">-</div>
        </div>
        <div class="sensor-card">
          <div class="sensor-name">Shield</div>
          <div class="sensor-val" id="pend_shield">-</div>
        </div>
        <div class="sensor-card">
          <div class="sensor-name">Sensor web</div>
          <div class="sensor-val" id="pend_web">-</div>
        </div>
      </div>
    </div>

    <div class="dashboard-section-title">
      <span>Tempos médios de processamento</span>
      <p>Métricas de latência operacional para inserção e lançamento de entregas.</p>
    </div>
    <div class="section">
      <h2>Tempo para inserir pedido após entrega</h2>
      <div class="latency-grid">
        <div class="sensor-card">
          <div class="sensor-name">Média (horas)</div>
          <div class="sensor-val" id="lat_avg_h">-</div>
        </div>
      </div>
      <div class="t">Cálculo: <code>orders.created_at - sync_orders.delivery_date</code> por pedido.</div>
    </div>
    <div class="section">
      <h2>Tempo para lançar data de entrega no sistema</h2>
      <div class="latency-grid">
        <div class="sensor-card">
          <div class="sensor-name">Média (horas)</div>
          <div class="sensor-val" id="dl_avg_h">-</div>
        </div>
        <div class="sensor-card">
          <div class="sensor-name">Pedidos válidos</div>
          <div class="sensor-val" id="dl_valid">-</div>
        </div>
      </div>
      <div class="t">Cálculo: <code>Data_Lancamento - Data_Entrega</code> na ocorrência 32 (somente valores não negativos).</div>
      <div class="t" id="dl_status"></div>
    </div>
    <div class="section chart-section">
      <h2>Média de horas para lançar data de entrega por dia</h2>
      <div class="legend">
        <div><span class="dot" style="background:#1e40af"></span>Média horas para lançamento da entrega</div>
      </div>
      <div class="chart-wrap"><svg id="chartDeliveryLaunch"></svg></div>
      <div class="t">Exemplo: pedidos entregues em 10/04 mostram a média de horas até a data de entrega ser lançada no sistema.</div>
    </div>
    <div class="section chart-section">
      <h2>Média de horas por dia para inserir pedidos</h2>
      <div class="legend">
        <div><span class="dot" style="background:#2563eb"></span>Média horas (delivery_date x created_at)</div>
      </div>
      <div class="chart-wrap"><svg id="chartLatency"></svg></div>
      <div class="t">Exemplo: para 16/04, o valor mostra a média de horas dos pedidos com entrega em 16/04 que já subiram no Aura.</div>
    </div>
    <div class="dashboard-section-title">
      <span>Análise por tecnologia/sensor</span>
      <p>Tempos médios item a item por tecnologia, mantendo a mesma base de cálculo atual.</p>
    </div>
    <div class="section chart-section" id="secLatencyAres">
      <h2>Média de horas por dia para inserir itens ARES</h2>
      <div class="legend">
        <div><span class="dot" style="background:#1d4ed8"></span>Média horas ARES (delivery do item x created_at do pedido)</div>
      </div>
      <div class="chart-wrap"><svg id="chartLatencyAres"></svg></div>
      <div class="t">Base item a item: cruza chave de <code>sync_items</code> com <code>order_items</code>.</div>
    </div>
    <div class="section chart-section" id="secLatencySyos">
      <h2>Média de horas por dia para inserir itens SYOS</h2>
      <div class="legend">
        <div><span class="dot" style="background:#0f766e"></span>Média horas SYOS (delivery do item x created_at do pedido)</div>
      </div>
      <div class="chart-wrap"><svg id="chartLatencySyos"></svg></div>
      <div class="t">Base item a item: cruza chave de <code>sync_items</code> com <code>order_items</code>.</div>
    </div>
    <div class="section chart-section" id="secLatencyShield">
      <h2>Média de horas por dia para inserir itens Shield</h2>
      <div class="legend">
        <div><span class="dot" style="background:#b45309"></span>Média horas Shield (delivery do item x created_at do pedido)</div>
      </div>
      <div class="chart-wrap"><svg id="chartLatencyShield"></svg></div>
      <div class="t">Base item a item: cruza chave de <code>sync_items</code> com <code>order_items</code>.</div>
    </div>
    <div class="section chart-section" id="secLatencyWeb">
      <h2>Média de horas por dia para inserir itens Sensor web</h2>
      <div class="legend">
        <div><span class="dot" style="background:#475569"></span>Média horas Sensor web (delivery do item x created_at do pedido)</div>
      </div>
      <div class="chart-wrap"><svg id="chartLatencyWeb"></svg></div>
      <div class="t">Base item a item: cruza chave de <code>sync_items</code> com <code>order_items</code>.</div>
    </div>
  </div>

  <script>
    const payload = {data_json};
    const fmt = (n) => new Intl.NumberFormat('pt-BR').format(n);
    const fmtPct = (n) => `${{n.toFixed(1)}}%`;

    const periodBadgeText = payload.end_date
      ? `${{formatDateBr(payload.start_date)}} até ${{formatDateBr(payload.end_date)}}`
      : `desde ${{formatDateBr(payload.start_date)}}`;
    document.getElementById('subtitle').textContent =
      `Acompanhamento operacional de pedidos entregues, pedidos inseridos, loggers e tempos médios de processamento.`;
    const headerPeriod = document.getElementById('header_period');
    const headerGenerated = document.getElementById('header_generated');
    if (headerPeriod) headerPeriod.textContent = periodBadgeText;
    if (headerGenerated) headerGenerated.textContent = payload.generated_at || '-';
    document.getElementById('gen_at').textContent = payload.generated_at;
    const allDaily = payload.daily || [];
    const allDlDaily = (payload.delivery_launch && payload.delivery_launch.daily) ? payload.delivery_launch.daily : [];
    const allSensorPendingDaily = payload.sensor_pending_daily || [];
    const allSensorDailyStats = payload.sensor_daily_stats || [];
    const allOrderDailyStats = payload.order_daily_stats || [];

    const chartRegistry = new Map();
    let chartTooltipEl = null;
    let chartResizeTimer = null;

    function svgNode(name, attrs = {{}}, text = null) {{
      const node = document.createElementNS('http://www.w3.org/2000/svg', name);
      for (const [key, value] of Object.entries(attrs)) {{
        if (value === null || value === undefined) continue;
        node.setAttribute(key, String(value));
      }}
      if (text !== null && text !== undefined) node.textContent = text;
      return node;
    }}

    function htmlEscape(value) {{
      const escapes = {{ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }};
      return String(value ?? '').replace(/[&<>"']/g, ch => escapes[ch]);
    }}

    function formatMetric(value, metricType = 'integer') {{
      const n = Number(value);
      if (!Number.isFinite(n)) return '-';
      const digits = metricType === 'decimal' ? 1 : 0;
      return new Intl.NumberFormat('pt-BR', {{
        maximumFractionDigits: digits,
        minimumFractionDigits: 0,
      }}).format(n);
    }}

    function formatDateShort(iso) {{
      const m = String(iso || '').match(/^(\\d{{4}})-(\\d{{2}})-(\\d{{2}})$/);
      if (!m) return String(iso || '');
      return `${{m[3]}}/${{m[2]}}`;
    }}

    function formatDateLong(iso) {{
      const m = String(iso || '').match(/^(\\d{{4}})-(\\d{{2}})-(\\d{{2}})$/);
      if (!m) return String(iso || '');
      return `${{m[3]}}/${{m[2]}}/${{m[1]}}`;
    }}

    function niceCeil(value) {{
      const raw = Math.max(1, Number(value) || 1);
      const exp = Math.floor(Math.log10(raw));
      const base = Math.pow(10, exp);
      const scaled = raw / base;
      const nice = scaled <= 1 ? 1 : scaled <= 2 ? 2 : scaled <= 5 ? 5 : 10;
      return nice * base;
    }}

    function getChartTooltip() {{
      if (chartTooltipEl) return chartTooltipEl;
      chartTooltipEl = document.createElement('div');
      chartTooltipEl.className = 'chart-tooltip';
      chartTooltipEl.setAttribute('role', 'status');
      document.body.appendChild(chartTooltipEl);
      return chartTooltipEl;
    }}

    function moveChartTooltip(event) {{
      if (!chartTooltipEl) return;
      const pad = 12;
      const rect = chartTooltipEl.getBoundingClientRect();
      const x = Math.min(window.innerWidth - rect.width - pad, event.clientX + 14);
      const y = Math.max(pad, event.clientY - rect.height - 14);
      chartTooltipEl.style.transform = `translate(${{Math.max(pad, x)}}px, ${{y}}px)`;
    }}

    function showChartTooltip(event, date, seriesName, value, color, metricType) {{
      const tip = getChartTooltip();
      tip.innerHTML = `
        <div class="tooltip-date">${{htmlEscape(formatDateLong(date))}}</div>
        <div class="tooltip-row">
          <span class="tooltip-swatch" style="background:${{htmlEscape(color)}}"></span>
          <span><strong>${{htmlEscape(seriesName)}}</strong>: ${{htmlEscape(formatMetric(value, metricType))}}</span>
        </div>`;
      tip.style.opacity = '1';
      moveChartTooltip(event);
    }}

    function hideChartTooltip() {{
      const tip = getChartTooltip();
      tip.style.opacity = '0';
      tip.style.transform = 'translate(-9999px, -9999px)';
    }}

    function drawEmptyChart(svg, width, height, message) {{
      svg.appendChild(svgNode('text', {{
        x: width / 2,
        y: height / 2,
        'text-anchor': 'middle',
        'font-size': 14,
        fill: '#64748b',
        'font-weight': 700,
      }}, message));
    }}

    function renderColumnChart(config) {{
      const svg = document.getElementById(config.svgId);
      if (!svg) return;
      if (chartTooltipEl) hideChartTooltip();
      const wrap = svg.closest('.chart-wrap') || svg.parentElement;
      const wrapWidth = Math.round((wrap && wrap.getBoundingClientRect().width) || svg.clientWidth || 720);
      const width = Math.max(320, wrapWidth);
      const rows = (config.data || []).map(row => {{
        const out = {{ date: row[config.dateField] }};
        for (const s of config.series) {{
          out[s.key] = Number(row[s.key]) || 0;
        }}
        return out;
      }});
      const dense = rows.length > 72;
      const height = Math.round(Math.max(250, Math.min(380, width * (dense ? 0.34 : 0.40))));
      const pad = {{
        top: width < 520 ? 18 : 22,
        right: width < 520 ? 8 : 16,
        bottom: width < 520 ? 36 : 44,
        left: width < 520 ? 44 : 58,
      }};
      const cw = Math.max(10, width - pad.left - pad.right);
      const ch = Math.max(10, height - pad.top - pad.bottom);
      const xAxisY = pad.top + ch;

      svg.innerHTML = '';
      svg.style.height = `${{height}}px`;
      svg.setAttribute('viewBox', `0 0 ${{width}} ${{height}}`);
      svg.setAttribute('preserveAspectRatio', 'xMidYMid meet');
      svg.setAttribute('role', 'img');
      svg.setAttribute('aria-label', config.ariaLabel || 'Gráfico de colunas');

      if (!rows.length) {{
        drawEmptyChart(svg, width, height, config.emptyMessage || 'Sem dados no período');
        return;
      }}

      const series = config.series || [];
      const values = rows.flatMap(row => series.map(s => Number(row[s.key]) || 0));
      const maxVal = niceCeil(Math.max(1, ...values));
      const groupW = cw / rows.length;
      const seriesCount = Math.max(1, series.length);
      const innerGap = Math.max(0.6, Math.min(4, groupW * 0.08));
      const usableGroupW = Math.max(1, groupW * 0.78);
      const barMax = seriesCount > 1 ? 20 : 28;
      const barW = Math.max(1.4, Math.min(barMax, (usableGroupW - innerGap * (seriesCount - 1)) / seriesCount));
      const totalBarsW = barW * seriesCount + innerGap * (seriesCount - 1);
      const showValueLabels = rows.length <= (width < 600 ? 10 : 18) && groupW > (seriesCount > 1 ? 32 : 24);
      const maxXTicks = Math.max(3, Math.floor(cw / (width < 520 ? 62 : 82)));
      const labelStep = Math.max(1, Math.ceil(rows.length / maxXTicks));
      const tickCount = 4;

      svg.appendChild(svgNode('line', {{
        x1: pad.left,
        y1: xAxisY,
        x2: pad.left + cw,
        y2: xAxisY,
        class: 'chart-axis',
      }}));

      for (let i = 0; i <= tickCount; i++) {{
        const y = pad.top + (ch * i / tickCount);
        const v = maxVal * (1 - i / tickCount);
        svg.appendChild(svgNode('line', {{
          x1: pad.left,
          y1: y,
          x2: pad.left + cw,
          y2: y,
          class: 'chart-grid',
        }}));
        svg.appendChild(svgNode('text', {{
          x: pad.left - 9,
          y: y + 4,
          'text-anchor': 'end',
          class: 'chart-y-label',
        }}, formatMetric(v, config.metricType)));
      }}

      rows.forEach((row, i) => {{
        const centerX = pad.left + i * groupW + groupW / 2;
        const startX = centerX - totalBarsW / 2;

        if (i === 0 || i === rows.length - 1 || i % labelStep === 0) {{
          svg.appendChild(svgNode('text', {{
            x: centerX,
            y: xAxisY + (width < 520 ? 17 : 19),
            'text-anchor': 'middle',
            class: 'chart-axis-label',
          }}, formatDateShort(row.date)));
        }}

        series.forEach((s, si) => {{
          const value = Number(row[s.key]) || 0;
          const rawH = (value / maxVal) * ch;
          const h = value > 0 ? Math.max(2, rawH) : 0;
          const x = startX + si * (barW + innerGap);
          const y = xAxisY - h;
          const bar = svgNode('rect', {{
            x: x,
            y: y,
            width: barW,
            height: h,
            rx: Math.min(5, Math.max(1, barW / 3)),
            fill: s.color,
            class: 'chart-bar',
          }});
          svg.appendChild(bar);

          if (showValueLabels && value > 0) {{
            svg.appendChild(svgNode('text', {{
              x: x + barW / 2,
              y: Math.max(12, y - 5),
              'text-anchor': 'middle',
              class: 'chart-value-label',
            }}, formatMetric(value, config.metricType)));
          }}

          const hitW = Math.max(barW, Math.min(groupW / seriesCount, 14));
          const hit = svgNode('rect', {{
            x: x + barW / 2 - hitW / 2,
            y: pad.top,
            width: hitW,
            height: ch,
            class: 'chart-bar-hit',
          }});
          hit.addEventListener('mouseenter', event => {{
            bar.classList.add('is-hover');
            showChartTooltip(event, row.date, s.label, value, s.color, config.metricType);
          }});
          hit.addEventListener('mousemove', moveChartTooltip);
          hit.addEventListener('mouseleave', () => {{
            bar.classList.remove('is-hover');
            hideChartTooltip();
          }});
          svg.appendChild(hit);
        }});
      }});
    }}

    function drawColumnChart(svgId, data, options) {{
      const config = {{
        svgId,
        data,
        dateField: options.dateField || 'dia',
        series: options.series || [],
        metricType: options.metricType || 'integer',
        ariaLabel: options.ariaLabel || '',
        emptyMessage: options.emptyMessage || 'Sem dados para o período selecionado',
      }};
      chartRegistry.set(svgId, config);
      renderColumnChart(config);
    }}

    function drawGroupedBars(svgId, labels, aVals, bVals, colors, names = ['Entregues', 'Inseridos']) {{
      const data = (labels || []).map((dia, i) => ({{
        dia,
        serie_a: Number(aVals[i]) || 0,
        serie_b: Number(bVals[i]) || 0,
      }}));
      drawColumnChart(svgId, data, {{
        dateField: 'dia',
        metricType: 'integer',
        ariaLabel: `${{names[0]}} x ${{names[1]}} por dia`,
        series: [
          {{ key: 'serie_a', label: names[0], color: colors[0] }},
          {{ key: 'serie_b', label: names[1], color: colors[1] }},
        ],
      }});
    }}

    function drawSingleBars(svgId, labels, vals, color, name = 'Valor') {{
      const data = (labels || []).map((dia, i) => ({{
        dia,
        valor: Number(vals[i]) || 0,
      }}));
      drawColumnChart(svgId, data, {{
        dateField: 'dia',
        metricType: 'decimal',
        ariaLabel: `${{name}} por dia`,
        series: [
          {{ key: 'valor', label: name, color }},
        ],
      }});
    }}

    window.addEventListener('resize', () => {{
      window.clearTimeout(chartResizeTimer);
      chartResizeTimer = window.setTimeout(() => {{
        for (const config of chartRegistry.values()) {{
          renderColumnChart(config);
        }}
      }}, 120);
    }});

    const dateUniverse = [
      ...new Set([
        ...allDaily.map(x => x.dia),
        ...allDlDaily.map(x => x.dia),
        ...allSensorPendingDaily.map(x => x.dia),
        ...allSensorDailyStats.map(x => x.dia),
        ...allOrderDailyStats.map(x => x.dia),
      ]),
    ].sort();
    const minDate = dateUniverse.length ? dateUniverse[0] : '';
    const maxDate = dateUniverse.length ? dateUniverse[dateUniverse.length - 1] : '';

    const fltStart = document.getElementById('flt_start');
    const fltEnd = document.getElementById('flt_end');
    const fltSensor = document.getElementById('flt_sensor');
    const fltThermal = document.getElementById('flt_thermal');
    const btnApply = document.getElementById('btn_apply');
    const btnReset = document.getElementById('btn_reset');
    const btnToday = document.getElementById('btn_today');
    const filterState = document.getElementById('filter_state');
    const chartPeriodState = document.getElementById('chart_period_state');
    const chartPeriodBtns = Array.from(document.querySelectorAll('[data-chart-period]'));
    let selectedChartPeriod = '30d';
    let currentChartPeriodAnchor = '';
    const chartPeriodLabels = {{
      '7d': 'Últimos 7 dias',
      '15d': 'Últimos 15 dias',
      '30d': 'Últimos 30 dias',
      month: 'Mês atual',
      all: 'Tudo',
    }};
    const sensorLabelMap = {{ all: 'Todos', ares: 'ARES', syos: 'SYOS', shield: 'Shield', web: 'Sensor web' }};
    const thermalLabelMap = {{ all: 'Todos', refrigerado: 'Refrigerado', congelado: 'Congelado' }};

    fltStart.min = minDate;
    fltStart.max = maxDate;
    fltEnd.min = minDate;
    fltEnd.max = maxDate;
    fltStart.value = minDate;
    fltEnd.value = maxDate;

    function toIsoDate(value) {{
      if (!value) return '';
      const v = String(value).trim();
      if (/^\\d{{4}}-\\d{{2}}-\\d{{2}}$/.test(v)) return v;
      const m = v.match(/^(\\d{{2}})\\/(\\d{{2}})\\/(\\d{{4}})$/);
      if (m) return `${{m[3]}}-${{m[2]}}-${{m[1]}}`;
      return '';
    }}

    function formatDateBr(iso) {{
      if (!iso) return '-';
      const m = String(iso).match(/^(\\d{{4}})-(\\d{{2}})-(\\d{{2}})$/);
      if (!m) return iso;
      return `${{m[3]}}/${{m[2]}}/${{m[1]}}`;
    }}

    function addDaysIso(iso, deltaDays) {{
      const d = new Date(`${{iso}}T00:00:00`);
      if (Number.isNaN(d.getTime())) return iso;
      d.setDate(d.getDate() + deltaDays);
      return d.toISOString().slice(0, 10);
    }}

    function maxIsoDate(items, dateField) {{
      return (items || [])
        .map(item => String(item && item[dateField] ? item[dateField] : ''))
        .filter(Boolean)
        .sort()
        .pop() || '';
    }}

    function filterChartDataByPeriod(data, dateField, selectedPeriod) {{
      const rows = (data || []).filter(row => row && row[dateField]);
      if (!rows.length || selectedPeriod === 'all') return rows;
      const anchor = currentChartPeriodAnchor || maxIsoDate(rows, dateField);
      if (!anchor) return rows;
      let start = '';
      let end = anchor;
      if (selectedPeriod === 'month') {{
        start = `${{anchor.slice(0, 8)}}01`;
      }} else {{
        const days = Number(String(selectedPeriod).replace('d', '')) || 30;
        start = addDaysIso(anchor, -(days - 1));
      }}
      return rows.filter(row => row[dateField] >= start && row[dateField] <= end);
    }}

    function updateChartPeriodButtons() {{
      for (const btn of chartPeriodBtns) {{
        const active = btn.dataset.chartPeriod === selectedChartPeriod;
        btn.classList.toggle('active', active);
        btn.setAttribute('aria-pressed', active ? 'true' : 'false');
      }}
    }}

    function updateChartPeriodState(visibleRows) {{
      if (!chartPeriodState) return;
      const dates = (visibleRows || [])
        .map(row => String(row && row.dia ? row.dia : ''))
        .filter(Boolean)
        .sort();
      const label = chartPeriodLabels[selectedChartPeriod] || selectedChartPeriod;
      if (!dates.length) {{
        chartPeriodState.textContent = `Gráficos: ${{label}} | sem dados no período`;
        return;
      }}
      chartPeriodState.textContent =
        `Gráficos: ${{label}} | ${{formatDateBr(dates[0])}} a ${{formatDateBr(dates[dates.length - 1])}} | ${{dates.length}} dias com dados`;
    }}

    function inRange(dia, start, end) {{
      if (start && dia < start) return false;
      if (end && dia > end) return false;
      return true;
    }}

    function sumBy(items, key) {{
      return items.reduce((acc, item) => acc + (Number(item[key]) || 0), 0);
    }}

    function weightedAvg(items, valKey, weightKey) {{
      let num = 0;
      let den = 0;
      for (const item of items) {{
        const v = item[valKey];
        const w = Number(item[weightKey]) || 0;
        if (v == null || w <= 0) continue;
        num += Number(v) * w;
        den += w;
      }}
      return den > 0 ? (num / den) : null;
    }}

    function setSensorSections(mode) {{
      const map = {{
        ares: document.getElementById('secLatencyAres'),
        syos: document.getElementById('secLatencySyos'),
        shield: document.getElementById('secLatencyShield'),
        web: document.getElementById('secLatencyWeb'),
      }};
      for (const [k, el] of Object.entries(map)) {{
        if (!el) continue;
        el.style.display = (mode === 'all' || mode === k) ? '' : 'none';
      }}
    }}

    function sensorNameToKey(name) {{
      const v = String(name || '').trim().toUpperCase();
      if (v === 'ARES') return 'ares';
      if (v === 'SYOS') return 'syos';
      if (v === 'SHIELD') return 'shield';
      if (v === 'SENSOR WEB') return 'web';
      return 'all';
    }}

    function thermalNameToKey(name) {{
      const v = String(name || '').trim().toLowerCase();
      if (v === 'refrigerado') return 'refrigerado';
      if (v === 'congelado') return 'congelado';
      return 'all';
    }}

    function applyFilters() {{
      let start = toIsoDate(fltStart.value) || minDate;
      let end = toIsoDate(fltEnd.value) || maxDate;
      const sensorMode = fltSensor.value || 'all';
      const thermalMode = fltThermal.value || 'all';
      if (start && end && start > end) {{
        const tmp = start;
        start = end;
        end = tmp;
        fltStart.value = start;
        fltEnd.value = end;
      }}

      const daily = allDaily.filter(x => inRange(x.dia, start, end));
      const dlDaily = allDlDaily.filter(x => inRange(x.dia, start, end));
      const sensorStats = allSensorDailyStats.filter(
        x =>
          inRange(x.dia, start, end) &&
          (sensorMode === 'all' || sensorNameToKey(x.sensor) === sensorMode) &&
          (thermalMode === 'all' || thermalNameToKey(x.thermal_class) === thermalMode)
      );
      const orderStats = allOrderDailyStats.filter(
        x =>
          inRange(x.dia, start, end) &&
          (sensorMode === 'all' || sensorNameToKey(x.sensor) === sensorMode) &&
          (thermalMode === 'all' || thermalNameToKey(x.thermal_class) === thermalMode)
      );

      const orderByDay = new Map();
      for (const row of orderStats) {{
        const k = row.dia;
        if (!orderByDay.has(k)) {{
          orderByDay.set(k, {{ ent: 0, ins: 0 }});
        }}
        const agg = orderByDay.get(k);
        agg.ent += Number(row.pedidos_entregues) || 0;
        agg.ins += Number(row.pedidos_inseridos) || 0;
      }}
      // Mantém o consolidado original quando nenhum filtro específico de logger/faixa está ativo.
      if (sensorMode === 'all' && thermalMode === 'all') {{
        orderByDay.clear();
        for (const row of daily) {{
          orderByDay.set(row.dia, {{
            ent: Number(row.pedidos_entregues) || 0,
            ins: Number(row.pedidos_inseridos) || 0,
          }});
        }}
      }}
      let pedidos_entregues_total = 0;
      let pedidos_inseridos_total = 0;
      for (const v of orderByDay.values()) {{
        pedidos_entregues_total += v.ent;
        pedidos_inseridos_total += v.ins;
      }}

      const loggerByDay = new Map();
      for (const row of sensorStats) {{
        const k = row.dia;
        if (!loggerByDay.has(k)) {{
          loggerByDay.set(k, {{ ent: 0, ins: 0, pen: 0 }});
        }}
        const agg = loggerByDay.get(k);
        agg.ent += Number(row.loggers_entregues) || 0;
        agg.ins += Number(row.loggers_inseridos) || 0;
        agg.pen += Number(row.loggers_pendentes) || 0;
      }}
      let loggers_entregues_total = 0;
      let loggers_inseridos_total = 0;
      let loggers_pendentes_total = 0;
      for (const v of loggerByDay.values()) {{
        loggers_entregues_total += v.ent;
        loggers_inseridos_total += v.ins;
        loggers_pendentes_total += v.pen;
      }}
      const pedidos_pendentes_total = pedidos_entregues_total - pedidos_inseridos_total;
      const pedidos_pct = pedidos_entregues_total > 0 ? (pedidos_inseridos_total / pedidos_entregues_total) * 100 : 0;
      const loggers_pct = loggers_entregues_total > 0 ? (loggers_inseridos_total / loggers_entregues_total) * 100 : 0;

      document.getElementById('p_ent').textContent = fmt(pedidos_entregues_total);
      document.getElementById('p_ins').textContent = fmt(pedidos_inseridos_total);
      document.getElementById('l_ent').textContent = fmt(loggers_entregues_total);
      document.getElementById('l_ins').textContent = fmt(loggers_inseridos_total);
      document.getElementById('p_pen').textContent = fmt(Math.max(0, pedidos_pendentes_total));
      document.getElementById('l_pen').textContent = fmt(Math.max(0, loggers_pendentes_total));
      document.getElementById('p_pct').textContent = fmtPct(pedidos_pct);
      document.getElementById('l_pct').textContent = fmtPct(loggers_pct);
      document.getElementById('p_fill').style.width = `${{Math.max(0, Math.min(100, pedidos_pct))}}%`;
      document.getElementById('l_fill').style.width = `${{Math.max(0, Math.min(100, loggers_pct))}}%`;

      const pendBySensor = {{ 'ARES': 0, 'SYOS': 0, 'Shield': 0, 'Sensor web': 0 }};
      for (const row of sensorStats) {{
        if (!pendBySensor.hasOwnProperty(row.sensor)) continue;
        pendBySensor[row.sensor] += Number(row.loggers_pendentes) || 0;
      }}
      document.getElementById('pend_ares').textContent = fmt(pendBySensor['ARES']);
      document.getElementById('pend_syos').textContent = fmt(pendBySensor['SYOS']);
      document.getElementById('pend_shield').textContent = fmt(pendBySensor['Shield']);
      document.getElementById('pend_web').textContent = fmt(pendBySensor['Sensor web']);

      const avgPedidoInsert = weightedAvg(daily, 'avg_horas_pedidos', 'pedidos_inseridos');
      document.getElementById('lat_avg_h').textContent = avgPedidoInsert == null ? 'N/D' : avgPedidoInsert.toFixed(1);

      const avgDl = weightedAvg(dlDaily, 'media_horas', 'pedidos_validos');
      const dlValid = sumBy(dlDaily, 'pedidos_validos');
      document.getElementById('dl_avg_h').textContent = avgDl == null ? 'N/D' : avgDl.toFixed(2);
      document.getElementById('dl_valid').textContent = dlDaily.length ? fmt(dlValid) : 'N/D';
      const dlStatus = document.getElementById('dl_status');
      if (dlStatus) {{
        if (!payload.delivery_launch || !payload.delivery_launch.available) {{
          const err = (payload.delivery_launch && payload.delivery_launch.error) ? String(payload.delivery_launch.error) : '';
          dlStatus.textContent = err
            ? `Sem dados do SQL Server: ${{err.slice(0, 180)}}`
            : 'Sem dados do SQL Server para este card.';
        }} else if (dlDaily.length === 0) {{
          dlStatus.textContent = 'Sem dados desse indicador no período selecionado.';
        }} else {{
          dlStatus.textContent = '';
        }}
      }}

      const baseLabels = [...new Set([
        ...Array.from(orderByDay.keys()),
        ...Array.from(loggerByDay.keys()),
      ])].sort();
      currentChartPeriodAnchor = end || maxIsoDate(baseLabels.map(dia => ({{ dia }})), 'dia') || maxDate;
      const chartDateRows = baseLabels.map(dia => ({{ dia }}));
      const visibleChartDateRows = filterChartDataByPeriod(chartDateRows, 'dia', selectedChartPeriod);
      const pedidoChartRows = filterChartDataByPeriod(
        baseLabels.map(dia => ({{
          dia,
          ent: orderByDay.get(dia)?.ent || 0,
          ins: orderByDay.get(dia)?.ins || 0,
        }})),
        'dia',
        selectedChartPeriod
      );
      const loggerChartRows = filterChartDataByPeriod(
        baseLabels.map(dia => ({{
          dia,
          ent: loggerByDay.get(dia)?.ent || 0,
          ins: loggerByDay.get(dia)?.ins || 0,
        }})),
        'dia',
        selectedChartPeriod
      );
      const h2Loggers = document.getElementById('h2_loggers');
      if (h2Loggers) {{
        const sensorText = sensorLabelMap[sensorMode] || sensorMode;
        const thermalText = thermalLabelMap[thermalMode] || thermalMode;
        h2Loggers.textContent = `Loggers por dia (${{sensorText}} | ${{thermalText}}) | entregues x inseridos`;
      }}
      updateChartPeriodButtons();
      updateChartPeriodState(visibleChartDateRows);
      drawGroupedBars(
        'chartPedidos',
        pedidoChartRows.map(x => x.dia),
        pedidoChartRows.map(x => x.ent),
        pedidoChartRows.map(x => x.ins),
        ['#2563eb', '#0ea5e9']
      );
      drawGroupedBars(
        'chartLoggers',
        loggerChartRows.map(x => x.dia),
        loggerChartRows.map(x => x.ent),
        loggerChartRows.map(x => x.ins),
        ['#1f9d7a', '#65c7ae']
      );

      const dlChartDaily = filterChartDataByPeriod(dlDaily, 'dia', selectedChartPeriod);
      const dlLabels = dlChartDaily.map(x => x.dia);
      drawSingleBars(
        'chartDeliveryLaunch',
        dlLabels,
        dlChartDaily.map(x => x.media_horas == null ? 0 : x.media_horas),
        '#1e40af',
        'Média horas para lançamento'
      );
      const chartDaily = filterChartDataByPeriod(daily, 'dia', selectedChartPeriod);
      const dailyLabels = chartDaily.map(x => x.dia);
      drawSingleBars(
        'chartLatency',
        dailyLabels,
        chartDaily.map(x => x.avg_horas_pedidos == null ? 0 : x.avg_horas_pedidos),
        '#2563eb',
        'Média horas para inserir pedidos'
      );
      drawSingleBars(
        'chartLatencyAres',
        dailyLabels,
        chartDaily.map(x => x.avg_horas_itens_ares == null ? 0 : x.avg_horas_itens_ares),
        '#1d4ed8',
        'Média horas ARES'
      );
      drawSingleBars(
        'chartLatencySyos',
        dailyLabels,
        chartDaily.map(x => x.avg_horas_itens_syos == null ? 0 : x.avg_horas_itens_syos),
        '#0f766e',
        'Média horas SYOS'
      );
      drawSingleBars(
        'chartLatencyShield',
        dailyLabels,
        chartDaily.map(x => x.avg_horas_itens_shield == null ? 0 : x.avg_horas_itens_shield),
        '#b45309',
        'Média horas Shield'
      );
      drawSingleBars(
        'chartLatencyWeb',
        dailyLabels,
        chartDaily.map(x => x.avg_horas_itens_sensor_web == null ? 0 : x.avg_horas_itens_sensor_web),
        '#475569',
        'Média horas Sensor web'
      );

      setSensorSections(sensorMode);
      if (filterState) {{
        const sensorLabel = sensorLabelMap[sensorMode] || 'Todos';
        const thermalLabel = thermalLabelMap[thermalMode] || 'Todos';
        filterState.textContent = `Filtro ativo: ${{formatDateBr(start)}} a ${{formatDateBr(end)}} | Sensor: ${{sensorLabel}} | Faixa térmica: ${{thermalLabel}} | Dias com dados: ${{baseLabels.length}}`;
      }}
    }}

    btnApply.addEventListener('click', applyFilters);
    btnReset.addEventListener('click', () => {{
      fltStart.value = minDate;
      fltEnd.value = maxDate;
      fltSensor.value = 'all';
      fltThermal.value = 'all';
      applyFilters();
    }});
    btnToday.addEventListener('click', () => {{
      const today = (payload.generated_at || '').slice(0, 10);
      if (today) {{
        fltStart.value = today;
        fltEnd.value = today;
      }}
      applyFilters();
    }});
    fltStart.addEventListener('change', applyFilters);
    fltEnd.addEventListener('change', applyFilters);
    fltSensor.addEventListener('change', applyFilters);
    fltThermal.addEventListener('change', applyFilters);
    for (const btn of chartPeriodBtns) {{
      btn.addEventListener('click', () => {{
        selectedChartPeriod = btn.dataset.chartPeriod || '30d';
        updateChartPeriodButtons();
        applyFilters();
      }});
    }}

    updateChartPeriodButtons();
    applyFilters();
  </script>
</body>
</html>
"""


def main() -> int:
    args = parse_args()
    start_date = (args.start_date or "").strip()
    end_date = (args.end_date or "").strip() or None
    out_path = build_output_path(start_date, end_date, args.output)
    try:
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
        payload = build_payload(
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
        html = render_html(payload)
        os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(html)
        print(f"arquivo={out_path}")
        print(f"dias={len(payload['daily'])}")
        print(f"pedidos_entregues_total={payload['totals']['pedidos_entregues_total']}")
        print(f"pedidos_inseridos_total={payload['totals']['pedidos_inseridos_total']}")
        print(f"loggers_entregues_total={payload['totals']['loggers_entregues_total']}")
        print(f"loggers_inseridos_total={payload['totals']['loggers_inseridos_total']}")
        return 0
    except Exception as exc:
        print(f"erro={exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())

