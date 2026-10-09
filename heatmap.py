#!/usr/bin/env python3
"""Mapa de calor do vault Obsidian.

Grava `heat: hot | warm | cold` no frontmatter de cada nota, conforme quanto
ela (e as notas ligadas a ela) vêm recebendo atualizações. No grafo, três
grupos de cor (`[heat:hot]`, `[heat:warm]`, `[heat:cold]`) viram o mapa.
Também exporta show/graph-data.js, usado pela página animada show/index.html.

Como o calor é calculado:
- A cada execução, se o mtime de uma nota mudou desde a última vez, conta
  uma atualização (histórico em heat-state.json, ao lado deste script).
- own = soma de e^(-dias/7) de cada atualização dos últimos 60 dias.
- score = own + 0.25 × soma do own das notas ligadas (links em qualquer
  sentido) — um hub esquenta quando suas notas são atualizadas.
- hot: score >= 2 · warm: score >= 0.5 · cold: o resto.

Ao gravar o `heat`, restaura o mtime original da nota, para que a escrita do
próprio script não conte como atualização.

Uso:
  heatmap.py --vault PASTA [--obsidian-root PASTA] [--dry-run]

  --vault          pasta com as notas a analisar (pode ser o vault inteiro
                   ou uma subpasta dele)
  --obsidian-root  raiz do vault aberto no Obsidian, se --vault for uma
                   subpasta (usado nos links obsidian:// da página)

Sem argumentos, lê config.local.json ao lado do script:
  {"vault": "...", "obsidian_root": "...", "notion_folder": "Notion",
   "group_colors": {"Pasta": "#rrggbb"}, "write_heat": true}
Com "notion_folder", roda antes o notion_mirror.py (espelho do Notion no vault).
Com "write_heat": false, não grava o `heat` nas notas (por exemplo, quando o
plugin Ember Brain grava); ainda calcula o calor e exporta a página.
"""
import argparse
import collections
import json
import math
import os
import re
import time
import urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "heat-state.json")
DECAY_DAYS = 7  # e^(-dias/7): meia-vida de ~5 dias
WINDOW_DAYS = 60
NEIGHBOR_WEIGHT = 0.25
HOT, WARM = 2.0, 0.5

parser = argparse.ArgumentParser(description="Mapa de calor do vault Obsidian.")
parser.add_argument("--vault")
parser.add_argument("--obsidian-root")
parser.add_argument("--dry-run", action="store_true")
args = parser.parse_args()
try:
    config = json.load(open(os.path.join(HERE, "config.local.json")))
except FileNotFoundError:
    config = {}
VAULT = args.vault or config.get("vault")
if not VAULT:
    parser.error("informe --vault ou crie config.local.json")
VAULT = os.path.abspath(os.path.expanduser(VAULT))
ROOT = os.path.abspath(os.path.expanduser(args.obsidian_root or config.get("obsidian_root") or VAULT))
DRY = args.dry_run
WRITE = config.get("write_heat", True) is not False
now = time.time()

# Espelho do Notion (notion_mirror.py) antes do cálculo, se configurado.
# Falhas no Notion não impedem o mapa de calor.
if config.get("notion_folder") and not args.vault and not DRY:
    import subprocess
    import sys
    r = subprocess.run([sys.executable, os.path.join(HERE, "notion_mirror.py")], capture_output=True, text=True)
    print((r.stdout or "").strip() or f"notion: falhou ({(r.stderr or '').strip().splitlines()[-1:]})")


def vault_files():
    for root, dirs, files in os.walk(VAULT):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for f in files:
            if not f.startswith("."):
                yield os.path.relpath(os.path.join(root, f), VAULT)


files = list(vault_files())
notes = [f for f in files if f.endswith(".md")]

# Resolução de links no estilo do Obsidian (pelo nome do arquivo).
byname = collections.defaultdict(list)
for f in files:
    b = os.path.basename(f).lower()
    byname[b].append(f)
    if b.endswith(".md"):
        byname[b[:-3]].append(f)


def resolve(target):
    t = urllib.parse.unquote(target.split("#")[0].split("|")[0].strip())
    if not t:
        return None
    c = byname.get(os.path.basename(t).lower())
    return c[0] if c else None


