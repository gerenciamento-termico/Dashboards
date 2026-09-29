# -*- coding: utf-8 -*-
"""Snapshot isolado Pedido -> Agente para consulta do Maestro (VPS Hetzner).

Fontes (mesma cadeia do indicador Entregas x Sincronismo):
  - vtc_stage.documentos          -> pedidos da janela + romaneios + loggers
  - dtbTransporte tbdMovimento    -> nr_Referencia -> ds_Agente (fonte principal)
  - dtbTransporte tbdMovimento    -> nr_Conhecimento = CT-e da VTC (+ chave em tbdLoteCTeMovimento)
  - REVERSA_DATALOGGERS.html      -> fallback por pedido

Saida: snapshot_pedido_agente/pedido_agente.json (+ .csv)
  - "pedido_agente": mapa simples pedido -> agente (compatibilidade Maestro)
  - "pedido_detalhe": pedido -> {agente, cte, serie, chave_cte, awb, romaneio, loggers, awbs[]}
Este script NAO publica em lugar nenhum; o envio a VPS e feito pela rotina
ATUALIZAR_PEDIDO_AGENTE_4X.ps1 (scp apos gerar).
"""
from __future__ import annotations

import csv
import json
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL

WORKSPACE = Path(__file__).resolve().parent
sys.path.insert(0, str(WORKSPACE))

from gerar_html_entregas_vs_sincronismo import (  # noqa: E402
    fetch_agentes_dtbtransporte,
    load_agentes,
    load_env,
    resolver_agente,
)
from gerar_html_retirada_aeroporto import fetch_ctes, fetch_stage_info  # noqa: E402

JANELA_DIAS = 60
OUT_DIR = WORKSPACE / "snapshot_pedido_agente"
OUT_JSON = OUT_DIR / "pedido_agente.json"
OUT_CSV = OUT_DIR / "pedido_agente.csv"


def fetch_pedidos_janela() -> list[str]:
    env = load_env(WORKSPACE / ".env.vtc_stage")
    if not env.get("VTC_STAGE_HOST"):
        raise RuntimeError("VTC_STAGE ausente (.env.vtc_stage)")
    url = URL.create(
        "postgresql+psycopg2",
        username=env["VTC_STAGE_USER"],
        password=env["VTC_STAGE_PASSWORD"],
        host=env["VTC_STAGE_HOST"],
        port=int(env.get("VTC_STAGE_PORT") or 5432),
        database=env["VTC_STAGE_NAME"],
    )
    engine = create_engine(url, pool_pre_ping=True)
    query = text(
        """
        SELECT DISTINCT TRIM(nr_pedido::text) AS pedido
        FROM vtc_stage.documentos
        WHERE NULLIF(TRIM(nr_pedido::text), '') IS NOT NULL
          AND (
                dt_coletaefetiva >= NOW() - INTERVAL ':d days'
             OR COALESCE(dt_entregaefetivaembarque, dt_entregaefetiva) >= NOW() - INTERVAL ':d days'
             OR dt_previstaentrega >= NOW() - INTERVAL ':d days'
          )
        """.replace(":d", str(JANELA_DIAS))
    )
    with engine.connect() as connection:
        pedidos = [r[0] for r in connection.execute(query)]
    engine.dispose()
    return sorted(pedidos)


def main() -> None:
    pedidos = fetch_pedidos_janela()
    print(f"vtc_stage: {len(pedidos)} pedidos na janela de {JANELA_DIAS} dias")

    transporte, stats = fetch_agentes_dtbtransporte(pedidos)
    print(
        f"dtbTransporte: {stats['mapeados']}/{stats['consultados']} pedidos com ds_Agente "
        f"({stats['linhas']} linhas)"
    )
    reversa = load_agentes()

    stage = fetch_stage_info(pedidos)
    print(f"vtc_stage: romaneios/loggers para {len(stage)} pedidos")
    ctes = fetch_ctes(pedidos)
    print(f"dtbPortal vwstmawbs: CT-e/AWB para {len(ctes)} pedidos")

    mapa: dict[str, str] = {}
    detalhe: dict[str, dict] = {}
    sem_agente = 0
    com_cte = 0
    for pedido in pedidos:
        agente = resolver_agente(pedido, transporte, reversa)
        mapa[pedido] = agente
        if agente == "SEM AGENTE":
            sem_agente += 1

        info = stage.get(pedido) or {}
        lst = sorted(
            ctes.get(pedido) or [],
            key=lambda x: (x["emissao"] is not None, x["emissao"]),
            reverse=True,
        )
        atual = lst[0] if lst else None
        if atual and atual["nr_cte"]:
            com_cte += 1
        detalhe[pedido] = {
            "agente": agente,
            "cte": atual["nr_cte"] if atual else "",
            "serie": atual["serie"] if atual else "",
            "chave_cte": atual["chave"] if atual else "",
            "awb": atual["awb"] if atual else "",
            "romaneio": info.get("rom", ""),
            "loggers": info.get("lg", 0),
            "awbs": [
                {
                    "awb": x["awb"],
                    "cte": x["nr_cte"],
                    "serie": x["serie"],
                    "chave_cte": x["chave"],
                    "emissao": x["emissao"].strftime("%Y-%m-%d %H:%M:%S") if x["emissao"] else "",
                }
                for x in lst
            ],
        }

    OUT_DIR.mkdir(exist_ok=True)
    gerado_em = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    payload = {
        "gerado_em": gerado_em,
        "janela_dias": JANELA_DIAS,
        "total_pedidos": len(mapa),
        "com_agente": len(mapa) - sem_agente,
        "sem_agente": sem_agente,
        "com_cte": com_cte,
        "fontes": [
            "vtc_stage.documentos (pedidos da janela + romaneio + loggers)",
            "dtbTransporte tbdMovimento (nr_Referencia -> ds_Agente)",
            "dtbTransporte tbdMovimento.nr_Conhecimento (CT-e da VTC, chave em tbdLoteCTeMovimento)",
            "REVERSA_DATALOGGERS.html (fallback)",
        ],
        "pedido_agente": mapa,
        "pedido_detalhe": detalhe,
    }
    tmp = OUT_JSON.with_suffix(".json.__new__")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(OUT_JSON)

    with open(OUT_CSV, "w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.writer(fh, delimiter=";")
        writer.writerow(["pedido", "agente", "cte", "serie", "chave_cte", "awb", "romaneio", "loggers"])
        for pedido in sorted(mapa):
            d = detalhe.get(pedido) or {}
            writer.writerow([
                pedido, mapa[pedido], d.get("cte", ""), d.get("serie", ""),
                d.get("chave_cte", ""), d.get("awb", ""), d.get("romaneio", ""), d.get("loggers", 0),
            ])

    print(
        f"SNAPSHOT OK: {len(mapa)} pedidos ({sem_agente} sem agente, {com_cte} com CT-e) em {gerado_em}"
    )
    print(f"JSON: {OUT_JSON}")
    print(f"CSV: {OUT_CSV}")


if __name__ == "__main__":
    main()
