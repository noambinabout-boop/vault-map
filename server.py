"""server.py — serveur MCP "vault-map".

La FAÇADE : expose à Claude les outils pour s'orienter dans un vault Obsidian
SANS lire les notes entières. Le pendant markdown de repo-map (code).

Outils (V1 = 80 % de la valeur, cf. note d'idée) :
  vault_map()               -> carte d'ensemble du vault (dossier, frontmatter, H2, liens)
  outline(note)             -> arbre des titres d'une note + n° de lignes
  get_section(note, titre)  -> le corps d'UNE section seulement
  query(filter)             -> le "Dataview de Claude" : filtre les notes par frontmatter
  index(path)               -> (re)cible le serveur sur un autre vault/dossier

Cible (TARGET) :
  - par défaut = le vault second_cerveau (VAULT par défaut ci-dessous),
    surchargeable au lancement par VAULT_MAP_TARGET, ou à la volée par index(path).

Fraîcheur : le graphe est reconstruit dès qu'une note change (signature = mtime+taille
agrégés du vault), sinon réutilisé. Sur ~50 notes le parse est négligeable -> anti-désync
gratuit, jamais d'outline périmé (un outline qui ment est pire qu'un Read honnête).
"""
import os

from mcp.server.fastmcp import FastMCP

import vault_map as vm

# vault par défaut = le second cerveau de Noam
DEFAULT_VAULT = r"c:\Users\Noam\Desktop\Noam\obsidian\second_cerveau"

mcp = FastMCP("vault-map")

STATE = {
    "target": os.path.abspath(os.environ.get("VAULT_MAP_TARGET") or DEFAULT_VAULT),
    "graph": None,
    "sig": None,
}


def _signature(vault):
    """Empreinte du vault = somme (mtime, taille) des .md. Change => rebuild."""
    total = 0.0
    for root, dirs, names in os.walk(vault):
        dirs[:] = [d for d in dirs if d not in vm.EXCLUDE_DIRS]
        for fn in names:
            if fn.lower().endswith(".md"):
                try:
                    st = os.stat(os.path.join(root, fn))
                    total += st.st_mtime + st.st_size
                except OSError:
                    pass
    return total


def _graph():
    """Graphe courant, (re)construit si le vault a changé depuis le dernier appel."""
    sig = _signature(STATE["target"])
    if STATE["graph"] is None or sig != STATE["sig"]:
        STATE["graph"] = vm.build(STATE["target"])
        STATE["sig"] = sig
    return STATE["graph"]


def _short(title, n=42):
    """Tronque un titre H2 pour garder la carte compacte (les titres du CLAUDE.md /
    journaux sont très longs). Coupe au dernier mot avant n caractères."""
    title = title.strip()
    if len(title) <= n:
        return title
    cut = title[:n].rsplit(" ", 1)[0]
    return (cut or title[:n]) + "…"


def _fm_tag(fm):
    """Frontmatter clé, compact : [status score next_review] si présents."""
    parts = []
    for k in ("status", "score", "next_review"):
        if fm.get(k) and fm[k].lower() not in ("null", "none", ""):
            parts.append(f"{k}={fm[k]}")
    return f" [{'  '.join(parts)}]" if parts else ""


@mcp.tool()
def index(path: str) -> str:
    """(Re)cible le serveur sur un vault / dossier de notes .md et construit sa carte.
    Utile pour pointer un autre vault que celui par défaut. Les autres outils opèrent
    ensuite sur ce dossier."""
    if not os.path.isdir(path):
        return f"Dossier introuvable : {path}"
    STATE["target"] = os.path.abspath(path)
    STATE["graph"] = None
    g = _graph()
    n = len(g["notes"])
    if n == 0:
        return f"Ciblé sur {STATE['target']}, mais aucune note .md trouvée."
    return (f"Ciblé sur {STATE['target']} : {n} notes indexées. "
            f"Outils prêts (vault_map / outline / get_section / query).")


@mcp.tool()
def vault_map() -> str:
    """Carte d'ensemble du vault : pour chaque note, son dossier PARA, son frontmatter
    clé (status/score/next_review), ses titres H2 et ses liens sortants. À appeler EN
    PREMIER pour t'orienter : ~1-2k tokens pour TOUT le vault au lieu d'ouvrir 10 notes."""
    g = _graph()
    if not g["notes"]:
        return f"Aucune note dans {STATE['target']}."
    by_folder = {}
    for rel, d in g["notes"].items():
        by_folder.setdefault(d["folder"], []).append((rel, d))
    out = [f"# Carte du vault — {len(g['notes'])} notes ({STATE['target']})"]
    for folder in sorted(by_folder):
        out.append(f"\n## {folder}")
        for rel, d in sorted(by_folder[folder]):
            h2 = [_short(h["title"]) for h in d["headings"] if h["level"] == 2]
            line = f"- {d['name']} ({d['lines']}l){_fm_tag(d['frontmatter'])}"
            if h2:
                shown = " · ".join(h2[:6])
                if len(h2) > 6:
                    shown += f" · (+{len(h2) - 6})"
                line += "  ›  " + shown
            if d["links"]:
                line += "  →  " + " ".join(f"[[{x}]]" for x in d["links"][:6])
            out.append(line)
    return "\n".join(out)


