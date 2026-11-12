"""Tests du score des prospects (scoring.py) : le score est calculé par du code, pas par l'IA."""

import scoring

ATTENDU = [
    ("Maison Alizé", 95), ("Capilla Nova", 93), ("Lumen Derma", 88), ("Solenza", 85), ("Hommage Paris", 83),
    ("Ondine & Sel", 80), ("Petit Nuage", 73), ("Bulle d'Écume", 68), ("Laboratoire Sources de Cambo", 58),
    ("Terre de Lys", 53), ("Atelier Verdane", 50),
]


def test_classement_complet():
    assert [(p["nom"], p["score"]) for p in scoring.classer()] == ATTENDU


def test_classement_stable():
    assert scoring.classer() == scoring.classer()


def test_scores_recalcules_correspondent_a_story_txt():
    for p in scoring.classer():
        assert p["anomalies"] == [], f"{p['nom']} : {p['anomalies']}"


def test_maison_alize_perd_5_points_sur_la_porte_d_entree():
    p = scoring.prospect_score("maison-alize")
    assert p["details"]["porte_entree"]["points"] == 5
    assert p["details"]["porte_entree"]["maximum"] == 10
    assert sum(d["maximum"] - d["points"] for d in p["details"].values()) == 5


def test_atelier_verdane_est_11e_a_cause_du_contrat_exclusif():
    p = scoring.prospect_score("atelier-verdane")
    assert p["rang"] == 11
    assert p["details"]["fournisseur"]["points"] == 0


def test_alerte_si_le_total_ecrit_est_faux():
    faux = dict(scoring.prospect_score("maison-alize"))
    faux["donnees_score"] = faux["donnees_score"].replace("Score total : 95/100", "Score total : 99/100")
    resultat = scoring.calculer(faux)
    assert resultat["score"] == 95  # le score recalculé fait foi
    assert any("99" in a for a in resultat["anomalies"])


def test_alerte_si_une_valeur_n_est_pas_dans_la_grille():
    faux = dict(scoring.prospect_score("maison-alize"))
    faux["donnees_score"] = faux["donnees_score"].replace("lancement 25", "lancement 20")
    assert any("lancement" in a for a in scoring.calculer(faux)["anomalies"])


def test_prospects_regionaux_sans_score_exclus_du_classement():
    noms = {p["nom"] for p in scoring.classer()}
    assert "Brasserie du Béarn" not in noms and "Miellerie des Landes" not in noms
