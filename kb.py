"""Accès structuré à la base de connaissances : types de sections, Digital Twins, clients.

Utilisé par `ingest.py` (métadonnées calculées à partir du titre de chaque section)
et par `api.py` (retrouver une fiche entière plutôt que seulement des morceaux proches).
"""

from __future__ import annotations

import re
import unicodedata

# --- Titres de sections -> métadonnées -------------------------------------------------

_PREFIX_RE = re.compile(r"^[IVXLC]+\.\s+")
_TWIN_RE = re.compile(r"^Digital Twin\s+—\s+(?P<name>[^,]+),\s*(?P<rest>.+)$")
_FICHE_RE = re.compile(r"^(?:Fiche (?:client perdu|client|prospect)|Customer profile)\s+—\s+(?P<company>.+)$")
_VISITE_RE = re.compile(r"^Compte rendu de visite\s+—\s+(?P<company>[^,]+)")
_PARENS_RE = re.compile(r"\s*\(.*?\)\s*$")

# (mot-clé dans le titre normalisé, type de section)
_OTHER_KINDS = [
    ("catalogue", "catalogue"),
    ("techniques d argumentaire", "methodes"),
    ("regles commerciales", "regles"),
    ("sources utilisees", "sources"),
    ("veille", "veille"),
    ("tendances", "marche"),
    ("concurrence", "objections"),
    ("procedure", "procedure"),
    ("scenarios d entrainement", "grille"),
]


def normalize(text: str) -> str:
    """Minuscules, sans accents ni ponctuation : sert aux comparaisons de noms."""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def section_metadata(title: str) -> dict[str, str]:
    """Déduit du titre d'une section son type et, le cas échéant, le client / le twin concerné.

    kind : twin | fiche | visite | catalogue | methodes | regles | sources | veille
           | marche | objections | procedure | grille | autre
    """
    t = _PREFIX_RE.sub("", title.strip())
    meta = {"kind": "autre", "company": "", "twin": "", "role": ""}

    if m := _TWIN_RE.match(t):
        parts = [p.strip() for p in m["rest"].split(",")]
        meta.update(kind="twin", twin=m["name"].strip(), company=parts[-1], role=", ".join(parts[:-1]))
    elif m := _FICHE_RE.match(t):
        meta.update(kind="fiche", company=_PARENS_RE.sub("", m["company"]).strip())
    elif m := _VISITE_RE.match(t):
        meta.update(kind="visite", company=m["company"].strip())
    else:
        nt = normalize(t)
        for keyword, kind in _OTHER_KINDS:
            if keyword in nt:
                meta["kind"] = kind
                break
    return meta


# --- Lecture de la base -------------------------------------------------------------------

def all_rows(collection) -> list[dict]:
    """Tous les morceaux de la base, dans l'ordre du document."""
    data = collection.get(include=["documents", "metadatas"])
    rows = [{"id": i, "text": d, "meta": m} for i, d, m in zip(data["ids"], data["documents"], data["metadatas"])]
    rows.sort(key=lambda r: (r["meta"].get("chapter_index", 0), r["meta"].get("chunk_index", 0)))
    return rows


def companies_match(a: str, b: str) -> bool:
    """« Atlantic Drinks » et « Atlantic Drinks Ltd » désignent la même entreprise."""
    na, nb = normalize(a), normalize(b)
    return bool(na and nb and (na in nb or nb in na))


def rows_of_kind(rows: list[dict], *kinds: str) -> list[dict]:
    return [r for r in rows if r["meta"].get("kind") in kinds]


# --- Digital Twins --------------------------------------------------------------------------

def list_twins(rows: list[dict]) -> list[dict]:
    seen: dict[str, dict] = {}
    for r in rows_of_kind(rows, "twin"):
        m = r["meta"]
        seen.setdefault(m["twin"], {
            "name": m["twin"], "role": m.get("role", ""), "company": m.get("company", ""), "chapter": m["chapter"],
        })
    return list(seen.values())


def find_twins(query: str, rows: list[dict]) -> list[dict]:
    """Twins dont le nom correspond à `query` (nom complet, prénom ou nom de famille)."""
    nq = normalize(query)
    if not nq:
        return []
    found = []
    for twin in list_twins(rows):
        nn = normalize(twin["name"])
        if nq == nn:
            return [twin]
        if nq in nn or nn in nq:
            found.append(twin)
    return found


def twin_rows(twin: dict, rows: list[dict]) -> tuple[list[dict], list[dict]]:
    """(fiche du twin, contexte client : fiche entreprise + comptes rendus de visite)."""
    sheet = [r for r in rows_of_kind(rows, "twin") if r["meta"]["twin"] == twin["name"]]
    background = [r for r in rows_of_kind(rows, "fiche", "visite") if companies_match(r["meta"]["company"], twin["company"])]
    return sheet, background


# --- Détection des clients / personnes cités dans une question ---------------------------------

_ARTICLES = {"la", "le", "les", "l"}
_STOPWORDS = {"de", "des", "du", "d"}
_SOLO_ALIASES = {"laiterie", "brasserie", "miellerie", "savonnerie", "conserveries"}


def _company_aliases(name: str) -> set[str]:
    words = normalize(name).split()
    if words and words[0] in _ARTICLES:
        words = words[1:]
    aliases = {" ".join(words)}
    if len(words) > 1 and words[-1] == "ltd":
        aliases.add(" ".join(words[:-1]))
    if len(words) > 1 and words[0] not in _STOPWORDS and words[1] not in _STOPWORDS and words[0] in {
        "biscuiterie", "cosmetiques", "laboratoire", "brasserie", "miellerie", "savonnerie", "laiterie",
    }:
        aliases.add(" ".join(words[1:]))  # « Maison Larrieu », « Océane »...
    if words and words[0] in _SOLO_ALIASES:
        aliases.add(words[0])  # « la laiterie »
    return {a for a in aliases if len(a) >= 4}


def _person_aliases(name: str) -> set[str]:
    words = normalize(name).split()
    aliases = {" ".join(words)}
    aliases.update(w for w in (words[0], words[-1]) if len(w) >= 4)  # prénom ou nom de famille
    return aliases


def _mentions(padded_question: str, aliases: set[str]) -> bool:
    return any(f" {a} " in padded_question for a in aliases)


def entity_rows(question: str, rows: list[dict]) -> list[dict]:
    """Morceaux consacrés aux clients ou aux personnes nommés dans la question.

    - un client cité -> toutes ses sections (fiche, visite, twins) ;
    - une personne citée -> sa fiche de Digital Twin et la fiche de son entreprise.
    """
    padded = f" {normalize(question)} "
    companies = {r["meta"]["company"] for r in rows
                 if r["meta"].get("company") and _mentions(padded, _company_aliases(r["meta"]["company"]))}
    people = [t for t in list_twins(rows) if _mentions(padded, _person_aliases(t["name"]))]

    selected: dict[str, dict] = {}
    for twin in people:  # les personnes d'abord : ce sont les cibles les plus précises
        sheet, _ = twin_rows(twin, rows)
        for r in sheet:
            selected.setdefault(r["id"], r)
        for r in rows_of_kind(rows, "fiche"):
            if companies_match(r["meta"]["company"], twin["company"]):
                selected.setdefault(r["id"], r)
    for r in rows:
        if r["meta"].get("company") and any(companies_match(r["meta"]["company"], c) for c in companies):
            selected.setdefault(r["id"], r)
    return list(selected.values())
