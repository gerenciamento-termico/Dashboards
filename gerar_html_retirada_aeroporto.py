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

Tema claro premium (cards brancos, icones Font Awesome, abas de filtro rapido).
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parent
sys.path.insert(0, str(WORKSPACE))

from gerar_html_entregas_vs_sincronismo import _sqlserver_cfg, load_env  # noqa: E402

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


def fetch_stage_info(pedidos: list[str]) -> dict[str, dict]:
    """Dataloggers e romaneios por pedido (vtc_stage.documentos).

    loggers=0 = carga seca ou logger nao registrado no stage; por isso filtramos, nao excluimos.
    """
    from sqlalchemy import bindparam, create_engine, text
    from sqlalchemy.engine import URL

    env = load_env(WORKSPACE / ".env.vtc_stage")
    if not env.get("VTC_STAGE_HOST"):
        raise RuntimeError("VTC_STAGE ausente (.env.vtc_stage)")
    url = URL.create(
        "postgresql+psycopg2",
        username=env["VTC_STAGE_USER"], password=env["VTC_STAGE_PASSWORD"],
        host=env["VTC_STAGE_HOST"], port=int(env.get("VTC_STAGE_PORT") or 5432),
        database=env["VTC_STAGE_NAME"],
    )
    query = text(
        """
        SELECT TRIM(nr_pedido::text) AS pedido,
               COUNT(DISTINCT NULLIF(TRIM(ds_tag), '')) AS loggers,
               STRING_AGG(DISTINCT NULLIF(TRIM(nr_romaneio::text), ''), ', ') AS romaneios
        FROM vtc_stage.documentos
        WHERE TRIM(nr_pedido::text) IN :peds
        GROUP BY 1
        """
    ).bindparams(bindparam("peds", expanding=True))
    out: dict[str, dict] = {}
    engine = create_engine(url, pool_pre_ping=True)
    try:
        with engine.connect() as conn:
            for i in range(0, len(pedidos), 500):
                chunk = pedidos[i : i + 500]
                for row in conn.execute(query, {"peds": chunk}):
                    out[str(row[0])] = {"lg": int(row[1] or 0), "rom": str(row[2] or "").strip()}
    finally:
        engine.dispose()
    return out


