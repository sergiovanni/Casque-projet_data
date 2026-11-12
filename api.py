"""API « Givaudan AI Sales Copilot » (équipe Casque).

Règle d'or : l'IA ne calcule rien et ne décide rien de chiffré.
- les scores des prospects viennent de `scoring.py` ;
- les remises, températures, minimums et délais viennent de `rules.py` ;
- le LLM (Ollama) rédige seulement du texte à partir de données déjà vérifiées.

Lancement :
    python api.py                 # ou : uvicorn api:app --reload
Swagger : http://localhost:8000/docs
"""

import json
import re
import tempfile
import uuid
from datetime import datetime
from typing import Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

import config
import knowledge
import prompts
import rag
import rules
import scoring

app = FastAPI(
    title="Givaudan AI Sales Copilot API",
    description="Copilote du commercial Jordan Dubois : **Assistant** (/ask, /pitch), **prospection** (/prospects), "
                "**Digital Twin** pour s'entraîner (/twin/start, /twin/message) et **Coach** (/coach/evaluate). "
                "Les chiffres (scores, remises, températures) sont vérifiés par du code Python, pas par le LLM.",
    version="3.0.0",
)

# Le front tourne sur http://localhost:<port> pendant le hackathon
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # pour le hackathon ; à restreindre en production
    allow_methods=["*"],
    allow_headers=["*"],
)


def kn() -> knowledge.Knowledge:
    return knowledge.get_knowledge()


# ----------------------------------------------------------------------------------------
# Modèles de requête / réponse
# ----------------------------------------------------------------------------------------

