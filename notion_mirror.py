#!/usr/bin/env python3
"""Espelha a estrutura do Notion dentro do vault do Obsidian.

Cada página do Notion compartilhada com a integração vira uma nota leve em
<vault>/<notion_folder>/, com:
- título, link para abrir no Notion e a página-mãe ([[...]]);
- os links para outras páginas: subpáginas, menções, blocos "link to page",
  links notion.so no texto e relações de banco de dados.

O texto das páginas NÃO é copiado — só a estrutura, para o grafo.

A data de modificação de cada nota passa a ser a última edição no Notion,
então o heatmap.py conta uma edição no Notion como atualização da nota.
Só reescreve uma nota quando algo mudou, e preserva a linha `heat:`.

Token da integração (somente leitura), em ordem de prioridade:
1. variável de ambiente NOTION_TOKEN
2. Keychain do macOS: serviço "notion-ember-brain"
   (security add-generic-password -a "$USER" -s notion-ember-brain -w)

Uso: notion_mirror.py [--vault PASTA] [--folder Notion] [--dry-run]
Sem --vault, usa "vault" e "notion_folder" do config.local.json.
"""
import argparse
import calendar
import json
import os
import re
import subprocess
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "notion-state.json")
API = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"
KEYCHAIN_SERVICE = "notion-ember-brain"
UUID = re.compile(r"([0-9a-f]{32}|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})", re.I)


def norm(i):
    return i.replace("-", "").lower() if i else i


def get_token():
    tok = os.environ.get("NOTION_TOKEN")
    if tok:
        return tok.strip()
    try:
        out = subprocess.run(["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-w"],
                             capture_output=True, text=True, check=True)
        return out.stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


class Notion:
    def __init__(self, token):
        self.token = token
        self.calls = 0

    def request(self, method, path, body=None):
        data = json.dumps(body).encode() if body is not None else None
        for attempt in range(5):
            req = urllib.request.Request(API + path, data=data, method=method, headers={
                "Authorization": f"Bearer {self.token}",
                "Notion-Version": NOTION_VERSION,
                "Content-Type": "application/json",
            })
            try:
                self.calls += 1
                with urllib.request.urlopen(req, timeout=30) as r:
                    return json.load(r)
            except urllib.error.HTTPError as e:
                if e.code == 429 or e.code >= 500:  # limite de taxa / instabilidade: espera e tenta de novo
                    time.sleep(float(e.headers.get("Retry-After", 1 + attempt * 2)))
                    continue
                raise
            finally:
                time.sleep(0.34)  # ~3 requisições/s, o limite médio da API
        raise RuntimeError(f"Notion API falhou repetidamente em {path}")

    def paginate(self, method, path, body=None):
        cursor = None
        while True:
            if method == "POST":
                b = dict(body or {}, page_size=100, **({"start_cursor": cursor} if cursor else {}))
                res = self.request("POST", path, b)
            else:
                sep = "&" if "?" in path else "?"
                res = self.request("GET", f"{path}{sep}page_size=100" + (f"&start_cursor={cursor}" if cursor else ""))
            yield from res.get("results", [])
            if not res.get("has_more"):
                return
            cursor = res.get("next_cursor")


def plain(rich):
    return "".join(t.get("plain_text", "") for t in rich or [])


def title_of(obj):
    if obj["object"] == "database":
        return plain(obj.get("title")) or "Sem título"
    for prop in obj.get("properties", {}).values():
        if prop.get("type") == "title":
            return plain(prop.get("title")) or "Sem título"
    return "Sem título"


def links_in_rich(rich):
    out = set()
    for t in rich or []:
        if t.get("type") == "mention":
            m = t["mention"]
            if m.get("type") in ("page", "database"):
                out.add(norm(m[m["type"]]["id"]))
        href = t.get("href") or ""
        if "notion.so" in href or href.startswith("/"):
            ids = UUID.findall(href.split("?")[0])
            if ids:
                out.add(norm(ids[-1]))
    return out


def block_links(api, block_id, depth=0):
    """Links de uma página: percorre os blocos (sem entrar em subpáginas, que são nós próprios)."""
    out = set()
    for b in api.paginate("GET", f"/blocks/{block_id}/children"):
        t = b["type"]
        if t in ("child_page", "child_database"):
            out.add(norm(b["id"]))
            continue
        body = b.get(t, {})
        if t == "link_to_page":
            target = body.get(body.get("type"))
            if target:
                out.add(norm(target))
        out |= links_in_rich(body.get("rich_text"))
        out |= links_in_rich(body.get("caption"))
        # Cópias de synced block apontam para o original, que já é lido onde está.
        is_synced_copy = t == "synced_block" and body.get("synced_from")
        if b.get("has_children") and depth < 8 and not is_synced_copy:
            out |= block_links(api, b["id"], depth + 1)
    return out


def property_links(page):
    out = set()
    for prop in page.get("properties", {}).values():
        if prop.get("type") == "relation":
            out |= {norm(r["id"]) for r in prop.get("relation", [])}
        elif prop.get("type") in ("rich_text", "title"):
            out |= links_in_rich(prop.get(prop["type"]))
    return out


def parent_of(obj):
    p = obj.get("parent", {})
    kind = p.get("type")
    return norm(p.get(kind)) if kind in ("page_id", "database_id", "block_id") else None


