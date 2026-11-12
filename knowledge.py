"""Lecture structurée de story.txt.

Le fichier est lu une seule fois au démarrage et transformé en données Python :
- `sections`   : toutes les sections du document (titre, type, entreprise, texte) ;
- `entreprises`: les clients et les prospects, avec leurs sections liées ;
- `prospects`  : les fiches prospects (accroche, signaux, contact, points de score...) ;
- `produits`   : le catalogue (code, dosage, température, allergènes, prix...) ;
- `twins`      : les Digital Twins (contacts que l'IA peut jouer) ;
- `regles`     : les règles commerciales chiffrées (remises, délais, minimum de commande).

Rien ici n'utilise l'IA : tout vient d'expressions régulières sur le texte.
"""

from __future__ import annotations

import math
import re
import unicodedata
from functools import lru_cache
from pathlib import Path

import config


# ----------------------------------------------------------------------------------------
# Petits outils de texte
# ----------------------------------------------------------------------------------------

def normalize(text: str) -> str:
    """Minuscules, sans accents ni ponctuation : sert à comparer des noms."""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def slugify(text: str) -> str:
    """« Maison Alizé » -> « maison-alize »."""
    return normalize(text).replace(" ", "-")


def roman_to_int(numero: str) -> int:
    values = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100}
    total = 0
    for i, char in enumerate(numero):
        value = values[char]
        total += -value if i + 1 < len(numero) and values[numero[i + 1]] > value else value
    return total


def strip_parentheses(text: str) -> str:
    return re.sub(r"\s*\(.*?\)\s*$", "", text).strip()


def companies_match(a: str, b: str) -> bool:
    """« Atlantic Drinks » et « Atlantic Drinks Ltd » désignent la même entreprise."""
    na, nb = normalize(a), normalize(b)
    return bool(na and nb and (na in nb or nb in na))


# ----------------------------------------------------------------------------------------
# Découpage en sections
# ----------------------------------------------------------------------------------------

# Un titre de section : numéro romain, point, titre. Ex. « XXVIII. Fiche prospect — Maison Alizé (score 95/100) »
SECTION_TITLE_RE = re.compile(r"^(?P<numero>[IVXLC]+)\.\s+(?P<titre>\S.*)$")

_TWIN_RE = re.compile(r"^Digital Twin\s+—\s+(?P<nom>[^,]+),\s*(?P<reste>.+)$")
_CLIENT_RE = re.compile(r"^(?P<genre>Fiche client perdu|Fiche client|Customer profile)\s+—\s+(?P<entreprise>.+)$")
_PROSPECT_RE = re.compile(r"^Fiche prospect\s+—\s+(?P<entreprise>.+)$")
_COMPTE_RENDU_RE = re.compile(r"^Compte rendu de visite\s+—\s+(?P<entreprise>[^,]+)")

# (mot-clé dans le titre normalisé, sujet) pour les sections de référence
_REFERENCE_SUJETS = [
    ("alertes de veille", "alertes"),
    ("veille", "veille"),
    ("grille de score", "grille_score"),
    ("recapitulatif", "recap"),
    ("presentation du secteur", "secteur"),
    ("techniques", "techniques"),
    ("sources utilisees", "sources"),
    ("tendances", "tendances"),
    ("concurrence", "concurrence"),
    ("procedure", "procedure"),
    ("scenarios", "scenarios"),
]


def classify_title(titre: str) -> dict:
    """Déduit du titre d'une section son type, son sujet et l'entreprise concernée.

    type : client | prospect | compte_rendu | twin | produits | reference
    """
    info = {"type": "reference", "sujet": "", "entreprise": "", "twin": "", "role": "", "statut": ""}
    if m := _TWIN_RE.match(titre):
        parts = [p.strip() for p in m["reste"].split(",")]
        info.update(type="twin", twin=m["nom"].strip(), entreprise=parts[-1], role=", ".join(parts[:-1]))
    elif m := _CLIENT_RE.match(titre):
        statut = "perdu" if m["genre"] == "Fiche client perdu" else "actif"
        info.update(type="client", statut=statut, entreprise=strip_parentheses(m["entreprise"]))
    elif m := _PROSPECT_RE.match(titre):
        info.update(type="prospect", statut="prospect", entreprise=strip_parentheses(m["entreprise"]))
    elif m := _COMPTE_RENDU_RE.match(titre):
        info.update(type="compte_rendu", entreprise=m["entreprise"].strip())
    elif titre.startswith("Catalogue"):
        info.update(type="produits", sujet="catalogue")
    else:
        nt = normalize(titre)
        for mot_cle, sujet in _REFERENCE_SUJETS:
            if mot_cle in nt:
                info["sujet"] = sujet
                break
    return info


