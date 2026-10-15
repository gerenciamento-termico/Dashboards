# -*- coding: utf-8 -*-
"""
Semeia no Firebase (quadro do SINCRONISMO_48H) as ocorrencias da planilha de
pendencias passada pelo Caio em 15/10/2026, casando por numero de pedido com
as pendencias atuais do ENTREGAS_VS_SINCRONISMO (st P/G, qualquer idade).

Chave do no: pedido|logger|entISO com [.#$[]/] -> _  (mesma regra da pagina).
Valor: PATCH {"obs": {"x": texto, "q": nome, "u": login, "t": epoch_ms}}
(preserva o campo "cob" se existir).
"""
import json
import re
import time
import urllib.request
from datetime import datetime
from pathlib import Path

FB = "https://gt-sincronismo-default-rtdb.firebaseio.com/sync48"
FONTE = Path(__file__).resolve().parent / "ENTREGAS_VS_SINCRONISMO.html"

RESP = {
    "Caio":   ("Caio Cedraz", "caio.cedraz"),
    "Tiago":  ("Tiago Cicero", "tiago.cicero"),
    "Marcus": ("Marcus Galvao", "marcus.galvao"),
    "Marlon": ("Marlon Henrique", "marlon.henrique"),
    "":       ("Caio Cedraz", "caio.cedraz"),  # sem responsavel na planilha
}

# pedido -> (texto da ocorrencia, responsavel)
OCORRENCIAS = {
    "570781": ("Enviado para GRU", ""),
    "570844": ("Ir\u00e1 sinc assim que receber novamente o equip", "Caio"),
    "571888": ("Barbalha - logger desc e agente n\u00e3o possui bateria", "Marcus"),
    "571719": ("Em retorno para base - solicitado sinc", "Tiago"),
    "571708": ("Imperatriz - logger desc e agente n\u00e3o possui bateria", "Marcus"),
    "571425": ("Solicitado sinc", "Marlon"),
    "571635": ("Solicitado sinc", "Marlon"),
    "571442": ("Solicitado sinc", "Marlon"),
    "572098": ("Solicitado sinc", "Marlon"),
    "571413": ("Solicitado sinc", "Caio"),
    "571160": ("Pedido devolvido", "Tiago"),
    "571194": ("Solicitado sinc", "Tiago"),
    "571508": ("Solicitado sinc", "Tiago"),
    "571713": ("Solicitado sinc", "Tiago"),
    "571468": ("Solicitado sinc", "Tiago"),
    "572348": ("Solicitado sinc", "Tiago"),
    "567357": ("Sem dados de movimenta\u00e7\u00e3o - rodovi\u00e1rio paletizado", "Tiago"),
    "567376": ("Sem dados de movimenta\u00e7\u00e3o - rodovi\u00e1rio paletizado", "Tiago"),
    "572018": ("Solicitado sinc", "Tiago"),
    "571637": ("Solicitado sinc", "Tiago"),
    "571696": ("Solicitado sinc", "Tiago"),
    "571297": ("Solicitado sinc", "Caio"),
    "571870": ("Solicitado sinc", "Caio"),
    "564728": ("Sem dados - rodovi\u00e1rio paletizado", "Tiago"),
    "571665": ("Sem logger - rodovi\u00e1rio paletizado", "Tiago"),
    "571672": ("Sem logger - rodovi\u00e1rio paletizado", "Tiago"),
    "570249": ("Sem logger - rodovi\u00e1rio paletizado", "Tiago"),
    "571699": ("Sem logger - rodovi\u00e1rio paletizado", "Tiago"),
    "571673": ("Sem logger - rodovi\u00e1rio paletizado", "Tiago"),
    "571271": ("Sem logger - rodovi\u00e1rio paletizado", "Tiago"),
    "568714": ("Sem logger - rodovi\u00e1rio paletizado", "Tiago"),
    "571291": ("Ares sincronizados, confirmar elitech", ""),
}


def parse_dt(txt):
    txt = (txt or "").strip()
    if not txt:
        return None
    try:
        return datetime.strptime(txt, "%d/%m/%Y %H:%M")
    except ValueError:
        return None


def chave(p, l, ent):
    bruto = f"{p}|{l}|{ent}"
    return re.sub(r"[.#$\[\]/]", "_", bruto)


def patch(url, payload):
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"),
                                 method="PATCH", headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.status


def main():
    html = FONTE.read_text(encoding="utf-8")
    m = re.search(r"const ROWS\s*=\s*(\[.*?\]);", html, re.DOTALL)
    rows = json.loads(m.group(1).replace("NaN", "null"))
    pend = [r for r in rows if r.get("st") in ("P", "G")]

    agora_ms = int(time.time() * 1000)
    gravadas, pedidos_ok = 0, set()
    for r in pend:
        p = str(r.get("p") or "")
        if p not in OCORRENCIAS:
            continue
        ent = parse_dt(r.get("e")) or parse_dt(r.get("emb"))
        if ent is None:
            continue
        texto, resp_nome = OCORRENCIAS[p]
        nome, login = RESP[resp_nome]
        k = chave(p, str(r.get("l") or ""), ent.strftime("%Y-%m-%dT%H:%M"))
        url = f"{FB}/{urllib.parse.quote(k, safe='')}.json"
        st = patch(url, {"obs": {"x": texto, "q": nome, "u": login, "t": agora_ms}})
        if st == 200:
            gravadas += 1
            pedidos_ok.add(p)

    faltaram = sorted(set(OCORRENCIAS) - pedidos_ok)
    print(f"linhas gravadas: {gravadas} | pedidos atendidos: {len(pedidos_ok)}/{len(OCORRENCIAS)}")
    if faltaram:
        print("pedidos sem pendencia correspondente (nao gravados): " + ", ".join(faltaram))


if __name__ == "__main__":
    main()
