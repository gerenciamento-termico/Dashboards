# -*- coding: utf-8 -*-
"""Mede o tempo de cada fase do gerar_html_sync48."""
import time

t0 = time.time()
import gerar_html_sync48 as g  # noqa: E402

print(f"import (pesado por causa do modulo fonte): {time.time()-t0:.1f}s")

import json, re  # noqa: E402

html = g.FONTE.read_text(encoding="utf-8")
rows = json.loads(re.search(r"const ROWS\s*=\s*(\[.*?\]);", html, re.DOTALL).group(1).replace("NaN", "null"))
pend = [r for r in rows if r.get("st") in ("P", "G")]
pedidos = sorted({str(r.get("p") or "") for r in pend if r.get("p")})
tags = sorted({str(r.get("l") or "") for r in pend if r.get("l")})

t1 = time.time()
devs = g.fetch_dispositivos(pedidos)
print(f"fetch_dispositivos ({len(pedidos)} pedidos): {time.time()-t1:.1f}s")

t2 = time.time()
users = g.fetch_sync_users(tags)
print(f"fetch_sync_users ({len(tags)} tags): {time.time()-t2:.1f}s | nomes={len(users)}")
