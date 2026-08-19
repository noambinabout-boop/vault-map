"""server.py — serveur MCP "vault-map".

La FAÇADE : expose à Claude les outils pour s'orienter dans un vault Obsidian
SANS lire les notes entières. Le pendant markdown de repo-map (code).

Outils (V1 = 80 % de la valeur, cf. note d'idée) :
  vault_map()               -> carte des dossiers + leurs notes-hub (coût borné)
  vault_map(folder=…)       -> zoom sur un dossier (frontmatter, H2, liens de chaque note)
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


def _notes(n):
    """« 1 note » / « 12 notes » — un rapport qui écrit « 1 notes » se relit mal."""
    return f"{n} note{'s' if n > 1 else ''}"


def _count(n):
    """« 1 fichier .md » / « 12 fichiers .md »."""
    return f"{n} fichier{'s' if n > 1 else ''} .md"


BUDGET_DEFAUT = 6000  # caractères ≈ 1,7k tokens. Le coût de la carte est BORNÉ par
# construction, il ne se promet plus dans une docstring : à 40 notes comme à 4000, la
# sortie tient dans ce plafond et annonce ce qu'elle a dû laisser de côté.


def _entrants(g):
    """Nombre de liens ENTRANTS par note. Une note très citée est un point d'entrée réel
    du vault (un hub), pas un fichier de plus : c'est ce qui mérite d'être montré quand
    on ne peut pas tout montrer."""
    counts = {}
    for rel, d in g["notes"].items():
        for raw in d["links"]:
            tgt, _ = vm.resolve(g, raw)
            if tgt and tgt != rel:
                counts[tgt] = counts.get(tgt, 0) + 1
    return counts


def _ligne_note(d):
    """La ligne détaillée d'une note : nom (lignes) [frontmatter] › titres H2 → liens."""
    line = f"- {d['name']} ({d['lines']}l){_fm_tag(d['frontmatter'])}"
    h2 = [_short(h["title"]) for h in d["headings"] if h["level"] == 2]
    if h2:
        shown = " · ".join(h2[:6])
        if len(h2) > 6:
            shown += f" · (+{len(h2) - 6})"
        line += "  ›  " + shown
    if d["links"]:
        line += "  →  " + " ".join(f"[[{x}]]" for x in d["links"][:6])
    return line


def _tenir_budget(items, budget, poids, reserve=0, forcer_un=True):
    """Garde le début de `items` (déjà trié par importance) qui tient dans `budget`.
    Rend (gardés, nb_omis). budget=0 -> tout. `reserve` = place gardée pour l'en-tête et
    la ligne de troncature, pour que le budget borne la sortie ENTIÈRE et pas seulement
    le corps : une limite qu'on dépasse de 2 % est une limite à laquelle on cesse de
    croire. Ne coupe JAMAIS en silence : l'appelant doit annoncer le nombre d'omis,
    sinon une carte tronquée se lit comme complète."""
    if not budget:
        return list(items), 0
    gardes, taille = [], max(0, reserve)
    for it in items:
        c = poids(it)
        # `forcer_un` : mieux vaut une ligne qui deborde qu'une carte vide, pour la liste
        # PRINCIPALE. Pour un bloc secondaire (les notes directes d'un resume), non : le
        # bloc principal a deja consomme le budget, forcer ferait sauter le plafond.
        if taille + c > budget and (gardes or not forcer_un):
            break
        gardes.append(it)
        taille += c
    return gardes, len(items) - len(gardes)


def _dossiers(g):
    """Tout dossier contenant au moins une note, en chemin relatif complet — y compris
    les sous-dossiers, pour qu'on puisse zoomer à n'importe quelle profondeur."""
    out = set()
    for rel in g["notes"]:
        parts = rel.split("/")[:-1]
        for i in range(len(parts)):
            out.add("/".join(parts[:i + 1]))
    return sorted(out)


def _resoudre_base(g, folder):
    """Le dossier visé, à partir d'un nom tolérant : chemin complet, dernier segment, ou
    fragment. Rend (base, erreur)."""
    cible = folder.strip().strip("/").lower()
    noms = _dossiers(g)
    for candidats in ([f for f in noms if f.lower() == cible],
                      [f for f in noms if f.rsplit("/", 1)[-1].lower() == cible],
                      [f for f in noms if cible in f.lower()]):
        if len(candidats) == 1:
            return candidats[0], None
        if len(candidats) > 1:
            return None, f"« {folder} » est ambigu : {' · '.join(candidats)}"
    return None, (f"Aucun dossier « {folder} » dans {g['vault']}.\n"
                  f"Dossiers : {' · '.join(noms)}")


def _sous(g, base):
    """Ce que contient `base` : (notes directes, {sous-dossier -> [(rel, note)]}).
    base = "" -> la racine du vault."""
    prefixe = (base + "/") if base else ""
    directes, sous = [], {}
    for rel, d in g["notes"].items():
        if not rel.startswith(prefixe):
            continue
        reste = rel[len(prefixe):]
        if "/" in reste:
            sous.setdefault(prefixe + reste.split("/", 1)[0], []).append((rel, d))
        else:
            directes.append((rel, d))
    return directes, sous


