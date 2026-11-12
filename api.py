"""API de l'assistant commercial Givaudan (RAG + Ollama), avec trois modes :

- Assistant : le commercial pose une question, l'IA répond à partir des documents (POST /ask).
- Twin      : l'IA joue le client, le commercial s'entraîne comme en rendez-vous (POST /twin).
- Coach     : l'IA note le commercial à partir d'une simulation, avec la grille de la section XXX (POST /coach).

Lancement :
    python api.py                 # ou : uvicorn api:app --reload
Swagger : http://localhost:8000/docs
"""

from typing import Literal

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

import config
import kb
import rag

# --------------------------------------------------------------------------------------
# Prompts
# --------------------------------------------------------------------------------------

ASSISTANT_PROMPT = """Tu es l'assistant commercial de Givaudan pour le secteur France Sud-Ouest (équipe Casque).
Tu aides le commercial à préparer ses rendez-vous, à répondre à un client en direct et à piloter son secteur.

Règles :
- Réponds UNIQUEMENT à la question posée, de façon courte et directe (quelques phrases ou une courte liste). N'ajoute rien qui n'a pas été demandé.
- Appuie-toi UNIQUEMENT sur les extraits fournis (règles commerciales, catalogue, fiches clients, Digital Twins, comptes rendus, veille).
- Si la question sort du cadre (culture générale, autre sujet) ou si l'information n'est pas dans les extraits, réponds simplement que tu n'as pas cette information dans la base commerciale, sans rien inventer. Si les extraits donnent une information partielle (ex. « 12 % moins cher »), donne-la et précise ce qui manque.
- N'invente jamais un chiffre, un nom, un produit, une date, une adresse e-mail ou un numéro de téléphone.
- Cite les noms de contacts, les références produits (ex. TW-502, TW-221E) et les chiffres seulement quand ils servent la réponse.
- Ne confonds pas ce que propose Givaudan avec ce que propose un concurrent (Symrise, IFF, dsm-firmenich).
- Pour conseiller sur la façon de parler à quelqu'un, appuie-toi sur son profil DISC, ses mobiles SONCAS, ce qui le convainc et ce qui l'agace.
- Utilise la structure « interlocuteurs (DISC, SONCAS), points sensibles, opportunités, produits à présenter (2 maximum), remise autorisée » SEULEMENT si on te demande explicitement de préparer un rendez-vous.
- Pendant un rendez-vous (question courte), réponds en 2 ou 3 phrases maximum.
- Réponds dans la langue de la question (français ou anglais)."""


def twin_prompt(twin: dict, sheet: str, background: str) -> str:
    return f"""Tu joues le rôle de {twin['name']}, {twin['role']} chez {twin['company']}. Un commercial de Givaudan s'entraîne à te rencontrer : parle comme dans un vrai rendez-vous.

Règles du jeu :
- Reste TOUJOURS dans ton personnage. Parle à la première personne. Ne dis jamais que tu es une IA, un jeu de rôle, une simulation ou que tu lis une fiche.
- Respecte ta fiche : personnalité (profil DISC, mobiles SONCAS), façon de parler (vouvoiement ou tutoiement, phrases courtes ou longues, phrases typiques), ce qui te convainc, ce qui t'agace.
- Réponds par des répliques courtes (1 à 4 phrases), uniquement les paroles prononcées : pas de didascalies, pas de listes, pas de titres, pas de guillemets autour de ta réplique.
- Si le commercial fait quelque chose qui figure dans « Ce qui t'agace » (minimiser un problème, discours trop général ou commercial, parler de ton prédécesseur comme d'un ami, mot qui te déplaît...), réagis comme ton personnage le ferait : coupe court, te ferme ou recadre.
- Tu as tes propres préoccupations (points sensibles, objections typiques) : si le commercial ne les aborde pas, ramène-les toi-même dans la conversation.
- Les informations de la rubrique « Ce qu'il/elle ne dit que si on lui pose les bonnes questions » (« What he only reveals if asked the right questions » en anglais) ne sont à révéler QUE si le commercial pose une question précise qui s'y rapporte. Sinon, esquive ou reste vague. Ne les donne jamais toutes d'un coup.
- Tu ne sais que ce qui figure dans ta fiche et dans le contexte ci-dessous. Si on te demande une information absente (ex. ton chiffre d'affaires total, ta vie privée), n'invente RIEN : dis que tu ne la communiques pas ou que tu ne sais pas, dans le style de ton personnage.
- Si ta fiche impose une langue (ex. « English only »), réponds uniquement dans cette langue. Sinon, réponds dans la langue du commercial.
- Si le commercial te fait une proposition (remise, délai...), réagis en client réaliste. Tu n'évalues pas sa performance : tu n'es pas un coach.

=== TA FICHE ===
{sheet}

=== CONTEXTE : TA RELATION AVEC GIVAUDAN ===
{background}"""


