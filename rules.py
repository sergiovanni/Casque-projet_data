"""Règles métier de Givaudan, vérifiées par du code Python (sans IA).

Règle d'or du projet : l'IA ne calcule rien et ne décide rien de chiffré.
Les remises, températures, minimums de commande et délais sont vérifiés ici ;
le LLM reçoit seulement le résultat sous forme de « faits vérifiés ».

Les chiffres viennent de `story.txt` (section « Présentation du secteur »),
lus par `knowledge.py`.
"""

from __future__ import annotations

import re

import knowledge

# Statuts renvoyés par check_remise
AUTORISE = "autorise"
CONTRAT_REQUIS = "contrat_requis"
VALIDATION_REQUISE = "validation_requise"

# Statuts renvoyés par check_temperature
COMPATIBLE = "compatible"
NON_COMPATIBLE = "non compatible"
INCONNU = "inconnu"


def _regles() -> dict:
    return knowledge.get_knowledge().regles


# ----------------------------------------------------------------------------------------
# Remises
# ----------------------------------------------------------------------------------------

def check_remise(pourcentage: float, contrat_annees: float = 0) -> dict:
    """Une remise est-elle autorisée ?

    - au-delà de 8 % : validation de Thomas Lefèvre obligatoire, quel que soit le contrat ;
    - de 6 à 8 % : possible seulement avec un contrat de deux ans minimum ;
    - jusqu'à 5 % : remises standard (3 % au-delà de 500 kg par an, 5 % au-delà de 2 tonnes).
    """
    r = _regles()
    if pourcentage > r["remise_validation_au_dela"]:
        return {"statut": VALIDATION_REQUISE, "autorise": False,
                "message": f"Remise de {pourcentage:g} % : supérieure à {r['remise_validation_au_dela']} %, "
                           f"validation de Thomas Lefèvre obligatoire."}
    if pourcentage >= r["remise_contrat_min"]:
        if contrat_annees >= 2:
            return {"statut": AUTORISE, "autorise": True,
                    "message": f"Remise de {pourcentage:g} % : autorisée avec un contrat de deux ans minimum."}
        return {"statut": CONTRAT_REQUIS, "autorise": False,
                "message": f"Remise de {pourcentage:g} % : possible seulement avec un contrat de deux ans minimum "
                           f"(remises de {r['remise_contrat_min']} à {r['remise_contrat_max']} %)."}
    return {"statut": AUTORISE, "autorise": True,
            "message": f"Remise de {pourcentage:g} % : dans les remises standard "
                       f"({r['remise_standard']} % au-delà de 500 kg par an, {r['remise_volume']} % au-delà de 2 tonnes)."}


# ----------------------------------------------------------------------------------------
# Température
# ----------------------------------------------------------------------------------------

def check_temperature(code_produit: str, temperature_procede: float) -> dict:
    """Le produit supporte-t-il la température du procédé ? « inconnu » si la fiche ne le dit pas."""
    produit = knowledge.get_knowledge().produit(code_produit)
    if produit is None:
        return {"statut": INCONNU, "message": f"Produit {code_produit} inconnu du catalogue."}
    maximum = produit["temperature_max"]
    if maximum is None:
        return {"statut": INCONNU,
                "message": f"{produit['code']} {produit['nom']} : la fiche ne donne aucune température maximale "
                           f"(tenue à {temperature_procede:g} °C inconnue)."}
    if temperature_procede <= maximum:
        return {"statut": COMPATIBLE,
                "message": f"{produit['code']} {produit['nom']} : stable jusqu'à {maximum} °C, "
                           f"compatible avec {temperature_procede:g} °C."}
    return {"statut": NON_COMPATIBLE,
            "message": f"{produit['code']} {produit['nom']} : stable jusqu'à {maximum} °C seulement, "
                       f"non compatible avec {temperature_procede:g} °C."}


# ----------------------------------------------------------------------------------------
# Bio
# ----------------------------------------------------------------------------------------

def check_bio(code_produit: str) -> dict:
    """Aucun arôme alimentaire n'est bio ; seul FB-112 est certifié COSMOS (cosmétique)."""
    produit = knowledge.get_knowledge().produit(code_produit)
    if produit is None:
        return {"bio": False, "certification": None, "message": f"Produit {code_produit} inconnu du catalogue."}
    if produit["bio"]:
        return {"bio": True, "certification": "COSMOS",
                "message": f"{produit['code']} {produit['nom']} : certifié COSMOS (naturel et bio), usage cosmétique."}
    return {"bio": False, "certification": None,
            "message": f"{produit['code']} {produit['nom']} : non certifié bio. "
                       f"Aucun arôme alimentaire du catalogue n'est bio ; seul FB-112 est certifié COSMOS."}


# ----------------------------------------------------------------------------------------
# Quantités
# ----------------------------------------------------------------------------------------

