"""Tests des règles métier (rules.py) : aucune IA, aucun Ollama."""

import rules


def test_remise_au_dela_de_8_pourcent_demande_une_validation():
    resultat = rules.check_remise(12, 0)
    assert resultat["statut"] == rules.VALIDATION_REQUISE
    assert not resultat["autorise"]
    assert "Thomas Lefèvre" in resultat["message"]


def test_remise_au_dela_de_8_pourcent_meme_avec_contrat():
    assert rules.check_remise(12, 2)["statut"] == rules.VALIDATION_REQUISE


def test_remise_6_a_8_pourcent_demande_un_contrat_de_deux_ans():
    assert rules.check_remise(7, 0)["statut"] == rules.CONTRAT_REQUIS
    assert rules.check_remise(7, 1)["statut"] == rules.CONTRAT_REQUIS
    assert rules.check_remise(7, 2)["statut"] == rules.AUTORISE
    assert rules.check_remise(8, 2)["autorise"]


def test_remise_standard():
    assert rules.check_remise(3, 0)["statut"] == rules.AUTORISE
    assert rules.check_remise(5, 0)["statut"] == rules.AUTORISE


def test_temperature():
    assert rules.check_temperature("TW-221", 180)["statut"] == rules.NON_COMPATIBLE
    assert rules.check_temperature("TW-221", 60)["statut"] == rules.COMPATIBLE
    assert rules.check_temperature("TW-618", 121)["statut"] == rules.COMPATIBLE
    assert rules.check_temperature("TW-104", 180)["statut"] == rules.COMPATIBLE


def test_temperature_inconnue():
    # TW-310 n'a été testé qu'en boisson : sa tenue à la cuisson est inconnue
    assert rules.check_temperature("TW-310", 180)["statut"] == rules.INCONNU
    assert rules.check_temperature("TW-999", 100)["statut"] == rules.INCONNU


def test_bio():
    assert not rules.check_bio("TW-104")["bio"]
    assert not rules.check_bio("FB-031")["bio"]
    cosmos = rules.check_bio("FB-112")
    assert cosmos["bio"] and cosmos["certification"] == "COSMOS"


def test_quantite():
    assert not rules.check_quantite(10)["minimum_ok"]
    assert rules.check_quantite(25)["minimum_ok"]
    assert rules.check_quantite(1000, par_an=True)["stock_securite_possible"]
    assert not rules.check_quantite(900, par_an=True)["stock_securite_possible"]
    assert not rules.check_quantite(2000)["stock_securite_possible"]  # ce n'est pas un volume annuel


def types(texte):
    return {i["type"] for i in rules.detect_infractions(texte)}


def test_infraction_remise():
    assert types("Je vous propose 12 % de remise.") == {"remise"}
    assert types("Une remise de 7 % sur deux ans.") == set()
    assert types("La TVA est à 20 %.") == set()  # pas une remise


def test_infraction_bio():
    assert "bio" in types("Notre vanille TW-104 est bio.")
    assert "bio" not in types("Aucun arôme du catalogue n'est bio.")
    assert "bio" not in types("Ce n'est pas bio.")


def test_infraction_delai():
    assert "delai" in types("Je vous livre en 3 jours.")
    assert "delai" in types("Votre échantillon sur mesure arrive en 5 jours.")
    assert "delai" in types("L'échantillon standard part en 2 jours.")
    assert "delai" not in types("Je vous livre en 10 jours ouvrés.")
    assert "delai" not in types("Votre échantillon sur mesure arrive en 3 semaines.")


def test_infraction_sans_allergene():
    assert "allergene" in types("Le FB-112 est sans allergène.")
    assert "allergene" not in types("Le FB-155 est sans allergène à déclarer.")


def test_message_sans_probleme():
    assert rules.detect_infractions("Bonjour, je vous propose un stock de sécurité et un engagement écrit.") == []
