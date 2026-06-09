from __future__ import annotations

import json
import os
from datetime import datetime

import psycopg2

from env_utils import load_env_file


QUERIES = {
    "total_linhas": """
        SELECT COUNT(*) AS valor
        FROM vtc_stage.documentos
    """,
    "total_chaves_pedido_logger": """
        SELECT COUNT(DISTINCT nr_pedido::text || '|' || TRIM(ds_tag)) AS valor
        FROM vtc_stage.documentos
        WHERE NULLIF(TRIM(ds_tag), '') IS NOT NULL
    """,
    "loggers_em_transito": """
        SELECT COUNT(DISTINCT nr_pedido::text || '|' || TRIM(ds_tag)) AS valor
        FROM vtc_stage.documentos
        WHERE dt_coletaefetiva IS NOT NULL
          AND dt_entregaefetiva IS NULL
          AND NULLIF(TRIM(ds_tag), '') IS NOT NULL
    """,
    "loggers_entregues": """
        SELECT COUNT(DISTINCT nr_pedido::text || '|' || TRIM(ds_tag)) AS valor
        FROM vtc_stage.documentos
        WHERE dt_entregaefetiva IS NOT NULL
          AND NULLIF(TRIM(ds_tag), '') IS NOT NULL
    """,
    "loggers_entregues_20d": """
        SELECT COUNT(DISTINCT nr_pedido::text || '|' || TRIM(ds_tag)) AS valor
        FROM vtc_stage.documentos
        WHERE dt_entregaefetiva IS NOT NULL
          AND dt_entregaefetiva >= CURRENT_DATE - INTERVAL '20 days'
          AND NULLIF(TRIM(ds_tag), '') IS NOT NULL
    """,
    "duplicidades_pedido_logger_total": """
        SELECT COUNT(*) AS valor
        FROM (
            SELECT nr_pedido, TRIM(ds_tag) AS logger
            FROM vtc_stage.documentos
            WHERE NULLIF(TRIM(ds_tag), '') IS NOT NULL
            GROUP BY nr_pedido, TRIM(ds_tag)
            HAVING COUNT(*) > 1
        ) dup
    """,
}

DUPLICATES_TOP_QUERY = """
    SELECT nr_pedido, TRIM(ds_tag) AS logger, COUNT(*) AS qtd_linhas
    FROM vtc_stage.documentos
    WHERE NULLIF(TRIM(ds_tag), '') IS NOT NULL
    GROUP BY nr_pedido, TRIM(ds_tag)
    HAVING COUNT(*) > 1
    ORDER BY qtd_linhas DESC
    LIMIT 50
"""


def postgres_cfg() -> dict:
    load_env_file()
    cfg = {
        "host": os.getenv("AURA_POSTGRES_HOST", ""),
        "database": os.getenv("AURA_POSTGRES_NAME", ""),
        "user": os.getenv("AURA_POSTGRES_USER", ""),
        "password": os.getenv("AURA_POSTGRES_PASSWORD", ""),
        "port": int(os.getenv("AURA_POSTGRES_PORT", "5432")),
    }
    missing = [key for key, value in cfg.items() if key != "port" and not value]
    if missing:
        raise RuntimeError("Variaveis AURA_POSTGRES_* ausentes: " + ", ".join(missing))
    return cfg


def main() -> int:
    cfg = postgres_cfg()
    result: dict[str, object] = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "source": "vtc_stage.documentos",
        "dedupe_rule": "nr_pedido || '|' || trim(ds_tag)",
        "queries": {},
        "duplicidades_top50": [],
    }

    with psycopg2.connect(connect_timeout=20, **cfg) as conn:
        with conn.cursor() as cur:
            for name, sql in QUERIES.items():
                cur.execute(sql)
                result["queries"][name] = cur.fetchone()[0]

            cur.execute(DUPLICATES_TOP_QUERY)
            result["duplicidades_top50"] = [
                {"nr_pedido": row[0], "logger": row[1], "qtd_linhas": row[2]}
                for row in cur.fetchall()
            ]

    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