class Message(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(..., min_length=1)


class AskRequest(BaseModel):
    question: str = Field(..., min_length=1, examples=["Prépare mon rendez-vous du 3 décembre avec la Laiterie des Pyrénées."])
    history: list[Message] = Field(default_factory=list, description="Échanges précédents du chat (facultatif)")
    entreprise_id: str | None = Field(None, examples=["capilla-nova"], description="Limite la recherche à un client ou prospect")
    top_k: int = Field(config.TOP_K, ge=1, le=20, description="Nombre de passages retrouvés par similarité")


class Source(BaseModel):
    chapter: str
    text: str
    score: float | None = Field(None, description="Similarité cosinus (1 = identique). Vide si le passage a été ajouté "
                                                   "parce que la question nomme cette entreprise, cette personne ou ce produit.")


class AskResponse(BaseModel):
    question: str
    answer: str
    model: str
    faits_verifies: list[str] = Field(description="Faits calculés par programme et donnés au LLM")
    sources: list[Source]


class ProspectSearchRequest(BaseModel):
    query: str = Field("", examples=["Trouve-moi 10 marques de cosmétiques françaises qui pourraient avoir besoin de nouvelles fragrances"])
    limit: int = Field(10, ge=1, le=50)


class PitchRequest(BaseModel):
    entreprise_id: str = Field(..., examples=["capilla-nova"])


class AlertRequest(BaseModel):
    entreprise_id: str = Field(..., examples=["atelier-verdane"])
    type: str = Field(..., examples=["fin d'un contrat exclusif"])


class TwinStartRequest(BaseModel):
    entreprise_id: str = Field(..., examples=["laiterie-des-pyrenees"])
    contact: str | None = Field(None, examples=["Hélène Bordes"], description="Nom du contact à jouer (sinon le premier de l'entreprise)")


class TwinMessageRequest(BaseModel):
    session_id: str
    message: str = Field(..., min_length=1, examples=["Bonjour Monsieur Etchegaray, parlons du retard de mars."])


class EvaluateRequest(BaseModel):
    session_id: str


# ----------------------------------------------------------------------------------------
# Outils communs
# ----------------------------------------------------------------------------------------

def llm(messages: list[dict], temperature: float = 0.2, json_mode: bool = False, max_tokens: int | None = None) -> str:
    try:
        if max_tokens:
            return rag.chat(messages, temperature, json_mode=json_mode, max_tokens=max_tokens).strip()
        return rag.chat(messages, temperature, json_mode=json_mode).strip()
    except Exception as exc:  # Ollama injoignable, modèle absent...
        raise HTTPException(502, f"Erreur Ollama : {exc}") from exc


_REFUS_RE = re.compile(r"^\W*(?:je n['’]ai pas cette information|je ne dispose pas de cette information|"
                       r"i don['’]t have this information)[^.\n]*[.\n]+\s*", re.IGNORECASE)


def remove_leading_refusal(answer: str) -> str:
    """Un petit modèle écrit parfois « Je n'ai pas cette information… » puis donne quand même la réponse.
    On retire ce refus de tête quand une vraie réponse suit ; une réponse qui n'est QUE le refus reste intacte."""
    reste = _REFUS_RE.sub("", answer, count=1).strip()
    return reste if len(reste) >= 30 else answer


def entreprise_or_404(entreprise_id: str) -> dict:
    ent = kn().entreprise(entreprise_id)
    if ent is None:
        raise HTTPException(404, f"Entreprise « {entreprise_id} » introuvable. Voir GET /clients et POST /prospects/search.")
    return ent


def build_context(sources: list[Source]) -> tuple[str, list[Source]]:
    """Assemble les passages dans la limite MAX_CONTEXT_CHARS. Retourne (texte, passages retenus)."""
    parts, used, total = [], [], 0
    for s in sources:
        block = f"[{s.chapter}]\n{s.text}"
        if parts and total + len(block) > config.MAX_CONTEXT_CHARS:
            continue
        parts.append(block)
        used.append(s)
        total += len(block)
    return "\n\n---\n\n".join(parts), used


# ----------------------------------------------------------------------------------------
# Recherche des passages (mode Assistant)
# ----------------------------------------------------------------------------------------

def collect_sources(question: str, entreprise_id: str | None, top_k: int) -> list[Source]:
    """Passages à donner au LLM, du plus sûr au moins sûr :
    1. les fiches des clients, prospects et personnes nommés dans la question (chargées par nom) ;
    2. les fiches des produits nommés ;
    3. les règles commerciales et les réponses aux objections (courtes, toujours utiles) ;
    4. les passages les plus proches de la question (recherche vectorielle, filtrée si entreprise_id).
    """
    k = kn()
    sections = {s["id"]: s for s in k.sections_for(question)}
    nom_filtre = None
    if entreprise_id:
        ent = entreprise_or_404(entreprise_id)
        nom_filtre = ent["nom"]
        for s in k.sections_of_entreprise(entreprise_id):
            sections.setdefault(s["id"], s)

    sources = [Source(chapter=s["chapter"], text=s["texte"]) for s in sections.values()]
    sources += [Source(chapter=f"Fiche produit {p['code']} {p['nom']}", text=p["texte"]) for p in k.produits_cites(question)]
    # Paragraphes qui citent un nom propre ou une référence de la question (ex. SweetBalance) : instantané, sans IA
    deja = {s["id"] for s in k.sections_for(question)}
    for p in k.search_paragraphs(question, limit=5):
        if p["section_id"] not in deja:
            sources.append(Source(chapter=p["chapter"], text=p["texte"]))
    # Ces deux sections ne sont ajoutées que si la question les concerne : un contexte plus court donne
    # une réponse plus rapide et plus ciblée avec un petit modèle.
    nq = knowledge.normalize(question)
    sujets = []
    if re.search(r"remise|delai|echantillon|livraison|commande|minimum|stock|contrat|rendez vous|rdv|prepare|pitch|regles", nq):
        sujets.append("secteur")
    if re.search(r"moins cher|concurren|symrise|iff|firmenich|objection|delais? (sont )?trop|naturel|denigr|rendez vous|rdv|prepare", nq):
        sujets.append("concurrence")
    for s in k.sections_of_sujet(*sujets):
        if s["id"] not in sections:
            sources.append(Source(chapter=s["chapter"], text=s["texte"]))

    vus = {s.text for s in sources}
    for hit in rag.search(question, top_k, entreprise=nom_filtre):
        if hit["text"] not in vus:
            vus.add(hit["text"])
            sources.append(Source(chapter=hit["meta"]["chapter"], text=hit["text"], score=hit["score"]))
    return sources


def verified_facts(question: str) -> list[str]:
    """FAITS VÉRIFIÉS : résultats de rules.py et scoring.py quand la question parle de
    remise, température, bio, quantité, délai ou score de prospect. Le LLM les reprend tels quels."""
    k = kn()
    nq = knowledge.normalize(question)
    faits: list[str] = []
    sections = k.sections_for(question)
    entreprises = {e["id"]: e for s in sections if s["entreprise"]
                   for e in k.entreprises.values() if e["nom"] == s["entreprise"]}
    produits = k.produits_cites(question)

    # Remise
    if re.search(r"\b(remise|accorder|rabais)\b", nq):
        contrat = 2 if re.search(r"\b(deux|2) ans\b", nq) else 0
        for brut in re.findall(r"(\d+(?:[.,]\d+)?)\s*%", question):
            valeur = float(brut.replace(",", "."))
            faits.append(rules.check_remise(valeur, contrat)["message"])
            if valeur >= k.regles["remise_contrat_min"] and contrat == 0:
                faits.append(f"Avec un contrat de deux ans minimum : " + rules.check_remise(valeur, 2)["message"])
        for ent in entreprises.values():
            if ent.get("remise_actuelle") is not None:
                faits.append(f"{ent['nom']} : remise actuelle de {ent['remise_actuelle']} %.")

    # Température
    if produits and re.search(r"°\s*c|temp[ée]rature|cuit|cuisson|st[ée]ril|four|pasteuris", question.lower()):
        temperatures = [float(t) for t in re.findall(r"(\d+)\s*°\s*C", question, re.IGNORECASE)]
        if not temperatures and "steril" in nq:
            for s in sections:
                if m := re.search(r"stérilisées à (\d+)\s*°C", s["texte"]):
                    temperatures.append(float(m.group(1)))
        for p in produits:
            if temperatures:
                faits += [rules.check_temperature(p["code"], t)["message"] for t in temperatures]
            elif p["temperature_max"] is not None:
                faits.append(f"{p['code']} {p['nom']} : température maximale {p['temperature_max']} °C (non stable au-delà).")
            else:
                faits.append(f"{p['code']} {p['nom']} : aucune température maximale donnée dans la fiche.")

    # Bio
    if re.search(r"\bbio\b", nq):
        if produits:
            faits += [rules.check_bio(p["code"])["message"] for p in produits]
        else:
            faits.append("Aucun arôme alimentaire du catalogue n'est certifié bio ; seul FB-112 Ambre Solaire Bio "
                         "est certifié COSMOS (cosmétique).")

    # Quantités
    if re.search(r"\bkg\b|tonne|minimum|quantit", nq):
        trouve = False
        for valeur, unite in re.findall(r"(\d+(?:[.,]\d+)?)\s*(kg|tonnes?)", question, re.IGNORECASE):
            kg = float(valeur.replace(",", ".")) * (1000 if unite.lower().startswith("tonne") else 1)
            faits.append(rules.check_quantite(kg, par_an="an" in nq)["message"])
            trouve = True
        if not trouve:
            faits.append(f"Commande minimum : {k.regles['commande_minimum_kg']} kg par référence ; stock de sécurité réservé "
                         f"possible à partir de {k.regles['stock_securite_tonnes']} tonne par an.")

    # Délais
    if re.search(r"\b(delai|delais|echantillons?|livraison|livrer)\b", nq):
        r = k.regles
        faits.append(f"Délais : échantillon standard {r['delai_echantillon_jours']} jours ouvrés ; échantillon sur mesure "
                     f"{r['delai_sur_mesure_semaines']} semaines ; livraison d'un produit catalogue en stock "
                     f"{r['delai_livraison_jours']} jours ouvrés ; première production sur mesure "
                     f"{r['delai_premiere_production_semaines']} semaines.")

    # Scores des prospects
    classement = scoring.classer()
    if re.search(r"chiffre d affaires|\bca\b|\bachats?\b|\bachete|commande|factur", nq):
        for ent in entreprises.values():
            if ent["type"] == "prospect":
                faits.append(f"{ent['nom']} est un prospect, pas encore client de Givaudan : aucun chiffre d'affaires, "
                             f"aucune commande et aucune remise avec Givaudan.")
    veut_le_score = re.search(r"\bscore\b|\brang\b|\btop\b|classement|pourquoi|points|prioritaire|\bnote\b", nq)
    for p in classement:
        if veut_le_score and p["id"] in entreprises:
            detail = " ; ".join(f"{d['libelle']} {d['points']}/{d['maximum']}" + (f" ({d['commentaire']})" if d["commentaire"] else "")
                                for d in p["details"].values())
            faits.append(f"Score de {p['nom']} : {p['score']}/100, rang {p['rang']} sur {len(classement)} (calculé par programme). "
                         f"Détail : {detail}.")
    if re.search(r"\bprospects?\b|marques? de cosmetiques|top 10|classement", nq):
        faits.append("Classement des prospects cosmétiques (score calculé par programme) : "
                     + " ; ".join(f"{p['rang']}. {p['nom']} {p['score']}/100" for p in classement) + ".")
    return faits


# ----------------------------------------------------------------------------------------
# Écran 0 — Assistant
# ----------------------------------------------------------------------------------------

@app.get("/health", tags=["système"])
def health():
    """Vérifie Ollama et le nombre de morceaux indexés."""
    ollama_ok = rag.ollama_available()
    try:
        chunks = rag.get_collection().count()
    except Exception:
        chunks = 0
    return {"status": "ok" if ollama_ok and chunks else "degraded", "ollama": ollama_ok, "chunks": chunks,
            "llm_model": config.LLM_MODEL, "embed_model": config.EMBED_MODEL}


@app.get("/prompts", tags=["assistant"])
def bibliotheque_prompts():
    """Liste fixe de questions par catégorie (préparation, pendant le RDV, entraînement, prospection)."""
    return prompts.BIBLIOTHEQUE


@app.post("/search", response_model=list[Source], tags=["assistant"])
def search(req: AskRequest):
    """Passages retrouvés seulement (sans LLM), pour déboguer."""
    return collect_sources(req.question, req.entreprise_id, req.top_k)


@app.post("/ask", response_model=AskResponse, tags=["assistant"])
def ask(req: AskRequest):
    """**Mode Assistant** : préparer un RDV, répondre en direct, piloter, prospecter. Avec `entreprise_id`, la recherche est filtrée."""
    if re.search(r"pitch", knowledge.normalize(req.question)):
        cible = req.entreprise_id or next((e["id"] for s in kn().sections_for(req.question) if s["entreprise"]
                                           for e in kn().entreprises.values() if e["nom"] == s["entreprise"]), None)
        if cible:  # « Prépare un pitch pour X » : même logique que /pitch (produits adaptés, DISC)
            r = pitch(PitchRequest(entreprise_id=cible))
            return AskResponse(question=req.question, answer=r["pitch"], model=config.LLM_MODEL, faits_verifies=[],
                               sources=[Source(chapter=f"Pitch — {r['entreprise']}", text=", ".join(r["produits"]) or "sur mesure")])
    sources = collect_sources(req.question, req.entreprise_id, req.top_k)
    context, used = build_context(sources)
    faits = verified_facts(req.question)

    texte_faits = "\n".join(f"- {f}" for f in faits) or "- Aucun fait calculé pour cette question."
    messages = [{"role": "system", "content": prompts.ASSISTANT_PROMPT}]
    messages += [{"role": m.role, "content": m.content} for m in req.history[-config.ASK_HISTORY:]]
    messages.append({"role": "user", "content": (
        f"Extraits des documents :\n\n{context}\n\nFAITS VÉRIFIÉS (calculés par programme) :\n{texte_faits}\n\n"
        f"Question : {req.question}")})
    answer = remove_leading_refusal(llm(messages, temperature=0.2, max_tokens=350))
    return AskResponse(question=req.question, answer=answer, model=config.LLM_MODEL, faits_verifies=faits, sources=used)


@app.post("/voice/transcribe", tags=["assistant"])
async def voice_transcribe(request: Request):
    """Bonus : audio -> texte, avec `faster-whisper` en local. Envoyer le fichier audio brut dans le corps de la requête."""
    audio = await request.body()
    if not audio:
        raise HTTPException(400, "Aucun audio reçu : envoie le fichier audio dans le corps de la requête.")
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise HTTPException(501, "faster-whisper n'est pas installé : pip install faster-whisper") from exc
    with tempfile.NamedTemporaryFile(suffix=".audio", delete=False) as fichier:
        fichier.write(audio)
    segments, _ = WhisperModel("base", device="cpu", compute_type="int8").transcribe(fichier.name, language="fr")
    return {"text": " ".join(s.text.strip() for s in segments)}


@app.post("/attach", tags=["assistant"])
def attach():
    """Le bouton « Attach » est hors périmètre du hackathon."""
    raise HTTPException(501, "L'envoi de pièces jointes n'est pas disponible dans cette version.")


# ----------------------------------------------------------------------------------------
# Écrans 1 à 3 — Prospection cosmétique (sans IA)
# ----------------------------------------------------------------------------------------

# Mot de la requête -> segment cherché dans les fiches (la requête sert de filtre simple, pas de recherche IA)
SEGMENTS = {"bebe": "bebe", "solaire": "solaire", "homme": "homme", "barbe": "barbe", "capillaire": "cheveux",
            "cheveux": "cheveux", "bien etre": "bien etre", "recharge": "recharge", "peaux sensibles": "peaux sensibles",
            "thalasso": "thalasso"}


@app.post("/prospects/search", tags=["prospection"])
def prospects_search(req: ProspectSearchRequest):
    """Écran 1 — prospects cosmétiques triés par score (calculé par du code Python, jamais par l'IA).

    La requête ne sert qu'à filtrer si elle nomme un segment précis (bébé, solaire, homme, recharge...).
    Sinon, tous les prospects sont classés. Le résultat est le même à chaque appel.
    """
    classement = scoring.classer()
    nq = knowledge.normalize(req.query)
    for mot, segment in SEGMENTS.items():
        if f" {mot} " in f" {nq} ":
            classement = [p for p in classement
                          if segment in knowledge.normalize(" ".join([p["identite"], p["accroche"], p["signaux"], p["pourquoi"]]))]
            break
    return {
        "query": req.query,
        "total": len(classement),
        "prospects": [{"id": p["id"], "nom": p["nom"], "ville": p["ville"], "score": p["score"], "rang": p["rang"],
                       "accroche": p["accroche"]} for p in classement[:req.limit]],
    }


@app.get("/prospects/{prospect_id}", tags=["prospection"])
def prospect_detail(prospect_id: str):
    """Écrans 2 et 3 — fiche détaillée, « Pourquoi cette entreprise ? » et détail du score."""
    prospect = kn().prospect(prospect_id)
    if prospect is None:
        raise HTTPException(404, f"Prospect « {prospect_id} » introuvable.")
    p = scoring.prospect_score(prospect_id) or {**prospect, "score": None, "rang": None, "details": {}, "anomalies": []}
    produits = [pr["code"] for pr in kn().produits if pr["code"] in p["produits_adaptes"]]
    return {
        "id": p["id"], "nom": p["nom"], "ville": p["ville"], "score": p["score"], "score_maximum": scoring.SCORE_MAXIMUM,
        "rang": p["rang"], "accroche": p["accroche"], "identite": p["identite"], "signaux": p["signaux"],
        "pourquoi_cette_entreprise": p["pourquoi"], "produits_adaptes": p["produits_adaptes"], "codes_produits": produits,
        "contact": {"nom": p["contact_nom"], "role": p["contact_role"], "texte": p["contact_texte"]},
        "profil_digital_twin": p["profil_twin"],
        "details_score": [{"critere": cle, **d} for cle, d in p["details"].items()],
        "points_manquants": [{"critere": cle, "libelle": d["libelle"], "manque": d["maximum"] - d["points"]}
                             for cle, d in p["details"].items() if d["points"] < d["maximum"]],
        "prochaine_etape": p["prochaine_etape"],
        "anomalies_score": p["anomalies"],
    }


@app.post("/pitch", tags=["prospection"])
def pitch(req: PitchRequest):
    """Pitch avec la méthode CAP, adapté au profil DISC du contact et basé sur les produits adaptés."""
    k = kn()
    ent = entreprise_or_404(req.entreprise_id)
    prospect = k.prospect(req.entreprise_id)
    if prospect:
        fiche = k.twin_text(k.twin_for(req.entreprise_id) or {"source": "prospect", "entreprise_id": req.entreprise_id}) \
            if prospect["profil_twin"] else prospect["identite"]
        adaptes, contact = prospect["produits_adaptes"], prospect["contact_nom"]
        profil, accroche = prospect["profil_twin"], prospect["accroche"]
    else:
        textes = [s["texte"] for s in k.sections_of_entreprise(req.entreprise_id)]
        fiche = "\n\n".join(textes)
        adaptes = " ".join(m.group(0) for t in textes for m in re.finditer(r"(Opportunités|Opportunities|Pistes de solutions)\s*:[^\n]+", t))
        twin = k.twin_for(req.entreprise_id)
        contact, profil, accroche = (twin["nom"] if twin else ""), "", ""
    codes = sorted({m.upper() for m in re.findall(r"\b((?:TW|FB)-\d+E?)\b", adaptes, re.IGNORECASE)})
    produits = [p for p in (k.produit(c) for c in codes) if p]
    texte_produits = "\n".join(f"- {p['texte']}" for p in produits) or "Aucun produit du catalogue : développement sur mesure."

    messages = [
        {"role": "system", "content": prompts.PITCH_PROMPT},
        {"role": "user", "content": (
            f"Entreprise : {ent['nom']}\nContact : {contact or 'non identifié'}\nProjet : {accroche}\n\n"
            f"Fiche :\n{fiche}\n\nProfil DISC / SONCAS : {profil or 'voir la fiche'}\n\n"
            f"Produits adaptés (fiches techniques) :\n{texte_produits}\n\nÉcris le pitch.")},
    ]
    return {"entreprise": ent["nom"], "contact": contact, "produits": [p["code"] for p in produits],
            "pitch": llm(messages, temperature=0.2, max_tokens=300), "model": config.LLM_MODEL}


# ----------------------------------------------------------------------------------------
# Alertes de veille (enregistrées dans alerts.json)
# ----------------------------------------------------------------------------------------

def load_alerts() -> list[dict]:
    try:
        return json.loads(config.ALERTS_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return []


@app.post("/alerts", status_code=201, tags=["alertes"])
def create_alert(req: AlertRequest):
    """Crée une alerte : une entreprise (client ou prospect) et un type de signal listé dans « Alertes de veille »."""
    ent = entreprise_or_404(req.entreprise_id)
    types = {knowledge.normalize(t): t for t in kn().types_alertes}
    type_alerte = types.get(knowledge.normalize(req.type))
    if type_alerte is None:
        raise HTTPException(422, f"Type d'alerte inconnu. Types possibles : {', '.join(kn().types_alertes)}.")
    alerte = {"id": uuid.uuid4().hex[:8], "entreprise_id": ent["id"], "entreprise": ent["nom"], "type": type_alerte,
              "cree_le": datetime.now().isoformat(timespec="seconds")}
    alertes = load_alerts() + [alerte]
    config.ALERTS_PATH.write_text(json.dumps(alertes, ensure_ascii=False, indent=2), encoding="utf-8")
    return alerte


@app.get("/alerts", tags=["alertes"])
def list_alerts():
    return {"alertes": load_alerts(), "types": kn().types_alertes, "recommandees": kn().alertes_recommandees}


# ----------------------------------------------------------------------------------------
# Menus : Mes clients, Insights, Solutions (sans IA)
# ----------------------------------------------------------------------------------------

def client_summary(ent: dict) -> dict:
    return {"id": ent["id"], "nom": ent["nom"], "statut": ent["statut"], "chiffre_affaires_2025": ent["chiffre_affaires_2025"],
            "remise_actuelle": ent["remise_actuelle"], "client_depuis": ent["client_depuis"],
            "contacts": [{"nom": t["nom"], "role": t["role"]} for t in kn().twins if t["entreprise_id"] == ent["id"]]}


@app.get("/clients", tags=["menus"])
def clients():
    return [client_summary(e) for e in kn().entreprises.values() if e["type"] == "client"]


@app.get("/clients/{client_id}", tags=["menus"])
def client_detail(client_id: str):
    ent = kn().entreprise(client_id)
    if ent is None or ent["type"] != "client":
        raise HTTPException(404, f"Client « {client_id} » introuvable. Voir GET /clients.")
    sections = kn().sections_of_entreprise(client_id)
    return {**client_summary(ent),
            "fiche": next(s["texte"] for s in sections if s["type"] == "client"),
            "comptes_rendus": [{"titre": s["chapter"], "texte": s["texte"]} for s in sections if s["type"] == "compte_rendu"]}


@app.get("/insights", tags=["menus"])
def insights():
    k = kn()

    def lignes(sujet: str) -> list[dict]:
        sortie = []
        for section in k.sections_of_sujet(sujet):
            for paragraphe in section["paragraphes"]:
                titre, _, texte = paragraphe.partition(" : ")
                sortie.append({"titre": titre, "texte": texte or paragraphe})
        return sortie

    return {"tendances": lignes("tendances"), "veille": lignes("veille"),
            "alertes_recommandees": k.alertes_recommandees, "alertes": load_alerts()}


@app.get("/solutions", tags=["menus"])
def solutions():
    return [{key: p[key] for key in ("code", "nom", "division", "dosage", "temperature_max", "allergenes", "prix_kg", "certifications", "bio")}
            for p in kn().produits]


# ----------------------------------------------------------------------------------------
# Simulation de rendez-vous avec un Digital Twin
# ----------------------------------------------------------------------------------------

SESSIONS: dict[str, dict] = {}  # en mémoire : perdues au redémarrage de l'API
MAX_SESSIONS = 200


def session_or_404(session_id: str) -> dict:
    session = SESSIONS.get(session_id)
    if session is None:
        raise HTTPException(404, "Session inconnue : démarre une simulation avec POST /twin/start.")
    return session


@app.post("/twin/start", tags=["twin"])
def twin_start(req: TwinStartRequest):
    """Le LLM joue le client. Le profil est chargé en entier par nom (jamais par recherche vectorielle)."""
    ent = entreprise_or_404(req.entreprise_id)
    twin = kn().twin_for(req.entreprise_id, req.contact)
    if twin is None:
        disponibles = [t["nom"] for t in kn().twins if t["entreprise_id"] == req.entreprise_id]
        detail = (f"Contacts disponibles : {', '.join(disponibles)}." if disponibles
                  else f"{ent['nom']} n'a pas de Digital Twin.")
        raise HTTPException(404, f"Digital Twin introuvable. {detail}")

    profil = kn().twin_text(twin)
    system = prompts.twin_prompt(twin["nom"], twin["role"], twin["entreprise"], profil, twin["langue"])
    ouverture = llm([{"role": "system", "content": system}, {"role": "user", "content": prompts.OUVERTURE_TWIN}], temperature=0.5, max_tokens=120)

    if len(SESSIONS) >= MAX_SESSIONS:
        SESSIONS.pop(next(iter(SESSIONS)))
    session_id = uuid.uuid4().hex[:12]
    SESSIONS[session_id] = {"twin": twin, "system": system, "profil": profil,
                            "messages": [{"role": "assistant", "content": ouverture}]}
    return {"session_id": session_id, "twin": {"nom": twin["nom"], "role": twin["role"], "entreprise": twin["entreprise"]},
            "reply": ouverture}


@app.post("/twin/message", tags=["twin"])
def twin_message(req: TwinMessageRequest):
    """Le commercial parle ; le Twin répond. L'historique est gardé en mémoire (8 derniers échanges envoyés au LLM)."""
    session = session_or_404(req.session_id)
    twin = session["twin"]
    historique = session["messages"][-2 * config.TWIN_EXCHANGES:]
    messages = [{"role": "system", "content": session["system"]}, *historique,
                {"role": "system", "content": prompts.twin_reminder(twin["nom"], session["profil"], twin["langue"])},
                {"role": "user", "content": req.message}]
    reply = llm(messages, temperature=0.4, max_tokens=150)
    session["messages"] += [{"role": "user", "content": req.message}, {"role": "assistant", "content": reply}]
    return {"session_id": req.session_id, "reply": reply, "tour": sum(m["role"] == "user" for m in session["messages"])}


# ----------------------------------------------------------------------------------------
# Évaluation (Coach)
# ----------------------------------------------------------------------------------------

def parse_json(brut: str) -> dict:
    try:
        return json.loads(brut)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", brut, re.DOTALL)
        if not m:
            raise
        return json.loads(m.group(0))


def clamp_note(valeur) -> int:
    try:
        return max(0, min(5, int(round(float(valeur)))))
    except (TypeError, ValueError):
        return 0


@app.post("/coach/evaluate", tags=["coach"])
def coach_evaluate(req: EvaluateRequest):
    """Note la simulation avec la grille. Les infractions sont détectées par `rules.detect_infractions`
    (code Python) et plafonnent la note « règles » : le LLM ne peut pas les contredire."""
    session = session_or_404(req.session_id)
    twin = session["twin"]
    infractions = []
    tour = 0
    for m in session["messages"]:
        if m["role"] != "user":
            continue
        tour += 1
        for inf in rules.detect_infractions(m["content"]):
            infractions.append({**inf, "tour": tour, "extrait": m["content"][:160]})
    if tour == 0:
        raise HTTPException(400, "Aucun message du commercial à évaluer : écris d'abord au Twin avec POST /twin/message.")

    transcription = "\n".join(
        f"{prompts.COMMERCIAL if m['role'] == 'user' else twin['nom']} : {m['content']}" for m in session["messages"])
    texte_infractions = "\n".join(f"- Tour {i['tour']} : {i['message']}" for i in infractions) or "- Aucune infraction détectée."
    techniques = "\n\n".join(s["texte"] for s in kn().sections_of_sujet("techniques"))
    messages = [
        {"role": "system", "content": prompts.COACH_PROMPT},
        {"role": "user", "content": (
            f"Profil du Digital Twin ({twin['nom']}), pour repérer les informations cachées :\n{session['profil']}\n\n"
            f"Techniques de vente de l'équipe :\n{techniques}\n\n"
            f"INFRACTIONS DÉTECTÉES (vérifiées par programme) :\n{texte_infractions}\n\n"
            f"CONVERSATION :\n{transcription}")},
    ]
    resultat = None
    for temperature in (0.2, 0.0):  # un second essai si le JSON est invalide
        try:
            resultat = parse_json(llm(messages, temperature=temperature, json_mode=True, max_tokens=600))
            break
        except json.JSONDecodeError:
            continue
    if resultat is None:
        raise HTTPException(502, "Le LLM n'a pas renvoyé un JSON valide : relance l'évaluation.")

    brutes = resultat.get("notes") if isinstance(resultat.get("notes"), dict) else {}
    notes = {critere: clamp_note(brutes.get(critere)) for critere in prompts.CRITERES_COACH}
    if infractions:  # décision de code, pas du LLM
        notes["regles"] = min(notes["regles"], 1)
    decouvertes = resultat.get("infos_cachees_decouvertes")
    return {
        "session_id": req.session_id, "twin": twin["nom"], "notes": notes,
        "total": sum(notes.values()), "total_maximum": 5 * len(notes),
        "point_fort": resultat.get("point_fort", ""), "a_ameliorer": resultat.get("a_ameliorer", ""),
        "phrase_plus_efficace": resultat.get("phrase_plus_efficace", ""),
        "infos_cachees_decouvertes": decouvertes if isinstance(decouvertes, list) else [],
        "infractions": infractions,
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("api:app", host="127.0.0.1", port=8000, reload=True)