def safe_name(title):
    name = re.sub(r'[\\/:*?"<>|#^\[\]]', "-", title).strip(" .") or "Sem título"
    return name[:120]


def main():
    ap = argparse.ArgumentParser(description="Espelha a estrutura do Notion no vault.")
    ap.add_argument("--vault")
    ap.add_argument("--folder")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    try:
        config = json.load(open(os.path.join(HERE, "config.local.json")))
    except FileNotFoundError:
        config = {}
    vault = args.vault or config.get("vault")
    if not vault:
        ap.error("informe --vault ou crie config.local.json")
    vault = os.path.abspath(os.path.expanduser(vault))
    folder = os.path.join(vault, args.folder or config.get("notion_folder") or "Notion")

    token = get_token()
    if not token:
        raise SystemExit("Sem token do Notion: defina NOTION_TOKEN ou guarde no Keychain (serviço notion-ember-brain).")
    api = Notion(token)

    try:
        state = json.load(open(STATE))
    except (FileNotFoundError, json.JSONDecodeError):
        state = {}
    pages = state.setdefault("pages", {})  # id -> {edited, links, file}

    objs = {}
    for o in api.paginate("POST", "/search", {}):
        if o.get("archived") or o.get("in_trash"):
            continue
        objs[norm(o["id"])] = o

    # Links: só refaz a leitura dos blocos de quem mudou desde a última vez.
    refreshed = 0
    for i, o in objs.items():
        prev = pages.get(i, {})
        if prev.get("edited") == o["last_edited_time"] and "links" in prev:
            continue
        links = set()
        if o["object"] == "page":  # em bancos de dados, "properties" é o esquema, não valores
            links |= property_links(o)
            try:
                links |= block_links(api, o["id"])
            except urllib.error.HTTPError:
                pass  # bloco sem acesso: fica só com as propriedades
        pages[i] = dict(prev, edited=o["last_edited_time"], links=sorted(links))
        refreshed += 1
    for i in list(pages):
        if i not in objs:
            pages[i]["gone"] = True

    # Nomes de arquivo estáveis: título; em caso de repetição, título + id curto.
    titles = {i: title_of(o) for i, o in objs.items()}
    count = {}
    for t in titles.values():
        count[safe_name(t)] = count.get(safe_name(t), 0) + 1
    names = {i: safe_name(t) if count[safe_name(t)] == 1 else f"{safe_name(t)} ({i[:6]})" for i, t in titles.items()}

    os.makedirs(folder, exist_ok=True)
    written = removed = 0
    for i, o in objs.items():
        name = names[i]
        par = parent_of(o)
        links = [l for l in pages[i]["links"] if l in names and l != i]
        lines = [
            "---",
            f"notion_id: {i}",
            f"notion_url: {o.get('url', '')}",
            f"notion_tipo: {'banco de dados' if o['object'] == 'database' else 'página'}",
            f"criado_em: {o['created_time'][:10]}",
            f"editado_em: {o['last_edited_time'][:10]}",
            "tags:",
            "  - notion",
            "---",
            "",
            f"# {titles[i]}",
            "",
            f"[Abrir no Notion]({o.get('url', '')})",
            "",
        ]
        if par in names:
            lines += [f"**Dentro de:** [[{names[par]}]]", ""]
        if links:
            lines += ["## Links", *[f"- [[{names[l]}]]" for l in links], ""]
        content = "\n".join(lines)

        path = os.path.join(folder, name + ".md")
        old_file = pages[i].get("file")
        if old_file and old_file != name and os.path.exists(os.path.join(folder, old_file + ".md")):
            if not args.dry_run:
                os.rename(os.path.join(folder, old_file + ".md"), path)  # página renomeada no Notion
        current = open(path).read() if os.path.exists(path) else None
        heat = re.search(r"^heat:.*$", current or "", re.M)
        if heat:  # preserva o heat: gravado pelo heatmap.py
            content = content.replace("tags:\n", heat.group(0) + "\ntags:\n", 1)
        pages[i]["file"] = name
        if content == current:
            continue
        written += 1
        if args.dry_run:
            continue
        with open(path, "w") as fh:
            fh.write(content)
        edited = calendar.timegm(time.strptime(o["last_edited_time"][:19], "%Y-%m-%dT%H:%M:%S"))  # UTC
        os.utime(path, (edited, edited))

    # Remove só notas espelhadas de páginas que sumiram (apagadas ou sem acesso).
    for i in [i for i, p in pages.items() if p.get("gone")]:
        f = pages[i].get("file")
        path = os.path.join(folder, f + ".md") if f else None
        if path and os.path.exists(path) and f"notion_id: {i}" in open(path).read():
            removed += 1
            if not args.dry_run:
                os.remove(path)
        if not args.dry_run:
            del pages[i]

    if not args.dry_run:
        json.dump(state, open(STATE, "w"))
    print(f"{time.strftime('%Y-%m-%d %H:%M')} notion: {len(objs)} páginas, {refreshed} relidas, "
          f"{written} notas gravadas, {removed} removidas, {api.calls} chamadas{' (dry-run)' if args.dry_run else ''}")


if __name__ == "__main__":
    main()
