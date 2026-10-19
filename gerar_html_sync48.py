# -*- coding: utf-8 -*-
"""
SINCRONISMO 48H - entregues sem sincronismo, corrida contra o relogio.

NAO consulta banco e NAO altera o indicador existente: le o ENTREGAS_VS_SINCRONISMO.html
recem-gerado pelo mesmo ciclo de 10 minutos e deriva a visao focada em 48 horas.

Regra: pendencias (st='P' cobranca agente, st='G' cobranca GRU) com entrega nas
ultimas 96h. Niveis de criticidade pela idade da entrega:
  N1 0-12h No prazo | N2 12-24h Atencao | N3 24-36h Alerta | N4 36-48h CRITICO | N5 >48h ESTOURADO
O nivel e recalculado AO VIVO no navegador (countdown por linha).

Publica SINCRONISMO_48H.html (rotina ATUALIZAR_ENTREGAS_VS_SINCRONISMO_5_MIN).
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parent


def fetch_dispositivos(pedidos: list[str]) -> dict[str, list]:
    """Todas as tags vinculadas aos pedidos no vtc_stage.documentos, classificadas
    pelo cadastro oficial do dtbPortal (ds_tipodatalogger); padrao da tag e fallback.

    Retorna {pedido: [[tag, tipo], ...]}. Lanca excecao em falha (tratada no main).
    """
    from sqlalchemy import bindparam, create_engine, text
    from sqlalchemy.engine import URL

    from gerar_html_entregas_vs_sincronismo import (
        fetch_tipos_cadastro,
        infer_tipo,
        load_env,
        norm_tag,
    )

    if not pedidos:
        return {}
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
    engine = create_engine(url, pool_pre_ping=True, connect_args={"connect_timeout": 15})
    query = text(
        """
        SELECT TRIM(nr_pedido::text) AS pedido,
               REPLACE(REPLACE(UPPER(TRIM(ds_tag)), '-', ''), ' ', '') AS tag
        FROM vtc_stage.documentos
        WHERE TRIM(nr_pedido::text) IN :peds
          AND NULLIF(TRIM(ds_tag), '') IS NOT NULL
        GROUP BY 1, 2
        """
    ).bindparams(bindparam("peds", expanding=True))
    pares: list[tuple[str, str]] = []
    with engine.connect() as connection:
        for i in range(0, len(pedidos), 500):
            chunk = pedidos[i : i + 500]
            for rec in connection.execute(query, {"peds": chunk}):
                tag = norm_tag(rec.tag)
                if tag:
                    pares.append((str(rec.pedido), tag))
    engine.dispose()

    # tipo oficial no cadastro dtbPortal; fallback: padrao da tag
    tipos = fetch_tipos_cadastro([t for _, t in pares])
    out: dict[str, list] = {}
    for pedido, tag in pares:
        tipo = tipos.get(tag) or infer_tipo(tag)
        lst = out.setdefault(pedido, [])
        if not any(x[0] == tag for x in lst):
            lst.append([tag, tipo])
    return out
FONTE = WORKSPACE / "ENTREGAS_VS_SINCRONISMO.html"
OUT_HTML = WORKSPACE / "SINCRONISMO_48H.html"
JANELA_H = 96  # foco 0-48h + estourados ate 96h


def parse_dt(txt: str) -> datetime | None:
    txt = (txt or "").strip()
    if not txt:
        return None
    try:
        return datetime.strptime(txt, "%d/%m/%Y %H:%M")
    except ValueError:
        return None


def fetch_sync_users(tags: list[str]) -> dict[str, str]:
    """Nome de quem fez o ultimo sync por logger: user.name do documento mais
    recente em ares-prod.beacon_devices_logs (mesma informacao do portal).

    Retorna {tag: nome}. Lanca excecao em falha (tratada no main).
    """
    import os
    from concurrent.futures import ThreadPoolExecutor

    from pymongo import MongoClient

    from gerar_html_entregas_vs_sincronismo import (
        STAGE_DIR,
        _mongo_lookup_names,
        load_env,
        norm_tag,
    )

    if not tags:
        return {}
    for path in [STAGE_DIR / ".env.ares_mongo", STAGE_DIR / ".env", WORKSPACE / ".env"]:
        for key, value in load_env(path).items():
            os.environ.setdefault(key, value)
    uri = (os.getenv("ARES_MONGO_URI") or os.getenv("ARES_MONGODB_URI") or "").strip()
    if not uri:
        raise RuntimeError("ARES_MONGO_URI ausente")

    lookup = _mongo_lookup_names(sorted({norm_tag(t) for t in tags if norm_tag(t)}))
    client = MongoClient(uri, serverSelectionTimeoutMS=20000, connectTimeoutMS=20000)
    try:
        db = client[os.getenv("ARES_MONGO_DB") or "ares-prod"]
        tag_mac: dict[str, str] = {}
        for i in range(0, len(lookup), 2000):
            for doc in db["beacon_devices"].find(
                {"name": {"$in": lookup[i : i + 2000]}}, {"name": 1, "mac": 1}
            ):
                tag = norm_tag(doc.get("name"))
                mac = str(doc.get("mac") or "").strip()
                if tag and mac:
                    tag_mac[tag] = mac

        logs = db["beacon_devices_logs"]

        def _ultimo_usuario(item: tuple[str, str]) -> tuple[str, str]:
            tag, mac = item
            doc = logs.find_one(
                {"mac": mac}, {"user.name": 1}, sort=[("createdAt", -1)]
            )
            nome = ""
            if doc and isinstance(doc.get("user"), dict):
                nome = str(doc["user"].get("name") or "").strip()
            return tag, nome

        out: dict[str, str] = {}
        with ThreadPoolExecutor(max_workers=12) as pool:
            for tag, nome in pool.map(_ultimo_usuario, tag_mac.items()):
                if nome:
                    out[tag] = nome
        return out
    finally:
        client.close()


def main() -> None:
    if not FONTE.exists():
        raise RuntimeError(f"fonte ausente: {FONTE.name}")
    html = FONTE.read_text(encoding="utf-8")
    m = re.search(r"const ROWS\s*=\s*(\[.*?\]);", html, re.DOTALL)
    if not m:
        raise RuntimeError("seed ROWS nao encontrado na fonte")
    rows = json.loads(m.group(1).replace("NaN", "null"))
    fonte_em = datetime.fromtimestamp(FONTE.stat().st_mtime)
    agora = datetime.now()

    pend = [r for r in rows if r.get("st") in ("P", "G")]
    saida = []
    antigos = 0
    for r in pend:
        ent = parse_dt(r.get("e")) or parse_dt(r.get("emb"))
        if ent is None:
            continue
        idade_h = (agora - ent).total_seconds() / 3600.0
        velho = 1 if idade_h > JANELA_H else 0
        if velho:
            antigos += 1
        # antigos (>96h) entram no seed com vel=1: ficam fora da aba Geral,
        # mas aparecem na aba Rodoviario quando tiverem ocorrencia rodoviaria
        saida.append({
            "p": str(r.get("p") or ""),
            "ro": str(r.get("ro") or ""),
            "l": str(r.get("l") or ""),
            "uf": str(r.get("uf") or ""),
            "ag": str(r.get("ag") or ""),
            "cli": str(r.get("cli") or ""),
            "ent": ent.strftime("%Y-%m-%dT%H:%M"),
            "cob": "GRU" if r.get("st") == "G" else "AGENTE",
            "s": str(r.get("s") or ""),
            "vel": velho,
            # modal da fonte: "Rodoviario" ou "MultiModal" -> R / M
            "mo": "R" if str(r.get("mo") or "").upper().startswith("RODOVI") else "M",
        })

    # base compacta de VERIFICACAO (fora da grade): sincronizados e erros operacionais
    # da janela da fonte, para a busca confirmar "ja sincronizou?" sem poluir a grade
    verificacao = []
    for r in rows:
        if r.get("st") not in ("S", "E"):
            continue
        ent = parse_dt(r.get("e")) or parse_dt(r.get("emb"))
        verificacao.append([
            str(r.get("p") or ""),
            str(r.get("l") or ""),
            str(r.get("s") or ""),
            ent.strftime("%d/%m %H:%M") if ent else "",
            str(r.get("uf") or ""),
            str(r.get("ag") or "")[:38],
            str(r.get("st") or ""),
        ])

    # mapa pedido -> todos os dataloggers vinculados na fonte (qualquer status),
    # usado no hover card do pedido: [logger, status, ultimo_sync]
    pedidos_na_pagina = {r["p"] for r in saida}
    logs: dict[str, list] = {}
    for r in rows:
        p = str(r.get("p") or "")
        l = str(r.get("l") or "")
        if not p or not l or p not in pedidos_na_pagina:
            continue
        lst = logs.setdefault(p, [])
        if not any(x[0] == l for x in lst):
            lst.append([l, str(r.get("st") or ""), str(r.get("s") or "")])

    # outros dispositivos (Syos/Shield/Elitech...) via vtc_stage + cadastro dtbPortal;
    # fail-safe: se o banco nao responder, o card mostra so os Ares da fonte
    dispositivos: dict[str, list] = {}
    try:
        dispositivos = fetch_dispositivos(sorted(pedidos_na_pagina))
        com_stage = sum(1 for p in pedidos_na_pagina if dispositivos.get(p))
        bate = sum(
            1 for r in saida
            if r["l"] and any(x[0] == r["l"].upper() for x in dispositivos.get(r["p"], []))
        )
        print(
            f"[dispositivos] stage cobre {com_stage}/{len(pedidos_na_pagina)} pedidos | "
            f"logger da pagina presente no stage em {bate}/{len(saida)} linhas"
        )
    except Exception as exc:  # noqa: BLE001
        print(f"[aviso] dispositivos vtc_stage indisponiveis ({type(exc).__name__}: {exc}); card mostrara so os Ares")

    # quem fez o ultimo sync (user.name do ultimo beacon_devices_logs por logger);
    # fail-safe: sem Mongo, a pagina sai sem o nome
    try:
        sync_users = fetch_sync_users([r["l"] for r in saida if r["l"]])
        atribuidos = 0
        for r in saida:
            nome = sync_users.get(r["l"].upper().replace("-", "").replace(" ", ""), "")
            r["su"] = nome
            if nome:
                atribuidos += 1
        print(f"[sync-user] nome do ultimo sync em {atribuidos}/{len(saida)} linhas")
    except Exception as exc:  # noqa: BLE001
        for r in saida:
            r.setdefault("su", "")
        print(f"[aviso] usuario do sync indisponivel ({type(exc).__name__}: {exc})")

    modelo = {
        "gerado_em": agora.strftime("%d/%m/%Y %H:%M"),
        "fonte_em": fonte_em.strftime("%d/%m/%Y %H:%M"),
        "rows": saida,
        "logs": logs,
        "devs": dispositivos,
        "ver": verificacao,
        "antigos": antigos,
        "total_pendencias": len(pend),
        "ufs": sorted({r["uf"] for r in saida if r["uf"]}),
        "bases": sorted({r["ag"] for r in saida if r["ag"]}),
    }
    write_html(modelo)
    print(
        f"OK SINCRONISMO_48H.html | pendencias_fonte={len(pend)} | na_janela_96h={len(saida)} | "
        f"antigos(>96h)={antigos} | fonte_gerada_em={modelo['fonte_em']}"
    )


HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta http-equiv="refresh" content="600">
<meta name="theme-color" content="#0f172a">
<meta name="description" content="Pendencias de sincronismo com contagem regressiva de 48h - VTC LOG Gerenciamento Termico">
<title>Sincronismo 48h &mdash; VTC LOG</title>
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'%3E%3Crect width='100' height='100' rx='22' fill='%230f172a'/%3E%3Ccircle cx='50' cy='56' r='28' fill='none' stroke='%23fff' stroke-width='7'/%3E%3Cpath d='M50 56 L50 38 M42 14 h16 M50 14 v10' stroke='%23fff' stroke-width='7' stroke-linecap='round'/%3E%3C/svg%3E">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.2/css/all.min.css">
<style>
:root{
  --bg:#f6f7f9; --card:#ffffff; --line:#e5e8ee; --line2:#eef0f4;
  --tx:#111827; --tx2:#6b7280; --tx3:#9aa3b2; --ink:#0f172a; --ink2:#1e293b;
  --blue:#2563eb; --blue2:#1d4ed8;
  --n1:#16a34a; --n2:#d97706; --n3:#ea580c; --n4:#dc2626; --n5:#991b1b;
  --shadow:0 1px 2px rgba(15,23,42,.05);
  --shadow2:0 1px 2px rgba(15,23,42,.05), 0 8px 24px rgba(15,23,42,.07);
}
*{margin:0;padding:0;box-sizing:border-box}
html{-webkit-font-smoothing:antialiased;text-rendering:optimizeLegibility}
body{font-family:'Inter',system-ui,-apple-system,sans-serif;background:var(--bg);color:var(--tx);min-height:100vh}

/* ---- barra superior ---- */
.topbar{background:#fff;border-bottom:1px solid var(--line);position:sticky;top:0;z-index:50}
.tb-in{max-width:1860px;margin:0 auto;padding:12px 24px;display:flex;align-items:center;justify-content:space-between;gap:14px;flex-wrap:wrap}
.brand{display:flex;align-items:center;gap:12px}
.hicon{width:40px;height:40px;min-width:40px;border-radius:10px;background:var(--ink);display:flex;align-items:center;justify-content:center;color:#fff;font-size:.95rem}
h1{font-size:1.04rem;font-weight:700;letter-spacing:-.2px;line-height:1.2}
h1 small{display:block;font-size:.66rem;font-weight:500;color:var(--tx2);text-transform:uppercase;letter-spacing:.8px;margin-top:2px}
.tb-meta{display:flex;align-items:center;gap:9px;flex-wrap:wrap}
.pill{display:inline-flex;align-items:center;gap:7px;font-size:.71rem;font-weight:500;color:var(--tx2);background:var(--bg);border:1px solid var(--line);border-radius:999px;padding:6px 12px;white-space:nowrap}
.pill b{color:var(--tx);font-weight:600}
.pill i{font-size:.68rem;color:var(--tx3)}
.livedot{width:7px;height:7px;border-radius:50%;background:var(--n1);animation:blink 2.2s ease-in-out infinite}
@keyframes blink{0%,100%{opacity:1}50%{opacity:.3}}

.wrap{max-width:1860px;margin:0 auto;padding:20px 24px 56px}
.sub{color:var(--tx2);font-size:.79rem;line-height:1.6;max-width:900px;margin-bottom:16px}
.sub b{color:var(--tx);font-weight:600}

/* ---- KPIs ---- */
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(178px,1fr));gap:12px;margin-bottom:14px}
.kpi{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px 16px 13px;cursor:pointer;transition:border-color .15s, box-shadow .15s;box-shadow:var(--shadow)}
.kpi:hover{border-color:#c9d2de;box-shadow:var(--shadow2)}
.kpi.ativo{border-color:var(--blue);box-shadow:0 0 0 3px rgba(37,99,235,.12)}
.kpi .lbl{display:flex;align-items:center;gap:7px;font-size:.63rem;font-weight:600;color:var(--tx2);text-transform:uppercase;letter-spacing:.5px;line-height:1.3}
.kpi .lbl::before{content:"";width:8px;height:8px;border-radius:3px;flex:none}
.kpi.k1 .lbl::before{background:var(--n1)}
.kpi.k2 .lbl::before{background:var(--n2)}
.kpi.k3 .lbl::before{background:var(--n3)}
.kpi.k4 .lbl::before{background:var(--n4)}
.kpi.k5 .lbl::before{background:var(--n5)}
.kpi .v{font-size:1.72rem;font-weight:700;letter-spacing:-.6px;margin-top:7px;font-variant-numeric:tabular-nums;line-height:1}
.kpi.k4 .v{color:var(--n4)} .kpi.k5 .v{color:var(--n5)}
.kpi.k4.pulse .lbl::before{animation:blink 1.5s ease-in-out infinite}

/* ---- filtros ---- */
.fcard{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:12px 14px;margin-bottom:12px;box-shadow:var(--shadow)}
.fbar{display:grid;grid-template-columns:1.3fr 1fr .5fr .95fr .9fr .8fr .9fr auto;gap:10px;align-items:end}
.fbar label{display:block;font-size:.6rem;color:var(--tx2);text-transform:uppercase;letter-spacing:.5px;margin-bottom:5px;font-weight:600}
.fbar input,.fbar select{width:100%;background:#fff;border:1px solid var(--line);border-radius:8px;color:var(--tx);padding:8px 10px;font-size:.8rem;font-family:inherit;max-width:100%;transition:border-color .12s, box-shadow .12s}
.fbar select{text-overflow:ellipsis}
.fbar input:focus,.fbar select:focus{outline:none;border-color:var(--blue);box-shadow:0 0 0 3px rgba(37,99,235,.1)}
.search{position:relative}
.search>i{position:absolute;left:11px;bottom:10px;color:var(--tx3);font-size:.72rem;pointer-events:none}
.search input{padding-left:30px}
.btn{display:inline-flex;align-items:center;gap:7px;background:var(--ink);border:1px solid var(--ink);border-radius:8px;color:#fff;padding:8px 14px;font-size:.76rem;font-weight:600;cursor:pointer;white-space:nowrap;font-family:inherit;transition:background .12s}
.btn:hover{background:var(--ink2)}
.btn.ghost{background:#fff;color:var(--tx2);border-color:var(--line)}
.btn.ghost:hover{border-color:#c9d2de;color:var(--tx)}

.cnt{font-size:.74rem;color:var(--tx2);margin:0 2px 10px}
.cnt b{color:var(--tx);font-weight:600}

/* ---- abas da grade (Geral / Rodoviario) ---- */
.gtabs{display:inline-flex;gap:4px;background:var(--card);border:1px solid var(--line);border-radius:11px;padding:4px;margin-bottom:12px;box-shadow:var(--shadow)}
.gtab{font:inherit;font-size:.76rem;font-weight:600;color:var(--tx2);background:none;border:none;border-radius:8px;padding:8px 16px;cursor:pointer;display:inline-flex;align-items:center;gap:8px;transition:all .13s}
.gtab:hover{color:var(--tx)}
.gtab.ativo{background:var(--ink);color:#fff}
.gt-n{font-size:.64rem;font-weight:700;background:var(--line2);color:var(--tx2);border-radius:999px;padding:2px 8px;font-variant-numeric:tabular-nums}
.gtab.ativo .gt-n{background:rgba(255,255,255,.18);color:#fff}

/* ---- grade ---- */
.tblwrap{background:var(--card);border:1px solid var(--line);border-radius:12px;overflow:auto;max-height:78vh;box-shadow:var(--shadow)}
table{width:100%;border-collapse:separate;border-spacing:0;font-size:.78rem;min-width:1280px;font-variant-numeric:tabular-nums}
th{position:sticky;top:0;background:#fff;color:var(--tx2);text-transform:uppercase;font-size:.61rem;font-weight:600;letter-spacing:.5px;padding:11px 10px;text-align:left;white-space:nowrap;z-index:2;border-bottom:1px solid var(--line);user-select:none}
th.sv{cursor:pointer;transition:color .12s}
th.sv:hover{color:var(--tx)}
th.sv::after{content:"\2195";margin-left:5px;color:var(--tx3);font-weight:400}
th.sv.asc::after{content:"\2191";color:var(--blue)}
th.sv.desc::after{content:"\2193";color:var(--blue)}
td{padding:9px 10px;border-bottom:1px solid var(--line2);white-space:nowrap;vertical-align:middle;background:#fff}
tbody tr:last-child td{border-bottom:none}
tbody tr:hover td{background:#f8fafc}
tr.lv1 td:first-child{box-shadow:inset 3px 0 0 var(--n1)}
tr.lv2 td:first-child{box-shadow:inset 3px 0 0 var(--n2)}
tr.lv3 td:first-child{box-shadow:inset 3px 0 0 var(--n3)}
tr.lv4 td:first-child{box-shadow:inset 3px 0 0 var(--n4)}
tr.lv5 td:first-child{box-shadow:inset 3px 0 0 var(--n5)}

.badge{display:inline-flex;align-items:center;gap:6px;font-size:.63rem;font-weight:600;border-radius:6px;padding:3px 8px;letter-spacing:.3px;border:1px solid var(--line);background:#fff;color:#475569}
.b1::before,.b2::before,.b3::before,.b4::before,.b5::before{content:"";width:7px;height:7px;border-radius:50%;flex:none}
.b1::before{background:var(--n1)}
.b2::before{background:var(--n2)}
.b3::before{background:var(--n3)}
.b4::before{background:var(--n4)}
.b5::before{background:#fff}
.b4{color:var(--n4);border-color:#fecaca;background:#fff}
.b5{color:#fff;background:var(--n5);border-color:var(--n5)}
.b-gru{color:#0369a1;border-color:#dbeafe;background:#f0f9ff}
.b-ag{color:#6d28d9;border-color:#e9e2fd;background:#f8f6ff}
.b-rod{color:#92400e;border-color:#fde8c8;background:#fffbeb}
.b-mm{color:#334155;border-color:var(--line);background:#f8fafc}
.agc{max-width:280px;overflow:hidden;text-overflow:ellipsis}
.resta{font-weight:700}
.resta.ok{color:var(--tx)} .resta.at{color:var(--tx)} .resta.al{color:var(--n3)} .resta.cr{color:var(--n4)} .resta.es{color:var(--n5)}
.pbar{width:120px;height:6px;background:var(--line2);border-radius:99px;overflow:hidden}
.pbar>div{height:100%;border-radius:99px}

/* ---- sinalizacao compartilhada ---- */
tr.flagged td{opacity:.5}
tr.flagged td:first-child, tr.flagged td.cobcell{opacity:1}
.b-cob{color:#1d4ed8;border-color:#bfdbfe;background:#eff6ff;cursor:pointer}
.b-cob:hover{background:#dbeafe}
.btn-flag{background:#fff;border:1px dashed #cbd5e1;border-radius:7px;color:var(--tx2);font-size:.62rem;font-weight:600;padding:4px 10px;cursor:pointer;font-family:inherit;letter-spacing:.3px;transition:all .12s}
.btn-flag:hover{border-color:var(--ink);border-style:solid;color:var(--ink)}
/* ---- hover card do pedido (ares pendente + outros dataloggers) ---- */
.pedcell{cursor:help}
.pedcell b{border-bottom:1px dashed #c3cbd8;padding-bottom:1px}
#hovercard{position:fixed;display:none;z-index:600;background:#fff;border:1px solid var(--line);border-radius:13px;box-shadow:0 14px 40px rgba(15,23,42,.18);padding:13px 15px;min-width:270px;max-width:340px;pointer-events:none;font-size:.76rem}
#hovercard .hc-t{font-weight:700;font-size:.8rem;margin-bottom:9px;color:var(--tx)}
#hovercard .hc-t small{color:var(--tx3);font-weight:500}
#hovercard .hc-ares{border:1px solid #fecaca;background:#fef7f7;border-radius:9px;padding:8px 10px;margin-bottom:8px}
#hovercard .hc-ares .l1{font-weight:700;color:var(--n4);font-size:.78rem;display:flex;align-items:center;gap:6px}
#hovercard .hc-ares .l2{color:var(--tx2);font-size:.68rem;margin-top:3px;line-height:1.5}
#hovercard .hc-sec{font-size:.6rem;font-weight:700;color:var(--tx3);text-transform:uppercase;letter-spacing:.5px;margin:9px 0 5px}
#hovercard .hc-it{display:flex;align-items:center;gap:7px;padding:3px 0;color:var(--tx)}
#hovercard .hc-it .pt{width:7px;height:7px;border-radius:50%;flex:none}
#hovercard .hc-it small{color:var(--tx3);margin-left:auto;padding-left:10px;white-space:nowrap}
#hovercard .hc-nada{color:var(--tx3);font-size:.7rem;padding:2px 0}

.obscell{cursor:pointer;max-width:240px}
.obscell:hover .obs-txt{color:var(--blue)}
.obs-txt{display:block;white-space:normal;line-height:1.35;font-size:.73rem;color:var(--tx);max-width:240px;font-weight:500}
.obs-meta{display:block;font-size:.61rem;color:var(--tx3);margin-top:2px}

/* ---- modal ---- */
.mo-ov{position:fixed;inset:0;background:rgba(16,24,40,.45);backdrop-filter:blur(4px);z-index:900;display:flex;align-items:center;justify-content:center;padding:18px;opacity:0;pointer-events:none;transition:opacity .18s ease}
.mo-ov.aberto{opacity:1;pointer-events:auto}
.mo-card{background:var(--card);border-radius:18px;width:100%;max-width:480px;box-shadow:0 24px 70px rgba(16,24,40,.28);transform:translateY(14px) scale(.98);transition:transform .22s cubic-bezier(.2,.9,.3,1.2);overflow:hidden}
.mo-ov.aberto .mo-card{transform:translateY(0) scale(1)}
.mo-head{display:flex;align-items:center;gap:13px;padding:18px 20px 14px;border-bottom:1px solid var(--line)}
.mo-ico{width:42px;height:42px;border-radius:12px;background:var(--ink);color:#fff;display:flex;align-items:center;justify-content:center;font-size:1rem;flex:none}
.mo-tit{font-weight:800;font-size:.95rem;color:var(--tx)}
.mo-sub{font-size:.72rem;color:var(--tx2);margin-top:2px}
.mo-x{margin-left:auto;background:none;border:none;color:var(--tx2);font-size:1rem;cursor:pointer;width:32px;height:32px;border-radius:9px;flex:none}
.mo-x:hover{background:var(--bg);color:var(--tx)}
.mo-body{padding:16px 20px 6px}
.mo-lbl{display:block;font-size:.66rem;font-weight:800;color:var(--tx2);text-transform:uppercase;letter-spacing:.6px;margin:10px 0 6px}
.mo-lbl small{text-transform:none;letter-spacing:0;font-weight:600;opacity:.8}
.mo-inp{width:100%;box-sizing:border-box;border:1.5px solid var(--line);border-radius:11px;padding:11px 13px;font:inherit;font-size:.86rem;color:var(--tx);background:var(--bg);outline:none;transition:border-color .15s, box-shadow .15s}
.mo-inp:focus{border-color:var(--blue);box-shadow:0 0 0 3px rgba(37,99,235,.14);background:#fff}
.mo-ta{resize:vertical;min-height:76px;line-height:1.45}
.mo-cnt{text-align:right;font-size:.64rem;color:var(--tx2);margin-top:4px}
.mo-chips{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:8px}
.mo-chip{border:1px solid var(--line);background:var(--bg);color:var(--tx2);font-size:.68rem;font-weight:700;padding:5px 10px;border-radius:999px;cursor:pointer;transition:all .12s}
.mo-chip:hover{border-color:var(--blue);color:var(--blue);background:#eff6ff}
.mo-foot{display:flex;align-items:center;gap:9px;padding:14px 20px 18px}
.mo-btn{font:inherit;font-size:.8rem;font-weight:700;padding:10px 18px;border-radius:11px;cursor:pointer;border:1.5px solid transparent;transition:all .13s}
.mo-primary{background:var(--ink);color:#fff}
.mo-primary:hover{background:var(--ink2)}
.mo-primary:disabled{opacity:.45;cursor:not-allowed;box-shadow:none}
.mo-ghost{background:none;border-color:var(--line);color:var(--tx2)}
.mo-ghost:hover{border-color:var(--tx2);color:var(--tx)}
.mo-danger{background:none;border-color:#fecaca;color:#dc2626}
.mo-danger:hover{background:#fef2f2;border-color:#dc2626}

/* ---- login ---- */
.lg-ov{display:none;position:fixed;inset:0;background:var(--bg);z-index:950;align-items:center;justify-content:center;padding:18px}
body.bloqueada .lg-ov{display:flex}
body.bloqueada .topbar, body.bloqueada .wrap{filter:blur(7px);pointer-events:none;user-select:none}
.lg-card{background:#fff;border:1px solid var(--line);border-radius:18px;box-shadow:var(--shadow2);width:100%;max-width:380px;padding:30px 28px 26px}
.lg-brand{display:flex;align-items:center;gap:12px;margin-bottom:22px}
.lg-tit{font-size:1.05rem;font-weight:700;letter-spacing:-.2px;margin-bottom:4px}
.lg-sub{font-size:.76rem;color:var(--tx2);line-height:1.55;margin-bottom:16px}
.lg-err{font-size:.73rem;color:#dc2626;background:#fef2f2;border:1px solid #fecaca;border-radius:9px;padding:9px 12px;margin-top:12px;display:none}
.lg-err.on{display:block}
.lg-foot{font-size:.65rem;color:var(--tx3);text-align:center;margin-top:16px;line-height:1.5}
.tb-user{display:flex;align-items:center;gap:9px}
.tb-user .pill b{font-weight:600}
.pill-user{cursor:pointer;transition:border-color .12s, box-shadow .12s}
.pill-user:hover{border-color:#c9d2de;box-shadow:var(--shadow)}

/* ---- area do usuario ---- */
.up-card{max-width:580px}
.up-tabs{display:flex;gap:2px;padding:0 20px;border-bottom:1px solid var(--line)}
.up-tab{font:inherit;font-size:.73rem;font-weight:600;color:var(--tx2);background:none;border:none;border-bottom:2px solid transparent;padding:11px 12px;cursor:pointer;transition:color .12s}
.up-tab:hover{color:var(--tx)}
.up-tab.ativo{color:var(--ink);border-bottom-color:var(--ink)}
.up-pane{padding:4px 0 8px}
.up-msg{font-size:.72rem;border-radius:9px;padding:8px 12px;margin-top:12px;display:none}
.up-msg.ok{display:block;background:#f0fdf4;border:1px solid #bbf7d0;color:#15803d}
.up-msg.erro{display:block;background:#fef2f2;border:1px solid #fecaca;color:#dc2626}
.up-lista{display:flex;flex-direction:column;gap:8px;max-height:300px;overflow:auto;margin-top:6px}
.up-item{border:1px solid var(--line);border-radius:10px;padding:10px 12px;font-size:.76rem;background:#fff}
.up-item .t{font-weight:600;color:var(--tx)}
.up-item .x{color:var(--tx);margin-top:4px;line-height:1.4}
.up-item .m{font-size:.66rem;color:var(--tx3);margin-top:3px}
.up-item .acao{margin-top:8px;display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.up-mini{font:inherit;font-size:.68rem;font-weight:600;border:1px solid var(--line);background:#fff;color:var(--tx2);border-radius:7px;padding:5px 11px;cursor:pointer;transition:all .12s}
.up-mini:hover{border-color:var(--ink);color:var(--ink)}
.up-sec{font-size:.65rem;font-weight:700;color:var(--tx2);text-transform:uppercase;letter-spacing:.6px;margin:16px 0 4px}
.up-vazio{font-size:.75rem;color:var(--tx2);padding:14px 4px}
.oco-lock{color:var(--tx3);font-size:.6rem;margin-left:5px}

/* ---- painel de verificacao (busca em sincronizados, fora da grade) ---- */
#verbox{background:#f0fdf4;border:1px solid #bbf7d0;border-radius:12px;padding:12px 16px;margin-bottom:12px}
#verbox .vb-t{font-size:.7rem;font-weight:700;color:#15803d;text-transform:uppercase;letter-spacing:.5px;margin-bottom:8px}
#verbox .vb-t i{margin-right:6px}
#verbox .vb-it{display:flex;align-items:center;gap:10px;flex-wrap:wrap;padding:5px 0;font-size:.78rem;color:var(--tx);border-top:1px dashed #d1e9d9}
#verbox .vb-it:first-of-type{border-top:none}
#verbox .vb-it small{color:var(--tx2)}
#verbox.vazio{background:#fffbeb;border-color:#fde8c8}
#verbox.vazio .vb-t{color:#92400e}
.btn-sair{display:inline-flex;align-items:center;gap:6px;background:#fff;border:1px solid var(--line);border-radius:999px;color:var(--tx2);padding:6px 13px;font-size:.71rem;font-weight:600;cursor:pointer;font-family:inherit;transition:all .12s}
.btn-sair:hover{border-color:#fecaca;color:#dc2626;background:#fef2f2}

.cards{display:none}

/* ---- modo compacto: a grade inteira cabe em monitores menores ---- */
@media(max-width:1680px){
  #gridWrap table{min-width:0;font-size:.73rem}
  #gridWrap th, #gridWrap td{padding:7px 6px}
  #gridWrap .pbar{width:64px}
  #gridWrap .agc{max-width:150px}
  #gridWrap .obs-txt{max-width:170px}
  #gridWrap .badge{font-size:.6rem;padding:3px 6px;gap:4px}
  .mtx{display:none} /* modal vira so o icone (texto completo no title e no CSV) */
  /* "Horas desde entrega" e redundante com "Resta p/ 48h" (48h - resta) */
  #gridWrap th:nth-child(8), #gridWrap td:nth-child(8){display:none}
}
@media(max-width:1440px){
  /* barra de progresso (a urgencia ja esta em "Resta p/ 48h") e romaneio saem */
  #gridWrap th:nth-child(10), #gridWrap td:nth-child(10){display:none}
  #gridWrap th:nth-child(3), #gridWrap td:nth-child(3){display:none}
  #gridWrap .obs-txt{max-width:140px}
  #gridWrap .agc{max-width:120px}
  #gridWrap .btn-flag{padding:3px 7px;font-size:.58rem}
}
@media(max-width:1200px){.fbar{grid-template-columns:1fr 1fr 1fr}}
@media(max-width:860px){
  .wrap{padding:14px 10px 50px}
  .tb-in{padding:10px 12px}
  .tb-meta{display:none}
  h1{font-size:.98rem} .sub{display:none}
  .tblwrap{display:none}
  .fbar{grid-template-columns:1fr 1fr}
  .cards{display:grid;grid-template-columns:1fr;gap:10px}
  .card{position:relative;background:var(--card);border:1px solid var(--line);border-radius:14px;padding:14px;box-shadow:var(--shadow);overflow:hidden}
  .card::before{content:"";position:absolute;top:0;left:0;bottom:0;width:4px;background:var(--n1)}
  .card.lv2::before{background:var(--n2)} .card.lv3::before{background:var(--n3)}
  .card.lv4::before{background:var(--n4)} .card.lv5::before{background:var(--n5)}
  .card .topo{display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;gap:8px;flex-wrap:wrap}
  .card .ped{font-weight:900;font-size:1.02rem}
  .card .kv{display:grid;grid-template-columns:auto 1fr;gap:3px 10px;font-size:.78rem}
  .card .kv .k{color:var(--tx2);font-size:.65rem;text-transform:uppercase;letter-spacing:.4px;padding-top:2px;font-weight:700}
}
footer{margin-top:26px;text-align:center;color:var(--tx2);font-size:.7rem;line-height:1.7}
</style>
</head>
<body class="bloqueada">
<div class="topbar">
  <div class="tb-in">
    <div class="brand">
      <div class="hicon"><i class="fa-solid fa-stopwatch"></i></div>
      <h1>Sincronismo 48h<small>VTC LOG &middot; Gerenciamento T&eacute;rmico</small></h1>
    </div>
    <div class="tb-meta">
      <span class="pill"><span class="livedot"></span> Ao vivo &middot; recalculado a cada 30s</span>
      <span class="pill"><i class="fa-regular fa-clock"></i> Atualizado <b>__GERADO__</b> &middot; ciclo 10 min</span>
      <span class="pill"><i class="fa-solid fa-database"></i> Fonte __FONTE_EM__</span>
    </div>
    <div class="tb-user">
      <span class="pill pill-user" onclick="abrirPainelUsuario()" title="&Aacute;rea do usu&aacute;rio"><i class="fa-solid fa-circle-user"></i> <b id="quem-nome"></b> <i class="fa-solid fa-chevron-down" style="font-size:.58rem"></i></span>
      <button class="btn-sair" onclick="sair()" title="Encerrar sess&atilde;o"><i class="fa-solid fa-right-from-bracket"></i> Sair</button>
    </div>
  </div>
</div>
<div class="wrap">

<div class="sub">Pedidos <b>entregues e ainda sem sincronismo</b>, com contagem regressiva ao vivo: nenhuma pend&ecirc;ncia
pode passar de <b>48 horas</b>. Derivado do indicador Entregas &times; Sincronismo (mesmas regras), janela de 96h.
Clique no cabe&ccedil;alho de qualquer coluna para ordenar a grade.</div>

<div class="kpis" id="kpis">
  <div class="kpi k1" data-n="1"><div class="lbl">No prazo &middot; 0&ndash;12h</div><div class="v" id="k1">0</div></div>
  <div class="kpi k2" data-n="2"><div class="lbl">Aten&ccedil;&atilde;o &middot; 12&ndash;24h</div><div class="v" id="k2">0</div></div>
  <div class="kpi k3" data-n="3"><div class="lbl">Alerta &middot; 24&ndash;36h</div><div class="v" id="k3">0</div></div>
  <div class="kpi k4 pulse" data-n="4"><div class="lbl">Cr&iacute;tico &middot; 36&ndash;48h</div><div class="v" id="k4">0</div></div>
  <div class="kpi k5" data-n="5"><div class="lbl">Estourado &middot; &gt;48h</div><div class="v" id="k5">0</div></div>
</div>

<div class="fcard">
<div class="fbar">
  <div class="search"><label>Busca (pedido / logger / romaneio / base / ocorr&ecirc;ncia)</label><i class="fa-solid fa-magnifying-glass"></i><input id="f-busca" type="text" placeholder="Ex.: 572344, A8841, RODOTEC..."></div>
  <div><label>Base / Agente</label><select id="f-base"><option value="">Todas</option>__BASES__</select></div>
  <div><label>UF</label><select id="f-uf"><option value="">Todas</option>__UFS__</select></div>
  <div><label>Regi&atilde;o</label><select id="f-reg"><option value="">Todas</option><option value="COSE">Centro-Oeste e Sudeste</option><option value="NOSU">Norte e Sul</option><option value="NE">Nordeste</option></select></div>
  <div><label>N&iacute;vel</label><select id="f-nivel"><option value="">Todos</option><option value="1">No prazo (0-12h)</option><option value="2">Aten&ccedil;&atilde;o (12-24h)</option><option value="3">Alerta (24-36h)</option><option value="4">Cr&iacute;tico (36-48h)</option><option value="5">Estourado (&gt;48h)</option></select></div>
  <div><label>Cobran&ccedil;a</label><select id="f-cob"><option value="">Todas</option><option value="AGENTE">Agente / Base</option><option value="GRU">GRU</option></select></div>
  <div><label>Sinaliza&ccedil;&atilde;o</label><select id="f-flag"><option value="">Todas</option><option value="N">N&atilde;o cobradas</option><option value="S">J&aacute; cobradas</option><option value="O">Com ocorr&ecirc;ncia</option><option value="X">Sem ocorr&ecirc;ncia</option></select></div>
  <div style="display:flex;gap:8px"><button class="btn ghost" onclick="limpar()"><i class="fa-solid fa-eraser"></i> Limpar</button><button class="btn" onclick="baixarCsv()"><i class="fa-solid fa-file-csv"></i> CSV</button></div>
</div>
</div>

<div id="verbox" style="display:none"></div>

<div class="gtabs">
  <button class="gtab ativo" data-tab="geral" onclick="setTab('geral')"><i class="fa-solid fa-plane-up"></i> Geral <span class="gt-n" id="gt-geral">0</span></button>
  <button class="gtab" data-tab="rod" onclick="setTab('rod')" title="Pendencias com modal Rodoviario na fonte ou com ocorrencia de rodoviario paletizado, em qualquer idade"><i class="fa-solid fa-truck"></i> Rodovi&aacute;rio <span class="gt-n" id="gt-rod">0</span></button>
  <button class="gtab" data-tab="oco" onclick="setTab('oco')" title="Historico de todas as ocorrencias registradas, inclusive de pendencias ja sincronizadas (retencao de 30 dias)"><i class="fa-solid fa-clipboard-list"></i> Ocorr&ecirc;ncias <span class="gt-n" id="gt-oco">0</span></button>
</div>

<div class="cnt"><span id="cnt"></span> &nbsp;&middot;&nbsp; <span style="color:var(--blue)"><i class="fa-solid fa-clock"></i> contagem regressiva recalculada ao vivo (a cada 30s) &middot; pend&ecirc;ncias com mais de 96h: <b>__ANTIGOS__</b> (ver indicador principal)</span></div>

<div class="tblwrap" id="ocoWrap" style="display:none">
<table style="min-width:1100px">
  <thead><tr>
    <th>Situa&ccedil;&atilde;o</th><th>Pedido</th><th>Logger</th><th>Modal</th><th>Entrega</th><th>UF</th><th>Base / Agente</th>
    <th>Ocorr&ecirc;ncia</th><th>Registrada por</th><th>Quando</th><th></th>
  </tr></thead>
  <tbody id="tb-oco"></tbody>
</table>
</div>

<div class="tblwrap" id="gridWrap">
<table>
  <thead><tr>
    <th class="sv" data-k="nivel">N&iacute;vel</th><th class="sv" data-k="p" title="Passe o mouse sobre o pedido para ver o logger Ares pendente, quem fez o ultimo sync e os demais dispositivos vinculados">Pedido</th><th class="sv" data-k="ro">Romaneio</th><th class="sv" data-k="uf">UF</th><th class="sv" data-k="ag">Base / Agente</th>
    <th class="sv" data-k="mo" title="Modal do pedido na fonte: Rodoviario ou MultiModal">Modal</th>
    <th class="sv" data-k="ent">Entrega</th><th class="sv" data-k="h" title="Horas desde a entrega">Desde a entrega</th><th class="sv asc" data-k="resta">Resta p/ 48h</th><th title="Progresso de 0 a 48h">Progresso</th><th class="sv" data-k="s">&Uacute;ltimo sync</th><th class="sv" data-k="cob">Cobran&ccedil;a</th><th title="Quadro compartilhado: a marcacao aparece para todos que abrirem a pagina">Sinaliza&ccedil;&atilde;o</th><th title="Ocorrencia compartilhada: por que nao sincronizou, onde esta o logger, etc. So quem registrou pode alterar">Ocorr&ecirc;ncia</th>
  </tr></thead>
  <tbody id="tb"></tbody>
</table>
</div>
<div class="cards" id="cards"></div>

<footer>
  VTC LOG &middot; Gerenciamento T&eacute;rmico &mdash; Sincronismo 48h &middot; derivado do indicador Entregas &times; Sincronismo (mesmas regras; nada foi alterado nele)<br>
  N&iacute;veis pela idade da entrega: 0&ndash;12h no prazo &middot; 12&ndash;24h aten&ccedil;&atilde;o &middot; 24&ndash;36h alerta &middot; <b style="color:var(--n4)">36&ndash;48h cr&iacute;tico</b> &middot; <b style="color:var(--n5)">&gt;48h estourado</b> &middot; Sincronizou? Sai sozinho na pr&oacute;xima atualiza&ccedil;&atilde;o (10 min)<br>
  Sinaliza&ccedil;&atilde;o "cobrado" e ocorr&ecirc;ncias em quadro compartilhado: aparecem para todos, com nome e hor&aacute;rio de quem registrou; s&oacute; o autor pode desfazer a pr&oacute;pria sinaliza&ccedil;&atilde;o ou alterar a pr&oacute;pria ocorr&ecirc;ncia; a sinaliza&ccedil;&atilde;o sai sozinha quando a pend&ecirc;ncia sincroniza e a ocorr&ecirc;ncia fica no hist&oacute;rico por 30 dias
</footer>
</div>

<div id="hovercard"></div>

<div class="lg-ov" id="lg-ov">
  <div class="lg-card">
    <div class="lg-brand">
      <div class="hicon"><i class="fa-solid fa-stopwatch"></i></div>
      <h1 style="font-size:.98rem">Sincronismo 48h<small>VTC LOG &middot; Gerenciamento T&eacute;rmico</small></h1>
    </div>
    <div class="lg-tit">Acesso restrito</div>
    <div class="lg-sub">Entre com seu usu&aacute;rio para visualizar as pend&ecirc;ncias e registrar cobran&ccedil;as e observa&ccedil;&otilde;es em nome da sua conta.</div>
    <label class="mo-lbl" for="lg-user">Usu&aacute;rio</label>
    <input id="lg-user" class="mo-inp" placeholder="nome.sobrenome" autocomplete="username" autocapitalize="none" spellcheck="false">
    <label class="mo-lbl" for="lg-pass">Senha</label>
    <input id="lg-pass" type="password" class="mo-inp" placeholder="&bull;&bull;&bull;&bull;&bull;&bull;&bull;&bull;" autocomplete="current-password">
    <div class="lg-err" id="lg-err"></div>
    <button class="mo-btn mo-primary" id="lg-btn" onclick="fazerLogin()" style="width:100%;margin-top:16px;padding:12px"><i class="fa-solid fa-right-to-bracket"></i> Entrar</button>
    <div class="lg-foot">Quadro compartilhado do Gerenciamento T&eacute;rmico &middot; toda marca&ccedil;&atilde;o fica registrada com o nome de quem fez</div>
  </div>
</div>

<div class="mo-ov" id="up-ov">
  <div class="mo-card up-card" role="dialog" aria-modal="true">
    <div class="mo-head">
      <span class="mo-ico"><i class="fa-solid fa-circle-user"></i></span>
      <div>
        <div class="mo-tit">&Aacute;rea do usu&aacute;rio</div>
        <div class="mo-sub" id="up-sub"></div>
      </div>
      <button class="mo-x" onclick="fecharPainel()" title="Fechar"><i class="fa-solid fa-xmark"></i></button>
    </div>
    <div class="up-tabs">
      <button class="up-tab ativo" data-t="conta" onclick="upTab('conta')"><i class="fa-solid fa-key"></i> Minha conta</button>
      <button class="up-tab" data-t="oco" onclick="upTab('oco')"><i class="fa-solid fa-clipboard-list"></i> Minhas ocorr&ecirc;ncias</button>
      <button class="up-tab" data-t="usuarios" onclick="upTab('usuarios')"><i class="fa-solid fa-users-gear"></i> Usu&aacute;rios</button>
    </div>
    <div class="mo-body">
      <div class="up-pane" id="up-conta">
        <label class="mo-lbl" for="up-s0">Senha atual</label>
        <input id="up-s0" type="password" class="mo-inp" autocomplete="current-password">
        <label class="mo-lbl" for="up-s1">Nova senha <small>&middot; m&iacute;nimo 6 caracteres</small></label>
        <input id="up-s1" type="password" class="mo-inp" autocomplete="new-password">
        <label class="mo-lbl" for="up-s2">Confirmar nova senha</label>
        <input id="up-s2" type="password" class="mo-inp" autocomplete="new-password">
        <div class="up-msg" id="up-msg-conta"></div>
        <button class="mo-btn mo-primary" onclick="trocarSenha()" style="margin-top:14px"><i class="fa-solid fa-check"></i> Salvar nova senha</button>
      </div>
      <div class="up-pane" id="up-oco" hidden>
        <div class="up-sec">Ocorr&ecirc;ncias registradas por voc&ecirc; nas pend&ecirc;ncias atuais</div>
        <div class="up-lista" id="up-oco-lista"></div>
      </div>
      <div class="up-pane" id="up-usuarios" hidden>
        <div class="up-sec">Usu&aacute;rios cadastrados <small style="text-transform:none;letter-spacing:0">&middot; cada usu&aacute;rio altera somente a pr&oacute;pria senha</small></div>
        <div class="up-lista" id="up-user-lista"></div>
        <div class="up-sec" style="margin-top:18px">Cadastrar novo usu&aacute;rio</div>
        <label class="mo-lbl" for="up-nu-login">Login <small>&middot; padr&atilde;o nome.sobrenome, min&uacute;sculas</small></label>
        <input id="up-nu-login" class="mo-inp" placeholder="nome.sobrenome" autocapitalize="none" spellcheck="false">
        <label class="mo-lbl" for="up-nu-nome">Nome de exibi&ccedil;&atilde;o</label>
        <input id="up-nu-nome" class="mo-inp" placeholder="Ex.: Maria Silva">
        <label class="mo-lbl" for="up-nu-senha">Senha inicial <small>&middot; m&iacute;nimo 6 caracteres</small></label>
        <input id="up-nu-senha" type="password" class="mo-inp" autocomplete="new-password">
        <div class="up-msg" id="up-msg-user"></div>
        <button class="mo-btn mo-primary" onclick="cadastrarUsuario()" style="margin-top:14px"><i class="fa-solid fa-user-plus"></i> Cadastrar</button>
      </div>
    </div>
    <div class="mo-foot" style="padding-top:4px">
      <span style="flex:1"></span>
      <button class="mo-btn mo-ghost" onclick="fecharPainel()">Fechar</button>
    </div>
  </div>
</div>

<div class="mo-ov" id="mo-ov">
  <div class="mo-card" role="dialog" aria-modal="true" aria-labelledby="mo-tit">
    <div class="mo-head">
      <span class="mo-ico" id="mo-ico"><i class="fa-solid fa-pen-to-square"></i></span>
      <div>
        <div class="mo-tit" id="mo-tit">Observa&ccedil;&atilde;o</div>
        <div class="mo-sub" id="mo-sub"></div>
      </div>
      <button class="mo-x" onclick="fecharModal(null)" title="Fechar"><i class="fa-solid fa-xmark"></i></button>
    </div>
    <div class="mo-body">
      <div id="mo-obs-wrap">
        <label class="mo-lbl" for="mo-obs">Motivo / ocorr&ecirc;ncia <small>&middot; por que n&atilde;o sincronizou, onde est&aacute; o logger...</small></label>
        <div class="mo-chips" id="mo-chips"></div>
        <textarea id="mo-obs" class="mo-inp mo-ta" maxlength="200" placeholder="Descreva o motivo ou toque numa sugest&atilde;o acima"></textarea>
        <div class="mo-cnt"><span id="mo-cnt">0</span>/200 &middot; registrado como <b id="mo-quem"></b></div>
      </div>
    </div>
    <div class="mo-foot">
      <button class="mo-btn mo-danger" id="mo-remover" onclick="fecharModal({remover:true})"><i class="fa-solid fa-trash-can"></i> Remover</button>
      <span style="flex:1"></span>
      <button class="mo-btn mo-ghost" onclick="fecharModal(null)">Cancelar</button>
      <button class="mo-btn mo-primary" id="mo-salvar" onclick="modalSalvar()"><i class="fa-solid fa-check"></i> Salvar</button>
    </div>
  </div>
</div>

<script>
const R = __ROWS__;
const LOGS = __LOGS__; // pedido -> [[logger, status, ultimo_sync], ...] (Ares da fonte)
const DEVS = __DEVS__; // pedido -> [[tag, tipo], ...] (vtc_stage + cadastro dtbPortal)
const VER = __VER__;   // verificacao fora da grade: [pedido, logger, sync, entrega, uf, base, st] com st S/E

const esc = t => String(t).replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const fmtEnt = s => { const d = new Date(s); return String(d.getDate()).padStart(2,"0")+"/"+String(d.getMonth()+1).padStart(2,"0")+" "+String(d.getHours()).padStart(2,"0")+":"+String(d.getMinutes()).padStart(2,"0"); };

function calc(r){
  const h = (Date.now() - new Date(r.ent).getTime()) / 36e5;
  const nivel = h < 12 ? 1 : h < 24 ? 2 : h < 36 ? 3 : h <= 48 ? 4 : 5;
  return { h: h, nivel: nivel, resta: 48 - h };
}
const NOMES = {1:["NO PRAZO","b1","ok","#16a34a"], 2:["ATEN\u00c7\u00c3O","b2","at","#ca8a04"], 3:["ALERTA","b3","al","#ea580c"], 4:["CR\u00cdTICO","b4","cr","#dc2626"], 5:["ESTOUROU","b5","es","#7f1d1d"]};

/* regioes do Brasil (agrupamentos de cobranca) */
const REGIOES = {
  COSE: ["GO","MT","MS","DF","SP","RJ","MG","ES"],                 // Centro-Oeste e Sudeste
  NOSU: ["AC","AP","AM","PA","RO","RR","TO","PR","RS","SC"],       // Norte e Sul
  NE:   ["AL","BA","CE","MA","PB","PE","PI","RN","SE"]             // Nordeste
};

/* ===== sinalizacao "ja cobrado" COMPARTILHADA (Firebase Realtime DB) ===== */
const FB = "https://gt-sincronismo-default-rtdb.firebaseio.com/sync48";
let FLAGS = {};
let FB_OK = false;

/* ===== login (usuarios cadastrados no Firebase, senha nunca trafega nem fica salva: hash SHA-256) ===== */
const SKEY = "sync48_sessao";
let SESSION = null;

async function sha256(txt){
  const buf = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(txt));
  return Array.from(new Uint8Array(buf)).map(b => b.toString(16).padStart(2, "0")).join("");
}
function lgErr(msg){
  const el = document.getElementById("lg-err");
  if (msg){ el.textContent = msg; el.classList.add("on"); } else el.classList.remove("on");
}
function aplicarSessao(){
  document.body.classList.remove("bloqueada");
  document.getElementById("quem-nome").textContent = SESSION.nome;
  try { localStorage.setItem("sync48_operador", SESSION.nome); } catch(e){}
}
function bloquear(){
  SESSION = null;
  try { localStorage.removeItem(SKEY); } catch(e){}
  document.body.classList.add("bloqueada");
  document.getElementById("lg-pass").value = "";
  lgErr("");
  setTimeout(() => document.getElementById("lg-user").focus(), 150);
}
function sair(){ bloquear(); }
async function fazerLogin(){
  const u = document.getElementById("lg-user").value.trim().toLowerCase();
  const p = document.getElementById("lg-pass").value;
  if (!u || !p){ lgErr("Preencha usu\u00e1rio e senha."); return; }
  const btn = document.getElementById("lg-btn");
  btn.disabled = true;
  btn.innerHTML = '<i class="fa-solid fa-circle-notch fa-spin"></i> Verificando...';
  try {
    const chaveU = u.replace(/[.#$\[\]\/]/g, "_"); // Firebase nao aceita "." em chaves
    const resp = await fetch(FB + "/_usuarios/" + encodeURIComponent(chaveU) + ".json", { cache: "no-store" });
    if (!resp.ok) throw new Error(resp.status);
    const reg = await resp.json();
    const h = await sha256(u + ":" + p);
    if (!reg || !reg.h || reg.h !== h){
      lgErr("Usu\u00e1rio ou senha incorretos.");
    } else {
      SESSION = { u: u, nome: reg.nome || u, t: Date.now() };
      try { localStorage.setItem(SKEY, JSON.stringify(SESSION)); } catch(e){}
      lgErr("");
      aplicarSessao();
    }
  } catch(e){
    lgErr("Sem conex\u00e3o com o servidor de acesso. Tente novamente.");
  } finally {
    btn.disabled = false;
    btn.innerHTML = '<i class="fa-solid fa-right-to-bracket"></i> Entrar';
  }
}
async function validarSessaoSalva(){
  try { SESSION = JSON.parse(localStorage.getItem(SKEY) || "null"); } catch(e){ SESSION = null; }
  if (!SESSION || !SESSION.u || !SESSION.nome){ bloquear(); return; }
  aplicarSessao();
  try { // confirma que o usuario ainda existe no banco; se foi removido, encerra a sessao
    const chaveU = SESSION.u.replace(/[.#$\[\]\/]/g, "_");
    const resp = await fetch(FB + "/_usuarios/" + encodeURIComponent(chaveU) + ".json", { cache: "no-store" });
    if (resp.ok && (await resp.json()) === null) bloquear();
  } catch(e){ /* offline: mantem a sessao local */ }
}
["lg-user","lg-pass"].forEach(id => document.getElementById(id).addEventListener("keydown", e => {
  if (e.key === "Enter"){ e.preventDefault(); fazerLogin(); }
}));

/* ===== area do usuario (senha, ocorrencias, administracao) ===== */
function chaveUsuario(login){ return login.replace(/[.#$\[\]\/]/g, "_"); }
function abrirPainelUsuario(){
  if (!SESSION){ bloquear(); return; }
  document.getElementById("up-sub").textContent = SESSION.nome + " \u00b7 " + SESSION.u;
  ["up-s0","up-s1","up-s2","up-nu-login","up-nu-nome","up-nu-senha"].forEach(i => document.getElementById(i).value = "");
  upMsg("conta", ""); upMsg("user", "");
  upTab("conta");
  document.getElementById("up-ov").classList.add("aberto");
}
function fecharPainel(){ document.getElementById("up-ov").classList.remove("aberto"); }
function upTab(t){
  document.querySelectorAll(".up-tab").forEach(b => b.classList.toggle("ativo", b.dataset.t === t));
  ["conta","oco","usuarios"].forEach(x => { document.getElementById("up-" + x).hidden = (x !== t); });
  if (t === "oco") renderMinhasOco();
  if (t === "usuarios") carregarUsuarios();
}
function upMsg(qual, msg, ok){
  const el = document.getElementById("up-msg-" + qual);
  el.textContent = msg || "";
  el.className = "up-msg" + (msg ? (ok ? " ok" : " erro") : "");
}
async function trocarSenha(){
  const s0 = document.getElementById("up-s0").value;
  const s1 = document.getElementById("up-s1").value;
  const s2 = document.getElementById("up-s2").value;
  if (!s0 || !s1 || !s2){ upMsg("conta", "Preencha os tr\u00eas campos."); return; }
  if (s1 !== s2){ upMsg("conta", "A nova senha e a confirma\u00e7\u00e3o n\u00e3o conferem."); return; }
  if (s1.length < 6){ upMsg("conta", "A nova senha precisa de pelo menos 6 caracteres."); return; }
  try {
    const key = chaveUsuario(SESSION.u);
    const reg = await (await fetch(FB + "/_usuarios/" + key + ".json", { cache: "no-store" })).json();
    if (!reg){ upMsg("conta", "Cadastro n\u00e3o encontrado. Fa\u00e7a login novamente."); return; }
    if (reg.h !== await sha256(SESSION.u + ":" + s0)){ upMsg("conta", "Senha atual incorreta."); return; }
    const resp = await fetch(FB + "/_usuarios/" + key + ".json", { method: "PATCH",
      body: JSON.stringify({ h: await sha256(SESSION.u + ":" + s1) }) });
    if (!resp.ok) throw new Error(resp.status);
    ["up-s0","up-s1","up-s2"].forEach(i => document.getElementById(i).value = "");
    upMsg("conta", "Senha alterada com sucesso.", true);
  } catch(e){ upMsg("conta", "Sem conex\u00e3o com o servidor. Tente novamente."); }
}
async function carregarUsuarios(){
  const alvo = document.getElementById("up-user-lista");
  alvo.innerHTML = '<div class="up-vazio">Carregando...</div>';
  try {
    const todos = await (await fetch(FB + "/_usuarios.json", { cache: "no-store" })).json() || {};
    alvo.innerHTML = Object.keys(todos).sort().map(key => {
      const reg = todos[key];
      const login = reg.login || key.replace(/_/g, ".");
      const souEu = login === SESSION.u;
      return '<div class="up-item" id="upu-' + key + '">'
        + '<div class="t">' + esc(reg.nome || login) + (souEu ? ' <small style="color:var(--tx3)">(voc\u00ea)</small>' : '') + '</div>'
        + '<div class="m">' + esc(login) + (souEu
          ? ' \u00b7 altere sua senha na aba "Minha conta"'
          : ' \u00b7 <i class="fa-solid fa-lock" style="font-size:.58rem"></i> somente o pr\u00f3prio usu\u00e1rio altera a senha') + '</div>'
        + '</div>';
    }).join("") || '<div class="up-vazio">Nenhum usu\u00e1rio cadastrado.</div>';
  } catch(e){ alvo.innerHTML = '<div class="up-vazio">Sem conex\u00e3o com o servidor.</div>'; }
}
async function cadastrarUsuario(){
  const login = document.getElementById("up-nu-login").value.trim().toLowerCase();
  const nome = document.getElementById("up-nu-nome").value.trim();
  const senha = document.getElementById("up-nu-senha").value;
  if (!/^[a-z0-9]+([._-][a-z0-9]+)*$/.test(login)){ upMsg("user", "Login inv\u00e1lido. Use min\u00fasculas no padr\u00e3o nome.sobrenome."); return; }
  if (!nome){ upMsg("user", "Informe o nome de exibi\u00e7\u00e3o."); return; }
  if (senha.length < 6){ upMsg("user", "A senha inicial precisa de pelo menos 6 caracteres."); return; }
  try {
    const key = chaveUsuario(login);
    const existe = await (await fetch(FB + "/_usuarios/" + key + ".json", { cache: "no-store" })).json();
    if (existe){ upMsg("user", "J\u00e1 existe usu\u00e1rio com esse login."); return; }
    const resp = await fetch(FB + "/_usuarios/" + key + ".json", { method: "PUT",
      body: JSON.stringify({ nome: nome, login: login, h: await sha256(login + ":" + senha) }) });
    if (!resp.ok) throw new Error(resp.status);
    ["up-nu-login","up-nu-nome","up-nu-senha"].forEach(i => document.getElementById(i).value = "");
    upMsg("user", "Usu\u00e1rio " + login + " cadastrado.", true);
    carregarUsuarios();
  } catch(e){ upMsg("user", "Sem conex\u00e3o com o servidor. Tente novamente."); }
}
function donaDaOco(obs){
  if (!obs) return true;
  return obs.u ? obs.u === SESSION.u : obs.q === SESSION.nome;
}
function renderMinhasOco(){
  const alvo = document.getElementById("up-oco-lista");
  const minhas = [];
  for (const k of Object.keys(FLAGS)){
    const f = FLAGS[k];
    if (!f.obs || !donaDaOco(f.obs)) continue;
    minhas.push({ k: k, i: infoChave(k), obs: f.obs });
  }
  minhas.sort((a,b) => b.obs.t - a.obs.t);
  alvo.innerHTML = minhas.map(m => {
    const ctx = "Pedido <b>" + esc(m.i.p) + "</b> \u00b7 logger " + esc(m.i.l) + (m.i.ag ? " \u00b7 " + esc(m.i.ag) : "")
      + (m.i.atual ? "" : ' \u00b7 <span style="color:var(--n1);font-weight:700">sincronizado</span>');
    const k = m.k.replace(/"/g, "&quot;");
    return '<div class="up-item">'
      + '<div class="t">' + ctx + '</div>'
      + '<div class="x">' + esc(m.obs.x) + '</div>'
      + '<div class="m">registrada h\u00e1 ' + idadeFlag(m.obs.t) + '</div>'
      + '<div class="acao"><button class="up-mini" onclick="fecharPainel();editarObs(\'' + k + '\')"><i class="fa-solid fa-pen"></i> Editar</button></div>'
      + '</div>';
  }).join("") || '<div class="up-vazio">Voc\u00ea ainda n\u00e3o registrou ocorr\u00eancias.</div>';
}
document.getElementById("up-ov").addEventListener("mousedown", e => { if (e.target === e.currentTarget) fecharPainel(); });
function chave(r){ return (r.p + "|" + r.l + "|" + r.ent).replace(/[.#$\[\]\/]/g, "_"); }
function normalizar(v){
  if (!v) return null;
  if (v.cob || v.obs) return v;
  if (v.q) return { cob: { q: v.q, t: v.t } }; // formato antigo
  return v;
}
async function buscarFlags(){
  try {
    const resp = await fetch(FB + ".json", { cache: "no-store" });
    if (!resp.ok) throw new Error(resp.status);
    const bruto = (await resp.json()) || {};
    FLAGS = {};
    for (const k of Object.keys(bruto)){
      if (k.charAt(0) === "_") continue; // nos reservados (ex.: _usuarios)
      const v = normalizar(bruto[k]);
      if (v) FLAGS[k] = v;
    }
    FB_OK = true;
    try { localStorage.setItem("sync48_espelho", JSON.stringify(FLAGS)); } catch(e){}
  } catch(e){
    FB_OK = false;
    try { FLAGS = JSON.parse(localStorage.getItem("sync48_espelho") || "{}"); } catch(x){ FLAGS = {}; }
  }
}
function tsFlag(v){
  return Math.max((v && v.cob && v.cob.t) || 0, (v && v.obs && v.obs.t) || 0);
}
async function limpezaFlags(){
  if (!FB_OK) return;
  const atuais = new Set(R.map(chave));
  for (const k of Object.keys(FLAGS)){
    const f = FLAGS[k];
    if (f.obs){
      // ocorrencia e historico: sobrevive ao sincronismo; expira em 30 dias
      if ((Date.now() - (f.obs.t || 0)) > 30*86400000){
        delete FLAGS[k];
        fetch(FB + "/" + encodeURIComponent(k) + ".json", { method: "DELETE" }).catch(() => {});
      } else if (!atuais.has(k) && f.cob){
        // pendencia resolvida: limpa so o "cobrado", preserva a ocorrencia
        delete f.cob;
        fetch(FB + "/" + encodeURIComponent(k) + ".json", { method: "PATCH", body: JSON.stringify({ cob: null }) }).catch(() => {});
      }
    } else if (!atuais.has(k) || (Date.now() - tsFlag(f)) > 7*86400000){
      delete FLAGS[k];
      fetch(FB + "/" + encodeURIComponent(k) + ".json", { method: "DELETE" }).catch(() => {});
    }
  }
}
function infoChave(k){
  const r = R.find(x => chave(x) === k);
  if (r) return { p: r.p, l: r.l, uf: r.uf, ag: r.ag, ent: r.ent, mo: r.mo, atual: r };
  const f = FLAGS[k] || {};
  const partes = k.split("|");
  return { p: partes[0] || "?", l: partes[1] || "?", ent: partes[2] || "",
           uf: (f.obs && f.obs.uf) || "", ag: (f.obs && f.obs.ag) || "",
           mo: (f.obs && f.obs.mo) || "", atual: null };
}
/* ---- modal profissional (substitui window.prompt) ---- */
const MOTIVOS = ["Solicitado sinc", "Enviado para GRU", "Logger descarregado", "Agente sem bateria",
                 "Em retorno para base", "Pedido devolvido", "Rodovi\u00e1rio paletizado", "Sem logger",
                 "Aguardando retirada", "Nota retida", "Realizado ressalva"];
let MO_RESOLVE = null;

function abrirModal(opts){
  // opts: { titulo, sub, textoObs, podeRemover, leitura }
  return new Promise(resolve => {
    MO_RESOLVE = resolve;
    document.getElementById("mo-tit").textContent = opts.titulo;
    document.getElementById("mo-sub").innerHTML = opts.sub || "";
    const ta = document.getElementById("mo-obs");
    ta.value = opts.textoObs || "";
    ta.disabled = !!opts.leitura;
    atualizaCnt();
    document.getElementById("mo-quem").textContent = SESSION ? SESSION.nome : "";
    document.querySelector(".mo-cnt").style.display = opts.leitura ? "none" : "";
    document.getElementById("mo-chips").innerHTML = opts.leitura ? "" : MOTIVOS.map(m =>
      '<button type="button" class="mo-chip" onclick="usarChip(this)">'+m+'</button>').join("");
    document.getElementById("mo-remover").style.display = opts.podeRemover ? "" : "none";
    document.getElementById("mo-salvar").style.display = opts.leitura ? "none" : "";
    document.getElementById("mo-ov").classList.add("aberto");
    if (!opts.leitura) setTimeout(() => ta.focus(), 180);
  });
}
function fecharModal(resultado){
  document.getElementById("mo-ov").classList.remove("aberto");
  const r = MO_RESOLVE; MO_RESOLVE = null;
  if (r) r(resultado);
}
function modalSalvar(){
  if (!SESSION){ fecharModal(null); bloquear(); return; }
  fecharModal({ nome: SESSION.nome, texto: document.getElementById("mo-obs").value.trim().slice(0, 200) });
}
function usarChip(el){
  const ta = document.getElementById("mo-obs");
  ta.value = ta.value.trim() ? ta.value.trim() + " - " + el.textContent.toLowerCase() : el.textContent;
  atualizaCnt(); ta.focus();
}
function atualizaCnt(){
  document.getElementById("mo-cnt").textContent = document.getElementById("mo-obs").value.length;
}
document.getElementById("mo-obs").addEventListener("input", atualizaCnt);
document.getElementById("mo-obs").addEventListener("keydown", e => {
  if (e.key === "Enter" && (e.ctrlKey || e.metaKey)){ e.preventDefault(); modalSalvar(); }
});
document.getElementById("mo-ov").addEventListener("mousedown", e => { if (e.target === e.currentTarget) fecharModal(null); });
document.addEventListener("keydown", e => {
  if (e.key === "Escape"){
    if (MO_RESOLVE) fecharModal(null);
    else fecharPainel();
  }
});

function subPendencia(k){
  const i = infoChave(k);
  return "Pedido <b>"+esc(i.p)+"</b> \u00b7 logger <b>"+esc(i.l)+"</b>" + (i.ag ? " \u00b7 "+esc(i.ag) : "") +
         (i.atual ? "" : " \u00b7 <b style=\"color:var(--n1)\">j\u00e1 sincronizado</b>");
}
async function obterNome(k){
  if (!SESSION){ bloquear(); return null; } // exige login: nunca marca cobrado sem nome
  return SESSION.nome;
}
async function patchFlag(k, campos){
  const url = FB + "/" + encodeURIComponent(k) + ".json";
  const resp = await fetch(url, { method: "PATCH", body: JSON.stringify(campos) });
  if (!resp.ok) throw new Error(resp.status);
}
async function toggleCobrado(k){
  const atual = FLAGS[k] || {};
  let nome = null;
  if (!atual.cob){
    nome = await obterNome(k);
    if (!nome) return; // cancelou o modal
  } else if (!donaDaOco(atual.cob)){
    return; // sinalizacao de outro usuario: so quem marcou pode desfazer
  }
  try {
    if (atual.cob){
      delete atual.cob;
      if (!atual.obs) delete FLAGS[k]; else FLAGS[k] = atual;
      apply();
      await patchFlag(k, { cob: null });
    } else {
      atual.cob = { q: nome, t: Date.now(), u: SESSION.u };
      FLAGS[k] = atual;
      apply();
      await patchFlag(k, { cob: atual.cob });
    }
    try { localStorage.setItem("sync48_espelho", JSON.stringify(FLAGS)); } catch(e){}
  } catch(e){
    alert("Sem conex\u00e3o com o quadro compartilhado agora; tente novamente.");
    await buscarFlags(); apply();
  }
}
async function editarObs(k){
  if (!SESSION){ bloquear(); return; }
  const atual = FLAGS[k] || {};
  if (atual.obs && !donaDaOco(atual.obs)){
    // ocorrencia de outro usuario: somente leitura
    await abrirModal({
      titulo: "Ocorr\u00eancia de " + atual.obs.q,
      sub: subPendencia(k) + " \u00b7 registrada h\u00e1 " + idadeFlag(atual.obs.t) + " \u00b7 s\u00f3 " + esc(atual.obs.q) + " pode alterar",
      textoObs: atual.obs.x,
      podeRemover: false,
      leitura: true
    });
    return;
  }
  const res = await abrirModal({
    titulo: atual.obs ? "Editar ocorr\u00eancia" : "Nova ocorr\u00eancia",
    sub: subPendencia(k),
    textoObs: atual.obs ? atual.obs.x : "",
    podeRemover: !!atual.obs
  });
  if (!res) return; // cancelou
  try {
    const limpo = res.remover ? "" : (res.texto || "");
    if (limpo){
      const info = infoChave(k); // guarda uf/base/modal junto: o contexto sobrevive quando a pendencia sincroniza
      atual.obs = { x: limpo, q: res.nome, u: SESSION.u, t: Date.now(),
                    uf: info.uf || "", ag: info.ag || "", mo: info.mo || "" };
      FLAGS[k] = atual;
      apply();
      await patchFlag(k, { obs: atual.obs });
    } else {
      delete atual.obs;
      if (!atual.cob) delete FLAGS[k]; else FLAGS[k] = atual;
      apply();
      await patchFlag(k, { obs: null });
    }
    try { localStorage.setItem("sync48_espelho", JSON.stringify(FLAGS)); } catch(e){}
  } catch(e){
    alert("Sem conex\u00e3o com o quadro compartilhado agora; tente novamente.");
    await buscarFlags(); apply();
  }
}
function idadeFlag(t){
  const h = (Date.now() - t) / 36e5;
  if (h < 1) return Math.round(h * 60) + "min";
  if (h < 48) return h.toFixed(0) + "h";
  return Math.round(h / 24) + "d";
}

function fmtResta(resta){
  if (resta >= 0){
    const hh = Math.floor(resta), mm = Math.round((resta - hh) * 60);
    return "faltam " + hh + "h" + String(mm).padStart(2,"0");
  }
  const v = -resta, hh = Math.floor(v), mm = Math.round((v - hh) * 60);
  return "estourou h\u00e1 " + hh + "h" + String(mm).padStart(2,"0");
}

let VIEW = [];
let TAB = "geral";
function ehRod(fobs){ return !!(fobs && /rodovi/i.test(fobs.x)); }
function modalBadge(mo){
  return mo === "R"
    ? '<span class="badge b-rod" title="Rodovi\u00e1rio"><i class="fa-solid fa-truck" style="font-size:.58rem"></i><span class="mtx"> Rodovi\u00e1rio</span></span>'
    : '<span class="badge b-mm" title="MultiModal"><i class="fa-solid fa-plane" style="font-size:.58rem"></i><span class="mtx"> MultiModal</span></span>';
}
function modalNome(mo){ return mo === "R" ? "Rodovi\u00e1rio" : "MultiModal"; }

/* ---- hover card: ares pendente + outros dataloggers do pedido ---- */
const ST_LOG = { S: ["Sincronizado", "#16a34a"], P: ["Pendente", "#dc2626"], G: ["Pendente (GRU)", "#dc2626"], E: ["Erro operacional", "#9aa3b2"] };
const COR_DEV = { "ARES": "#dc2626", "ARES COM SONDA": "#dc2626", "SYOS": "#0369a1", "SHIELD": "#6d28d9", "ELITECH": "#d97706", "SENSOR WEB": "#16a34a" };
function mostrarCard(ev, p, l, s, h, su){
  const el = document.getElementById("hovercard");
  const todos = LOGS[p] || [];
  const outros = todos.filter(x => x[0] !== l);
  let html = '<div class="hc-t">Pedido ' + esc(p) + ' <small>\u00b7 ' + todos.length + ' datalogger(s) vinculado(s)</small></div>'
    + '<div class="hc-ares"><div class="l1"><i class="fa-solid fa-tower-broadcast"></i> ' + esc(l) + ' \u2014 ARES PENDENTE</div>'
    + '<div class="l2">Sem sincronismo h\u00e1 <b>' + h + '</b> desde a entrega<br>\u00daltimo sync: ' + esc(s || "nunca registrado")
    + (su ? '<br>\u00daltimo sync feito por: <b>' + esc(su) + '</b>' : '') + '</div></div>';
  if (outros.length){
    html += '<div class="hc-sec">Outros Ares do pedido</div>' + outros.map(x => {
      const st = ST_LOG[x[1]] || ["?", "#9aa3b2"];
      return '<div class="hc-it"><span class="pt" style="background:' + st[1] + '"></span><b>' + esc(x[0]) + '</b> ' + st[0] + '<small>' + esc(x[2] || "") + '</small></div>';
    }).join("");
  }
  // outros dispositivos (Syos, Shield, Elitech...) do vtc_stage, classificados pelo cadastro dtbPortal
  const jaListados = new Set([l.toUpperCase()].concat(todos.map(x => String(x[0]).toUpperCase())));
  const devs = (DEVS[p] || []).filter(x => !jaListados.has(String(x[0]).toUpperCase()));
  if (devs.length){
    html += '<div class="hc-sec">Outros dispositivos do pedido <small style="font-weight:500;text-transform:none">(vtc_stage)</small></div>' + devs.map(x => {
      const cor = COR_DEV[x[1]] || "#9aa3b2";
      return '<div class="hc-it"><span class="pt" style="background:' + cor + '"></span><b>' + esc(x[0]) + '</b><small>' + esc(x[1]) + '</small></div>';
    }).join("");
  }
  if (!outros.length && !devs.length){
    html += '<div class="hc-nada">Sem outros dispositivos vinculados a este pedido.</div>';
  }
  el.innerHTML = html;
  el.style.display = "block";
  moverCard(ev);
}
function moverCard(ev){
  const el = document.getElementById("hovercard");
  if (el.style.display !== "block") return;
  const w = el.offsetWidth, hh = el.offsetHeight;
  let x = ev.clientX + 16, y = ev.clientY + 14;
  if (x + w > window.innerWidth - 10) x = ev.clientX - w - 16;
  if (y + hh > window.innerHeight - 10) y = ev.clientY - hh - 14;
  el.style.left = Math.max(8, x) + "px";
  el.style.top = Math.max(8, y) + "px";
}
function esconderCard(){ document.getElementById("hovercard").style.display = "none"; }
function setTab(t){
  TAB = t;
  document.querySelectorAll(".gtab").forEach(b => b.classList.toggle("ativo", b.dataset.tab === t));
  document.getElementById("gridWrap").style.display = (t === "oco") ? "none" : "";
  document.getElementById("ocoWrap").style.display = (t === "oco") ? "" : "none";
  document.getElementById("cards").style.display = (t === "oco") ? "none" : "";
  apply();
}
let SORT = { k: "resta", d: 1 };
const CMP = {
  nivel: (a,b) => a.h - b.h,
  h:     (a,b) => a.h - b.h,
  resta: (a,b) => a.resta - b.resta,
  ent:   (a,b) => a.ent.localeCompare(b.ent),
  p:  (a,b) => String(a.p).localeCompare(String(b.p), undefined, {numeric:true}),
  l:  (a,b) => String(a.l).localeCompare(String(b.l), undefined, {numeric:true}),
  ro: (a,b) => String(a.ro).localeCompare(String(b.ro), undefined, {numeric:true}),
  uf: (a,b) => String(a.uf).localeCompare(String(b.uf)),
  ag: (a,b) => String(a.ag).localeCompare(String(b.ag)),
  s:  (a,b) => String(a.s).localeCompare(String(b.s)),
  cob:(a,b) => String(a.cob).localeCompare(String(b.cob)),
  mo: (a,b) => String(a.mo).localeCompare(String(b.mo)),
};
function apply(){
  const q = document.getElementById("f-busca").value.trim().toLowerCase();
  const ba = document.getElementById("f-base").value;
  const uf = document.getElementById("f-uf").value;
  const rg = document.getElementById("f-reg").value;
  const nv = document.getElementById("f-nivel").value;
  const cb = document.getElementById("f-cob").value;
  const fl = document.getElementById("f-flag").value;
  const TODAS = R.map(r => {
    const f = FLAGS[chave(r)] || {};
    return {...r, ...calc(r), fcob: f.cob || null, fobs: f.obs || null, rod: r.mo === "R" || ehRod(f.obs)};
  });
  document.getElementById("gt-geral").textContent = TODAS.filter(r => !r.vel && !r.rod).length;
  document.getElementById("gt-rod").textContent = TODAS.filter(r => r.rod).length;
  document.getElementById("gt-oco").textContent = Object.keys(FLAGS).filter(k => FLAGS[k].obs).length;
  VIEW = TODAS.filter(r => TAB === "rod" ? r.rod : (!r.vel && !r.rod)).filter(r => {
    if (ba && r.ag !== ba) return false;
    if (uf && r.uf !== uf) return false;
    if (rg && REGIOES[rg].indexOf(r.uf) === -1) return false;
    if (nv && String(r.nivel) !== nv) return false;
    if (cb && r.cob !== cb) return false;
    if (fl === "N" && r.fcob) return false;
    if (fl === "S" && !r.fcob) return false;
    if (fl === "O" && !r.fobs) return false;
    if (fl === "X" && r.fobs) return false;
    if (q){
      const alvo = (r.p+" "+r.l+" "+r.ro+" "+r.ag+" "+r.cli+" "+r.uf+" "+(r.fobs ? r.fobs.x : "")).toLowerCase();
      if (!alvo.includes(q)) return false;
    }
    return true;
  });
  const cmp = CMP[SORT.k] || CMP.resta;
  VIEW.sort((a,b) => cmp(a,b) * SORT.d || a.resta - b.resta); // desempate: mais urgente primeiro
  render();
  if (TAB === "oco") renderOco();
  renderVerificacao(q);
}

/* busca tambem verifica quem JA sincronizou (fora da grade), para tirar duvidas */
function renderVerificacao(q){
  const box = document.getElementById("verbox");
  if (!q || q.length < 4){ box.style.display = "none"; return; }
  const hits = VER.filter(v => v[0].toLowerCase().includes(q) || v[1].toLowerCase().includes(q)).slice(0, 12);
  if (hits.length){
    box.className = "";
    box.innerHTML = '<div class="vb-t"><i class="fa-solid fa-circle-check"></i>Verifica\u00e7\u00e3o: j\u00e1 resolvidos que casam com a busca (fora da grade)</div>'
      + hits.map(v => {
        const st = v[6] === "E"
          ? '<span class="badge" style="color:#9aa3b2">ERRO OPERACIONAL</span>'
          : '<span class="badge b1">SINCRONIZADO</span>';
        return '<div class="vb-it">' + st
          + '<b>' + esc(v[0]) + '</b><span>logger ' + esc(v[1]) + '</span>'
          + (v[2] ? '<span>sync <b>' + esc(v[2]) + '</b></span>' : '')
          + (v[3] ? '<small>entrega ' + esc(v[3]) + '</small>' : '')
          + '<small>' + esc(v[4]) + (v[5] ? ' \u00b7 ' + esc(v[5]) : '') + '</small></div>';
      }).join("");
    box.style.display = "";
  } else if (!VIEW.length){
    box.className = "vazio";
    box.innerHTML = '<div class="vb-t"><i class="fa-solid fa-magnifying-glass"></i>N\u00e3o encontrado nem nas pend\u00eancias nem nos sincronizados da janela do indicador</div>';
    box.style.display = "";
  } else {
    box.style.display = "none";
  }
}

function renderOco(){
  const q = document.getElementById("f-busca").value.trim().toLowerCase();
  const ba = document.getElementById("f-base").value;
  const uf = document.getElementById("f-uf").value;
  const rg = document.getElementById("f-reg").value;
  const itens = [];
  for (const k of Object.keys(FLAGS)){
    const f = FLAGS[k];
    if (!f.obs) continue;
    const i = infoChave(k);
    if (ba && i.ag !== ba) continue;
    if (uf && i.uf !== uf) continue;
    if (rg && REGIOES[rg].indexOf(i.uf) === -1) continue;
    if (q){
      const alvo = (i.p + " " + i.l + " " + i.ag + " " + i.uf + " " + f.obs.x + " " + f.obs.q).toLowerCase();
      if (!alvo.includes(q)) continue;
    }
    itens.push({ k: k, i: i, obs: f.obs });
  }
  itens.sort((a,b) => b.obs.t - a.obs.t);
  document.getElementById("cnt").innerHTML = "<b>" + itens.length + "</b> ocorr\u00eancia(s) no recorte \u00b7 <b>" +
    itens.filter(m => !m.i.atual).length + "</b> de pend\u00eancias j\u00e1 sincronizadas \u00b7 hist\u00f3rico mantido por 30 dias" +
    (FB_OK ? " <i class=\"fa-solid fa-cloud\" style=\"color:#16a34a\"></i>" : " <i class=\"fa-solid fa-cloud-arrow-down\" style=\"color:#d97706\"></i>");
  document.getElementById("tb-oco").innerHTML = itens.map(m => {
    const k = m.k.replace(/"/g, "&quot;");
    const dona = donaDaOco(m.obs);
    return '<tr>'
      + '<td>' + (m.i.atual ? '<span class="badge ' + NOMES[calc(m.i.atual).nivel][1] + '">' + NOMES[calc(m.i.atual).nivel][0] + '</span>' : '<span class="badge b1">SINCRONIZADO</span>') + '</td>'
      + '<td><b>' + esc(m.i.p) + '</b></td>'
      + '<td>' + esc(m.i.l) + '</td>'
      + '<td>' + (m.i.mo ? modalBadge(m.i.mo) : "\u2014") + '</td>'
      + '<td>' + (m.i.ent ? fmtEnt(m.i.ent) : "\u2014") + '</td>'
      + '<td>' + esc(m.i.uf || "\u2014") + '</td>'
      + '<td class="agc" title="' + esc(m.i.ag) + '">' + esc(m.i.ag || "\u2014") + '</td>'
      + '<td class="obscell" onclick="editarObs(\'' + k + '\')" title="' + esc(m.obs.x) + '"><span class="obs-txt" style="max-width:340px">' + esc(m.obs.x) + '</span></td>'
      + '<td>' + esc(m.obs.q) + (dona ? '' : ' <i class="fa-solid fa-lock oco-lock"></i>') + '</td>'
      + '<td>h\u00e1 ' + idadeFlag(m.obs.t) + '</td>'
      + '<td>' + (dona ? '<button class="btn-flag" onclick="editarObs(\'' + k + '\')">editar</button>' : '') + '</td>'
      + '</tr>';
  }).join("") || '<tr><td colspan="11" style="text-align:center;padding:30px;color:var(--tx2)">Nenhuma ocorr\u00eancia registrada neste recorte.</td></tr>';
}

function render(){
  // KPIs: corrida das 48h da aba Geral (sem antigos e sem rodoviario, que nao tem logger a sincronizar)
  const todos = R.filter(r => !r.vel && r.mo !== "R" && !ehRod((FLAGS[chave(r)] || {}).obs)).map(r => calc(r));
  for (let n = 1; n <= 5; n++){
    document.getElementById("k"+n).textContent = todos.filter(x => x.nivel === n).length;
  }
  document.getElementById("cnt").innerHTML = "<b>" + VIEW.length + "</b> pend\u00eancia(s) no recorte \u00b7 <b>" +
    VIEW.filter(r => r.nivel === 4).length + "</b> cr\u00edticas (36\u201348h) \u00b7 <b>" +
    VIEW.filter(r => r.nivel === 5).length + "</b> estouradas \u00b7 <b>" +
    VIEW.filter(r => r.fcob).length + "</b> j\u00e1 cobradas \u00b7 <b>" +
    VIEW.filter(r => r.fobs).length + "</b> com ocorr\u00eancia" + (FB_OK ? " <i class=\"fa-solid fa-cloud\" title=\"quadro compartilhado online\" style=\"color:#16a34a\"></i>" : " <i class=\"fa-solid fa-cloud-arrow-down\" title=\"offline - mostrando ultima copia local\" style=\"color:#d97706\"></i>");

  document.getElementById("tb").innerHTML = VIEW.map(r => {
    const [nome, bcls, rcls, cor] = NOMES[r.nivel];
    const pct = Math.max(2, Math.min(100, (r.h / 48) * 100));
    const k = chave(r).replace(/"/g, "&quot;");
    const meuCob = r.fcob ? donaDaOco(r.fcob) : true;
    const celCob = r.fcob
      ? (meuCob
        ? '<td class="cobcell"><span class="badge b-cob" onclick="toggleCobrado(\''+k+'\')" title="Clique para desfazer (s\u00f3 voc\u00ea pode)">\u2713 COBRADO \u00b7 '+esc(r.fcob.q)+' \u00b7 h\u00e1 '+idadeFlag(r.fcob.t)+'</span></td>'
        : '<td class="cobcell"><span class="badge b-cob" style="cursor:default" title="S\u00f3 '+esc(r.fcob.q)+' pode desfazer esta sinaliza\u00e7\u00e3o">\u2713 COBRADO \u00b7 '+esc(r.fcob.q)+' \u00b7 h\u00e1 '+idadeFlag(r.fcob.t)+' <i class="fa-solid fa-lock oco-lock"></i></span></td>')
      : '<td class="cobcell"><button class="btn-flag" onclick="toggleCobrado(\''+k+'\')">marcar cobrado</button></td>';
    const minhaObs = r.fobs ? donaDaOco(r.fobs) : true;
    const celObs = r.fobs
      ? '<td class="cobcell obscell" onclick="editarObs(\''+k+'\')" title="'+esc(r.fobs.x)+' \u2014 '+esc(r.fobs.q)+', h\u00e1 '+idadeFlag(r.fobs.t)+(minhaObs ? ' (clique para editar)' : ' (s\u00f3 quem registrou pode alterar)')+'"><span class="obs-txt">'+esc(r.fobs.x)+'</span><span class="obs-meta">'+esc(r.fobs.q)+' \u00b7 '+idadeFlag(r.fobs.t)+(minhaObs ? '' : ' <i class="fa-solid fa-lock oco-lock"></i>')+'</span></td>'
      : '<td class="cobcell"><button class="btn-flag" onclick="editarObs(\''+k+'\')">+ ocorr\u00eancia</button></td>';
    const hArg = r.h.toFixed(1) + 'h';
    return '<tr class="lv'+r.nivel+(r.fcob ? ' flagged' : '')+'">'
      + '<td><span class="badge '+bcls+'">'+nome+'</span></td>'
      + '<td class="pedcell" onmouseenter="mostrarCard(event,\''+esc(r.p)+'\',\''+esc(r.l)+'\',\''+esc(r.s)+'\',\''+hArg+'\',\''+esc(r.su || "")+'\')" onmousemove="moverCard(event)" onmouseleave="esconderCard()"><b>'+esc(r.p)+'</b></td>'
      + '<td>'+esc(r.ro)+'</td>'
      + '<td>'+esc(r.uf)+'</td>'
      + '<td class="agc" title="'+esc(r.ag)+'">'+esc(r.ag)+'</td>'
      + '<td>'+modalBadge(r.mo)+'</td>'
      + '<td>'+fmtEnt(r.ent)+'</td>'
      + '<td><b>'+r.h.toFixed(1)+'h</b></td>'
      + '<td class="resta '+rcls+'">'+fmtResta(r.resta)+'</td>'
      + '<td><div class="pbar"><div style="width:'+pct+'%;background:'+cor+'"></div></div></td>'
      + '<td>'+esc(r.s || "\u2014")+'</td>'
      + '<td><span class="badge '+(r.cob === "GRU" ? "b-gru" : "b-ag")+'">'+r.cob+'</span></td>'
      + celCob + celObs
      + '</tr>';
  }).join("") || '<tr><td colspan="14" style="text-align:center;padding:30px;color:var(--tx2)"><i class="fa-solid fa-circle-check" style="color:var(--n1);margin-right:7px"></i>Nenhuma pend\u00eancia neste recorte.</td></tr>';

  document.getElementById("cards").innerHTML = VIEW.map(r => {
    const [nome, bcls, rcls] = NOMES[r.nivel];
    const k = chave(r).replace(/"/g, "&quot;");
    const linhaCob = (r.fcob
      ? (donaDaOco(r.fcob)
        ? '<span class="k">Sinaliza\u00e7\u00e3o</span><span><span class="badge b-cob" onclick="toggleCobrado(\''+k+'\')">\u2713 COBRADO \u00b7 '+esc(r.fcob.q)+' \u00b7 h\u00e1 '+idadeFlag(r.fcob.t)+'</span></span>'
        : '<span class="k">Sinaliza\u00e7\u00e3o</span><span><span class="badge b-cob" style="cursor:default">\u2713 COBRADO \u00b7 '+esc(r.fcob.q)+' \u00b7 h\u00e1 '+idadeFlag(r.fcob.t)+' <i class="fa-solid fa-lock oco-lock"></i></span></span>')
      : '<span class="k">Sinaliza\u00e7\u00e3o</span><span><button class="btn-flag" onclick="toggleCobrado(\''+k+'\')">marcar cobrado</button></span>')
      + (r.fobs
      ? '<span class="k">Ocorr\u00eancia</span><span onclick="editarObs(\''+k+'\')" style="cursor:pointer">'+esc(r.fobs.x)+' <small style="color:var(--tx2)">('+esc(r.fobs.q)+' \u00b7 '+idadeFlag(r.fobs.t)+')</small></span>'
      : '<span class="k">Ocorr\u00eancia</span><span><button class="btn-flag" onclick="editarObs(\''+k+'\')">+ ocorr\u00eancia</button></span>');
    return '<div class="card lv'+r.nivel+'">'
      + '<div class="topo"><span class="ped">'+esc(r.p)+'</span><span class="badge '+bcls+'">'+nome+'</span></div>'
      + '<div class="kv">'
      + '<span class="k">Logger</span><span><b>'+esc(r.l)+'</b> \u00b7 rom. '+esc(r.ro)+' \u00b7 '+esc(r.uf)+'</span>'
      + '<span class="k">Base</span><span>'+esc(r.ag)+'</span>'
      + '<span class="k">Modal</span><span>'+modalNome(r.mo)+'</span>'
      + '<span class="k">Entrega</span><span>'+fmtEnt(r.ent)+' ('+r.h.toFixed(1)+'h atr\u00e1s)</span>'
      + '<span class="k">Rel\u00f3gio</span><span class="resta '+rcls+'">'+fmtResta(r.resta)+'</span>'
      + '<span class="k">Cobran\u00e7a</span><span>'+r.cob+'</span>'
      + linhaCob
      + '</div></div>';
  }).join("");
}

function limpar(){
  ["f-busca","f-base","f-uf","f-reg","f-nivel","f-cob","f-flag"].forEach(id => { document.getElementById(id).value = ""; });
  apply();
}

function baixarCsv(){
  const cab = ["nivel","pedido","logger","romaneio","uf","base_agente","modal","cliente","entrega","horas_desde_entrega","resta_para_48h","ultimo_sync","sync_por","cobranca","ja_cobrado","cobrado_por","cobrado_em","ocorrencia","ocorrencia_por","ocorrencia_em"];
  const linhas = VIEW.map(r => [NOMES[r.nivel][0], r.p, r.l, r.ro, r.uf, '"'+r.ag.replace(/"/g,'""')+'"', modalNome(r.mo), '"'+r.cli.replace(/"/g,'""')+'"', r.ent, r.h.toFixed(1), r.resta.toFixed(1), r.s, '"'+String(r.su || "").replace(/"/g,'""')+'"', r.cob,
    r.fcob ? "SIM" : "", r.fcob ? '"'+String(r.fcob.q).replace(/"/g,'""')+'"' : "", r.fcob ? new Date(r.fcob.t).toLocaleString("pt-BR") : "",
    r.fobs ? '"'+String(r.fobs.x).replace(/"/g,'""')+'"' : "", r.fobs ? '"'+String(r.fobs.q).replace(/"/g,'""')+'"' : "", r.fobs ? new Date(r.fobs.t).toLocaleString("pt-BR") : ""].join(";"));
  const blob = new Blob(["\ufeff"+cab.join(";")+"\n"+linhas.join("\n")], {type:"text/csv;charset=utf-8"});
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = "sincronismo_48h.csv";
  document.body.appendChild(a); a.click();
  setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 3000);
}

["f-busca","f-base","f-uf","f-reg","f-nivel","f-cob","f-flag"].forEach(id => {
  const el = document.getElementById(id);
  el.addEventListener(el.tagName === "INPUT" ? "input" : "change", apply);
});
document.querySelectorAll("th.sv").forEach(th => th.addEventListener("click", () => {
  const k = th.dataset.k;
  if (SORT.k === k) SORT.d = -SORT.d; else SORT = { k: k, d: 1 };
  document.querySelectorAll("th.sv").forEach(x => x.classList.remove("asc","desc"));
  th.classList.add(SORT.d === 1 ? "asc" : "desc");
  apply();
}));
document.querySelectorAll(".kpi").forEach(k => k.addEventListener("click", () => {
  const sel = document.getElementById("f-nivel");
  sel.value = sel.value === k.dataset.n ? "" : k.dataset.n;
  document.querySelectorAll(".kpi").forEach(x => x.classList.remove("ativo"));
  if (sel.value) k.classList.add("ativo");
  apply();
}));
(async () => {
  await validarSessaoSalva();
  await buscarFlags();
  await limpezaFlags();
  apply();
})();
setInterval(async () => { await buscarFlags(); apply(); }, 30000); // countdown + quadro compartilhado ao vivo
</script>
</body>
</html>
"""


def write_html(modelo: dict) -> None:
    import html as _html

    bases = "".join(
        f'<option value="{_html.escape(b, quote=True)}">{_html.escape(b)}</option>'
        for b in modelo["bases"]
    )
    ufs = "".join(f'<option value="{u}">{u}</option>' for u in modelo["ufs"])
    out = (
        HTML_TEMPLATE
        .replace("__GERADO__", modelo["gerado_em"])
        .replace("__FONTE_EM__", modelo["fonte_em"])
        .replace("__ANTIGOS__", str(modelo["antigos"]))
        .replace("__BASES__", bases)
        .replace("__UFS__", ufs)
        .replace("__ROWS__", json.dumps(modelo["rows"], ensure_ascii=False, separators=(",", ":")))
        .replace("__LOGS__", json.dumps(modelo["logs"], ensure_ascii=False, separators=(",", ":")))
        .replace("__DEVS__", json.dumps(modelo["devs"], ensure_ascii=False, separators=(",", ":")))
        .replace("__VER__", json.dumps(modelo["ver"], ensure_ascii=False, separators=(",", ":")))
    )
    OUT_HTML.write_text(out, encoding="utf-8")


if __name__ == "__main__":
    main()
