#!/usr/bin/env python3
"""Gera show/demo-data.js: um vault fictício para demonstrar a página (index.html?demo)."""
import json
import os
import random
import time

random.seed(7)
now = time.time() * 1000
DAY = 86400 * 1000
nodes, links = [], []


def note(path, heat="cold", score=0.0, age_days=None):
    age = age_days if age_days is not None else random.uniform(5, 180)
    nodes.append({
        "p": path + ".md",
        "n": os.path.basename(path),
        "g": path.split("/")[0] if "/" in path else "",
        "h": heat,
        "s": score,
        "b": int(now - age * DAY),
    })
    return len(nodes) - 1


def link(a, b):
    links.append([min(a, b), max(a, b)])


index = note("00 Índice", "warm", 0.8, 200)
mocs = {name: note(f"01 MOCs/MOC - {name}", "cold", 0.3, 190)
        for name in ["Produtos", "Pesquisa", "Conteúdo", "Carreira", "Ferramentas"]}
for m in mocs.values():
    link(index, m)

# Dois produtos com releases: um muito ativo (em brasa), outro morno.
for product, releases, heat in [("Atlas", 28, "hot"), ("Orion", 9, "hot"), ("Vega", 5, "cold")]:
    hub = note(f"02 Projetos/{product}", heat, 3.0 if heat == "hot" else 0.2, 150)
    link(hub, mocs["Produtos"])
    for i in range(releases):
        recent = heat == "hot" and i > releases * .6
        r = note(f"02 Projetos/release {product.lower()} v0.{i + 1}.0",
                 "warm" if recent else "cold", 0.9 if recent else 0.1, 140 - i * (130 / releases))
        link(r, hub)

# Diário: muitas daily notes ligadas a um hub.
journal = note("Daily Notes/Diário", "warm", 1.2, 170)
for i in range(70):
    d = note(f"Daily Notes/{time.strftime('%Y-%m-%d', time.localtime((now - (160 - i * 2.2) * DAY) / 1000))}",
             "warm" if i > 62 else "cold", 0.7 if i > 62 else 0, 160 - i * 2.2)
    link(d, journal)
    if random.random() < .15:
        link(d, random.choice(list(mocs.values())))

# Pesquisa e skills: pequenas constelações.
for name, moc, n in [("03 Pesquisa", "Pesquisa", 14), ("04 Skills", "Ferramentas", 8)]:
    prev = None
    for i in range(n):
        x = note(f"{name}/{name.split()[1]} {i + 1}", random.choice(["cold", "cold", "warm"]), 0.3)
        link(x, mocs[moc]) if prev is None or random.random() < .4 else link(x, prev)
        prev = x

# Notas soltas na raiz, encadeadas.
prev = None
for i in range(18):
    x = note(f"Nota {i + 1}")
    if prev is not None and random.random() < .7:
        link(x, prev)
    prev = x

links = sorted({tuple(l) for l in links})
out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "show", "demo-data.js")
with open(out, "w") as fh:
    fh.write("window.VAULT_GRAPH = ")
    json.dump({"updated": int(now), "vault": "Demo", "prefix": "", "nodes": nodes, "links": links}, fh, ensure_ascii=False)
    fh.write(";\n")
print(f"{len(nodes)} notas, {len(links)} conexões -> show/demo-data.js")
