# -*- coding: utf-8 -*-
"""
RETIRADA NO AEROPORTO - site de cobranca de retirada de cargas desembarcadas.

Fonte precisa (mesma do Tracking Aereo do portal):
  dtbTransporte (SQL Server) -> vwExcel_AcompanhamentoAwb
    MERCADORIA_DESEMBARCADA  = pouso no aeroporto de destino
    MERCADORIA_RETIRADA_AGENTE = retirada pelo agente/tecnico

Publica RETIRADA_AEROPORTO.html (rotina ATUALIZAR_TUDO_10_MIN).
Enfase especial nas SEXTAS-FEIRAS: carga que desembarca na sexta e nao e
retirada dorme o fim de semana inteiro no aeroporto (risco termico).

Tema: dashboard-template (sidebar escura + conteudo claro em cards).
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parent
sys.path.insert(0, str(WORKSPACE))

from gerar_html_entregas_vs_sincronismo import _sqlserver_cfg  # noqa: E402

OUT_HTML = WORKSPACE / "RETIRADA_AEROPORTO.html"
JANELA_DIAS = 30

CIA_CURTA = (
    ("GOL", "GOL"),
    ("TAM", "LATAM"),
    ("LATAM", "LATAM"),
    ("AZUL", "AZUL"),
)


def _cia_curta(nome: str) -> str:
    up = (nome or "").upper()
    for chave, curta in CIA_CURTA:
        if chave in up:
            return curta
    return (nome or "").strip()[:18]


def fetch_rows() -> list[dict]:
    cfg = _sqlserver_cfg()
    if not cfg["host"] or not cfg["user"]:
        raise RuntimeError("dtbTransporte ausente (.env AURA_SQLSERVER_*)")
    import pytds

    corte = (datetime.now() - timedelta(days=JANELA_DIAS)).strftime("%Y-%m-%d")
    query = """
        SELECT Pedido, AWB, ORIGEM, DESTINO, CIA, AGENTE, VOLUME,
               MERCADORIA_DESEMBARCADA, MERCADORIA_RETIRADA_AGENTE
        FROM vwExcel_AcompanhamentoAwb
        WHERE MERCADORIA_DESEMBARCADA IS NOT NULL
          AND MERCADORIA_DESEMBARCADA >= %s
          AND NULLIF(LTRIM(RTRIM(Pedido)), '') IS NOT NULL
    """
    with pytds.connect(
        server=cfg["host"], port=int(cfg["port"]), database=str(cfg["database"]),
        user=str(cfg["user"]), password=str(cfg["password"]),
        timeout=120, login_timeout=30, validate_host=False,
    ) as conn:
        cur = conn.cursor()
        cur.execute(query, (corte,))
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, raw)) for raw in cur.fetchall()]


def build_model(raw: list[dict]) -> dict:
    agora = datetime.now()

    # dedup por pedido: mantem o desembarque mais recente; empate -> com retirada
    por_pedido: dict[str, dict] = {}
    for r in raw:
        p = str(r["Pedido"]).strip()
        atual = por_pedido.get(p)
        if atual is None:
            por_pedido[p] = r
            continue
        da, db = atual["MERCADORIA_DESEMBARCADA"], r["MERCADORIA_DESEMBARCADA"]
        if db > da or (db == da and r["MERCADORIA_RETIRADA_AGENTE"] and not atual["MERCADORIA_RETIRADA_AGENTE"]):
            por_pedido[p] = r

    rows = []
    for p, r in por_pedido.items():
        de: datetime = r["MERCADORIA_DESEMBARCADA"]
        re_: datetime | None = r["MERCADORIA_RETIRADA_AGENTE"]
        fim = re_ or agora
        horas = max(0.0, (fim - de).total_seconds() / 3600.0)
        noites = max(0, (fim.date() - de.date()).days)
        sexta = de.weekday() == 4
        rows.append({
            "p": p,
            "awb": str(r["AWB"] or "").strip(),
            "o": str(r["ORIGEM"] or "").strip(),
            "d": str(r["DESTINO"] or "").strip(),
            "cia": _cia_curta(str(r["CIA"] or "")),
            "ag": str(r["AGENTE"] or "").strip(),
            "vol": int(r["VOLUME"] or 0),
            "de": de.strftime("%Y-%m-%dT%H:%M"),
            "re": re_.strftime("%Y-%m-%dT%H:%M") if re_ else None,
            "st": "R" if re_ else "A",
            "h": round(horas, 1),
            "n": noites,
            "sx": sexta,
            "fds": bool(sexta and noites >= 1),
        })

    # ultima sexta-feira (se hoje e sexta, considera hoje)
    dias_desde_sexta = (agora.weekday() - 4) % 7
    ultima_sexta = (agora - timedelta(days=dias_desde_sexta)).date()

    aguardando = [r for r in rows if r["st"] == "A"]
    criticos = [r for r in aguardando if r["h"] >= 24]
    hoje = agora.date().isoformat()
    retirados_hoje = [r for r in rows if r["re"] and r["re"][:10] == hoje]
    fds_ultima_sexta = [
        r for r in rows
        if r["sx"] and r["de"][:10] == ultima_sexta.isoformat() and (r["n"] >= 1 or r["st"] == "A")
    ]
    ret_7d = [r for r in rows if r["st"] == "R" and r["de"] >= (agora - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M")]
    media_7d = round(sum(r["h"] for r in ret_7d) / len(ret_7d), 1) if ret_7d else 0.0

    def _vols(grupo: list[dict]) -> int:
        """Volumetria correta: VOLUME e por AWB; soma cada AWB distinta uma vez."""
        por_awb: dict[str, int] = {}
        for r in grupo:
            por_awb[r["awb"]] = r["vol"]
        return sum(por_awb.values())

    return {
        "gerado_em": agora.strftime("%d/%m/%Y %H:%M"),
        "rows": sorted(rows, key=lambda r: (r["st"] != "A", -r["h"])),
        "kpi": {
            "aguardando": len(aguardando),
            "aguardando_vol": _vols(aguardando),
            "criticos": len(criticos),
            "criticos_vol": _vols(criticos),
            "fds": len(fds_ultima_sexta),
            "fds_vol": _vols(fds_ultima_sexta),
            "retirados_hoje": len(retirados_hoje),
            "retirados_hoje_vol": _vols(retirados_hoje),
            "media_7d": media_7d,
        },
        "ultima_sexta": ultima_sexta.strftime("%d/%m"),
        "destinos": sorted({r["d"] for r in rows if r["d"]}),
        "bases": sorted({r["ag"] for r in rows if r["ag"]}),
    }


HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Retirada no Aeroporto &mdash; VTC LOG</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800;900&display=swap" rel="stylesheet">
<style>
:root{
  --sb:#0f1e35; --sb2:#16294a; --sbtx:#c3d2e8; --sbmuted:#7e93b4;
  --bg:#f2f5fa; --card:#ffffff; --line:#e4e9f2; --tx:#1e293b; --tx2:#64748b;
  --blue:#2563eb; --red:#dc2626; --redbg:#fee2e2; --orange:#d97706; --orangebg:#fef3c7;
  --green:#16a34a; --greenbg:#dcfce7; --purple:#7c3aed; --purplebg:#ede9fe;
  --cyan:#0891b2; --cyanbg:#cffafe;
  --shadow:0 1px 3px rgba(15,30,53,.08), 0 4px 14px rgba(15,30,53,.05);
}
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:'Inter',system-ui,sans-serif;background:var(--bg);color:var(--tx);min-height:100vh}
.app{display:flex;min-height:100vh}

/* ===== sidebar ===== */
.sb{width:232px;min-width:232px;background:linear-gradient(180deg,var(--sb),#0c1930);color:var(--sbtx);display:flex;flex-direction:column;position:sticky;top:0;height:100vh}
.brand{display:flex;align-items:center;gap:11px;padding:20px 18px 18px;border-bottom:1px solid rgba(255,255,255,.07)}
.brand .lg{width:40px;height:40px;border-radius:11px;background:linear-gradient(135deg,#2563eb,#0891b2);display:flex;align-items:center;justify-content:center;font-weight:900;color:#fff;font-size:.82rem;letter-spacing:.5px}
.brand b{display:block;color:#fff;font-size:.92rem;letter-spacing:.3px}
.brand span{display:block;font-size:.66rem;color:var(--sbmuted);margin-top:2px}
nav{padding:16px 12px;flex:1}
.nav-title{font-size:.6rem;text-transform:uppercase;letter-spacing:1px;color:var(--sbmuted);padding:0 8px 8px}
.nv{display:flex;align-items:center;gap:10px;padding:10px 12px;border-radius:9px;color:var(--sbtx);font-size:.82rem;font-weight:600;cursor:pointer;margin-bottom:3px;transition:background .15s;text-decoration:none}
.nv:hover{background:rgba(255,255,255,.06)}
.nv.active{background:var(--blue);color:#fff}
.nv .ic{width:20px;text-align:center}
.sb-foot{padding:14px 18px;border-top:1px solid rgba(255,255,255,.07);font-size:.68rem;color:var(--sbmuted);line-height:1.6}
.sb-foot b{color:#8fd8ff}

/* ===== conteudo ===== */
main{flex:1;padding:26px 30px 60px;min-width:0}
.top{display:flex;flex-wrap:wrap;justify-content:space-between;align-items:flex-start;gap:10px;margin-bottom:18px}
h1{font-size:1.35rem;font-weight:800;letter-spacing:-.2px}
.sub{color:var(--tx2);font-size:.8rem;margin-top:5px;max-width:820px;line-height:1.55}
.upd{font-size:.72rem;color:var(--tx2);background:var(--card);border:1px solid var(--line);border-radius:9px;padding:7px 13px;white-space:nowrap;box-shadow:var(--shadow)}
.upd b{color:var(--blue)}

/* banners */
.friday{display:none;background:#fef2f2;border:1px solid #fecaca;border-left:5px solid var(--red);border-radius:11px;padding:14px 18px;margin-bottom:14px;font-weight:700;font-size:.92rem;color:#991b1b;box-shadow:var(--shadow);animation:pulse 1.8s ease-in-out infinite}
.friday.on{display:block}
@keyframes pulse{0%,100%{box-shadow:var(--shadow)}50%{box-shadow:0 0 0 4px rgba(220,38,38,.12)}}
.wkend{background:#fffbeb;border:1px solid #fde68a;border-left:5px solid #f59e0b;border-radius:11px;padding:12px 18px;margin-bottom:18px;font-size:.8rem;line-height:1.6;color:#78350f;box-shadow:var(--shadow)}
.wkend b{color:#b45309}
.wkend .rl{color:var(--red);font-weight:800}

/* kpis */
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(196px,1fr));gap:14px;margin-bottom:18px}
.kpi{background:var(--card);border:1px solid var(--line);border-radius:13px;padding:16px;display:flex;gap:13px;align-items:center;box-shadow:var(--shadow)}
.kpi .ic{width:46px;height:46px;min-width:46px;border-radius:12px;display:flex;align-items:center;justify-content:center;font-size:1.25rem}
.kpi.vermelho .ic{background:var(--redbg)} .kpi.pu .ic{background:var(--purplebg)}
.kpi.gr .ic{background:var(--greenbg)} .kpi.cy .ic{background:var(--cyanbg)}
.kpi .v{font-size:1.5rem;font-weight:800;line-height:1.1}
.kpi.vermelho .v{color:var(--red)} .kpi.pu .v{color:var(--purple)} .kpi.gr .v{color:var(--green)} .kpi.cy .v{color:var(--cyan)}
.kpi .v2{font-size:.7rem;color:var(--tx2);font-weight:700;margin-top:1px}
.kpi .v2 b{color:var(--tx)}
.kpi .lbl{font-size:.66rem;color:var(--tx2);text-transform:uppercase;letter-spacing:.4px;margin-top:3px;line-height:1.35;font-weight:600}

/* filtros */
.fcard{background:var(--card);border:1px solid var(--line);border-radius:13px;padding:16px;margin-bottom:14px;box-shadow:var(--shadow)}
.fbar{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;align-items:end}
.fbar label{display:block;font-size:.62rem;color:var(--tx2);text-transform:uppercase;letter-spacing:.5px;margin-bottom:5px;font-weight:700}
.fbar input,.fbar select{width:100%;background:#f8fafc;border:1px solid var(--line);border-radius:8px;color:var(--tx);padding:8px 11px;font-size:.82rem;font-family:inherit;max-width:100%}
.fbar select{text-overflow:ellipsis}
.fbar input:focus,.fbar select:focus{outline:none;border-color:var(--blue);background:#fff;box-shadow:0 0 0 3px rgba(37,99,235,.1)}
.fbar label.chk{display:flex;align-items:center;gap:7px;font-size:.76rem;font-weight:800;color:var(--orange);white-space:nowrap;margin:0 0 8px;padding:0;text-transform:none;letter-spacing:0;cursor:pointer;align-self:end}
.fbar label.chk input{width:auto;accent-color:var(--orange)}
.fbar .low{display:flex;gap:8px;align-items:center;justify-content:flex-end}
.btn{background:var(--blue);border:1px solid var(--blue);border-radius:8px;color:#fff;padding:8px 16px;font-size:.78rem;font-weight:700;cursor:pointer;white-space:nowrap;font-family:inherit;transition:opacity .15s}
.btn:hover{opacity:.88}
.btn.ghost{background:#fff;color:var(--tx);border-color:var(--line)}
.btn.ghost:hover{border-color:var(--blue);color:var(--blue);opacity:1}
.cnt{font-size:.75rem;color:var(--tx2);margin:0 2px 10px}
.cnt b{color:var(--tx)}
.cnt .dica{color:var(--blue)}

/* tabela */
.tblwrap{background:var(--card);border:1px solid var(--line);border-radius:13px;overflow:auto;max-height:72vh;box-shadow:var(--shadow)}
table{width:100%;border-collapse:collapse;font-size:.8rem;min-width:980px}
th{position:sticky;top:0;height:38px;background:#f8fafc;color:var(--tx2);text-transform:uppercase;font-size:.63rem;letter-spacing:.5px;padding:9px;text-align:left;white-space:nowrap;z-index:2;border-bottom:1px solid var(--line)}
th[data-s]{cursor:pointer;user-select:none;transition:color .15s}
th[data-s]:hover{color:var(--blue)}
th[data-s]::after{content:" \2195";opacity:.35}
th[data-s].asc::after{content:" \25B2";opacity:1;color:var(--blue)}
th[data-s].desc::after{content:" \25BC";opacity:1;color:var(--blue)}
.frow th{top:38px;padding:5px 6px;height:auto;background:#f1f5f9;border-bottom:1px solid var(--line)}
.frow input{width:100%;min-width:54px;background:#fff;border:1px solid var(--line);border-radius:6px;color:var(--tx);padding:4px 7px;font-size:.7rem;font-family:inherit}
.frow input:focus{outline:none;border-color:var(--blue)}
td{padding:9px;border-top:1px solid var(--line);white-space:nowrap;vertical-align:middle}
tbody tr:nth-child(even) td{background:#fafbfd}
tbody tr:hover td{background:#eff6ff}
tr.crit td{background:#fef2f2}
tr.crit:hover td{background:#fee2e2}
tr.warn td{background:#fffbeb}
tr.warn:hover td{background:#fef3c7}
.badge{display:inline-block;font-size:.62rem;font-weight:800;border-radius:6px;padding:3px 8px;letter-spacing:.4px}
.b-ag{background:var(--redbg);color:#b91c1c}
.b-ok{background:var(--greenbg);color:#15803d}
.b-sx{background:var(--purplebg);color:var(--purple);margin-left:4px}
.b-fds{background:var(--orangebg);color:#b45309;margin-left:4px}
.hrs{font-weight:800}
.hrs.c{color:var(--red)} .hrs.w{color:var(--orange)} .hrs.k{color:var(--green)}
.rota{color:var(--blue);font-weight:700}
.agc{max-width:230px;overflow:hidden;text-overflow:ellipsis}

/* cards mobile */
.cards{display:none}
.mbar{display:none}
@media(max-width:960px){
  .sb{display:none}
  .mbar{display:flex;align-items:center;justify-content:space-between;gap:10px;background:var(--sb);color:#fff;padding:13px 16px;position:sticky;top:0;z-index:30}
  .mbar .lg{width:32px;height:32px;border-radius:9px;background:linear-gradient(135deg,#2563eb,#0891b2);display:flex;align-items:center;justify-content:center;font-weight:900;font-size:.68rem}
  .mbar b{font-size:.85rem}
  .mbar span{display:block;font-size:.62rem;color:var(--sbmuted)}
  .mbar .mupd{font-size:.62rem;color:#8fd8ff;text-align:right;line-height:1.4}
  .app{display:block}
  main{padding:16px 12px 50px}
}
@media(max-width:860px){
  .tblwrap{display:none}
  .cards{display:grid;grid-template-columns:1fr;gap:10px}
  .card{position:relative;background:var(--card);border:1px solid var(--line);border-radius:13px;padding:14px 14px 12px;overflow:hidden;box-shadow:var(--shadow)}
  .card::before{content:"";position:absolute;top:0;left:0;bottom:0;width:4px;background:var(--blue)}
  .card.crit::before{background:var(--red)}
  .card.warn::before{background:var(--orange)}
  .card .topo{display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;gap:8px;flex-wrap:wrap}
  .card .ped{font-weight:900;font-size:1.02rem}
  .card .kv{display:grid;grid-template-columns:auto 1fr;gap:3px 10px;font-size:.78rem}
  .card .kv .k{color:var(--tx2);font-size:.66rem;text-transform:uppercase;letter-spacing:.4px;padding-top:2px;font-weight:700}
  .fbar{grid-template-columns:1fr 1fr}
  h1{font-size:1.1rem}
}
footer{margin-top:26px;text-align:center;color:var(--tx2);font-size:.7rem;line-height:1.7}
</style>
</head>
<body>
<div class="mbar">
  <div style="display:flex;align-items:center;gap:9px"><div class="lg">VTC</div><div><b>Retirada no Aeroporto</b><span>VTC LOG &middot; Gerenciamento T&eacute;rmico</span></div></div>
  <div class="mupd">Atualizado<br><b>__GERADO__</b></div>
</div>
<div class="app">

<aside class="sb">
  <div class="brand">
    <div class="lg">VTC</div>
    <div><b>VTC LOG</b><span>GERENCIAMENTO T&Eacute;RMICO</span></div>
  </div>
  <nav>
    <div class="nav-title">Retirada no Aeroporto</div>
    <a class="nv" onclick="preset('', this)"><span class="ic">&#128230;</span> Vis&atilde;o geral</a>
    <a class="nv active" onclick="preset('A', this)"><span class="ic">&#9203;</span> Aguardando retirada</a>
    <a class="nv" onclick="preset('crit', this)"><span class="ic">&#128680;</span> Cr&iacute;ticos &ge; 24h</a>
    <a class="nv" onclick="preset('sx', this)"><span class="ic">&#128197;</span> Sextas-feiras</a>
    <a class="nv" onclick="preset('R', this)"><span class="ic">&#9989;</span> Retirados</a>
  </nav>
  <div class="sb-foot">Atualizado em <b>__GERADO__</b><br>a cada 10 minutos<br>Fonte: Acompanhamento AWB</div>
</aside>

<main>
<header class="top">
  <div>
    <h1>Retirada no Aeroporto</h1>
    <div class="sub">Cargas desembarcadas no aeroporto de destino &times; retirada pelo agente de cargas / t&eacute;cnico.
    Carga parada no aeroporto &eacute; risco t&eacute;rmico: a retirada deve acontecer <b>assim que a carga desembarca</b>.</div>
  </div>
  <div class="upd">Atualizado em <b>__GERADO__</b> &middot; a cada 10 min</div>
</header>

<div class="friday" id="friday-banner">&#128680; HOJE &Eacute; SEXTA-FEIRA &mdash; PONTO CR&Iacute;TICO: toda carga que desembarcar hoje e n&atilde;o for retirada vai dormir o <u>FIM DE SEMANA INTEIRO</u> no aeroporto. Cobre a retirada AINDA HOJE.</div>

<div class="wkend">&#9888;&#65039; <b>SEXTA-FEIRA &eacute; ponto cr&iacute;tico de aten&ccedil;&atilde;o:</b> carga que desembarca na sexta e n&atilde;o &eacute; retirada no mesmo dia dorme <span class="rl">o fim de semana inteiro</span> no aeroporto &mdash; risco de excurs&atilde;o t&eacute;rmica e ocorr&ecirc;ncia junto ao Gerenciamento T&eacute;rmico. Linhas marcadas com <span class="badge b-sx">SEXTA</span> desembarcaram numa sexta-feira; <span class="badge b-fds">FIM DE SEMANA</span> indica que a carga pernoitou no aeroporto.</div>

<div class="kpis">
  <div class="kpi vermelho"><div class="ic">&#9203;</div><div><div class="v">__K_AG__</div><div class="v2"><b>__K_AG_V__</b> volumes</div><div class="lbl">Aguardando retirada agora</div></div></div>
  <div class="kpi vermelho"><div class="ic">&#128680;</div><div><div class="v">__K_CR__</div><div class="v2"><b>__K_CR_V__</b> volumes</div><div class="lbl">Cr&iacute;ticos &ge; 24h no aeroporto</div></div></div>
  <div class="kpi pu"><div class="ic">&#128197;</div><div><div class="v">__K_FDS__</div><div class="v2"><b>__K_FDS_V__</b> volumes</div><div class="lbl">Sexta __ULTSEXTA__: dormiram no aeroporto</div></div></div>
  <div class="kpi gr"><div class="ic">&#9989;</div><div><div class="v">__K_RH__</div><div class="v2"><b>__K_RH_V__</b> volumes</div><div class="lbl">Retirados hoje</div></div></div>
  <div class="kpi cy"><div class="ic">&#9201;&#65039;</div><div><div class="v">__K_MD__h</div><div class="v2">&nbsp;</div><div class="lbl">Tempo m&eacute;dio de retirada (7 dias)</div></div></div>
</div>

<div class="fcard">
<div class="fbar">
  <div><label>Busca (pedido / AWB / agente / CIA)</label><input id="f-busca" type="text" placeholder="Ex.: 569729, RODOTEC..."></div>
  <div><label>Base / Agente</label><select id="f-base"><option value="">Todas as bases</option>__BASES__</select></div>
  <div><label>Destino (aeroporto)</label><select id="f-dest"><option value="">Todos</option>__DESTINOS__</select></div>
  <div><label>Status</label><select id="f-st"><option value="">Todos</option><option value="A" selected>Aguardando retirada</option><option value="R">Retirados</option></select></div>
  <div><label>Per&iacute;odo (desembarque)</label><select id="f-per"><option value="7">7 dias</option><option value="15" selected>15 dias</option><option value="30">30 dias</option></select></div>
  <div><label>Ordena&ccedil;&atilde;o</label><select id="f-ord"><option value="h">Mais horas no aeroporto</option><option value="dn">Desembarque recente</option><option value="da">Desembarque antigo</option></select></div>
  <label class="chk"><input type="checkbox" id="f-sx"> S&oacute; sextas</label>
  <div class="low"><button class="btn ghost" onclick="limpar()">Limpar</button><button class="btn" onclick="baixarCsv()">Baixar CSV</button></div>
</div>
</div>

<div class="cnt"><span id="cnt"></span> &nbsp;&middot;&nbsp; <span class="dica">Dica: clique no t&iacute;tulo da coluna para ordenar (menor &rarr; maior / maior &rarr; menor)</span></div>

<div class="tblwrap">
<table>
  <thead><tr>
    <th data-s="p">Pedido</th><th data-s="awb">AWB</th><th data-s="rota">Rota</th><th data-s="cia">CIA</th><th data-s="ag">Agente / Base</th>
    <th data-s="vol" title="Volumes da AWB">Vol.</th>
    <th data-s="de">Desembarque</th><th data-s="re">Retirada</th><th data-s="h">Horas</th><th data-s="n">Noites</th><th data-s="st">Status</th>
  </tr>
  <tr class="frow">
    <th><input class="cf" data-col="p" placeholder="pedido"></th>
    <th><input class="cf" data-col="awb" placeholder="AWB"></th>
    <th><input class="cf" data-col="rota" placeholder="GRU, REC..."></th>
    <th><input class="cf" data-col="cia" placeholder="cia"></th>
    <th><input class="cf" data-col="ag" placeholder="base / agente"></th>
    <th></th>
    <th><input class="cf" data-col="de" placeholder="dd/mm"></th>
    <th><input class="cf" data-col="re" placeholder="dd/mm"></th>
    <th><input class="cf" data-col="h" placeholder="&#8805; h"></th>
    <th><input class="cf" data-col="n" placeholder="&#8805; n"></th>
    <th></th>
  </tr></thead>
  <tbody id="tb"></tbody>
</table>
</div>
<div class="cards" id="cards"></div>

<footer>
  VTC LOG &middot; Gerenciamento T&eacute;rmico &mdash; Retirada no Aeroporto &middot; Fonte: Acompanhamento AWB (dtbTransporte), mesma base do Tracking A&eacute;reo<br>
  Crit&eacute;rios: <b style="color:var(--red)">cr&iacute;tico</b> &ge; 24h sem retirada &middot; <b style="color:var(--orange)">aten&ccedil;&atilde;o</b> &ge; 12h &middot; sexta-feira = ponto cr&iacute;tico (risco de fim de semana)<br>
  Volumetria: a coluna Vol. &eacute; o total de volumes da AWB; nos KPIs cada AWB &eacute; somada uma &uacute;nica vez (uma AWB pode ter v&aacute;rios pedidos)
</footer>
</main>
</div>

<script>
const R = __ROWS__;

const fmt = s => { if(!s) return "\u2014"; const d = new Date(s); return String(d.getDate()).padStart(2,"0")+"/"+String(d.getMonth()+1).padStart(2,"0")+" "+String(d.getHours()).padStart(2,"0")+":"+String(d.getMinutes()).padStart(2,"0"); };
const esc = t => String(t).replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));

if (new Date().getDay() === 5) document.getElementById("friday-banner").classList.add("on");

let VIEW = [];
let SORT = null; /* ordenacao por clique no cabecalho: {col, dir 1|-1} */
function cmpVal(r, c){
  if (c === "rota") return r.o + r.d;
  if (c === "de") return r.de;
  if (c === "re") return r.re || "";
  if (c === "st") return r.st;
  return r[c];
}
function matchCols(r, colf){
  for (const [c, v] of colf){
    if (c === "h"){ const t = parseFloat(v.replace(",", ".")); if (isNaN(t) || r.h < t) return false; continue; }
    if (c === "n"){ const t = parseInt(v, 10); if (isNaN(t) || r.n < t) return false; continue; }
    let alvo;
    if (c === "rota") alvo = r.o + " " + r.d + " " + r.o + r.d;
    else if (c === "de") alvo = fmt(r.de);
    else if (c === "re") alvo = fmt(r.re);
    else alvo = String(r[c] || "");
    if (!alvo.toLowerCase().includes(v)) return false;
  }
  return true;
}
function apply(){
  const q  = document.getElementById("f-busca").value.trim().toLowerCase();
  const ba = document.getElementById("f-base").value;
  const de = document.getElementById("f-dest").value;
  const st = document.getElementById("f-st").value;
  const per= parseInt(document.getElementById("f-per").value, 10);
  const so = document.getElementById("f-sx").checked;
  const ord= document.getElementById("f-ord").value;
  const corte = new Date(Date.now() - per*86400000);
  const colf = [];
  document.querySelectorAll(".cf").forEach(i => {
    const v = i.value.trim().toLowerCase();
    if (v) colf.push([i.dataset.col, v]);
  });

  VIEW = R.filter(r => {
    if (new Date(r.de) < corte) return false;
    if (ba && r.ag !== ba) return false;
    if (de && r.d !== de) return false;
    if (st && r.st !== st) return false;
    if (so && !r.sx) return false;
    if (colf.length && !matchCols(r, colf)) return false;
    if (q){
      const alvo = (r.p+" "+r.awb+" "+r.ag+" "+r.cia+" "+r.o+" "+r.d).toLowerCase();
      if (!alvo.includes(q)) return false;
    }
    return true;
  });
  if (SORT){
    const col = SORT.col, dir = SORT.dir;
    VIEW.sort((a,b) => {
      const va = cmpVal(a, col), vb = cmpVal(b, col);
      if (typeof va === "number" && typeof vb === "number") return (va - vb) * dir;
      return String(va).localeCompare(String(vb)) * dir;
    });
  }
  else if (ord === "h") VIEW.sort((a,b) => (a.st!==b.st) ? (a.st==="A"?-1:1) : b.h-a.h);
  else if (ord === "dn") VIEW.sort((a,b) => b.de.localeCompare(a.de));
  else VIEW.sort((a,b) => a.de.localeCompare(b.de));
  render();
}

function classeH(r){ return r.st!=="A" ? "k" : (r.h>=24 ? "c" : (r.h>=12 ? "w" : "k")); }
function badges(r){
  let b = r.st==="A" ? '<span class="badge b-ag">AGUARDANDO</span>' : '<span class="badge b-ok">RETIRADO</span>';
  if (r.sx) b += '<span class="badge b-sx">SEXTA</span>';
  if (r.fds) b += '<span class="badge b-fds">FIM DE SEMANA</span>';
  return b;
}

function render(){
  const volsAwb = {};
  VIEW.forEach(r => { volsAwb[r.awb] = r.vol; });
  const totVol = Object.values(volsAwb).reduce((a,b) => a+b, 0);
  document.getElementById("cnt").innerHTML = "<b>" + VIEW.length + "</b> registro(s) \u00b7 <b>" + VIEW.filter(r=>r.st==="A").length + "</b> aguardando retirada \u00b7 <b>" + totVol + "</b> volumes (por AWB)";
  const tb = document.getElementById("tb");
  tb.innerHTML = VIEW.map(r => {
    const cls = r.st==="A" ? (r.h>=24 ? "crit" : (r.h>=12 ? "warn" : "")) : "";
    return '<tr class="'+cls+'">'
      + '<td><b>'+esc(r.p)+'</b></td>'
      + '<td>'+esc(r.awb)+'</td>'
      + '<td class="rota">'+esc(r.o)+' \u2192 '+esc(r.d)+'</td>'
      + '<td>'+esc(r.cia)+'</td>'
      + '<td class="agc" title="'+esc(r.ag)+'">'+esc(r.ag)+'</td>'
      + '<td><b>'+r.vol+'</b></td>'
      + '<td>'+fmt(r.de)+'</td>'
      + '<td>'+fmt(r.re)+'</td>'
      + '<td class="hrs '+classeH(r)+'">'+r.h.toFixed(1)+'h</td>'
      + '<td>'+r.n+'</td>'
      + '<td>'+badges(r)+'</td>'
      + '</tr>';
  }).join("");

  document.getElementById("cards").innerHTML = VIEW.map(r => {
    const cls = r.st==="A" ? (r.h>=24 ? "crit" : (r.h>=12 ? "warn" : "")) : "";
    return '<div class="card '+cls+'">'
      + '<div class="topo"><span class="ped">'+esc(r.p)+'</span><span>'+badges(r)+'</span></div>'
      + '<div class="kv">'
      + '<span class="k">Rota</span><span class="rota">'+esc(r.o)+' \u2192 '+esc(r.d)+' \u00b7 '+esc(r.cia)+'</span>'
      + '<span class="k">AWB</span><span>'+esc(r.awb)+' \u00b7 <b>'+r.vol+'</b> vol.</span>'
      + '<span class="k">Agente</span><span>'+esc(r.ag)+'</span>'
      + '<span class="k">Desembarque</span><span>'+fmt(r.de)+'</span>'
      + '<span class="k">Retirada</span><span>'+fmt(r.re)+'</span>'
      + '<span class="k">No aeroporto</span><span class="hrs '+classeH(r)+'">'+r.h.toFixed(1)+'h ('+r.n+' noite'+(r.n===1?'':'s')+')</span>'
      + '</div></div>';
  }).join("");
}

function preset(k, el){
  document.getElementById("f-st").value = "";
  document.getElementById("f-sx").checked = false;
  document.querySelector('.cf[data-col="h"]').value = "";
  if (k === "A") document.getElementById("f-st").value = "A";
  else if (k === "R") document.getElementById("f-st").value = "R";
  else if (k === "crit"){ document.getElementById("f-st").value = "A"; document.querySelector('.cf[data-col="h"]').value = "24"; }
  else if (k === "sx"){ document.getElementById("f-sx").checked = true; }
  document.querySelectorAll(".nv").forEach(n => n.classList.remove("active"));
  if (el) el.classList.add("active");
  apply();
}

function limpar(){
  document.getElementById("f-busca").value = "";
  document.getElementById("f-base").value = "";
  document.getElementById("f-dest").value = "";
  document.getElementById("f-st").value = "";
  document.getElementById("f-per").value = "15";
  document.getElementById("f-ord").value = "h";
  document.getElementById("f-sx").checked = false;
  document.querySelectorAll(".cf").forEach(i => { i.value = ""; });
  SORT = null;
  document.querySelectorAll("th[data-s]").forEach(t => t.classList.remove("asc","desc"));
  document.querySelectorAll(".nv").forEach(n => n.classList.remove("active"));
  document.querySelectorAll(".nv")[0].classList.add("active");
  apply();
}

function baixarCsv(){
  const cab = ["pedido","awb","volumes_awb","origem","destino","cia","agente","desembarque","retirada","horas","noites","status","sexta","fim_de_semana"];
  const linhas = VIEW.map(r => [r.p,r.awb,r.vol,r.o,r.d,r.cia,'"'+r.ag.replace(/"/g,'""')+'"',r.de,r.re||"",r.h,r.n,r.st==="A"?"AGUARDANDO":"RETIRADO",r.sx?"SIM":"",r.fds?"SIM":""].join(";"));
  const blob = new Blob(["\ufeff"+cab.join(";")+"\n"+linhas.join("\n")], {type:"text/csv;charset=utf-8"});
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = "retirada_aeroporto.csv";
  document.body.appendChild(a);
  a.click();
  setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 3000);
}

["f-busca","f-base","f-dest","f-st","f-per","f-sx"].forEach(id => {
  const el = document.getElementById(id);
  el.addEventListener(el.tagName==="INPUT" && el.type==="text" ? "input" : "change", apply);
});
document.getElementById("f-ord").addEventListener("change", () => {
  SORT = null;
  document.querySelectorAll("th[data-s]").forEach(t => t.classList.remove("asc","desc"));
  apply();
});
document.querySelectorAll(".cf").forEach(i => i.addEventListener("input", apply));
document.querySelectorAll("th[data-s]").forEach(th => th.addEventListener("click", () => {
  const c = th.dataset.s;
  if (SORT && SORT.col === c) SORT.dir = -SORT.dir;
  else SORT = { col: c, dir: 1 };
  document.querySelectorAll("th[data-s]").forEach(t => t.classList.remove("asc","desc"));
  th.classList.add(SORT.dir === 1 ? "asc" : "desc");
  apply();
}));
apply();
</script>
</body>
</html>
"""