def fetch_ctes(pedidos: list[str]) -> dict[str, list[dict]]:
    """CT-e da VTC por pedido via dtbTransporte.

    tbdMovimento.nr_Conhecimento = numero do CT-e emitido pela VTC (nao confundir
    com o CT-e da CIA aerea da vwstmawbs); chave de 44 digitos em
    tbdLoteCTeMovimento.ds_ChaveCTe (CNPJ emissor 24.893.687 = VTC).
    A serie e extraida da propria chave (posicoes 23-25).
    """
    cfg = _sqlserver_cfg()
    if not cfg["host"] or not cfg["user"]:
        raise RuntimeError("dtbTransporte ausente (.env AURA_SQLSERVER_*)")
    import pytds

    peds = sorted({str(p).strip() for p in pedidos if str(p).strip()})
    alvo = set(peds)
    query_mov = """
        SELECT m.id_Movimento,
               LTRIM(RTRIM(CAST(m.nr_Referencia AS varchar(40)))) AS ref,
               LTRIM(RTRIM(CAST(m.nr_PedidoCliente AS varchar(40)))) AS cli,
               LTRIM(RTRIM(COALESCE(m.nr_Conhecimento, ''))) AS cte,
               LTRIM(RTRIM(COALESCE(m.nr_AWB, ''))) AS awb,
               m.dt_Cadastro
        FROM tbdMovimento m
        WHERE m.dt_Cadastro >= DATEADD(day, -120, GETDATE())
          AND NULLIF(LTRIM(RTRIM(m.nr_Conhecimento)), '') IS NOT NULL
          AND (m.nr_Referencia IN ({ph}) OR m.nr_PedidoCliente IN ({ph}))
    """
    query_chave = """
        SELECT id_Movimento, MAX(LTRIM(RTRIM(COALESCE(ds_ChaveCTe, '')))) AS chave
        FROM tbdLoteCTeMovimento
        WHERE id_Movimento IN ({ph})
        GROUP BY id_Movimento
    """
    movimentos: list[dict] = []
    with pytds.connect(
        server=cfg["host"], port=int(cfg["port"]), database=str(cfg["database"]),
        user=str(cfg["user"]), password=str(cfg["password"]),
        timeout=180, login_timeout=30, validate_host=False,
    ) as conn:
        cur = conn.cursor()
        for i in range(0, len(peds), 800):
            chunk = peds[i : i + 800]
            ph = ",".join(["%s"] * len(chunk))
            cur.execute(query_mov.format(ph=ph), tuple(chunk) + tuple(chunk))
            for id_mov, ref, cli, cte, awb, cad in cur.fetchall():
                movimentos.append({
                    "id": int(id_mov), "ref": str(ref or "").strip(),
                    "cli": str(cli or "").strip(), "cte": str(cte or "").strip(),
                    "awb": "".join(ch for ch in str(awb or "") if ch.isdigit()),
                    "cad": cad,
                })
        chaves: dict[int, str] = {}
        ids = sorted({m["id"] for m in movimentos})
        for i in range(0, len(ids), 500):
            chunk = ids[i : i + 500]
            ph = ",".join(["%s"] * len(chunk))
            cur.execute(query_chave.format(ph=ph), tuple(chunk))
            for id_mov, chave in cur.fetchall():
                chaves[int(id_mov)] = str(chave or "").strip()

    out: dict[str, list[dict]] = {}
    for m in movimentos:
        chave = chaves.get(m["id"], "")
        serie = chave[22:25].lstrip("0") if len(chave) == 44 else ""
        item = {
            "awb": m["awb"], "nr_cte": m["cte"], "serie": serie,
            "chave": chave, "emissao": m["cad"],
        }
        for chave_ped in {m["ref"], m["cli"]}:
            if chave_ped and chave_ped in alvo:
                lst = out.setdefault(chave_ped, [])
                if not any(
                    x["nr_cte"] == item["nr_cte"] and x["awb"] == item["awb"]
                    and x["chave"] == item["chave"] for x in lst
                ):
                    lst.append(item)
    return out