@mcp.tool()
def outline(note: str) -> str:
    """Table des matières d'une note : arbre des titres (indenté par niveau) + n° de
    lignes de chaque section. À utiliser AVANT de lire une note : sur une note-monstre
    (400+ lignes), ~95 % moins de tokens qu'un Read complet."""
    g = _graph()
    rel, err = vm.resolve(g, note)
    if err:
        return err
    d = g["notes"][rel]
    if not d["headings"]:
        return f"{rel} ({d['lines']}l) : aucun titre (note plate)."
    out = [f"# Outline {rel} ({d['lines']}l)"]
    for h in d["headings"]:
        indent = "  " * (h["level"] - 1)
        out.append(f"{indent}{'#' * h['level']} {h['title']}  (L{h['line']}-{h['end_line']})")
    return "\n".join(out)


@mcp.tool()
def get_section(note: str, title: str) -> str:
    """Le corps d'UNE section seulement (titre + contenu jusqu'au prochain titre de
    même niveau ou supérieur). À n'appeler que pour la section qui t'intéresse, jamais
    pour t'orienter (pour ça : outline). `title` = sous-chaîne, insensible à la casse."""
    g = _graph()
    rel, err = vm.resolve(g, note)
    if err:
        return err
    d = g["notes"][rel]
    q = title.strip().lower()
    matches = [h for h in d["headings"] if q in h["title"].lower()]
    if not matches:
        titles = " · ".join(h["title"] for h in d["headings"])
        return f"Aucune section « {title} » dans {rel}. Titres : {titles}"
    if len(matches) > 1:
        exact = [h for h in matches if h["title"].lower() == q]
        matches = exact or matches[:1]
    h = matches[0]
    path = os.path.join(STATE["target"], rel)
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        lines = fh.readlines()
    body = lines[h["line"] - 1: h["end_line"]]
    return f"# {rel}:{h['line']}-{h['end_line']}\n" + "".join(body)


@mcp.tool()
def query(filter: str) -> str:
    """Le "Dataview de Claude" : filtre les notes par frontmatter, sans ouvrir les notes.
    `filter` = une ou plusieurs clauses séparées par des virgules (toutes doivent matcher).
    Opérateurs : `:` (contient), `>` `<` `>=` `<=` (numérique/date).
    Pseudo-champs (pour chercher une note par son NOM) : `title:<mot>`, `path:<dossier>`.
    Clé inconnue -> erreur explicite listant les clés requêtables (pas un « aucune note »
    trompeur). Ex : `status:challengee, score>5` ; `path:Repo-map` ; `title:vault`."""
    g = _graph()
    clauses = [c for c in (x.strip() for x in filter.split(",")) if c]
    if not clauses:
        return "Filtre vide. Ex : status:go, score>=8"

    # Clés réellement requêtables = frontmatter présent dans le vault + pseudo-champs.
    # But : rater BRUYAMMENT sur une clé inconnue plutôt que rendre « aucune note »
    # (qui laisse croire à un vault vide alors que la clé n'existe pas).
    fm_keys = set()
    for d in g["notes"].values():
        if d["frontmatter"]:
            fm_keys.update(d["frontmatter"].keys())
    queryable = fm_keys | set(vm._PSEUDO_KEYS)
    for c in clauses:
        key = vm._clause_key(c)
        if key is None:
            return (f"Clause « {c} » sans opérateur reconnu. "
                    f"Opérateurs : `:` (contient), `>` `<` `>=` `<=` (num/date).")
        if key not in queryable:
            return (f"Clé « {key} » inconnue (ni frontmatter, ni pseudo-champ).\n"
                    f"Clés requêtables : {', '.join(sorted(queryable))}.\n"
                    f"Pour trouver une note par son nom : `title:<mot>` ou `path:<dossier>` "
                    f"(ou vault_map() pour la carte d'ensemble).")

    hits = []
    for rel, d in g["notes"].items():
        # frontmatter + pseudo-champs (title = nom de fichier, path = chemin relatif)
        ctx = dict(d["frontmatter"] or {})
        ctx["title"] = d["name"]
        ctx["path"] = rel
        if all(vm._match_clause(ctx, c) for c in clauses):
            hits.append((rel, d))
    if not hits:
        return f"Aucune note ne matche « {filter} »."
    out = [f"# query(\"{filter}\") — {len(hits)} note(s)"]
    for rel, d in sorted(hits, key=lambda x: x[0]):
        out.append(f"  {rel}{_fm_tag(d['frontmatter'])}")
    return "\n".join(out)


if __name__ == "__main__":
    mcp.run()