def coach_prompt(twin: dict, context: str) -> str:
    return f"""Tu es le coach commercial de Givaudan (équipe Casque). Tu évalues un commercial après une simulation de rendez-vous avec le Digital Twin de {twin['name']} ({twin['role']}, {twin['company']}).

La conversation fournie est la seule chose que tu évalues. Cite des répliques exactes du commercial. N'invente aucun échange.

Évalue avec cette grille de 6 critères, chacun noté sur 5 :
1. Profil DISC : le commercial a-t-il adapté son discours au profil DISC et aux mobiles SONCAS de {twin['name']} ?
2. Questions SPIN : a-t-il posé des questions d'implication pour découvrir les vrais enjeux ?
3. Méthode CAP : a-t-il présenté ses produits en Caractéristique, Avantage, Preuve ?
4. Objections : les a-t-il traitées sans contredire ni dénigrer (écouter, questionner, reformuler, prouver) ?
5. Règles commerciales : remise maximale (au-delà de 8 %, validation de Thomas Lefèvre ; 6 à 8 % seulement avec un contrat de deux ans), délais, aucune promesse non vérifiée.
6. Engagement : a-t-il obtenu un engagement concret pour la suite (échantillon, test, rendez-vous daté) ?

Format de la réponse :
- Commence par signaler toute faute grave (ex. remise supérieure à 8 % sans validation, promesse non vérifiée, erreur sur un produit).
- Puis, pour chaque critère : la note /5, un point fort, un point à améliorer, et une phrase plus efficace que le commercial aurait pu dire.
- Si un critère ne peut pas être observé dans la conversation, note-le 0/5 et dis pourquoi.
- Termine par la note globale /30 et les 2 priorités de progression.
Réponds en français (les phrases suggérées sont dans la langue du client).

=== INFORMATIONS DE RÉFÉRENCE ===
{context}"""


# --------------------------------------------------------------------------------------
# Modèles de requête / réponse
# --------------------------------------------------------------------------------------

app = FastAPI(
    title="Givaudan AI Sales Copilot API",
    description="Assistant commercial Givaudan (équipe Casque), en trois modes : "
                "**Assistant** (/ask) pour préparer et piloter, **Twin** (/twin) pour s'entraîner face à un client simulé, "
                "**Coach** (/coach) pour évaluer l'entraînement. Les passages utiles sont retrouvés dans la base vectorielle, "
                "puis un LLM Ollama rédige la réponse.",
    version="2.0.0",
)


class AskRequest(BaseModel):
    question: str = Field(..., min_length=1, examples=["Prépare mon rendez-vous du 3 décembre avec la Laiterie des Pyrénées."])
    top_k: int = Field(config.TOP_K, ge=1, le=20, description="Nombre de passages à retrouver")


class Source(BaseModel):
    chapter: str
    text: str
    score: float | None = Field(None, description="Similarité cosinus (1 = identique). Vide si le passage a été ajouté "
                                                   "parce que la question nomme ce client ou cette personne.")


class AskResponse(BaseModel):
    question: str
    answer: str
    model: str
    sources: list[Source]


class Message(BaseModel):
    role: Literal["user", "assistant"] = Field(description="user = le commercial, assistant = le Digital Twin")
    content: str = Field(..., min_length=1)


class TwinRequest(BaseModel):
    twin: str = Field(..., examples=["Marc Etchegaray"], description="Nom (ou prénom) du Digital Twin, voir GET /twins")
    message: str = Field(..., min_length=1, examples=["Bonjour Monsieur Etchegaray, je viens vous présenter nos nouveautés."],
                         description="Ce que dit le commercial")
    history: list[Message] = Field(default_factory=list, description="Échanges précédents (sans le message courant)")


class TwinResponse(BaseModel):
    twin: str
    reply: str
    model: str


class CoachRequest(BaseModel):
    twin: str = Field(..., examples=["Marc Etchegaray"])
    history: list[Message] = Field(..., min_length=1, description="La conversation complète à évaluer")
    question: str | None = Field(None, examples=["Quelles erreurs ai-je faites avec ce client ?"],
                                 description="Question précise du commercial (sinon : évaluation complète selon la grille)")


class CoachResponse(BaseModel):
    twin: str
    evaluation: str
    model: str


class TwinInfo(BaseModel):
    name: str
    role: str
    company: str


# --------------------------------------------------------------------------------------
# Outils communs
# --------------------------------------------------------------------------------------

def load_rows() -> list[dict]:
    collection = rag.get_collection()
    if collection.count() == 0:
        raise HTTPException(503, "La base est vide : lance d'abord `python ingest.py`.")
    return kb.all_rows(collection)


def to_source(row: dict, score: float | None = None) -> Source:
    return Source(chapter=row["meta"]["chapter"], text=row["text"], score=score)