def write_html(model: dict) -> None:
    import html as _html

    destinos = "".join(f'<option value="{d}">{d}</option>' for d in model["destinos"])
    bases = "".join(
        f'<option value="{_html.escape(b, quote=True)}">{_html.escape(b)}</option>'
        for b in model["bases"]
    )
    html = (
        HTML_TEMPLATE
        .replace("__GERADO__", model["gerado_em"])
        .replace("__K_AG_V__", str(model["kpi"]["aguardando_vol"]))
        .replace("__K_CR_V__", str(model["kpi"]["criticos_vol"]))
        .replace("__K_FDS_V__", str(model["kpi"]["fds_vol"]))
        .replace("__K_RH_V__", str(model["kpi"]["retirados_hoje_vol"]))
        .replace("__K_AG__", str(model["kpi"]["aguardando"]))
        .replace("__K_CR__", str(model["kpi"]["criticos"]))
        .replace("__K_FDS__", str(model["kpi"]["fds"]))
        .replace("__K_RH__", str(model["kpi"]["retirados_hoje"]))
        .replace("__K_MD__", str(model["kpi"]["media_7d"]))
        .replace("__ULTSEXTA__", model["ultima_sexta"])
        .replace("__DESTINOS__", destinos)
        .replace("__BASES__", bases)
        .replace("__ROWS__", json.dumps(model["rows"], ensure_ascii=False, separators=(",", ":")))
    )
    OUT_HTML.write_text(html, encoding="utf-8")


def main() -> None:
    raw = fetch_rows()
    model = build_model(raw)
    write_html(model)
    k = model["kpi"]
    print(
        f"OK RETIRADA_AEROPORTO.html | pedidos={len(model['rows'])} | "
        f"aguardando={k['aguardando']} criticos24h={k['criticos']} "
        f"fds_sexta={k['fds']} retirados_hoje={k['retirados_hoje']} media7d={k['media_7d']}h"
    )


if __name__ == "__main__":
    main()
