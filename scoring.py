"""Score des prospects cosmétiques, calculé par du code (sans IA).

Chaque fiche prospect contient une ligne « Données de score : lancement 25 ; recrutement 15 ; ... ».
On additionne ces points, on vérifie qu'ils respectent la grille de la section
« Grille de score des prospects », et on compare le total au « Score total » écrit.
En cas d'écart, on écrit une alerte dans les logs : le score recalculé fait foi.
"""

from __future__ import annotations

import logging
import re

import knowledge

log = logging.getLogger("scoring")

# (clé, libellé tel qu'écrit dans story.txt, points maximum, valeurs autorisées par la grille)
CRITERES = [
    ("lancement", "lancement", 25, {0, 10, 25}),
    ("recrutement", "recrutement", 15, {0, 15}),
    ("tendance", "tendance", 15, {0, 15}),
    ("fournisseur", "fournisseur", 15, {0, 8, 15}),
    ("taille", "taille", 10, {0, 5, 10}),
    ("produit_adapte", "produit adapté", 10, {5, 10}),
    ("porte_entree", "porte d'entrée", 10, {0, 5, 10}),
]
SCORE_MAXIMUM = sum(c[2] for c in CRITERES)  # 100

_TOTAL_RE = re.compile(r"Score total\s*:\s*(\d+)\s*/\s*100")


def parse_points(donnees_score: str) -> dict[str, dict]:
    """« lancement 25 ; tendance 15 (bien-être) ; ... » -> {"lancement": {"points": 25, ...}, ...}"""
    texte = _TOTAL_RE.split(donnees_score)[0].strip().rstrip(".").strip()
    par_libelle = {knowledge.normalize(libelle): (cle, libelle, maximum) for cle, libelle, maximum, _ in CRITERES}
    details: dict[str, dict] = {}
    for morceau in texte.split(";"):
        m = re.match(r"(.+?)\s+(\d+)\s*(?:\((.*)\))?\s*$", morceau.strip())
        if not m or knowledge.normalize(m.group(1)) not in par_libelle:
            continue
        cle, libelle, maximum = par_libelle[knowledge.normalize(m.group(1))]
        details[cle] = {"libelle": libelle, "points": int(m.group(2)), "maximum": maximum, "commentaire": m.group(3) or ""}
    return details


def score_ecrit(donnees_score: str) -> int | None:
    m = _TOTAL_RE.search(donnees_score)
    return int(m.group(1)) if m else None


def calculer(prospect: dict) -> dict:
    """Retourne le prospect enrichi de `score` (recalculé), `details` et `anomalies`."""
    details = parse_points(prospect["donnees_score"])
    anomalies: list[str] = []
    for cle, libelle, _, autorisees in CRITERES:
        if cle not in details:
            anomalies.append(f"critère « {libelle} » manquant")
        elif details[cle]["points"] not in autorisees:
            anomalies.append(f"{libelle} : {details[cle]['points']} points, valeur non prévue par la grille {sorted(autorisees)}")
    score = sum(d["points"] for d in details.values())

    ecrit = score_ecrit(prospect["donnees_score"])
    if ecrit is not None and ecrit != score:
        anomalies.append(f"score total écrit ({ecrit}) différent du score recalculé ({score})")
    if prospect.get("score_ecrit") is not None and prospect["score_ecrit"] != score:
        anomalies.append(f"score du récapitulatif ({prospect['score_ecrit']}) différent du score recalculé ({score})")

    for anomalie in anomalies:
        log.warning("Prospect %s : %s", prospect["nom"], anomalie)
    return {**prospect, "score": score, "details": details, "anomalies": anomalies}


def classer(prospects: list[dict] | None = None) -> list[dict]:
    """Prospects cosmétiques (ceux qui ont des données de score), du meilleur au moins bon.

    Le tri est toujours le même : score décroissant, puis nom (ordre alphabétique) en cas d'égalité.
    """
    if prospects is None:
        prospects = knowledge.get_knowledge().prospects
    calcules = [calculer(p) for p in prospects if p["avec_score"]]
    calcules.sort(key=lambda p: (-p["score"], p["nom"]))
    for rang, p in enumerate(calcules, start=1):
        p["rang"] = rang
    return calcules


def prospect_score(prospect_id: str) -> dict | None:
    return next((p for p in classer() if p["id"] == prospect_id), None)