def split_sections(text: str) -> tuple[str, list[dict]]:
    """Retourne (titre du document, sections). Chaque section a ses paragraphes et son texte."""
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    doc_title = ""
    if paragraphs and not SECTION_TITLE_RE.match(paragraphs[0]):
        doc_title = paragraphs.pop(0)

    sections: list[dict] = []
    current = None
    for p in paragraphs:
        m = SECTION_TITLE_RE.match(p)
        if m and len(p) < 150:
            current = {"numero": m["numero"], "titre": m["titre"], "paragraphes": []}
            sections.append(current)
        elif current is not None:
            current["paragraphes"].append(p)
        # un texte avant la première section est ignoré

    for index, s in enumerate(sections, start=1):
        s["id"] = f"section-{index:02d}"
        s["index"] = index
        s["chapter"] = f"{s['numero']}. {s['titre']}"
        s["texte"] = "\n\n".join(s["paragraphes"])
        s.update(classify_title(s["titre"]))
    return doc_title, sections


def labelled_fields(paragraphs: list[str]) -> dict[str, str]:
    """« Accroche : lancement... » -> {"accroche": "lancement..."} (champs d'une fiche prospect)."""
    fields: dict[str, str] = {}
    for p in paragraphs:
        label, sep, value = p.partition(" : ")
        if sep and len(label) <= 40 and "." not in label:
            fields.setdefault(normalize(label), value.strip())
    return fields


# ----------------------------------------------------------------------------------------
# Détection d'un client, d'un prospect, d'une personne ou d'un produit dans une question
# ----------------------------------------------------------------------------------------

_ARTICLES = {"la", "le", "les", "l"}
_STOPWORDS = {"de", "des", "du", "d"}
_GENERIC_FIRST = {"biscuiterie", "cosmetiques", "laboratoire"}
_SOLO_ALIASES = {"laiterie", "brasserie", "miellerie", "savonnerie", "conserveries"}
_PRODUCT_STOPWORDS = {"naturelle", "reducteur", "masqueur", "composition", "pure"}


def company_aliases(nom: str) -> set[str]:
    words = normalize(nom).split()
    if words and words[0] in _ARTICLES:
        words = words[1:]
    aliases = {" ".join(words)}
    if len(words) > 1 and words[-1] == "ltd":
        aliases.add(" ".join(words[:-1]))
    if len(words) > 2 and words[0] in _GENERIC_FIRST and words[1] not in _STOPWORDS:
        aliases.add(" ".join(words[1:]))  # « Maison Larrieu », « Sources de Cambo »...
    if len(words) > 1 and words[0] in _SOLO_ALIASES:
        aliases.add(words[0])  # « la laiterie »
    return {a for a in aliases if len(a) >= 4}


def person_aliases(nom: str) -> set[str]:
    words = normalize(nom).split()
    aliases = {" ".join(words)}
    if words:
        aliases.update(w for w in (words[0], words[-1]) if len(w) >= 4)  # prénom ou nom de famille
    return aliases


def mentions(question: str, aliases: set[str]) -> bool:
    padded = f" {normalize(question)} "
    return any(f" {a} " in padded for a in aliases)


# ----------------------------------------------------------------------------------------
# Recherche par mots rares dans les paragraphes (complète la recherche vectorielle)
# ----------------------------------------------------------------------------------------

# Mots trop courants pour aider à trouver un passage
_QUESTION_STOP = {
    "quel", "quels", "quelle", "quelles", "comment", "pourquoi", "dans", "avec", "pour", "sont", "peut", "peuvent",
    "pourraient", "pourrait", "faut", "faire", "donne", "donner", "moi", "mon", "mes", "cite", "prepare", "preparer",
    "puis", "plus", "moins", "tout", "tous", "tres", "cette", "cela", "ceux", "elle", "elles", "leur", "leurs",
    "nous", "vous", "etre", "avoir", "aussi", "mais", "chez", "dont", "entre", "sans", "sous", "quoi", "alors",
    "what", "does", "want", "should", "about", "with", "that", "this", "have", "from",
}


def _stem(mot: str) -> str:
    """Racine grossière (6 premières lettres) : « acheter » et « achetés » se rejoignent."""
    return mot[:6]