def build_model(raw: list[dict], stage: dict[str, dict], ctes: dict[str, list[dict]]) -> dict:
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
        info = stage.get(p) or {}
        awb_txt = str(r["AWB"] or "").strip()
        awb_dig = "".join(ch for ch in awb_txt if ch.isdigit())
        cte_lst = ctes.get(p) or []
        cte = next((x for x in cte_lst if x["awb"] and x["awb"] == awb_dig), None)
        if cte is None and cte_lst:
            cte = max(cte_lst, key=lambda x: (x["emissao"] is not None, x["emissao"]))
        rows.append({
            "p": p,
            "awb": awb_txt,
            "cte": (cte["nr_cte"] + ("/" + cte["serie"] if cte["serie"] else "")) if cte and cte["nr_cte"] else "",
            "chv": cte["chave"] if cte else "",
            "rom": info.get("rom", ""),
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
            "lg": info.get("lg", 0),
        })

    # ultima sexta-feira (se hoje e sexta, considera hoje)
    dias_desde_sexta = (agora.weekday() - 4) % 7
    ultima_sexta = (agora - timedelta(days=dias_desde_sexta)).date()

    # KPIs consideram apenas carga monitorada (pedidos com datalogger);
    # carga seca continua disponivel na grade via filtro "Dataloggers: Todos/Sem".
    monitorados = [r for r in rows if r["lg"] > 0]
    aguardando = [r for r in monitorados if r["st"] == "A"]
    criticos = [r for r in aguardando if r["h"] >= 24]
    hoje = agora.date().isoformat()
    retirados_hoje = [r for r in monitorados if r["re"] and r["re"][:10] == hoje]
    fds_ultima_sexta = [
        r for r in monitorados
        if r["sx"] and r["de"][:10] == ultima_sexta.isoformat() and (r["n"] >= 1 or r["st"] == "A")
    ]
    ret_7d = [r for r in monitorados if r["st"] == "R" and r["de"] >= (agora - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M")]
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
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.2/css/all.min.css">
<style>
:root{
  --bg:#eef2f8; --card:#ffffff; --line:#e3e8f0; --tx:#101828; --tx2:#667085;
  --blue:#2563eb; --blue2:#1d4ed8;
  --red:#dc2626; --redbg:#fef1f1; --redtile:linear-gradient(135deg,#fecaca,#fee2e2);
  --orange:#d97706; --orangebg:#fef6e7; --orangetile:linear-gradient(135deg,#fde68a,#fef3c7);
  --green:#16a34a; --greenbg:#eefcf2; --greentile:linear-gradient(135deg,#bbf7d0,#dcfce7);
  --purple:#7c3aed; --purplebg:#f4f0fe; --purpletile:linear-gradient(135deg,#ddd0fb,#ede9fe);
  --cyan:#0891b2; --cyanbg:#ecfbfe; --cyantile:linear-gradient(135deg,#a5f0fc,#cffafe);
  --bluetile:linear-gradient(135deg,#bfdbfe,#dbeafe);
  --shadow:0 1px 2px rgba(16,24,40,.06), 0 6px 18px rgba(16,24,40,.05);
  --shadow2:0 4px 10px rgba(16,24,40,.08), 0 12px 30px rgba(16,24,40,.08);
}
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:'Inter',system-ui,sans-serif;background:linear-gradient(180deg,#e9eef7 0%,var(--bg) 240px);color:var(--tx);min-height:100vh}
.wrap{max-width:1960px;margin:0 auto;padding:26px 20px 60px}

/* ===== header ===== */
.top{display:flex;flex-wrap:wrap;justify-content:space-between;align-items:center;gap:14px;margin-bottom:20px}
.tleft{display:flex;gap:15px;align-items:center}
.hicon{width:52px;height:52px;min-width:52px;border-radius:15px;background:linear-gradient(135deg,#1d4ed8,#0891b2);display:flex;align-items:center;justify-content:center;color:#fff;font-size:1.3rem;box-shadow:0 6px 16px rgba(29,78,216,.3)}
h1{font-size:1.42rem;font-weight:800;letter-spacing:-.3px}
h1 small{display:block;font-size:.68rem;font-weight:700;color:var(--tx2);text-transform:uppercase;letter-spacing:1.2px;margin-top:2px}
.sub{color:var(--tx2);font-size:.79rem;margin-top:6px;max-width:760px;line-height:1.55}
.upd{font-size:.73rem;color:var(--tx2);background:var(--card);border:1px solid var(--line);border-radius:11px;padding:9px 15px;white-space:nowrap;box-shadow:var(--shadow)}
.upd i{color:var(--blue);margin-right:6px}
.upd b{color:var(--tx)}

/* banner sexta (aparece so as sextas) */
.friday{display:none;background:#fef1f1;border:1px solid #fecaca;border-left:5px solid var(--red);border-radius:12px;padding:14px 18px;margin-bottom:16px;font-weight:700;font-size:.9rem;color:#991b1b;box-shadow:var(--shadow);animation:pulse 1.8s ease-in-out infinite}
.friday.on{display:block}
.friday i{margin-right:8px}
@keyframes pulse{0%,100%{box-shadow:var(--shadow)}50%{box-shadow:0 0 0 4px rgba(220,38,38,.12)}}

/* ===== kpis premium ===== */
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(212px,1fr));gap:14px;margin-bottom:16px}
.kpi{background:var(--card);border:1px solid var(--line);border-radius:15px;padding:18px;display:flex;gap:14px;align-items:center;box-shadow:var(--shadow);transition:transform .18s, box-shadow .18s}
.kpi:hover{transform:translateY(-2px);box-shadow:var(--shadow2)}
.kpi .ic{width:50px;height:50px;min-width:50px;border-radius:13px;display:flex;align-items:center;justify-content:center;font-size:1.15rem}
.kpi.vermelho .ic{background:var(--redtile);color:var(--red)}
.kpi.pu .ic{background:var(--purpletile);color:var(--purple)}
.kpi.gr .ic{background:var(--greentile);color:var(--green)}
.kpi.cy .ic{background:var(--cyantile);color:var(--cyan)}
.kpi .v{font-size:1.62rem;font-weight:800;line-height:1.05;letter-spacing:-.4px}
.kpi.vermelho .v{color:var(--red)} .kpi.pu .v{color:var(--purple)} .kpi.gr .v{color:var(--green)} .kpi.cy .v{color:var(--cyan)}
.kpi .v2{font-size:.7rem;color:var(--tx2);font-weight:700;margin-top:2px}
.kpi .v2 b{color:var(--tx)}
.kpi .lbl{font-size:.64rem;color:var(--tx2);text-transform:uppercase;letter-spacing:.5px;margin-top:4px;line-height:1.35;font-weight:700}

/* ===== abas de filtro rapido (ex-menu lateral) ===== */
.tabs{display:flex;gap:8px;margin-bottom:14px;overflow-x:auto;padding-bottom:2px}
.tab{display:inline-flex;align-items:center;gap:8px;background:var(--card);border:1px solid var(--line);border-radius:999px;color:var(--tx2);padding:9px 17px;font-size:.78rem;font-weight:700;cursor:pointer;white-space:nowrap;font-family:inherit;box-shadow:var(--shadow);transition:all .15s}
.tab:hover{border-color:var(--blue);color:var(--blue)}
.tab.active{background:linear-gradient(135deg,var(--blue),var(--blue2));border-color:var(--blue2);color:#fff;box-shadow:0 5px 14px rgba(37,99,235,.32)}
.tab i{font-size:.8rem}

/* ===== filtros ===== */
.fcard{background:var(--card);border:1px solid var(--line);border-radius:15px;padding:16px;margin-bottom:12px;box-shadow:var(--shadow)}
.fbar{display:grid;grid-template-columns:repeat(5,1fr);gap:12px;align-items:end}
.fbar label{display:block;font-size:.61rem;color:var(--tx2);text-transform:uppercase;letter-spacing:.5px;margin-bottom:5px;font-weight:700}
.fbar input,.fbar select{width:100%;background:#f8fafc;border:1px solid var(--line);border-radius:9px;color:var(--tx);padding:9px 12px;font-size:.82rem;font-family:inherit;max-width:100%}
.fbar select{text-overflow:ellipsis}
.fbar input:focus,.fbar select:focus{outline:none;border-color:var(--blue);background:#fff;box-shadow:0 0 0 3px rgba(37,99,235,.1)}
.fbar label.chk{display:flex;align-items:center;gap:7px;font-size:.76rem;font-weight:800;color:var(--orange);white-space:nowrap;margin:0 0 9px;padding:0;text-transform:none;letter-spacing:0;cursor:pointer;align-self:end}
.fbar label.chk input{width:auto;accent-color:var(--orange)}
.fbar .low{display:flex;gap:8px;align-items:center;justify-content:flex-end}
.btn{display:inline-flex;align-items:center;gap:7px;background:linear-gradient(135deg,var(--blue),var(--blue2));border:1px solid var(--blue2);border-radius:9px;color:#fff;padding:9px 16px;font-size:.78rem;font-weight:700;cursor:pointer;white-space:nowrap;font-family:inherit;transition:opacity .15s;box-shadow:0 4px 12px rgba(37,99,235,.25)}
.btn:hover{opacity:.9}
.btn.ghost{background:#fff;color:var(--tx);border-color:var(--line);box-shadow:var(--shadow)}
.btn.ghost:hover{border-color:var(--blue);color:var(--blue);opacity:1}

/* filtros por coluna (fora da grade) */
.colf{display:grid;grid-template-columns:repeat(11,1fr);gap:8px;background:var(--card);border:1px solid var(--line);border-radius:15px;padding:12px 14px;margin-bottom:10px;box-shadow:var(--shadow)}
.colf label{display:block;font-size:.57rem;color:var(--tx2);text-transform:uppercase;letter-spacing:.4px;margin-bottom:3px;font-weight:700;white-space:nowrap}
.colf input{width:100%;background:#f8fafc;border:1px solid var(--line);border-radius:8px;color:var(--tx);padding:6px 9px;font-size:.74rem;font-family:inherit}
.colf input:focus{outline:none;border-color:var(--blue);background:#fff;box-shadow:0 0 0 3px rgba(37,99,235,.1)}

.cnt{font-size:.75rem;color:var(--tx2);margin:0 2px 10px}
.cnt b{color:var(--tx)}
.cnt .dica{color:var(--blue)}
.cnt .dica i{margin-right:4px}

/* ===== grade (maior) ===== */
.tblwrap{background:var(--card);border:1px solid var(--line);border-radius:15px;overflow:auto;max-height:84vh;box-shadow:var(--shadow)}
table{width:100%;border-collapse:collapse;font-size:.79rem;min-width:1140px}
th{position:sticky;top:0;height:40px;background:#f8fafc;color:var(--tx2);text-transform:uppercase;font-size:.62rem;letter-spacing:.4px;padding:9px 7px;text-align:left;white-space:nowrap;z-index:2;border-bottom:1px solid var(--line)}
th[data-s]{cursor:pointer;user-select:none;transition:color .15s}
th[data-s]:hover{color:var(--blue)}
th[data-s]::after{content:" \2195";opacity:.35}
th[data-s].asc::after{content:" \25B2";opacity:1;color:var(--blue)}
th[data-s].desc::after{content:" \25BC";opacity:1;color:var(--blue)}
td{padding:8px 7px;border-top:1px solid var(--line);white-space:nowrap;vertical-align:middle}
tbody tr:nth-child(even) td{background:#fafbfd}
tbody tr:hover td{background:#eff6ff}
tr.crit td{background:#fef1f1}
tr.crit:hover td{background:#fee2e2}
tr.warn td{background:#fef6e7}
tr.warn:hover td{background:#fef3c7}
.badge{display:inline-block;font-size:.6rem;font-weight:800;border-radius:6px;padding:2px 7px;letter-spacing:.3px}
.b-ag{background:#fee2e2;color:#b91c1c}
.b-ok{background:#dcfce7;color:#15803d}
.b-sx{background:#ede9fe;color:var(--purple);margin-left:4px}
.b-fds{background:#fef3c7;color:#b45309;margin-left:4px}
.b-seca{background:#f1f5f9;color:#64748b}
.hrs{font-weight:800}
.hrs.c{color:var(--red)} .hrs.w{color:var(--orange)} .hrs.k{color:var(--green)}
.rota{color:var(--blue);font-weight:700}
.agc{max-width:200px;overflow:hidden;text-overflow:ellipsis}
@media(min-width:1700px){.agc{max-width:340px}}

/* ===== cards mobile ===== */
.cards{display:none}
@media(max-width:1360px){.colf{grid-template-columns:repeat(6,1fr)}}
@media(max-width:960px){
  .wrap{padding:16px 12px 50px}
  .fbar{grid-template-columns:1fr 1fr}
  h1{font-size:1.12rem}
  .sub{display:none}
}
@media(max-width:860px){
  .tblwrap{display:none}
  .colf{display:none}
  .cards{display:grid;grid-template-columns:1fr;gap:10px}
  .card{position:relative;background:var(--card);border:1px solid var(--line);border-radius:14px;padding:14px 14px 12px;overflow:hidden;box-shadow:var(--shadow)}
  .card::before{content:"";position:absolute;top:0;left:0;bottom:0;width:4px;background:var(--blue)}
  .card.crit::before{background:var(--red)}
  .card.warn::before{background:var(--orange)}
  .card .topo{display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;gap:8px;flex-wrap:wrap}
  .card .ped{font-weight:900;font-size:1.02rem}
  .card .kv{display:grid;grid-template-columns:auto 1fr;gap:3px 10px;font-size:.78rem}
  .card .kv .k{color:var(--tx2);font-size:.66rem;text-transform:uppercase;letter-spacing:.4px;padding-top:2px;font-weight:700}
}
footer{margin-top:28px;text-align:center;color:var(--tx2);font-size:.7rem;line-height:1.7}
</style>
</head>
<body>
<div class="wrap">

<header class="top">
  <div class="tleft">
    <div class="hicon"><i class="fa-solid fa-plane-arrival"></i></div>
    <div>
      <h1>Retirada no Aeroporto<small>VTC LOG &middot; Gerenciamento T&eacute;rmico</small></h1>
      <div class="sub">Cargas desembarcadas no aeroporto de destino &times; retirada pelo agente de cargas / t&eacute;cnico.
      Carga parada no aeroporto &eacute; risco t&eacute;rmico: a retirada deve acontecer <b>assim que a carga desembarca</b>.</div>
    </div>
  </div>
  <div class="upd"><i class="fa-solid fa-rotate"></i>Atualizado em <b>__GERADO__</b> &middot; a cada 10 min</div>
</header>

<div class="friday" id="friday-banner"><i class="fa-solid fa-circle-exclamation"></i>HOJE &Eacute; SEXTA-FEIRA &mdash; PONTO CR&Iacute;TICO: toda carga que desembarcar hoje e n&atilde;o for retirada vai dormir o <u>FIM DE SEMANA INTEIRO</u> no aeroporto. Cobre a retirada AINDA HOJE.</div>

<div class="kpis">
  <div class="kpi vermelho"><div class="ic"><i class="fa-solid fa-hourglass-half"></i></div><div><div class="v">__K_AG__</div><div class="v2"><b>__K_AG_V__</b> volumes</div><div class="lbl">Aguardando retirada agora</div></div></div>
  <div class="kpi vermelho"><div class="ic"><i class="fa-solid fa-triangle-exclamation"></i></div><div><div class="v">__K_CR__</div><div class="v2"><b>__K_CR_V__</b> volumes</div><div class="lbl">Cr&iacute;ticos &ge; 24h no aeroporto</div></div></div>
  <div class="kpi pu"><div class="ic"><i class="fa-solid fa-calendar-week"></i></div><div><div class="v">__K_FDS__</div><div class="v2"><b>__K_FDS_V__</b> volumes</div><div class="lbl">Sexta __ULTSEXTA__: dormiram no aeroporto</div></div></div>
  <div class="kpi gr"><div class="ic"><i class="fa-solid fa-circle-check"></i></div><div><div class="v">__K_RH__</div><div class="v2"><b>__K_RH_V__</b> volumes</div><div class="lbl">Retirados hoje</div></div></div>
  <div class="kpi cy"><div class="ic"><i class="fa-solid fa-stopwatch"></i></div><div><div class="v">__K_MD__h</div><div class="v2">&nbsp;</div><div class="lbl">Tempo m&eacute;dio de retirada (7 dias)</div></div></div>
</div>

<div class="tabs">
  <button class="tab" onclick="preset('', this)"><i class="fa-solid fa-table-list"></i> Vis&atilde;o geral</button>
  <button class="tab active" onclick="preset('A', this)"><i class="fa-solid fa-hourglass-half"></i> Aguardando retirada</button>
  <button class="tab" onclick="preset('crit', this)"><i class="fa-solid fa-triangle-exclamation"></i> Cr&iacute;ticos &ge; 24h</button>
  <button class="tab" onclick="preset('sx', this)"><i class="fa-solid fa-calendar-week"></i> Sextas-feiras</button>
  <button class="tab" onclick="preset('R', this)"><i class="fa-solid fa-circle-check"></i> Retirados</button>
</div>

<div class="fcard">
<div class="fbar">
  <div><label>Busca (pedido / AWB / agente / CIA)</label><input id="f-busca" type="text" placeholder="Ex.: 569729, RODOTEC..."></div>
  <div><label>Base / Agente</label><select id="f-base"><option value="">Todas as bases</option>__BASES__</select></div>
  <div><label>Destino (aeroporto)</label><select id="f-dest"><option value="">Todos</option>__DESTINOS__</select></div>
  <div><label>Status</label><select id="f-st"><option value="">Todos</option><option value="A" selected>Aguardando retirada</option><option value="R">Retirados</option></select></div>
  <div><label>Dataloggers</label><select id="f-lg"><option value="C" selected>Com logger (monitorada)</option><option value="S">Sem logger (carga seca)</option><option value="">Todos</option></select></div>
  <div><label>Per&iacute;odo (desembarque)</label><select id="f-per"><option value="7">7 dias</option><option value="15" selected>15 dias</option><option value="30">30 dias</option></select></div>
  <div><label>Ordena&ccedil;&atilde;o</label><select id="f-ord"><option value="h">Mais horas no aeroporto</option><option value="dn">Desembarque recente</option><option value="da">Desembarque antigo</option></select></div>
  <label class="chk"><input type="checkbox" id="f-sx"> S&oacute; sextas</label>
  <div class="low"><button class="btn ghost" onclick="limpar()"><i class="fa-solid fa-eraser"></i> Limpar</button><button class="btn" onclick="baixarCsv()"><i class="fa-solid fa-file-csv"></i> Baixar CSV</button></div>
</div>
</div>

<div class="colf">
  <div><label>Pedido</label><input class="cf" data-col="p" placeholder="pedido"></div>
  <div><label>CTE</label><input class="cf" data-col="cte" placeholder="n&ordm; CT-e"></div>
  <div><label>Romaneio</label><input class="cf" data-col="rom" placeholder="romaneio"></div>
  <div><label>AWB</label><input class="cf" data-col="awb" placeholder="AWB"></div>
  <div><label>Rota</label><input class="cf" data-col="rota" placeholder="GRU, REC..."></div>
  <div><label>CIA</label><input class="cf" data-col="cia" placeholder="cia"></div>
  <div><label>Base / Agente</label><input class="cf" data-col="ag" placeholder="base / agente"></div>
  <div><label>Desembarque</label><input class="cf" data-col="de" placeholder="dd/mm"></div>
  <div><label>Retirada</label><input class="cf" data-col="re" placeholder="dd/mm"></div>
  <div><label>&#8805; Horas</label><input class="cf" data-col="h" placeholder="ex.: 24"></div>
  <div><label>&#8805; Noites</label><input class="cf" data-col="n" placeholder="ex.: 1"></div>
</div>

<div class="cnt"><span id="cnt"></span> &nbsp;&middot;&nbsp; <span class="dica"><i class="fa-solid fa-arrow-down-wide-short"></i>Clique no t&iacute;tulo da coluna para ordenar (menor &rarr; maior / maior &rarr; menor)</span></div>

<div class="tblwrap">
<table>
  <thead><tr>
    <th data-s="p">Pedido</th><th data-s="cte" title="CT-e emitido pela VTC (numero/serie; passe o mouse na celula para ver a chave)">CTE VTC</th><th data-s="rom">Romaneio</th><th data-s="awb">AWB</th><th data-s="rota">Rota</th><th data-s="cia">CIA</th><th data-s="ag">Agente / Base</th>
    <th data-s="vol" title="Volumes da AWB">Vol.</th>
    <th data-s="lg" title="Dataloggers no pedido (vtc_stage)">Loggers</th>
    <th data-s="de">Desembarque</th><th data-s="re">Retirada</th><th data-s="h">Horas</th><th data-s="n">Noites</th><th data-s="st">Status</th>
  </tr></thead>
  <tbody id="tb"></tbody>
</table>
</div>
<div class="cards" id="cards"></div>

<footer>
  VTC LOG &middot; Gerenciamento T&eacute;rmico &mdash; Retirada no Aeroporto &middot; Fonte: Acompanhamento AWB (dtbTransporte), mesma base do Tracking A&eacute;reo<br>
  Crit&eacute;rios: <b style="color:var(--red)">cr&iacute;tico</b> &ge; 24h sem retirada &middot; <b style="color:var(--orange)">aten&ccedil;&atilde;o</b> &ge; 12h &middot; sexta-feira = ponto cr&iacute;tico (risco de fim de semana)<br>
  Volumetria: a coluna Vol. &eacute; o total de volumes da AWB; nos KPIs cada AWB &eacute; somada uma &uacute;nica vez (uma AWB pode ter v&aacute;rios pedidos)<br>
  KPIs consideram apenas <b>carga monitorada</b> (pedidos com datalogger no vtc_stage); &quot;SECA&quot; = sem tag registrada &mdash; use o filtro Dataloggers para ver tudo
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
  const lg = document.getElementById("f-lg").value;
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
    if (lg === "C" && r.lg === 0) return false;
    if (lg === "S" && r.lg > 0) return false;
    if (so && !r.sx) return false;
    if (colf.length && !matchCols(r, colf)) return false;
    if (q){
      const alvo = (r.p+" "+r.awb+" "+r.cte+" "+r.chv+" "+r.rom+" "+r.ag+" "+r.cia+" "+r.o+" "+r.d).toLowerCase();
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
      + '<td title="'+esc(r.chv||'sem chave')+'">'+(r.cte ? esc(r.cte) : '\u2014')+'</td>'
      + '<td>'+(r.rom ? esc(r.rom) : '\u2014')+'</td>'
      + '<td>'+esc(r.awb)+'</td>'
      + '<td class="rota">'+esc(r.o)+' \u2192 '+esc(r.d)+'</td>'
      + '<td>'+esc(r.cia)+'</td>'
      + '<td class="agc" title="'+esc(r.ag)+'">'+esc(r.ag)+'</td>'
      + '<td><b>'+r.vol+'</b></td>'
      + '<td>'+(r.lg>0 ? '<b style="color:var(--blue)">'+r.lg+'</b>' : '<span class="badge b-seca">SECA</span>')+'</td>'
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
      + '<span class="k">AWB</span><span>'+esc(r.awb)+' \u00b7 <b>'+r.vol+'</b> vol. \u00b7 '+(r.lg>0 ? '<b>'+r.lg+'</b> logger'+(r.lg===1?'':'s') : 'carga seca')+'</span>'
      + '<span class="k">CTE</span><span>'+(r.cte ? esc(r.cte) : '\u2014')+(r.rom ? ' \u00b7 rom. '+esc(r.rom) : '')+'</span>'
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
  document.querySelectorAll(".tab").forEach(t => t.classList.remove("active"));
  if (el) el.classList.add("active");
  apply();
}

function limpar(){
  document.getElementById("f-busca").value = "";
  document.getElementById("f-base").value = "";
  document.getElementById("f-dest").value = "";
  document.getElementById("f-st").value = "";
  document.getElementById("f-lg").value = "C";
  document.getElementById("f-per").value = "15";
  document.getElementById("f-ord").value = "h";
  document.getElementById("f-sx").checked = false;
  document.querySelectorAll(".cf").forEach(i => { i.value = ""; });
  SORT = null;
  document.querySelectorAll("th[data-s]").forEach(t => t.classList.remove("asc","desc"));
  document.querySelectorAll(".tab").forEach(t => t.classList.remove("active"));
  document.querySelectorAll(".tab")[0].classList.add("active");
  apply();
}

function baixarCsv(){
  const cab = ["pedido","cte","chave_cte","romaneio","awb","volumes_awb","loggers","origem","destino","cia","agente","desembarque","retirada","horas","noites","status","sexta","fim_de_semana"];
  const linhas = VIEW.map(r => [r.p,r.cte,r.chv,'"'+r.rom+'"',r.awb,r.vol,r.lg,r.o,r.d,r.cia,'"'+r.ag.replace(/"/g,'""')+'"',r.de,r.re||"",r.h,r.n,r.st==="A"?"AGUARDANDO":"RETIRADO",r.sx?"SIM":"",r.fds?"SIM":""].join(";"));
  const blob = new Blob(["\ufeff"+cab.join(";")+"\n"+linhas.join("\n")], {type:"text/csv;charset=utf-8"});
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = "retirada_aeroporto.csv";
  document.body.appendChild(a);
  a.click();
  setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 3000);
}

["f-busca","f-base","f-dest","f-st","f-lg","f-per","f-sx"].forEach(id => {
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
    pedidos = sorted({str(r["Pedido"]).strip() for r in raw})
    stage = fetch_stage_info(pedidos)
    ctes = fetch_ctes(pedidos)
    model = build_model(raw, stage, ctes)
    write_html(model)
    k = model["kpi"]
    monitorados = sum(1 for r in model["rows"] if r["lg"] > 0)
    print(
        f"OK RETIRADA_AEROPORTO.html | pedidos={len(model['rows'])} (monitorados={monitorados}) | "
        f"aguardando={k['aguardando']} criticos24h={k['criticos']} "
        f"fds_sexta={k['fds']} retirados_hoje={k['retirados_hoje']} media7d={k['media_7d']}h"
    )


if __name__ == "__main__":
    main()
