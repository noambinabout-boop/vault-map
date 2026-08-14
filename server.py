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
  - par défaut = le dossier de travail courant,
    surchargeable au lancement par VAULT_MAP_TARGET, ou à la volée par index(path).
  - chaque outil accepte en plus un `path=` : il répond sur CE dossier pour l'appel,
    sans changer la cible. Sert à lire les .md d'un repo de code (README, docs/, ADR
    — le « pourquoi » qu'un repo-map ne voit pas) sans perdre le vault. Chaque cible
    interrogée garde son graphe en cache, donc l'aller-retour est gratuit.
  - un repo de code est reconnu à ses manifestes (package.json, pyproject.toml…) :
    dépendances, artefacts de build et caches sont alors ignorés.

Fraîcheur : le graphe est reconstruit dès qu'une note change (signature = mtime+taille
agrégés du vault), sinon réutilisé. Sur ~50 notes le parse est négligeable -> anti-désync
gratuit, jamais d'outline périmé (un outline qui ment est pire qu'un Read honnête).
"""
import os
import re
from typing import Optional

from mcp.server.fastmcp import FastMCP

import vault_map as vm

# Vault par défaut = le dossier courant. Pour pointer TOUJOURS le même vault quel que
# soit le projet où Claude est lancé, définir VAULT_MAP_TARGET dans la config du serveur
# MCP (cf. README) — sinon, index(path) recible à la volée.
DEFAULT_VAULT = os.getcwd()

mcp = FastMCP("vault-map")

STATE = {
    "target": os.path.abspath(os.environ.get("VAULT_MAP_TARGET") or DEFAULT_VAULT),
    # Une cible interrogée reste en cache : consulter les docs d'un repo ne coûte
    # donc pas la reconstruction du vault au retour.
    "cache": {},  # chemin absolu -> {"graph": …, "sig": …}
}


def _signature(target, mode=None):
    """Empreinte de la cible = somme (mtime, taille) de ses .md. Change => rebuild.
    Même périmètre que build() (vm.iter_md) : la fraîcheur ne peut pas se calculer
    sur un ensemble de fichiers différent de celui qui est indexé."""
    total = 0.0
    for path, _rel in vm.iter_md(target, mode):
        try:
            st = os.stat(path)
            total += st.st_mtime + st.st_size
        except OSError:
            pass
    return total


def _resolve_target(path=None):
    """La cible de CET appel : `path` s'il est fourni, sinon la cible courante."""
    if not path:
        return STATE["target"]
    return os.path.abspath(os.path.expanduser(path))


def _graph(path=None):
    """Graphe de la cible demandée, (re)construit seulement si elle a changé."""
    target = _resolve_target(path)
    sig = _signature(target)
    slot = STATE["cache"].get(target)
    if slot is None or slot["sig"] != sig:
        slot = {"graph": vm.build(target), "sig": sig}
        STATE["cache"][target] = slot
    return slot["graph"]


def _open(path=None):
    """(graphe, None) pour la cible de cet appel, ou (None, message d'erreur)."""
    target = _resolve_target(path)
    if not os.path.isdir(target):
        return None, (f"Dossier introuvable : {target}\n"
                      f"(cible courante : {STATE['target']})")
    return _graph(target), None


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


def _count(n):
    """« 1 fichier .md » / « 12 fichiers .md »."""
    return f"{n} fichier{'s' if n > 1 else ''} .md"


def _enclosing_heading(headings, lineno):
    """Le titre le plus SPÉCIFIQUE dont la plage [line, end_line] contient lineno
    (= la plus grande `line` qui englobe le hit -> la section H3 l'emporte sur son H2
    parent). None si le hit est au-dessus de tout titre (frontmatter / chapô)."""
    best = None
    for h in headings:
        if h["line"] <= lineno <= h["end_line"] and (best is None or h["line"] > best["line"]):
            best = h
    return best


@mcp.tool()
def index(path: str) -> str:
    """(Re)cible DURABLEMENT le serveur sur un vault / dossier de notes .md.
    Pour seulement consulter les docs d'un repo au passage, ne recible PAS : passe
    `path=` à l'outil voulu (vault_map, grep_notes, outline…), la cible courante est
    conservée. `index` sert à changer de vault pour toute la suite de la session."""
    if not os.path.isdir(path):
        return f"Dossier introuvable : {path}"
    STATE["target"] = os.path.abspath(os.path.expanduser(path))
    g = _graph()
    n = len(g["notes"])
    if n == 0:
        return f"Ciblé sur {STATE['target']}, mais aucun fichier .md trouvé."
    kind = ("repo de code : dépendances, build et caches ignorés"
            if g["mode"] == "repo" else "vault de notes")
    return (f"Ciblé sur {STATE['target']} : {_count(n)} indexés ({kind}). "
            f"Outils prêts (vault_map / outline / get_section / query / grep_notes).")


