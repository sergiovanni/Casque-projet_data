"""API de l'assistant commercial Givaudan (RAG + Ollama).
 
Lancement :
    uvicorn api:app --reload
Swagger : http://localhost:8000/docs
"""
 
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
 
import config
import rag
 
SYSTEM_PROMPT = """Tu es l'assistant commercial de Givaudan pour le secteur France Sud-Ouest.
Règles :
- Réponds UNIQUEMENT à la question posée, de façon courte et directe (quelques phrases ou une courte liste). N'ajoute rien qui n'a pas été demandé.
- Appuie-toi UNIQUEMENT sur les extraits fournis (fiches clients, catalogue, comptes rendus).
- Si la question sort du cadre (culture générale, autre sujet) ou si l'information n'est pas dans les extraits, réponds simplement que tu n'as pas cette information dans la base commerciale, sans rien inventer.
- N'invente jamais un chiffre, un nom, un produit, une adresse e-mail ou un numéro de téléphone.
- Cite les noms de contacts, les références produits (ex. TW-502) et les chiffres seulement quand ils servent la réponse.
- Utilise la structure « contacts, points sensibles, opportunités, produits à présenter » SEULEMENT si on te demande explicitement de préparer un rendez-vous.
- Réponds dans la langue de la question (français ou anglais)."""
 
app = FastAPI(
    title="Givaudan AI Sales Copilot API",
    description="Assistant commercial Givaudan (équipe Casque) : pose une question sur les clients, "
                "les prospects ou le catalogue. Les passages utiles sont retrouvés dans la base vectorielle, "
                "puis un LLM Ollama rédige la réponse.",
    version="1.0.0",
)
 from fastapi.responses import FileResponse

@app.get("/")
def page():
    return FileResponse("index.html")
 
class AskRequest(BaseModel):
    question: str = Field(..., min_length=1, examples=["Prépare mon rendez-vous avec la Laiterie des Pyrénées."])
    top_k: int = Field(config.TOP_K, ge=1, le=20, description="Nombre de passages à retrouver")
 
 
class Source(BaseModel):
    chapter: str
    text: str
    score: float = Field(description="Similarité cosinus (1 = identique)")
 
 
class AskResponse(BaseModel):
    question: str
    answer: str
    model: str
    sources: list[Source]
 
 
def retrieve(question: str, top_k: int) -> list[Source]:
    collection = rag.get_collection()
    if collection.count() == 0:
        raise HTTPException(503, "La base est vide : lance d'abord `python ingest.py`.")
    result = collection.query(
        query_embeddings=[rag.embed_query(question)],
        n_results=min(top_k, collection.count()),
    )
    return [
        Source(chapter=meta["chapter"], text=doc, score=round(1 - dist, 4))
        for doc, meta, dist in zip(result["documents"][0], result["metadatas"][0], result["distances"][0])
    ]
 
 
@app.get("/health", tags=["système"])
def health():
    """Vérifie que la base est chargée et indique les modèles utilisés."""
    return {
        "chunks": rag.get_collection().count(),
        "llm_model": config.LLM_MODEL,
        "embed_model": config.EMBED_MODEL,
    }
 
 
@app.post("/search", response_model=list[Source], tags=["assistant commercial"])
def search(req: AskRequest):
    """Retourne seulement les passages pertinents des documents commerciaux (sans appeler le LLM)."""
    return retrieve(req.question, req.top_k)

from fastapi.responses import FileResponse


@app.get("/", include_in_schema=False)
def page():
    """Affiche la page de l'assistant commercial."""
    return FileResponse("index.html")
 
 
@app.post("/ask", response_model=AskResponse, tags=["assistant commercial"])
def ask(req: AskRequest):
    """Répond à une question commerciale à partir des passages retrouvés (fiches clients, catalogue, comptes rendus)."""
    sources = retrieve(req.question, req.top_k)
    context = "\n\n---\n\n".join(f"[{s.chapter}]\n{s.text}" for s in sources)
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"Extraits des documents commerciaux :\n\n{context}\n\nQuestion : {req.question}"},
    ]
    try:
        answer = rag.chat(messages)
    except Exception as exc:  # Ollama injoignable, modèle absent...
        raise HTTPException(502, f"Erreur Ollama : {exc}") from exc
    return AskResponse(question=req.question, answer=answer.strip(), model=config.LLM_MODEL, sources=sources)
 
 
if __name__ == "__main__":
    import uvicorn
 
    uvicorn.run("api:app", host="127.0.0.1", port=8000, reload=True)
