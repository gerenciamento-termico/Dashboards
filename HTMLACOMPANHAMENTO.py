from __future__ import annotations

import os
import sys
import traceback
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from validar_dashboards_publicados import validate_acompanhamento_payload
from env_utils import load_env_file
from gerar_dashboard_entregas import (
    DEFAULT_DATABASE,
    DEFAULT_HOST,
    DEFAULT_PASSWORD,
    DEFAULT_PORT,
    DEFAULT_USER,
    build_payload,
    get_connection,
    query_data,
    render_html,
)


DEFAULT_START_DATE_HTML = "2025-12-04"
OUTPUT_FILE = Path(__file__).resolve().with_name("HTMLACOMPANHAMENTO.html")


def _db_args() -> SimpleNamespace:
    # Acompanhamento usa o banco principal do Aura: variaveis AURA_DB_*.
    return SimpleNamespace(
        host=os.getenv("AURA_DB_HOST", DEFAULT_HOST),
        database=os.getenv("AURA_DB_NAME", DEFAULT_DATABASE),
        user=os.getenv("AURA_DB_USER", DEFAULT_USER),
        password=os.getenv("AURA_DB_PASSWORD", DEFAULT_PASSWORD),
        port=int(os.getenv("AURA_DB_PORT", DEFAULT_PORT)),
    )


def _period() -> tuple[str, str]:
    start_date = os.getenv("AURA_START_DATE", DEFAULT_START_DATE_HTML).strip() or DEFAULT_START_DATE_HTML
    end_date = (os.getenv("AURA_END_DATE", "") or "").strip() or datetime.now().strftime("%Y-%m-%d")
    return start_date, end_date


def main() -> int:
    # Carrega .env sem sobrescrever variaveis ja definidas pelo Windows/Task Scheduler.
    load_env_file()

    try:
        args = _db_args()
        start_date, end_date = _period()
        print("[acompanhamento] Consultando banco principal Aura...")

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
        summary = validate_acompanhamento_payload(payload)

        html = render_html(payload)
        OUTPUT_FILE.write_text(html, encoding="utf-8")

        print(f"arquivo={OUTPUT_FILE}")
        print(f"periodo_desde={start_date}")
        print(f"periodo_ate={end_date}")
        print(f"dias={summary['dias']}")
        print(f"pedidos_entregues_total={summary['pedidos_entregues_total']}")
        print(f"pedidos_inseridos_total={summary['pedidos_inseridos_total']}")
        print(f"loggers_entregues_total={summary['loggers_entregues_total']}")
        print(f"loggers_inseridos_total={summary['loggers_inseridos_total']}")
        return 0
    except Exception:
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
