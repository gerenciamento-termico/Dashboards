# -*- coding: utf-8 -*-
"""Descobre a melhor fonte de 'quem sincronizou por ultimo' no dtbPortal."""
import sys
from pathlib import Path
sys.path.insert(0, ".")
from gerar_html_entregas_vs_sincronismo import load_env
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL

env = load_env(Path(".env"))
url = URL.create("postgresql+psycopg2", username=env["AURA_POSTGRES_USER"], password=env["AURA_POSTGRES_PASSWORD"],
                 host=env["AURA_POSTGRES_HOST"], port=int(env.get("AURA_POSTGRES_PORT") or 5432), database=env["AURA_POSTGRES_NAME"])
eng = create_engine(url, connect_args={"connect_timeout": 15})
with eng.connect() as c:
    print("=== colunas de vwtabelamovdataloggers com 'sinc'/'sync'/'usuario' ===")
    for r in c.execute(text("""
        SELECT column_name FROM information_schema.columns
        WHERE table_name = 'vwtabelamovdataloggers'
          AND (column_name ILIKE '%sinc%' OR column_name ILIKE '%sync%' OR column_name ILIKE '%usuario%')
        ORDER BY 1
    """)):
        print("  ", r[0])

    print("=== tipos de acao de movimentacao ===")
    for r in c.execute(text("SELECT id, text FROM vwtipos WHERE ds_tipo = 'tipoacaomovimentacao' ORDER BY 1")):
        print("  ", r[0], "|", r[1])

    print("=== exemplo: ultima acao de sync do A9202 (teste do caminho historicos) ===")
    for r in c.execute(text("""
        SELECT dh.dt_inclusao, tam.text,
               NULLIF(TRIM(COALESCE(u1.ds_nome,'') || ' ' || COALESCE(u1.ds_sobrenome,'')), '') AS usuario,
               NULLIF(TRIM(COALESCE(u2.ds_nome,'') || ' ' || COALESCE(u2.ds_sobrenome,'')), '') AS agente
        FROM public.tbddataloggerhistoricos dh
        INNER JOIN vwtipos tam ON tam.ds_tipo = 'tipoacaomovimentacao' AND dh.tp_acaomovimentacao = tam.id
        INNER JOIN tbdcaddataloggers d ON dh.id_datalogger = d.id_datalogger
        LEFT JOIN vwusuarios u1 ON u1.id_usuario = dh.id_usuarioinclusao
        LEFT JOIN vwusuarios u2 ON u2.id_usuario = dh.id_agenteusuarioinclusao
        WHERE REPLACE(REPLACE(UPPER(TRIM(d.ds_tag)), '-', ''), ' ', '') = 'A9202'
        ORDER BY dh.dt_inclusao DESC
        LIMIT 6
    """)):
        print("  ", r[0], "|", str(r[1])[:22], "| usuario=", r[2], "| agente=", r[3])
eng.dispose()
