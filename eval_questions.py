"""Lance les questions de questions_test.md sur l'API et écrit un rapport (rapport_eval.md).

Usage :
    python eval_questions.py                         # API sur http://127.0.0.1:8000
    python eval_questions.py --url http://127.0.0.1:8010
    python eval_questions.py --only "Axe 02"         # une seule partie du fichier
    python eval_questions.py --limit 5               # les 5 premières questions seulement

Comment c'est noté : pour chaque question, on extrait de la « réponse attendue » les éléments précis
(références produits, chiffres, noms propres) et on vérifie qu'ils apparaissent dans la réponse de l'API.
La question est comptée bonne si au moins 60 % de ces éléments sont présents. C'est un indicateur :
le rapport contient toutes les réponses, à relire pour les nuances (surtout les modes Twin et Coach).
"""

import argparse
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

import knowledge

SEUIL = 0.6
MOTS_COURANTS = {
    "deux", "oui", "non", "information", "inconnu", "aucun", "exemple", "note", "aller", "donner", "coupe", "court",
    "avant", "avantage", "preuve", "situation", "probleme", "implication", "benefice", "mobiles", "remise", "points",
    "porte", "contact", "signaux", "tendances", "caracteristique", "ton", "sec", "vouvoiement", "elle", "finit",
    "admettre", "accepte", "coach", "doit", "ensuite", "signaler", "faute", "répond", "répond", "pas", "plus",
}


def appeler(base: str, chemin: str, corps: dict | None = None):
    donnees = json.dumps(corps).encode("utf-8") if corps is not None else None
    requete = urllib.request.Request(base + chemin, data=donnees, headers={"Content-Type": "application/json; charset=utf-8"})
    try:
        return json.load(urllib.request.urlopen(requete, timeout=900))
    except urllib.error.HTTPError as exc:
        return {"erreur": f"HTTP {exc.code} : {exc.read().decode('utf-8', 'replace')[:200]}"}


def lire_questions(chemin: Path) -> list[dict]:
    """Extrait les lignes de tableaux (question | réponse attendue) avec le titre de leur partie."""
    lignes, partie = [], ""
    for ligne in chemin.read_text(encoding="utf-8").splitlines():
        if ligne.startswith("## "):
            partie = ligne[3:].strip()
        elif ligne.startswith("|") and not ligne.startswith("|---"):
            cellules = [c.strip() for c in ligne.strip().strip("|").split("|", 1)]
            if len(cellules) == 2 and cellules[0] not in ("Question", "Message du commercial", "Écran / question"):
                lignes.append({"partie": partie, "question": cellules[0], "attendu": cellules[1]})
    return lignes


def citation(texte: str) -> str | None:
    m = re.search(r"«\s*(.+?)\s*»|\"(.+?)\"", texte)
    return (m.group(1) or m.group(2)) if m else None


def trouver_twin(prenom: str):
    return next((t for t in knowledge.get_knowledge().twins if knowledge.normalize(prenom) in knowledge.normalize(t["nom"])), None)


def poser(base: str, q: dict) -> str | None:
    """Envoie la question au bon endpoint. Retourne la réponse, ou None si elle ne se teste pas automatiquement."""
    question = q["question"]
    if q["partie"].startswith("Axe 03 bis"):
        return None  # l'évaluation a besoin d'une vraie conversation : voir tests/test_api.py
    destinataire = re.search(r"\((?:en mode Twin, )?(?:à|to) ([^)]+)\)", question) or re.match(r"Simuler le RDV avec ([^:]+) :", question)
    if destinataire and citation(question):
        twin = trouver_twin(destinataire.group(1))
        if twin is None:
            return f"[Digital Twin « {destinataire.group(1)} » introuvable]"
        debut = appeler(base, "/twin/start", {"entreprise_id": twin["entreprise_id"], "contact": twin["nom"]})
        if "erreur" in debut:
            return debut["erreur"]
        reponse = appeler(base, "/twin/message", {"session_id": debut["session_id"], "message": citation(question)})
        return reponse.get("reply") or reponse.get("erreur")
    if question.startswith("Écran 1"):
        reponse = appeler(base, "/prospects/search", {"query": citation(question) or question, "limit": 10})
        return " ; ".join(f"{p['nom']} {p['score']}" for p in reponse.get("prospects", [])) or reponse.get("erreur")
    reponse = appeler(base, "/ask", {"question": citation(question) if re.match(r"(Écran \d|Piège) :", question) else question})
    return reponse.get("answer") or reponse.get("erreur")