neighbors = collections.defaultdict(set)
for f in notes:
    text = open(os.path.join(VAULT, f), errors="ignore").read()
    targets = re.findall(r"!?\[\[([^\]]+)\]\]", text)
    targets += [m for m in re.findall(r"\]\(([^)\s]+)\)", text) if not m.startswith("http")]
    for t in targets:
        d = resolve(t)
        if d and d != f and d.endswith(".md"):
            neighbors[f].add(d)
            neighbors[d].add(f)

# Histórico de atualizações.
try:
    state = json.load(open(STATE))
except (FileNotFoundError, json.JSONDecodeError):
    state = {}
for f in notes:
    mtime = os.stat(os.path.join(VAULT, f)).st_mtime
    entry = state.setdefault(f, {"mtime": 0, "updates": []})
    if abs(mtime - entry["mtime"]) > 1:
        entry["mtime"] = mtime
        entry["updates"].append(mtime)
    entry["updates"] = [u for u in entry["updates"] if now - u < WINDOW_DAYS * 86400]
for f in list(state):
    if f not in notes:
        del state[f]


def own(f):
    return sum(math.exp(-(now - u) / 86400 / DECAY_DAYS) for u in state[f]["updates"])


owns = {f: own(f) for f in notes}


def score_of(f):
    return owns[f] + NEIGHBOR_WEIGHT * sum(owns[n] for n in neighbors[f])


def heat_of(f):
    score = score_of(f)
    return "hot" if score >= HOT else "warm" if score >= WARM else "cold"


def set_heat(text, heat):
    line = f"heat: {heat}"
    m = re.match(r"---\n(.*?)\n---\n", text, re.S)
    if not m:
        return f"---\n{line}\n---\n{text}"
    fm = m.group(1)
    if re.search(r"^heat:.*$", fm, re.M):
        fm2 = re.sub(r"^heat:.*$", line, fm, count=1, flags=re.M)
    else:
        fm2 = fm + "\n" + line
    return text[: m.start(1)] + fm2 + text[m.end(1):]


counts = collections.Counter()
changed = 0
for f in notes:
    heat = heat_of(f)
    counts[heat] += 1
    path = os.path.join(VAULT, f)
    text = open(path, errors="ignore").read()
    if not WRITE:
        continue
    new = set_heat(text, heat)
    if new == text:
        continue
    changed += 1
    if DRY:
        continue
    st = os.stat(path)
    with open(path, "w") as fh:
        fh.write(new)
    os.utime(path, (st.st_atime, st.st_mtime))

if not DRY:
    json.dump(state, open(STATE, "w"))

    def created(path):
        st = os.stat(path)
        return getattr(st, "st_birthtime", st.st_ctime)  # birthtime só existe no macOS

    # Dados da página show/index.html (grafo animado do vault).
    index = {f: i for i, f in enumerate(notes)}
    nodes = [{
        "p": f,
        "n": os.path.basename(f)[:-3],
        "g": f.split(os.sep)[0] if os.sep in f else "",
        "h": heat_of(f),
        "s": round(score_of(f), 3),
        "b": int(created(os.path.join(VAULT, f)) * 1000),
    } for f in notes]
    links = sorted({tuple(sorted((index[a], index[b]))) for a in notes for b in neighbors[a]})
    show = os.path.join(HERE, "show")
    os.makedirs(show, exist_ok=True)
    with open(os.path.join(show, "graph-data.js"), "w") as fh:
        fh.write("window.VAULT_GRAPH = ")
        prefix = os.path.relpath(VAULT, ROOT)
        json.dump({
            "updated": int(now * 1000),
            "vault": os.path.basename(ROOT),
            "prefix": "" if prefix == "." else prefix + "/",
            "colors": config.get("group_colors", {}),
            "nodes": nodes,
            "links": links,
        }, fh, ensure_ascii=False)
        fh.write(";\n")

print(f"{time.strftime('%Y-%m-%d %H:%M')} hot={counts['hot']} warm={counts['warm']} "
      f"cold={counts['cold']} notas alteradas={changed}{' (dry-run)' if DRY else ''}"
      f"{'' if WRITE else ' (write_heat desligado)'}")
