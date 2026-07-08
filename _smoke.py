# -*- coding: utf-8 -*-
"""Test à froid du moteur vault_map sur le vrai vault + mesure du test décisif."""
import os, sys
sys.stdout.reconfigure(encoding="utf-8")
import vault_map as vm

VAULT = r"c:\Users\Noam\Desktop\Noam\obsidian\second_cerveau"

def toklen(s):
    """Approx tokens ~ chars/4 (grossier mais suffisant pour un ordre de grandeur)."""
    return len(s) // 4

g = vm.build(VAULT)
print(f"=== build : {len(g['notes'])} notes ===")

# 1) vault_map compact — combien de tokens pour TOUT le vault ?
import server
server.STATE["target"] = VAULT
server.STATE["graph"] = g
server.STATE["sig"] = 1
carte = server.vault_map()
print(f"\n[vault_map] {toklen(carte)} tokens approx pour {len(g['notes'])} notes")
print(carte[:1500])

# 2) TEST DÉCISIF sur la note-monstre
NOTE = "Repo-map-codebase-pour-Claude-Code"
rel, err = vm.resolve(g, NOTE)
print(f"\n=== TEST DÉCISIF sur {rel} ===")
full = open(os.path.join(VAULT, rel), encoding="utf-8").read()
ol = server.outline(NOTE)
sec = server.get_section(NOTE, "Design tranché")  # une section précise
scenario = toklen(ol) + toklen(sec)
print(f"Note entière      : {toklen(full):>6} tokens ({g['notes'][rel]['lines']} lignes)")
print(f"outline           : {toklen(ol):>6} tokens")
print(f"get_section(1)    : {toklen(sec):>6} tokens")
print(f"outline+section   : {scenario:>6} tokens")
print(f"GAIN vs tout-lire : {100*(1-scenario/toklen(full)):.0f}%")
print("\n--- outline (extrait) ---")
print("\n".join(ol.splitlines()[:12]))

# 3) query frontmatter
print("\n=== query ===")
print(server.query("status:challengee"))
