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
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700;800;900&display=swap" rel="stylesheet">
<style>
:root{
  --bg0:#0b1526; --bg1:#0f1e35; --panel:#13253f; --panel2:#182c4b;
  --line:rgba(148,180,220,.14); --tx:#e8f0fa; --tx2:#9fb4cf;
  --cyan:#39c2ff; --green:#2fd08a; --red:#ff5d6c; --orange:#ffb020; --purple:#b78bff;
}
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:'Inter',system-ui,sans-serif;background:linear-gradient(160deg,var(--bg0),var(--bg1) 60%,#0d1a2f);color:var(--tx);min-height:100vh;padding:22px 16px 60px}
.wrap{max-width:1280px;margin:0 auto}
header{display:flex;flex-wrap:wrap;align-items:flex-end;justify-content:space-between;gap:10px;margin-bottom:14px}
h1{font-size:1.5rem;font-weight:900;letter-spacing:.4px;text-transform:uppercase}
h1 .air{color:var(--cyan)}
.sub{color:var(--tx2);font-size:.82rem;margin-top:4px;max-width:760px;line-height:1.5}
.upd{font-size:.75rem;color:var(--tx2);background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:6px 12px;white-space:nowrap}
.upd b{color:var(--cyan)}

/* banner sexta */
.friday{display:none;background:linear-gradient(90deg,rgba(255,93,108,.22),rgba(255,93,108,.08));border:1px solid rgba(255,93,108,.55);border-radius:14px;padding:14px 18px;margin:14px 0;font-weight:800;font-size:.98rem;animation:pulse 1.6s ease-in-out infinite}
.friday.on{display:block}
@keyframes pulse{0%,100%{box-shadow:0 0 0 0 rgba(255,93,108,.35)}50%{box-shadow:0 0 22px 4px rgba(255,93,108,.25)}}
.wkend{background:linear-gradient(90deg,rgba(255,176,32,.16),rgba(255,176,32,.05));border:1px solid rgba(255,176,32,.45);border-radius:14px;padding:12px 18px;margin:14px 0;font-size:.88rem;line-height:1.55}
.wkend b{color:var(--orange)}
.wkend .rl{color:var(--red);font-weight:800}

/* kpis */
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px;margin:16px 0 20px}
.kpi{position:relative;background:var(--panel);border:1px solid var(--line);border-radius:16px;padding:16px 14px 14px;text-align:center;overflow:hidden}
.kpi::before{content:"";position:absolute;top:0;left:0;right:0;height:4px;background:linear-gradient(90deg,var(--red),#ff8d97)}
.kpi.cy::before{background:linear-gradient(90deg,var(--cyan),#7fd8ff)}
.kpi.gr::before{background:linear-gradient(90deg,var(--green),#7fe8b8)}
.kpi.pu::before{background:linear-gradient(90deg,var(--purple),#d3b8ff)}
.kpi .v{font-size:1.9rem;font-weight:900;line-height:1.1}
.kpi .lbl{font-size:.7rem;color:var(--tx2);text-transform:uppercase;letter-spacing:.6px;margin-top:5px;line-height:1.4}
.kpi .v2{font-size:.76rem;color:var(--tx2);font-weight:800;margin-top:3px}
.kpi .v2 b{color:var(--tx)}
.kpi.vermelho .v{color:var(--red)} .kpi.cy .v{color:var(--cyan)} .kpi.gr .v{color:var(--green)} .kpi.pu .v{color:var(--purple)}

/* filtros */
.fbar{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:14px;margin-bottom:14px;align-items:end}
.fbar label{display:block;font-size:.62rem;color:var(--tx2);text-transform:uppercase;letter-spacing:.5px;margin-bottom:4px;font-weight:700}
.fbar input,.fbar select{width:100%;background:var(--bg1);border:1px solid var(--line);border-radius:9px;color:var(--tx);padding:8px 10px;font-size:.82rem;font-family:inherit;max-width:100%}
.fbar select{text-overflow:ellipsis}
.fbar input:focus,.fbar select:focus{outline:none;border-color:var(--cyan)}
.fbar .low{display:flex;gap:10px;align-items:center;justify-content:flex-end}
.btn{background:var(--panel2);border:1px solid var(--line);border-radius:9px;color:var(--tx);padding:8px 14px;font-size:.78rem;font-weight:700;cursor:pointer;white-space:nowrap;font-family:inherit}
.btn:hover{border-color:var(--cyan);color:var(--cyan)}
.fbar label.chk{display:flex;align-items:center;gap:7px;font-size:.75rem;font-weight:800;color:var(--orange);white-space:nowrap;margin:0 0 8px;padding:0;text-transform:none;letter-spacing:0;cursor:pointer;align-self:end}
.fbar label.chk input{width:auto}
.cnt{font-size:.76rem;color:var(--tx2);margin:0 2px 8px}

/* tabela */
.tblwrap{background:var(--panel);border:1px solid var(--line);border-radius:14px;overflow:auto;max-height:72vh}
table{width:100%;border-collapse:collapse;font-size:.8rem;min-width:980px}
th{position:sticky;top:0;height:36px;background:var(--panel2);color:var(--tx2);text-transform:uppercase;font-size:.64rem;letter-spacing:.5px;padding:8px 9px;text-align:left;white-space:nowrap;z-index:2}
th[data-s]{cursor:pointer;user-select:none;transition:color .15s}
th[data-s]:hover{color:var(--cyan)}
th[data-s]::after{content:" \2195";opacity:.35}
th[data-s].asc::after{content:" \25B2";opacity:1;color:var(--cyan)}
th[data-s].desc::after{content:" \25BC";opacity:1;color:var(--cyan)}
.frow th{top:36px;padding:5px 6px;height:auto;background:#14283f;border-bottom:1px solid var(--line)}
.frow input{width:100%;min-width:54px;background:var(--bg1);border:1px solid var(--line);border-radius:6px;color:var(--tx);padding:4px 7px;font-size:.7rem;font-family:inherit}
.frow input:focus{outline:none;border-color:var(--cyan)}
td{padding:9px;border-top:1px solid var(--line);white-space:nowrap;vertical-align:middle}
tbody tr:nth-child(even) td{background:rgba(255,255,255,.015)}
tbody tr:hover td{background:rgba(57,194,255,.07)}
tr.crit td{background:rgba(255,93,108,.10)}
tr.warn td{background:rgba(255,176,32,.07)}
.badge{display:inline-block;font-size:.62rem;font-weight:800;border-radius:7px;padding:2px 8px;letter-spacing:.4px}
.b-ag{background:rgba(255,93,108,.18);color:var(--red);border:1px solid rgba(255,93,108,.4)}
.b-ok{background:rgba(47,208,138,.14);color:var(--green);border:1px solid rgba(47,208,138,.35)}
.b-sx{background:rgba(183,139,255,.15);color:var(--purple);border:1px solid rgba(183,139,255,.4);margin-left:4px}
.b-fds{background:rgba(255,176,32,.16);color:var(--orange);border:1px solid rgba(255,176,32,.45);margin-left:4px}
.hrs{font-weight:800}
.hrs.c{color:var(--red)} .hrs.w{color:var(--orange)} .hrs.k{color:var(--green)}
.rota{color:var(--cyan);font-weight:700}
.agc{max-width:230px;overflow:hidden;text-overflow:ellipsis}

/* cards mobile */
.cards{display:none}
@media(max-width:860px){
  body{padding:14px 10px 50px}
  .tblwrap{display:none}
  .cards{display:grid;grid-template-columns:1fr;gap:10px}
  .card{position:relative;background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:14px 14px 12px;overflow:hidden}
  .card::before{content:"";position:absolute;top:0;left:0;right:0;height:4px;background:linear-gradient(90deg,var(--cyan),#7fd8ff)}
  .card.crit::before{background:linear-gradient(90deg,var(--red),#ff8d97)}
  .card.warn::before{background:linear-gradient(90deg,var(--orange),#ffd27f)}
  .card .top{display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;gap:8px;flex-wrap:wrap}
  .card .ped{font-weight:900;font-size:1.05rem}
  .card .kv{display:grid;grid-template-columns:auto 1fr;gap:3px 10px;font-size:.78rem}
  .card .kv .k{color:var(--tx2);font-size:.68rem;text-transform:uppercase;letter-spacing:.4px;padding-top:2px}
  .fbar{grid-template-columns:1fr 1fr}
  h1{font-size:1.15rem}
}
footer{margin-top:26px;text-align:center;color:var(--tx2);font-size:.7rem;line-height:1.7}
</style>
</head>
<body>
<div class="wrap">
<header>
  <div>
    <h1>Retirada no <span class="air">Aeroporto</span></h1>
    <div class="sub">Cargas desembarcadas no aeroporto de destino &times; retirada pelo agente de cargas / t&eacute;cnico.
    Carga parada no aeroporto &eacute; risco t&eacute;rmico: a retirada deve acontecer <b>assim que a carga desembarca</b>.
    Fonte: Acompanhamento AWB (dtbTransporte) &mdash; mesma base do Tracking A&eacute;reo.</div>
  </div>
  <div class="upd">Atualizado em <b>__GERADO__</b> &middot; a cada 10 min</div>
</header>

<div class="friday" id="friday-banner">&#128680; HOJE &Eacute; SEXTA-FEIRA &mdash; PONTO CR&Iacute;TICO: toda carga que desembarcar hoje e n&atilde;o for retirada vai dormir o <u>FIM DE SEMANA INTEIRO</u> no aeroporto. Cobre a retirada AINDA HOJE.</div>

<div class="wkend">&#9888;&#65039; <b>SEXTA-FEIRA &eacute; ponto cr&iacute;tico de aten&ccedil;&atilde;o:</b> carga que desembarca na sexta e n&atilde;o &eacute; retirada no mesmo dia dorme <span class="rl">o fim de semana inteiro</span> no aeroporto &mdash; risco de excurs&atilde;o t&eacute;rmica e ocorr&ecirc;ncia junto ao Gerenciamento T&eacute;rmico. Linhas marcadas com <span class="badge b-sx">SEXTA</span> desembarcaram numa sexta-feira; <span class="badge b-fds">FIM DE SEMANA</span> indica que a carga pernoitou no aeroporto.</div>

<div class="kpis">
  <div class="kpi vermelho"><div class="v" id="k-ag">__K_AG__</div><div class="v2"><b>__K_AG_V__</b> volumes</div><div class="lbl">Aguardando retirada agora</div></div>
  <div class="kpi vermelho"><div class="v" id="k-cr">__K_CR__</div><div class="v2"><b>__K_CR_V__</b> volumes</div><div class="lbl">Cr&iacute;ticos &ge; 24h no aeroporto</div></div>
  <div class="kpi pu"><div class="v" id="k-fds">__K_FDS__</div><div class="v2"><b>__K_FDS_V__</b> volumes</div><div class="lbl">Sexta __ULTSEXTA__: dormiram no aeroporto</div></div>
  <div class="kpi gr"><div class="v" id="k-rh">__K_RH__</div><div class="v2"><b>__K_RH_V__</b> volumes</div><div class="lbl">Retirados hoje</div></div>
  <div class="kpi cy"><div class="v" id="k-md">__K_MD__h</div><div class="v2">&nbsp;</div><div class="lbl">Tempo m&eacute;dio de retirada (7 dias)</div></div>
</div>

<div class="fbar">
  <div><label>Busca (pedido / AWB / agente / CIA)</label><input id="f-busca" type="text" placeholder="Ex.: 569729, RODOTEC..."></div>
  <div><label>Base / Agente</label><select id="f-base"><option value="">Todas as bases</option>__BASES__</select></div>
  <div><label>Destino (aeroporto)</label><select id="f-dest"><option value="">Todos</option>__DESTINOS__</select></div>
  <div><label>Status</label><select id="f-st"><option value="">Todos</option><option value="A" selected>Aguardando retirada</option><option value="R">Retirados</option></select></div>
  <div><label>Per&iacute;odo (desembarque)</label><select id="f-per"><option value="7">7 dias</option><option value="15" selected>15 dias</option><option value="30">30 dias</option></select></div>
  <div><label>Ordena&ccedil;&atilde;o</label><select id="f-ord"><option value="h">Mais horas no aeroporto</option><option value="dn">Desembarque recente</option><option value="da">Desembarque antigo</option></select></div>
  <label class="chk"><input type="checkbox" id="f-sx"> S&oacute; sextas</label>
  <div class="low"><button class="btn" onclick="limpar()">Limpar</button><button class="btn" onclick="baixarCsv()">CSV</button></div>
</div>
<div class="cnt"><span id="cnt"></span> &nbsp;&middot;&nbsp; <span style="color:var(--cyan)">Dica: clique no t&iacute;tulo da coluna para ordenar (menor &rarr; maior / maior &rarr; menor)</span></div>

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
  VTC LOG &middot; Gerenciamento T&eacute;rmico &mdash; Retirada no Aeroporto<br>
  Crit&eacute;rios: <b style="color:var(--red)">cr&iacute;tico</b> &ge; 24h sem retirada &middot; <b style="color:var(--orange)">aten&ccedil;&atilde;o</b> &ge; 12h &middot; sexta-feira = ponto cr&iacute;tico (risco de fim de semana)<br>
  Volumetria: a coluna Vol. &eacute; o total de volumes da AWB; nos KPIs cada AWB &eacute; somada uma &uacute;nica vez (uma AWB pode ter v&aacute;rios pedidos)
</footer>
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
  document.getElementById("cnt").textContent = VIEW.length + " registro(s) \u00b7 " + VIEW.filter(r=>r.st==="A").length + " aguardando retirada \u00b7 " + totVol + " volumes (por AWB)";
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
      + '<div class="top"><span class="ped">'+esc(r.p)+'</span><span>'+badges(r)+'</span></div>'
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