def elements_cles(attendu: str) -> list[str]:
    elements = set(re.findall(r"\b(?:TW|FB)-\d+E?\b", attendu))
    elements |= set(re.findall(r"\d+(?:[.,]\d+)?", attendu))
    for mot in re.findall(r"\b[A-ZÉÈÀ][\wéèêàçôîï'’&-]{3,}\b", attendu):
        if knowledge.normalize(mot) not in MOTS_COURANTS:
            elements.add(mot)
    return sorted(elements)


def noter(q: dict, reponse: str | None) -> str:
    """✔ bonne réponse, ✘ mauvaise, — non testé automatiquement."""
    if reponse is None:
        return "—"
    attendu, rep = q["attendu"], knowledge.normalize(reponse)
    if re.match(r"(Information absente|Inconnu)", attendu):
        return "✔" if re.search(r"pas cette information|n ai pas|inconnu|ne sais pas", rep) else "✘"
    if "ne doit pas inventer" in attendu:
        return "✘" if re.search(r"\d", reponse) else "✔"
    elements = elements_cles(attendu)
    if not elements:
        return "—"
    trouves = sum(knowledge.normalize(e) in rep for e in elements)
    return "✔" if trouves / len(elements) >= SEUIL else "✘"


def court(texte: str | None, n: int = 400) -> str:
    texte = " ".join((texte or "").split())
    return (texte[:n] + "…") if len(texte) > n else texte


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--only", help="ne lance que les parties dont le titre contient ce texte")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--fichier", default=str(Path(__file__).parent / "questions_test.md"))
    parser.add_argument("--rapport", default=str(Path(__file__).parent / "rapport_eval.md"))
    args = parser.parse_args()

    questions = [q for q in lire_questions(Path(args.fichier)) if not args.only or args.only.lower() in q["partie"].lower()]
    if args.limit:
        questions = questions[:args.limit]

    resultats = []
    for numero, q in enumerate(questions, start=1):
        reponse = poser(args.url, q)
        verdict = noter(q, reponse)
        resultats.append((q, reponse, verdict))
        print(f"[{numero}/{len(questions)}] {verdict} {court(q['question'], 90)}")

    testees = [r for r in resultats if r[2] != "—"]
    bonnes = sum(r[2] == "✔" for r in testees)
    taux = f"{bonnes}/{len(testees)} = {100 * bonnes / len(testees):.0f} %" if testees else "aucune question testée"

    lignes = ["# Rapport d'évaluation", "",
              f"**Taux de bonnes réponses (automatique, indicatif) : {taux}**", "",
              f"{len(resultats) - len(testees)} question(s) non testée(s) automatiquement (—). "
              f"Une réponse est comptée bonne si au moins {int(SEUIL * 100)} % des éléments précis de la réponse attendue "
              "(références, chiffres, noms) y figurent : relire les réponses pour les nuances.", ""]
    partie = None
    for q, reponse, verdict in resultats:
        if q["partie"] != partie:
            partie = q["partie"]
            lignes += ["", f"## {partie}", "", "| | Question | Réponse de l'API | Attendu |", "|---|---|---|---|"]
        cellule = lambda t: court(t).replace("|", "\\|")
        lignes.append(f"| {verdict} | {cellule(q['question'])} | {cellule(reponse)} | {cellule(q['attendu'])} |")
    Path(args.rapport).write_text("\n".join(lignes) + "\n", encoding="utf-8")
    print(f"\nTaux de bonnes réponses : {taux}\nRapport écrit dans {args.rapport}")


if __name__ == "__main__":
    main()
