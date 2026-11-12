"""Teste l'API en direct avec quelques questions issues de questions_test.md.

Usage :
    python smoke_test.py                         # API sur http://127.0.0.1:8000
    python smoke_test.py http://127.0.0.1:8010   # autre adresse
    python smoke_test.py http://127.0.0.1:8000 twin   # un seul groupe : assistant | twin | coach

Rien n'est vérifié automatiquement : le script affiche la réponse et la réponse attendue,
à comparer à l'œil.
"""

import json
import sys
import urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
ONLY = sys.argv[2] if len(sys.argv) > 2 else None

# (question, réponse attendue)
ASSISTANT = [
    ("Prépare mon rendez-vous du 3 décembre avec la Laiterie des Pyrénées.",
     "Marc (Dominant) et Hélène (Consciencieuse) ; retard de mars, fraise, Symrise -12 % ; TW-221E, stock de sécurité, SweetBalance ; +2 % à valider par Thomas Lefèvre."),
    ("Quelle est la personnalité de Marc Etchegaray et comment lui parler ?",
     "DISC Dominant, mobiles Argent et Sécurité ; droit au but, chiffres, engagement écrit ; ne pas minimiser le retard de mars."),
    ("Le client me dit que Symrise est 12 % moins cher, je réponds quoi ?",
     "Coût total (stabilité, régularité, accompagnement labo) et test à l'aveugle ; ne pas dénigrer."),
    ("Peut-on utiliser la fraise TW-221 dans un gâteau cuit ?", "Non : pas stable au-delà de 90 °C."),
    ("Le ProMask tient-il à la stérilisation des Conserveries Basques ?", "Oui : stérilisation à 121 °C, ProMask stable jusqu'à 140 °C."),
    ("Puis-je accorder 7 % à Mathilde Garat ?", "Oui avec un contrat de deux ans (6 à 8 %) ; au-delà de 8 % : validation de Thomas Lefèvre."),
    ("Peut-on promettre un arôme bio à la Miellerie des Landes ?", "Non : aucun arôme alimentaire du catalogue n'est certifié bio."),
    ("What does Atlantic Drinks want for summer 2027?", 'A "zero sugar" iced tea range; TW-502 SweetBalance is a strong fit.'),
    ("Quel est le numéro de téléphone d'Hélène Bordes ?", "Information absente des documents."),
    ("Quel est le prix exact de la fraise chez Symrise ?", "Inconnu : on sait seulement qu'elle est 12 % moins chère."),
    ("Quel âge a Sophie Larrieu ?", "Information absente."),
    ("Cite moi 2 marques de cosmétique Française.", "Hors cadre : doit dire qu'il n'a pas l'information."),
]

# (twin, message du commercial, comportement attendu)
TWIN = [
    ("Marc Etchegaray", "Bonjour Monsieur Etchegaray, je viens vous présenter nos nouveautés.",
     "Coupe court : « Avant les nouveautés, parlons du retard de mars. » Ton sec, vouvoiement."),
    ("Marc Etchegaray", "Qu'est-ce que votre R&D pense des échantillons de Symrise ?", "Admet qu'Hélène les trouve moins bons que TW-221."),
    ("Marc Etchegaray", "Quel est votre chiffre d'affaires total ?", "N'invente pas de chiffre."),
    ("Hélène Bordes", "Quel est votre objectif précis sur le sucre ?", "25 % de sucre en moins, lancement septembre 2027, comité en février 2027."),
    ("Sophie Larrieu", "Notre arôme est très technologique et chimiquement stable.", "Se ferme : n'aime pas « chimique », veut une histoire d'origine."),
    ("Peio Harriet", "Bernard était un super ami chez nous !", "Se ferme poliment."),
    ("Sean O'Brien", "Hello Sean, how are you?", "Répond en anglais, poli et bref, rappelle la question sur l'étiquetage britannique."),
    ("Antoine Casaubon", "Quel est le problème avec votre fournisseur actuel ?", "Son distributeur toulousain n'a aucune solution pour la bière sans alcool."),
]

# Une mini-conversation avec Marc, puis évaluation
COACH_TWIN = "Marc Etchegaray"
COACH_CONVERSATION = [
    {"role": "user", "content": "Bonjour Monsieur Etchegaray. Je sais que le retard de mars a été difficile ; je ne veux pas le minimiser."},
    {"role": "assistant", "content": "Bon. Alors donnez-moi un chiffre : comment garantissez-vous que ça ne se reproduise pas ?"},
    {"role": "user", "content": "Je vous propose un stock de sécurité réservé et un engagement écrit sur les délais. Et 12 % de remise."},
    {"role": "assistant", "content": "12 %, c'est acceptable. Envoyez-moi ça par écrit."},
]


def post(path: str, payload: dict) -> dict:
    req = urllib.request.Request(
        BASE + path, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"},
    )
    return json.load(urllib.request.urlopen(req, timeout=600))


def show(title: str, answer: str, expected: str) -> None:
    print(f"\n=== {title}\n{answer}\n--- attendu : {expected}")


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    if ONLY in (None, "assistant"):
        print("\n######## MODE ASSISTANT")
        for question, expected in ASSISTANT:
            show(question, post("/ask", {"question": question})["answer"], expected)
    if ONLY in (None, "twin"):
        print("\n######## MODE TWIN")
        for twin, message, expected in TWIN:
            show(f"({twin}) {message}", post("/twin", {"twin": twin, "message": message})["reply"], expected)
    if ONLY in (None, "coach"):
        print("\n######## MODE COACH")
        result = post("/coach", {"twin": COACH_TWIN, "history": COACH_CONVERSATION})
        show("Évalue ma conversation avec Marc Etchegaray selon la grille.", result["evaluation"],
             "6 critères notés, point fort / point à améliorer / phrase plus efficace ; faute : remise > 8 % sans validation.")


if __name__ == "__main__":
    main()