@mcp.tool()
def vault_map(path: Optional[str] = None) -> str:
    """Carte d'ensemble du vault : pour chaque note, son dossier PARA, son frontmatter
    clé (status/score/next_review), ses titres H2 et ses liens sortants. À appeler EN
    PREMIER pour t'orienter : ~1-2k tokens pour TOUT le vault au lieu d'ouvrir 10 notes.
    `path` (optionnel) : cartographier un AUTRE dossier pour ce seul appel, sans changer
    la cible courante. Sur un repo de CODE, rend ses .md (README, docs/, ADR, CHANGELOG)
    — le « pourquoi » du projet, que repo-map ne voit pas puisqu'il n'indexe que le code."""
    g, err = _open(path)
    if err:
        return err
    if not g["notes"]:
        return f"Aucun fichier .md dans {g['vault']}."
    by_folder = {}
    for rel, d in g["notes"].items():
        by_folder.setdefault(d["folder"], []).append((rel, d))
    label = "Carte des docs" if g["mode"] == "repo" else "Carte du vault"
    out = [f"# {label} — {_count(len(g['notes']))} ({g['vault']})"]
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
def outline(note: str, path: Optional[str] = None) -> str:
    """Table des matières d'une note : arbre des titres (indenté par niveau) + n° de
    lignes de chaque section. À utiliser AVANT de lire une note : sur une note-monstre
    (400+ lignes), ~95 % moins de tokens qu'un Read complet.
    `path` (optionnel) : chercher la note dans un AUTRE dossier pour ce seul appel
    (ex. le README ou un ADR d'un repo), sans changer la cible courante."""
    g, err = _open(path)
    if err:
        return err
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
def get_section(note: str, title: str, path: Optional[str] = None) -> str:
    """Le corps d'UNE section seulement (titre + contenu jusqu'au prochain titre de
    même niveau ou supérieur). À n'appeler que pour la section qui t'intéresse, jamais
    pour t'orienter (pour ça : outline). `title` = sous-chaîne, insensible à la casse.
    `path` (optionnel) : lire la section d'un fichier d'un AUTRE dossier pour ce seul
    appel (ex. la section « Install » du README d'un repo), sans changer la cible."""
    g, err = _open(path)
    if err:
        return err
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
    full = os.path.join(g["vault"], rel)
    with open(full, "r", encoding="utf-8", errors="replace") as fh:
        lines = fh.readlines()
    body = lines[h["line"] - 1: h["end_line"]]
    return f"# {rel}:{h['line']}-{h['end_line']}\n" + "".join(body)


@mcp.tool()
def grep_notes(pattern: str, max_results: int = 40, path: Optional[str] = None) -> str:
    """Recherche par CONTENU (regex, insensible à la casse) dans le TEXTE des notes,
    chaque hit SITUÉ dans sa note ET sa section englobante. Complément de query (qui
    filtre le frontmatter) et d'outline (qui ne rend que les titres) : à utiliser pour
    retrouver un littéral, une phrase, un [[lien]], un mot précis — là où la structure
    ne suffit pas. Rend « note › section (Ln) : ligne », ce qui situe le hit au lieu
    d'une ligne nue (remplace le Grep brut sur le vault).
    `path` (optionnel) : fouiller un AUTRE dossier pour ce seul appel, sans changer la
    cible courante — sur un repo de code, cherche dans ses .md (README, docs/, ADR)."""
    g, err = _open(path)
    if err:
        return err
    if not g["notes"]:
        return f"Aucun fichier .md dans {g['vault']}."
    try:
        rx = re.compile(pattern, re.IGNORECASE)
    except re.error as e:
        return f"Regex invalide : {e}"
    out = []
    n = 0
    for rel in sorted(g["notes"]):
        d = g["notes"][rel]
        full = os.path.join(g["vault"], rel)
        try:
            with open(full, "r", encoding="utf-8", errors="replace") as fh:
                lines = fh.readlines()
        except OSError:
            continue
        for i, line in enumerate(lines, start=1):
            if rx.search(line):
                h = _enclosing_heading(d["headings"], i)
                loc = _short(h["title"]) if h else "(chapô)"
                out.append(f"  {d['name']} › {loc} (L{i}):  {line.strip()}")
                n += 1
                if n >= max_results:
                    out.append(f"  … (coupé à {max_results} ; affine le motif)")
                    return f"# grep_notes(/{pattern}/) — {n}+ résultats\n" + "\n".join(out)
    if not n:
        return (f"Aucune correspondance pour /{pattern}/ dans {g['vault']} "
                f"({len(g['notes'])} fichiers .md).")
    return f"# grep_notes(/{pattern}/) — {n} résultat(s)\n" + "\n".join(out)


@mcp.tool()
def query(filter: str, path: Optional[str] = None) -> str:
    """Le "Dataview de Claude" : filtre les notes par frontmatter, sans ouvrir les notes.
    `filter` = une ou plusieurs clauses séparées par des virgules (toutes doivent matcher).
    Opérateurs : `:` (contient), `>` `<` `>=` `<=` (numérique/date).
    Pseudo-champs (pour chercher une note par son NOM) : `title:<mot>`, `path:<dossier>`.
    Clé inconnue -> erreur explicite listant les clés requêtables (pas un « aucune note »
    trompeur). Ex : `status:challengee, score>5` ; `path:Repo-map` ; `title:vault`.
    (Le pseudo-champ `path:` filtre le chemin des notes ; l'argument `path`, lui, change
    le dossier interrogé pour ce seul appel — sans changer la cible courante.)"""
    g, err = _open(path)
    if err:
        return err
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