def keyword_tokens(text: str) -> set[str]:
    """Mots utiles d'un texte, sans accents, plus les références produits (tw-502, fb-140...)."""
    jetons = set(re.findall(r"(?:tw|fb)-\d+e?", text.lower()))
    jetons |= {_stem(w) for w in normalize(text).split() if len(w) >= 4 and w not in _QUESTION_STOP}
    return jetons


# ----------------------------------------------------------------------------------------
# La base de connaissances
# ----------------------------------------------------------------------------------------

_PRODUCT_RE = re.compile(r"^(?P<code>(?:TW|FB)-\d+E?)\s+(?P<nom>[^:]+?)\s*:")
_RECAP_RE = re.compile(r"^\d+\.\s+(?P<nom>.+?)\s+\((?P<ville>[^)]+)\)\s+—\s+(?P<score>\d+)/100\s+—\s+(?P<accroche>.+)$")


class Knowledge:
    def __init__(self, text: str):
        self.doc_title, self.sections = split_sections(text)
        self.sections_by_id = {s["id"]: s for s in self.sections}
        self.regles = self._parse_regles()
        self.produits = self._parse_produits()
        self.entreprises = self._build_entreprises()
        self.twins = self._build_twins()
        self.prospects = self._build_prospects()
        self.types_alertes, self.alertes_recommandees = self._parse_alertes()
        self._build_paragraph_index()

    # --- recherche par mots rares ----------------------------------------------------------

    def _build_paragraph_index(self) -> None:
        self._paragraphs = [
            {"section_id": s["id"], "chapter": s["chapter"], "texte": p, "tokens": keyword_tokens(p)}
            for s in self.sections for p in s["paragraphes"]
        ]
        total = len(self._paragraphs)
        frequence: dict[str, int] = {}
        for p in self._paragraphs:
            for t in p["tokens"]:
                frequence[t] = frequence.get(t, 0) + 1
        # Un mot présent dans peu de paragraphes (« sweetbalance ») compte plus qu'un mot courant (« client »)
        self._idf = {t: math.log(1 + total / n) ** 2 for t, n in frequence.items()}  # au carré : les mots rares dominent

    def _mots_cles(self, question: str) -> set[str]:
        """Éléments précis de la question : références produits et noms propres (mots en majuscule hors début de phrase).
        Les mots courants (« client », « recommandé ») ne servent pas à chercher : ils sont partout dans le document."""
        jetons = set(re.findall(r"(?:tw|fb)-\d+e?", question.lower()))
        for mot in re.findall(r"[A-ZÉÈÀÂÎÔÙ][\w'’-]{3,}", question)[0:]:
            if question.strip().startswith(mot):
                continue  # le premier mot est en majuscule à cause de la phrase, pas parce que c'est un nom
            jetons.add(_stem(normalize(mot).replace(" ", "")))
        return {t for t in jetons if self._idf.get(t, 0) >= 9}  # assez rare dans le document pour être utile

    def search_paragraphs(self, question: str, limit: int = 4, section_ids: set[str] | None = None) -> list[dict]:
        """Les paragraphes qui parlent d'un nom propre ou d'une référence de la question (sans IA, instantané).
        Exemple : « Quels clients pourraient acheter SweetBalance ? » -> les paragraphes qui citent SweetBalance."""
        cles = self._mots_cles(question)
        if not cles:
            return []
        trouves = []
        for p in self._paragraphs:
            if section_ids is not None and p["section_id"] not in section_ids:
                continue
            communs = cles & p["tokens"]
            if communs:
                score = sum(self._idf[t] for t in communs) / (len(p["tokens"]) ** 0.25)
                trouves.append((score, p))
        trouves.sort(key=lambda x: -x[0])
        return [p for _, p in trouves[:limit]]

    # --- accès simples -------------------------------------------------------------------

    def sections_of_sujet(self, *sujets: str) -> list[dict]:
        return [s for s in self.sections if s["sujet"] in sujets]

    def sections_of_type(self, *types: str) -> list[dict]:
        return [s for s in self.sections if s["type"] in types]

    def entreprise(self, entreprise_id: str) -> dict | None:
        return self.entreprises.get(entreprise_id)

    def sections_of_entreprise(self, entreprise_id: str) -> list[dict]:
        return [self.sections_by_id[sid] for sid in self.entreprises[entreprise_id]["sections"]]

    def produit(self, code: str) -> dict | None:
        return next((p for p in self.produits if p["code"].upper() == code.upper()), None)

    # --- règles chiffrées -------------------------------------------------------------------

    def _parse_regles(self) -> dict:
        """Chiffres de la section « Présentation du secteur » (valeurs par défaut si le texte change)."""
        secteur = " ".join(s["texte"] for s in self.sections if "Présentation du secteur" in s["titre"])

        def number(pattern: str, default: int) -> int:
            m = re.search(pattern, secteur)
            return int(m.group(1)) if m else default

        return {
            "remise_standard": 3, "remise_volume": 5, "remise_contrat_min": 6, "remise_contrat_max": 8,
            "remise_validation_au_dela": number(r"remise supérieure à (\d+) %", 8),
            "echantillons_par_trimestre": number(r"jusqu'à (\d+) échantillons", 5),
            "delai_echantillon_jours": 5,       # échantillon standard, en jours ouvrés
            "delai_sur_mesure_semaines": 3,     # échantillon sur mesure
            "delai_livraison_jours": 10,        # produit catalogue en stock, en jours ouvrés
            "delai_premiere_production_semaines": 6,
            "commande_minimum_kg": number(r"commande minimum est de (\d+) kg", 25),
            "stock_securite_tonnes": number(r"à partir de (\d+) tonne", 1),
        }

    # --- produits -----------------------------------------------------------------------------

    def _parse_produits(self) -> list[dict]:
        produits = []
        for section in self.sections_of_type("produits"):
            division = "Taste & Wellbeing" if "Taste" in section["titre"] else "Fragrance & Beauty"
            for paragraph in section["paragraphes"]:
                m = _PRODUCT_RE.match(paragraph)
                if not m:
                    continue
                produits.append(self._parse_produit(m["code"], m["nom"], division, paragraph))
        return produits

    @staticmethod
    def _parse_produit(code: str, nom: str, division: str, texte: str) -> dict:
        def find(pattern: str):
            m = re.search(pattern, texte)
            return m.group(1).strip() if m else None

        temperature = find(r"Stable jusqu['’]à (\d+) °C") or find(r"Non stable au-delà de (\d+) °C")
        allergenes_txt = find(r"Allergènes? à déclarer : ([^.]+)\.")
        if allergenes_txt:
            allergenes = [a.strip() for a in re.split(r",| et ", allergenes_txt) if a.strip()]
        elif m := re.search(r"Contient (?:du |de la |des )?(\w+) \(allergène", texte):
            allergenes = [m.group(1)]
        else:
            allergenes = []
        certifications = [label for key, label in (
            ("halal", "halal"), ("casher", "casher"), ("COSMOS", "COSMOS"), ("IFRA", "IFRA 51e amendement")) if key in texte]
        prix = find(r"Prix(?: catalogue)? : (\d+) €")
        return {
            "code": code, "nom": nom, "division": division,
            "dosage": find(r"Dosage(?: recommandé)? : (.+?)\.(?:\s|$)"),
            "temperature_max": int(temperature) if temperature else None,
            "allergenes": allergenes,
            "prix_kg": int(prix) if prix else None,
            "certifications": certifications,
            "bio": "COSMOS" in certifications,
            "texte": texte,
        }

    def product_aliases(self, produit: dict) -> set[str]:
        nom = produit["nom"]
        aliases = {normalize(produit["code"]), normalize(nom)}
        aliases.update(normalize(w) for w in re.findall(r"\b[A-Z][a-z]+[A-Z]\w*\b", nom))  # SweetBalance, ProMask
        for word in normalize(nom).split():
            if len(word) >= 6 and word not in _PRODUCT_STOPWORDS:
                aliases.add(word)  # « vanille », « yuzu », « gariguette »...
        return aliases

    def produits_cites(self, question: str) -> list[dict]:
        return [p for p in self.produits if mentions(question, self.product_aliases(p))]

    # --- entreprises --------------------------------------------------------------------------

    def _build_entreprises(self) -> dict[str, dict]:
        entreprises: dict[str, dict] = {}
        for s in self.sections_of_type("client", "prospect"):
            e = entreprises.setdefault(slugify(s["entreprise"]), {
                "id": slugify(s["entreprise"]), "nom": s["entreprise"], "type": s["type"],
                "statut": s["statut"], "sections": [],
            })
            e["sections"].append(s["id"])
        # Les comptes rendus et les Digital Twins se rattachent à l'entreprise dont la fiche porte le même nom.
        for s in self.sections_of_type("compte_rendu", "twin"):
            owner = next((e for e in entreprises.values() if companies_match(e["nom"], s["entreprise"])), None)
            if owner is None:
                continue
            s["entreprise"] = owner["nom"]
            owner["sections"].append(s["id"])
        for e in entreprises.values():
            e["sections"].sort(key=lambda sid: self.sections_by_id[sid]["index"])
            e["fiche"] = next(sid for sid in e["sections"] if self.sections_by_id[sid]["type"] in ("client", "prospect"))
            if e["type"] == "client":
                e.update(self._parse_client(self.sections_by_id[e["fiche"]]["texte"]))
        return entreprises

    @staticmethod
    def _parse_client(texte: str) -> dict:
        def find(pattern: str):
            m = re.search(pattern, texte)
            return m.group(1).strip() if m else None

        ca = find(r"(?:Chiffre d'affaires 2025(?: avec Givaudan)?|2025 revenue)\s*:\s*([\d\s]+)\s*€")
        remise = find(r"(?:Remise actuelle|Current discount)\s*:\s*(\d+)\s*%")
        depuis = find(r"(?:Client depuis|Customer since) (\d{4})")
        return {
            "chiffre_affaires_2025": int(re.sub(r"\D", "", ca)) if ca else None,
            "remise_actuelle": int(remise) if remise else None,
            "client_depuis": int(depuis) if depuis else None,
        }

    # --- Digital Twins ---------------------------------------------------------------------------

    def _build_twins(self) -> list[dict]:
        twins = []
        for s in self.sections_of_type("twin"):
            ent = next((e for e in self.entreprises.values() if s["id"] in e["sections"]), None)
            if ent is None:
                continue
            twins.append({
                "nom": s["twin"], "role": s["role"], "entreprise_id": ent["id"], "entreprise": ent["nom"],
                "section_id": s["id"], "source": "section",
                "langue": "en" if re.search(r"English only", s["texte"], re.IGNORECASE) else "fr",
            })
        return twins

    def _add_prospect_twins(self) -> None:
        """Les prospects cosmétiques n'ont pas de section « Digital Twin » : leur profil est dans la fiche."""
        for p in self.prospects:
            if not p["profil_twin"]:
                continue
            nom = p["contact_nom"] or f"le contact de {p['nom']}"
            self.twins.append({
                "nom": nom, "role": p["contact_role"] or "contact", "entreprise_id": p["id"], "entreprise": p["nom"],
                "section_id": p["section_id"], "source": "prospect", "langue": "fr",
            })

    def twin_for(self, entreprise_id: str, contact: str | None = None) -> dict | None:
        candidats = [t for t in self.twins if t["entreprise_id"] == entreprise_id]
        if contact:
            candidats = [t for t in candidats if normalize(contact) in normalize(t["nom"])
                         or normalize(t["nom"]) in normalize(contact)]
        return candidats[0] if candidats else None

    def twin_text(self, twin: dict) -> str:
        """Profil complet à donner au LLM : on le charge par nom, jamais par recherche vectorielle."""
        if twin["source"] == "section":
            ent = self.entreprises[twin["entreprise_id"]]
            sections = [self.sections_by_id[sid] for sid in ent["sections"]
                        if self.sections_by_id[sid]["id"] == twin["section_id"]
                        or self.sections_by_id[sid]["type"] in ("client", "prospect", "compte_rendu")]
            return "\n\n".join(f"[{s['chapter']}]\n{s['texte']}" for s in sections)
        # Prospect : on garde ce que le prospect sait de lui-même, pas le score ni la stratégie de Givaudan.
        p = next(x for x in self.prospects if x["id"] == twin["entreprise_id"])
        parts = [p["identite"], p["accroche"] and f"Projet : {p['accroche']}", p["signaux"] and f"Signaux publics : {p['signaux']}",
                 p["contact_texte"] and f"Contact : {p['contact_texte']}", f"Profil Digital Twin : {p['profil_twin']}"]
        return "\n\n".join(x for x in parts if x)

    # --- prospects -----------------------------------------------------------------------------------

    def _build_prospects(self) -> list[dict]:
        recap: dict[str, dict] = {}
        for s in self.sections_of_sujet("recap"):
            for paragraph in s["paragraphes"]:
                if m := _RECAP_RE.match(paragraph):
                    recap[normalize(m["nom"])] = {"ville": m["ville"], "score": int(m["score"]), "accroche": m["accroche"]}

        prospects = []
        for s in self.sections_of_type("prospect"):
            fields = labelled_fields(s["paragraphes"])
            donnees = fields.get("donnees de score", "")
            r = recap.get(normalize(s["entreprise"]), {})
            contact_texte = fields.get("contact") or fields.get("contact identifie") or ""
            contact_nom, contact_role = self._parse_contact(contact_texte, s["texte"])
            prospects.append({
                "id": slugify(s["entreprise"]), "nom": s["entreprise"], "section_id": s["id"],
                "ville": r.get("ville", ""),
                "avec_score": bool(donnees),            # les prospects « régionaux » n'ont pas de score
                "score_ecrit": r.get("score"),
                "identite": fields.get("identite", s["paragraphes"][0] if s["paragraphes"] else ""),
                "accroche": fields.get("accroche") or r.get("accroche", ""),
                "signaux": fields.get("signaux", ""),
                "pourquoi": fields.get("pourquoi cette entreprise", ""),
                "produits_adaptes": fields.get("produits givaudan adaptes") or fields.get("pistes de solutions", ""),
                "contact_texte": contact_texte, "contact_nom": contact_nom, "contact_role": contact_role,
                "profil_twin": fields.get("profil digital twin", ""),
                "donnees_score": donnees,
                "prochaine_etape": fields.get("prochaine etape", ""),
            })
        self.prospects = prospects
        self._add_prospect_twins()
        return prospects

    @staticmethod
    def _parse_contact(contact_texte: str, section_texte: str) -> tuple[str, str]:
        """« Élise Marchand, fondatrice et directrice générale. Identifiée... » -> (nom, fonction)."""
        if contact_texte.lower().startswith("aucun contact"):
            m = re.search(r"Le fondateur, ([^,]+),", contact_texte)  # ex. Hommage Paris : le fondateur est cité dans la presse
            return (m.group(1).strip(), "fondateur") if m else ("", "")
        parts = [x.strip() for x in contact_texte.split(",")]
        if len(parts) >= 2:
            role = re.split(r"[.;]| rencontré| qui | recommandé| identifié", parts[1])[0].strip()
            return parts[0], role
        return (parts[0] if parts and parts[0] else ""), ""

    def prospect(self, prospect_id: str) -> dict | None:
        return next((p for p in self.prospects if p["id"] == prospect_id), None)

    # --- alertes ------------------------------------------------------------------------------------------

    def _parse_alertes(self) -> tuple[list[str], list[dict]]:
        texte = " ".join(s["texte"] for s in self.sections_of_sujet("alertes"))
        types = []
        if m := re.search(r"Types d'alertes suivis : (.+?)\.\s", texte + " "):
            types = [t.strip() for t in m.group(1).split(";") if t.strip()]
        recommandees = []
        if m := re.search(r"Alertes recommandées : (.+?)\.?$", texte):
            for item in m.group(1).split(";"):
                if mm := re.match(r"\s*(?P<nom>.+?)\s*\((?P<signal>.+)\)\s*$", item):
                    recommandees.append({"entreprise": mm["nom"], "signal": mm["signal"]})
        return types, recommandees

    # --- détection dans une question ----------------------------------------------------------------------------

    def sections_for(self, question: str) -> list[dict]:
        """Sections qui parlent des clients, prospects ou personnes cités dans la question."""
        chosen: dict[str, dict] = {}
        people = [t for t in self.twins if mentions(question, person_aliases(t["nom"]))]
        for twin in people:  # les personnes d'abord : ce sont les cibles les plus précises
            chosen.setdefault(twin["section_id"], self.sections_by_id[twin["section_id"]])
            ent = self.entreprises[twin["entreprise_id"]]
            chosen.setdefault(ent["fiche"], self.sections_by_id[ent["fiche"]])
        for ent in self.entreprises.values():
            if mentions(question, company_aliases(ent["nom"])):
                for section in self.sections_of_entreprise(ent["id"]):
                    chosen.setdefault(section["id"], section)
        return list(chosen.values())

    def entreprises_citees(self, question: str) -> list[dict]:
        return [e for e in self.entreprises.values() if mentions(question, company_aliases(e["nom"]))]


@lru_cache(maxsize=1)
def get_knowledge(path: str | None = None) -> Knowledge:
    return Knowledge(Path(path or config.KNOWLEDGE_PATH).read_text(encoding="utf-8"))
