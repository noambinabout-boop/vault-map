# -*- coding: utf-8 -*-
"""vault_map.py — le MOTEUR de vault-map.

Transpose le principe de repo-map (« ne jamais charger un fichier entier pour
s'orienter ») au MARKDOWN d'un vault Obsidian. L'équivalent du « symbole » de code
= la SECTION d'une note (un titre `#…` + son corps).

Zéro dépendance : le markdown se parse à la regex.
  - frontmatter = bloc YAML de tête entre --- … ---
  - titres      = lignes `#`, `##`, … (hors blocs de code ```)
  - liens       = [[wikilink]] (réutilise le LINK_RE de cerveau-viz/build_graph.py)

Produit un index { note -> frontmatter, headings (avec plages de lignes), links }.
La façade MCP (server.py) consomme cet index.
"""
import os
import re

EXCLUDE_DIRS = {".obsidian", ".claude", ".trash", ".git", "node_modules"}

# capture la cible d'un [[lien]] (avant | ou #) — identique à cerveau-viz/build_graph.py
LINK_RE = re.compile(r"\[\[([^\]|#]+)")
# faux liens = exemples génériques cités dans du texte (ex. le CLAUDE.md), pas de vraies cibles
_FAKE_LINKS = {"liens", "lien", "…", "...", "lien", "nom", "name", "their-name"}
# un titre markdown : niveau (nb de #) + texte. On rejette les # collés (pas d'espace).
HEADING_RE = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")
FENCE_RE = re.compile(r"^\s*(```|~~~)")


def norm(p):
    """minuscules + slashs avant (même normalisation que le hook/viewer cerveau-viz)."""
    return p.replace("\\", "/").lower()


def _parse_frontmatter(lines):
    """Bloc YAML de tête (--- … ---). Parseur plat volontairement simple : le
    frontmatter du vault est { clé: valeur } sur une ligne. Rend un dict {clé: str}
    + l'index de la 1re ligne de contenu (après le 2e ---)."""
    if not lines or lines[0].strip() != "---":
        return {}, 0
    fm = {}
    for i in range(1, len(lines)):
        line = lines[i].rstrip("\n")
        if line.strip() == "---":
            return fm, i + 1
        m = re.match(r"^([A-Za-z0-9_-]+):\s*(.*)$", line)
        if m:
            fm[m.group(1).strip()] = m.group(2).strip()
    return fm, 0  # pas de --- fermant -> pas un vrai frontmatter


def parse_note(path):
    """Parse une note .md -> dict { frontmatter, headings, links, lines }.
    headings : liste triée { level, title, line, end_line } où end_line = juste avant
    le prochain titre de niveau <= (la section « possède » son corps jusque-là)."""
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        lines = fh.readlines()
    n = len(lines)
    fm, body_start = _parse_frontmatter(lines)

    headings = []
    in_fence = False
    for i in range(body_start, n):
        raw = lines[i]
        if FENCE_RE.match(raw):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        m = HEADING_RE.match(raw)
        if m:
            headings.append({
                "level": len(m.group(1)),
                "title": m.group(2).strip(),
                "line": i + 1,          # 1-indexé
                "end_line": n,          # provisoire, corrigé ci-dessous
            })

    # end_line = ligne juste avant le prochain titre de niveau <= au sien
    for idx, h in enumerate(headings):
        end = n
        for j in range(idx + 1, len(headings)):
            if headings[j]["level"] <= h["level"]:
                end = headings[j]["line"] - 1
                break
        h["end_line"] = end

    links = []
    seen = set()
    for m in LINK_RE.finditer("".join(lines)):
        t = m.group(1).strip()
        low = t.lower()
        if t and low not in seen and low not in _FAKE_LINKS:
            seen.add(low)
            links.append(t)

    return {"frontmatter": fm, "headings": headings, "links": links, "lines": n}


def build(vault):
    """Scanne le vault, parse chaque .md -> index requêtable.
    { vault, notes: {rel -> {name, folder, frontmatter, headings, links, lines}},
      by_name: {name_lower -> [rel, …]} }"""
    vault = os.path.abspath(vault)
    notes = {}
    by_name = {}
    for root, dirs, names in os.walk(vault):
        dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]
        for fn in names:
            if not fn.lower().endswith(".md"):
                continue
            path = os.path.join(root, fn)
            rel = os.path.relpath(path, vault).replace("\\", "/")
            name = os.path.splitext(fn)[0]
            folder = rel.split("/")[0] if "/" in rel else "(racine)"
            try:
                parsed = parse_note(path)
            except OSError:
                continue
            notes[rel] = {"name": name, "folder": folder, **parsed}
            by_name.setdefault(name.lower(), []).append(rel)
    return {"vault": vault, "notes": notes, "by_name": by_name}


def resolve(graph, ref):
    """Résout une référence de note (nom façon [[lien]] OU chemin relatif) -> rel.
    Rend (rel, None) si trouvé, (None, message) sinon (ambigu / inconnu)."""
    ref = ref.strip()
    # chemin relatif direct
    key = ref.replace("\\", "/")
    if key in graph["notes"]:
        return key, None
    if not key.lower().endswith(".md") and (key + ".md") in graph["notes"]:
        return key + ".md", None
    # par nom de note
    base = os.path.splitext(os.path.basename(key))[0].lower()
    hits = graph["by_name"].get(base, [])
    if len(hits) == 1:
        return hits[0], None
    if len(hits) > 1:
        return None, f"Note « {ref} » ambiguë : {', '.join(hits)}. Précise le chemin."
    return None, f"Note introuvable : « {ref} »."


# ---- comparateurs pour query() sur le frontmatter ----------------------------

_OPS = [
    (">=", lambda a, b: _num_or_str(a) >= _num_or_str(b)),
    ("<=", lambda a, b: _num_or_str(a) <= _num_or_str(b)),
    (">",  lambda a, b: _num_or_str(a) >  _num_or_str(b)),
    ("<",  lambda a, b: _num_or_str(a) <  _num_or_str(b)),
    (":",  lambda a, b: b.lower() in a.lower()),  # contient (souple)
]


def _num_or_str(v):
    """Convertit en float si possible (score, comparaisons numériques), sinon str."""
    try:
        return float(v)
    except (ValueError, TypeError):
        return v if isinstance(v, str) else str(v)


# pseudo-champs adossés au chemin/nom de fichier (pas du frontmatter) : chercher une
# note par son NOM est le réflexe naturel -> on le rend requêtable comme le reste.
_PSEUDO_KEYS = ("title", "path")


def _clause_key(clause):
    """Extrait la clé d'une clause (avant l'opérateur), ou None si aucun opérateur reconnu."""
    for op, _ in _OPS:
        if op in clause:
            return clause.split(op, 1)[0].strip()
    return None


def _match_clause(fm, clause):
    for op, fn in _OPS:
        if op in clause:
            key, val = clause.split(op, 1)
            key, val = key.strip(), val.strip()
            if key not in fm:
                return False
            try:
                return fn(fm[key], val)
            except (TypeError, AttributeError):
                # comparaison num vs str incompatible, ou valeur non-str (liste tags) -> pas de match
                return False
    return False  # clause sans opérateur reconnu