def retrieve(question: str, top_k: int, rows: list[dict]) -> list[Source]:
    """Passages consacrés aux clients / personnes cités, puis passages les plus proches de la question."""
    sources = [to_source(r) for r in kb.entity_rows(question, rows)]
    seen = {s.text for s in sources}

    collection = rag.get_collection()
    result = collection.query(
        query_embeddings=[rag.embed_query(question)],
        n_results=min(top_k, collection.count()),
    )
    for doc, meta, dist in zip(result["documents"][0], result["metadatas"][0], result["distances"][0]):
        if doc not in seen:
            seen.add(doc)
            sources.append(Source(chapter=meta["chapter"], text=doc, score=round(1 - dist, 4)))
    return sources


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


def blocks(rows: list[dict]) -> str:
    return "\n\n---\n\n".join(f"[{r['meta']['chapter']}]\n{r['text']}" for r in rows)


def resolve_twin(name: str, rows: list[dict]) -> dict:
    found = kb.find_twins(name, rows)
    if not found:
        available = ", ".join(t["name"] for t in kb.list_twins(rows))
        raise HTTPException(404, f"Digital Twin « {name} » introuvable. Disponibles : {available}.")
    if len(found) > 1:
        names = ", ".join(t["name"] for t in found)
        raise HTTPException(409, f"« {name} » correspond à plusieurs Digital Twins ({names}) : précise le nom complet.")
    return found[0]


def history_messages(history: list[Message]) -> list[dict]:
    return [{"role": m.role, "content": m.content} for m in history[-config.MAX_HISTORY:]]


def llm(messages: list[dict], temperature: float) -> str:
    try:
        return rag.chat(messages, temperature).strip()
    except Exception as exc:  # Ollama injoignable, modèle absent...
        raise HTTPException(502, f"Erreur Ollama : {exc}") from exc


# --------------------------------------------------------------------------------------
# Routes
# --------------------------------------------------------------------------------------

@app.get("/health", tags=["système"])
def health():
    """Vérifie que la base est chargée et indique les modèles utilisés."""
    return {
        "chunks": rag.get_collection().count(),
        "llm_model": config.LLM_MODEL,
        "embed_model": config.EMBED_MODEL,
    }


@app.get("/twins", response_model=list[TwinInfo], tags=["twin"])
def twins():
    """Liste les Digital Twins disponibles pour les modes Twin et Coach."""
    return [TwinInfo(**{k: t[k] for k in ("name", "role", "company")}) for t in kb.list_twins(load_rows())]


@app.post("/search", response_model=list[Source], tags=["assistant"])
def search(req: AskRequest):
    """Retourne seulement les passages pertinents des documents commerciaux (sans appeler le LLM)."""
    return retrieve(req.question, req.top_k, load_rows())


@app.post("/ask", response_model=AskResponse, tags=["assistant"])
def ask(req: AskRequest):
    """**Mode Assistant** : répond à une question commerciale (préparation de RDV, objection en direct, pilotage, prospection)."""
    sources = retrieve(req.question, req.top_k, load_rows())
    context, used = build_context(sources)
    messages = [
        {"role": "system", "content": ASSISTANT_PROMPT},
        {"role": "user", "content": f"Extraits des documents commerciaux :\n\n{context}\n\nQuestion : {req.question}"},
    ]
    answer = llm(messages, temperature=0.2)
    return AskResponse(question=req.question, answer=answer, model=config.LLM_MODEL, sources=used)


@app.post("/twin", response_model=TwinResponse, tags=["twin"])
def twin(req: TwinRequest):
    """**Mode Twin** : le Digital Twin répond au commercial, en restant dans son personnage.

    L'API ne garde pas la conversation : renvoie à chaque appel les échanges précédents dans `history`.
    """
    rows = load_rows()
    t = resolve_twin(req.twin, rows)
    sheet, background = kb.twin_rows(t, rows)
    messages = [
        {"role": "system", "content": twin_prompt(t, blocks(sheet), blocks(background))},
        *history_messages(req.history),
        {"role": "user", "content": req.message},
    ]
    return TwinResponse(twin=t["name"], reply=llm(messages, temperature=0.6), model=config.LLM_MODEL)


@app.post("/coach", response_model=CoachResponse, tags=["coach"])
def coach(req: CoachRequest):
    """**Mode Coach** : note la conversation du commercial avec le Digital Twin selon la grille d'évaluation."""
    rows = load_rows()
    t = resolve_twin(req.twin, rows)
    sheet, background = kb.twin_rows(t, rows)
    reference = kb.rows_of_kind(rows, "methodes", "regles", "objections", "grille")
    context = blocks(sheet + background + reference)

    twin_name = t["name"]
    transcript = "\n".join(
        f"{'Commercial' if m.role == 'user' else twin_name} : {m.content}" for m in req.history[-config.MAX_HISTORY:]
    )
    task = req.question or "Évalue ma conversation selon la grille."
    messages = [
        {"role": "system", "content": coach_prompt(t, context)},
        {"role": "user", "content": f"Conversation à évaluer :\n\n{transcript}\n\nDemande du commercial : {task}"},
    ]
    return CoachResponse(twin=twin_name, evaluation=llm(messages, temperature=0.3), model=config.LLM_MODEL)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("api:app", host="127.0.0.1", port=8000, reload=True)
