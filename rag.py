"""Briques communes : client Ollama, collection Chroma, embeddings, chat."""

from __future__ import annotations

import sys

import chromadb
import ollama

import config

_ollama = ollama.Client(host=config.OLLAMA_HOST)


def use_utf8_console() -> None:
    """Évite les caractères illisibles dans les consoles Windows (cp1252)."""
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")


def get_chroma_client() -> chromadb.ClientAPI:
    return chromadb.PersistentClient(path=str(config.CHROMA_DIR))


def get_collection(client: chromadb.ClientAPI | None = None):
    client = client or get_chroma_client()
    # Distance cosinus : adaptée aux embeddings de texte
    return client.get_or_create_collection(
        name=config.COLLECTION_NAME,
        metadata={"hnsw:space": "cosine", "embed_model": config.EMBED_MODEL},
    )


def embed(texts: list[str], prefix: str = "") -> list[list[float]]:
    """Calcule les embeddings via Ollama.

    `prefix` sert aux modèles qui distinguent requête et document
    (ex. nomic-embed-text : "search_query: " / "search_document: ").
    """
    inputs = [prefix + t for t in texts]
    return _ollama.embed(model=config.EMBED_MODEL, input=inputs)["embeddings"]


def _uses_nomic_prefixes() -> bool:
    return "nomic" in config.EMBED_MODEL


def embed_documents(texts: list[str]) -> list[list[float]]:
    return embed(texts, "search_document: " if _uses_nomic_prefixes() else "")


def embed_query(text: str) -> list[float]:
    return embed([text], "search_query: " if _uses_nomic_prefixes() else "")[0]


def search(question: str, top_k: int, entreprise: str | None = None) -> list[dict]:
    """Recherche par similarité. Avec `entreprise`, la recherche est limitée aux morceaux de cette entreprise.

    Retourne des dicts {id, text, meta, score} (score = similarité cosinus, 1 = identique).
    """
    collection = get_collection()
    total = collection.count()
    if total == 0:
        return []
    params = {"query_embeddings": [embed_query(question)], "n_results": min(top_k, total)}
    if entreprise:
        params["where"] = {"entreprise": entreprise}
    result = collection.query(**params)
    return [
        {"id": id_, "text": doc, "meta": meta, "score": round(1 - dist, 4)}
        for id_, doc, meta, dist in zip(result["ids"][0], result["documents"][0],
                                        result["metadatas"][0], result["distances"][0])
    ]


def chat(messages: list[dict], temperature: float = 0.2, json_mode: bool = False) -> str:
    """Appelle le LLM. Avec json_mode, Ollama force une réponse en JSON valide."""
    response = _ollama.chat(
        model=config.LLM_MODEL,
        messages=messages,
        options={"temperature": temperature, "num_ctx": config.NUM_CTX},
        format="json" if json_mode else None,
    )
    return response["message"]["content"]


def ollama_available() -> bool:
    try:
        _ollama.list()
        return True
    except Exception:
        return False