def _carte_plate(g, base, label):
    """La carte détaillée : une ligne par note, groupée par dossier. Reste la meilleure
    vue tant qu'elle tient dans le budget — sur les docs d'un repo (3 README) ou un petit
    dossier, résumer ferait perdre l'information utile."""
    prefixe = (base + "/") if base else ""
    dedans = {rel: d for rel, d in g["notes"].items() if rel.startswith(prefixe)}
    par_dossier = {}
    for rel, d in dedans.items():
        par_dossier.setdefault(rel.rsplit("/", 1)[0] if "/" in rel else "(racine)", []).append((rel, d))
    out = [f"# {label} — {_count(len(dedans))} ({g['vault']})"]
    for f in sorted(par_dossier):
        out.append(f"\n## {f}")
        for rel, d in sorted(par_dossier[f]):
            out.append(_ligne_note(d))
    return "\n".join(out)


def _carte_resume(g, base, label, budget):
    """Le résumé : un bloc par sous-dossier (volume + ses notes les plus citées), plus
    les notes posées directement là. Coût dicté par le nombre de SOUS-DOSSIERS, pas par
    le nombre de notes — c'est ce qui rend la carte insensible à la taille du vault."""
    ent = _entrants(g)
    directes, sous = _sous(g, base)
    total = len(directes) + sum(len(v) for v in sous.values())

    blocs = {}
    for f, rels in sous.items():
        lignes = sum(d["lines"] for _, d in rels)
        bloc = [f"\n## {f} — {_notes(len(rels))}, {lignes} l"]
        hubs = [(rel, d) for rel, d in sorted(rels, key=lambda rd: (-ent.get(rd[0], 0), rd[0]))[:3]
                if ent.get(rel)]
        if hubs:
            bloc.append("  entrées : " + " · ".join(f"{d['name']}({ent[rel]})" for rel, d in hubs))
        blocs[f] = "\n".join(bloc)

    entete = f"# {label} — {_count(total)} ({g['vault']})"
    ordre = sorted(sous, key=lambda f: (-len(sous[f]), f))
    gardes, omis = _tenir_budget(ordre, budget, lambda f: len(blocs[f]) + 1, len(entete) + 220)

    out = [entete]
    if directes:
        reste = budget - len(entete) - sum(len(blocs[f]) + 1 for f in gardes) - 220 if budget else 0
        lignes = {rel: _ligne_note(d) for rel, d in directes}
        vus, coupes = _tenir_budget(
            sorted(directes, key=lambda rd: (-ent.get(rd[0], 0), rd[0])),
            max(reste, 0), lambda rd: len(lignes[rd[0]]) + 1, forcer_un=False)
        if vus:
            out.append(f"\n## {base or '(racine)'} — {_notes(len(directes))}")
            out += [lignes[rel] for rel, _ in sorted(vus, key=lambda rd: rd[0])]
        if coupes:
            out.append(f"  … {coupes} notes non affichées ici, les moins citées "
                       f"(budget {budget} car atteint) → grep_notes(\"…\") ou budget=0")
    out += [blocs[f] for f in sorted(gardes)]
    if omis:
        out.append(f"\n… {omis} sous-dossiers non affichés (budget {budget} car atteint).")
    out.append('\n→ zoomer : vault_map(folder="…")  ·  chercher un mot : grep_notes("…")')
    return "\n".join(out)


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
def vault_map(path: Optional[str] = None, folder: Optional[str] = None,
              budget: int = BUDGET_DEFAUT) -> str:
    """Carte d'ensemble, à COÛT BORNÉ (elle ne grossit plus avec le vault).

    La carte est RÉCURSIVE : à chaque niveau, le détail note par note tant qu'il tient
    dans le budget, sinon le résumé des sous-dossiers (volume + notes les plus citées,
    c'est-à-dire les vrais points d'entrée).
    `folder="03-Connaissances"` ou `folder="03-Connaissances/pieges"` (ou juste
    `folder="pieges"`) : zoome à n'importe quelle profondeur.
    `budget` : plafond de la sortie EN CARACTÈRES (défaut 6000 ≈ 1,7k tokens). Ce qui ne
    rentre pas n'est jamais caché : le nombre d'éléments omis est annoncé.
    `budget=0` = tout, sans plafond — audit volontaire, peut coûter très cher.
    `path` : cartographier un AUTRE dossier pour ce seul appel, sans changer la cible
    courante. Sur un repo de CODE, rend ses .md (README, docs/, ADR, CHANGELOG) — le
    « pourquoi » du projet, que repo-map ne voit pas puisqu'il n'indexe que le code.

    Pour CHERCHER quelque chose de précis, ne pas passer par ici : grep_notes(pattern)
    situe chaque hit dans sa section pour quelques centaines de tokens."""
    g, err = _open(path)
    if err:
        return err
    if not g["notes"]:
        return f"Aucun fichier .md dans {g['vault']}."

    base = ""
    if folder is not None:
        base, err = _resoudre_base(g, folder)
        if err:
            return err

    label = ("Carte des docs" if g["mode"] == "repo" else "Carte du vault") if not base else base
    plate = _carte_plate(g, base, label)
    if not budget or len(plate) <= budget:
        return plate
    return _carte_resume(g, base, label, budget)


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
