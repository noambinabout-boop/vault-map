# -*- coding: utf-8 -*-
"""Test des garanties de vault_map() : coût borné, rien de perdu, rien de caché.

Ce sont les trois promesses de l'outil. Une promesse de coût qu'on ne teste pas
redevient fausse silencieusement dès que le vault grossit — c'est exactement ce qui
était arrivé à l'ancienne carte (annoncée 1-2k tokens, mesurée à 19k sur 240 notes).

Usage : python _test_carte.py [chemin_du_vault]  (sinon VAULT_MAP_TARGET, sinon cwd)
Sortie : code 0 si tout passe, 1 sinon.
"""
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import vault_map as vm
import server

VAULT = (sys.argv[1] if len(sys.argv) > 1
         else os.environ.get("VAULT_MAP_TARGET") or os.getcwd())

g = vm.build(VAULT)
server.STATE.update(target=os.path.abspath(VAULT),
                    cache={os.path.abspath(VAULT): {"graph": g, "sig": 1}})
carte = getattr(server.vault_map, "fn", server.vault_map)

DOSSIERS = sorted({d["folder"] for d in g["notes"].values()})   # 1er niveau : couverture
TOUS_DOSSIERS = server._dossiers(g)                              # toute profondeur : bornage
TOTAL = len(g["notes"])
echecs = []


def verifie(condition, libelle):
    print(("  OK    " if condition else "  ECHEC ") + libelle)
    if not condition:
        echecs.append(libelle)


print(f"=== vault_map — garanties, sur {TOTAL} notes ({VAULT}) ===\n")

# 1. COÛT BORNÉ — la promesse doit tenir à toutes les tailles de budget.
defaut = carte()
verifie(len(defaut) <= server.BUDGET_DEFAUT,
        f"vue par défaut sous le budget : {len(defaut)} / {server.BUDGET_DEFAUT} car")

gros = max(DOSSIERS, key=lambda f: len(carte(folder=f, budget=0)))
for b in (1500, 3000, server.BUDGET_DEFAUT, 20000):
    n = len(carte(folder=gros, budget=b))
    verifie(n <= b, f"zoom « {gros} » borné à budget={b} : {n} car")

# Le plafond doit tenir sur TOUS les dossiers, pas seulement le plus lourd : un dossier
# mêlant beaucoup de notes directes ET des sous-dossiers est le cas qui déborde le plus
# facilement (les deux blocs s'additionnent).
debordent = [(f, len(carte(folder=f)))
             for f in TOUS_DOSSIERS if len(carte(folder=f)) > server.BUDGET_DEFAUT]
verifie(not debordent, f"tous les dossiers bornés au budget par défaut ({len(TOUS_DOSSIERS)} testés, sous-dossiers compris)"
                       + (f" — débordent : {debordent}" if debordent else ""))

# La bascule est adaptative : le détail tant qu'il rentre, le résumé au-delà.
plate = len(carte(budget=0))
verifie(("entrées :" in defaut) == (plate > server.BUDGET_DEFAUT),
        "bascule détail/résumé cohérente avec le poids réel du vault")

# 2. RIEN DE PERDU — borner la sortie ne doit rendre aucune note injoignable.
# Note par note, jamais en sommant les dossiers : depuis que le zoom est récursif, un
# dossier parent contient aussi ses enfants, donc une somme compte deux fois.
# Les notes de la racine n'ont pas de dossier : elles se lisent dans la vue exhaustive.
_zoom = {f: carte(folder=f, budget=0) for f in TOUS_DOSSIERS}
_exhaustive = carte(budget=0)
introuvables = [rel for rel, d in g["notes"].items()
                if f"- {d['name']} (" not in _zoom.get(rel.rsplit("/", 1)[0], _exhaustive)]
verifie(not introuvables,
        f"chaque note joignable par le zoom de son dossier : {TOTAL - len(introuvables)}/{TOTAL}"
        + (f" — introuvables : {introuvables[:3]}" if introuvables else ""))
verifie(len(carte(budget=0)) > len(defaut) or TOTAL < 20,
        "budget=0 rend bien la vue exhaustive")

# 3. RIEN DE CACHÉ — une carte tronquée qui ne le dit pas se lit comme complète.
serre = carte(folder=gros, budget=800)
tronque = sum(1 for l in serre.splitlines() if l.startswith("- ")) < len(
    [r for r, d in g["notes"].items() if d["folder"] == gros])
verifie(not tronque or "non affichées" in serre,
        "troncature annoncée explicitement")
verifie(not tronque or "grep_notes" in serre,
        "troncature accompagnée d'une issue (grep_notes / budget=0)")

# 4. Ergonomie du zoom : tolérant à la casse et au fragment, explicite sinon.
if DOSSIERS:
    cible = DOSSIERS[-1]
    verifie(carte(folder=cible.upper()).startswith(f"# {cible}"), "zoom insensible à la casse")
    verifie("Aucun dossier" in carte(folder="zzz-inexistant-zzz"), "dossier inconnu : message clair")

# 5. Non-régression des outils voisins (le patch ne touche qu'à la carte).
outline = getattr(server.outline, "fn", server.outline)
grep = getattr(server.grep_notes, "fn", server.grep_notes)
une = g["notes"][sorted(g["notes"])[0]]["name"]
verifie(len(outline(une)) > 20, "outline() intact")
verifie(isinstance(grep("e"), str), "grep_notes() intact")

print(f"\n=== {len(echecs)} échec(s) ===" if echecs else "\n=== tout est vert ===")
for e in echecs:
    print("  -", e)
sys.exit(1 if echecs else 0)