def check_quantite(kg: float, par_an: bool = False) -> dict:
    """Minimum de commande (25 kg par référence) et stock de sécurité (dès 1 tonne par an)."""
    r = _regles()
    seuil_stock_kg = r["stock_securite_tonnes"] * 1000
    minimum_ok = kg >= r["commande_minimum_kg"]
    stock_possible = par_an and kg >= seuil_stock_kg
    morceaux = [f"{kg:g} kg : " + ("commande acceptée" if minimum_ok else
                                    f"inférieur au minimum de {r['commande_minimum_kg']} kg par référence")]
    if par_an:
        morceaux.append("stock de sécurité réservé possible" if stock_possible
                        else f"stock de sécurité possible à partir de {r['stock_securite_tonnes']} tonne par an")
    return {"minimum_ok": minimum_ok, "stock_securite_possible": stock_possible, "message": " ; ".join(morceaux)}


# ----------------------------------------------------------------------------------------
# Détection d'infractions dans un message du commercial
# ----------------------------------------------------------------------------------------

_POURCENT = re.compile(r"(\d+(?:[.,]\d+)?)\s*%")
_CODE_PRODUIT = re.compile(r"\b((?:TW|FB)-\d+E?)\b", re.IGNORECASE)
_DELAI = re.compile(r"(\d+)\s*(jours?|semaines?)", re.IGNORECASE)
_NEGATION = re.compile(r"\b(pas|aucun|aucune|non|jamais)\b", re.IGNORECASE)


def _jours_ouvres(valeur: int, unite: str) -> int:
    return valeur * 5 if unite.lower().startswith("semaine") else valeur


def detect_infractions(texte: str) -> list[dict]:
    """Repère dans un message du commercial : remise > 8 %, promesse de bio,
    promesse de délai impossible, affirmation « sans allergène » fausse.

    Retourne une liste de {"type", "message"} (vide si tout va bien).
    """
    r = _regles()
    connaissances = knowledge.get_knowledge()
    infractions: list[dict] = []
    bas = texte.lower()

    # 1. Remise supérieure au plafond sans validation
    if re.search(r"remise|rabais|discount|geste commercial", bas):
        for brut in _POURCENT.findall(texte):
            valeur = float(brut.replace(",", "."))
            if valeur > r["remise_validation_au_dela"]:
                infractions.append({"type": "remise",
                                    "message": f"Remise de {brut} % annoncée, supérieure à {r['remise_validation_au_dela']} % : "
                                               f"validation préalable de Thomas Lefèvre obligatoire."})

    # 2. Promesse de bio
    for m in re.finditer(r"\bbio\b", bas):
        debut_phrase = max(bas.rfind(sep, 0, m.start()) for sep in (".", "!", "?", ";")) + 1
        if not _NEGATION.search(bas[debut_phrase:m.start()]):  # « aucun arôme n'est bio » n'est pas une promesse
            infractions.append({"type": "bio",
                                "message": "Promesse de produit bio : aucun arôme alimentaire du catalogue n'est certifié bio "
                                           "(seul FB-112 est certifié COSMOS)."})
            break

    # 3. Promesse de délai impossible
    sur_mesure = "sur mesure" in bas or "sur-mesure" in bas
    for valeur, unite in _DELAI.findall(texte):
        jours = _jours_ouvres(int(valeur), unite)
        if "échantillon" in bas and sur_mesure and jours < r["delai_sur_mesure_semaines"] * 5:
            infractions.append({"type": "delai",
                                "message": f"Échantillon sur mesure promis en {valeur} {unite} : il faut {r['delai_sur_mesure_semaines']} semaines."})
        elif "échantillon" in bas and not sur_mesure and jours < r["delai_echantillon_jours"]:
            infractions.append({"type": "delai",
                                "message": f"Échantillon promis en {valeur} {unite} : le délai standard est de {r['delai_echantillon_jours']} jours ouvrés."})
        elif re.search(r"livr|délai", bas) and "échantillon" not in bas:
            minimum = r["delai_premiere_production_semaines"] * 5 if sur_mesure else r["delai_livraison_jours"]
            if jours < minimum:
                infractions.append({"type": "delai",
                                    "message": f"Livraison promise en {valeur} {unite} : le délai minimum est de "
                                               + (f"{r['delai_premiere_production_semaines']} semaines (première production sur mesure)."
                                                  if sur_mesure else f"{r['delai_livraison_jours']} jours ouvrés (produit en stock).")})

    # 4. « Sans allergène » dit d'un produit qui en contient
    if re.search(r"sans allerg", bas):
        for code in {c.upper() for c in _CODE_PRODUIT.findall(texte)}:
            produit = connaissances.produit(code)
            if produit and produit["allergenes"]:
                infractions.append({"type": "allergene",
                                    "message": f"{produit['code']} n'est pas « sans allergène » : allergène(s) à déclarer : "
                                               f"{', '.join(produit['allergenes'])}."})
    return infractions
