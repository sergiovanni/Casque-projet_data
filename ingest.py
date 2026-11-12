"""Charge story.txt dans la base vectorielle (ChromaDB).

Usage :
    python ingest.py                  # charge story.txt
    python ingest.py autre_fichier.txt

Chaque section est indexée avec des métadonnées :
- type       : client | prospect | compte_rendu | twin | produits | reference
- entreprise : le client ou prospect concerné (permet la recherche filtrée)
- sujet, twin, chapter, ... (voir knowledge.py)
Les sections courtes restent d'un seul tenant, pour qu'une fiche client ou un
Digital Twin ne soit jamais coupé en deux.
"""

import sys
from pathlib import Path

import config
import knowledge
import rag


def chunk_paragraphs(paragraphs: list[str]) -> list[str]:
    """Découpe une section en morceaux.

    Une section de moins de WHOLE_SECTION_MAX caractères reste entière. Sinon, les
    paragraphes sont regroupés en morceaux de ~CHUNK_SIZE caractères, avec un
    chevauchement de CHUNK_OVERLAP_PARAGRAPHS paragraphe(s).
    """
    if sum(len(p) for p in paragraphs) <= config.WHOLE_SECTION_MAX:
        return ["\n\n".join(paragraphs)]

    chunks: list[str] = []
    current: list[str] = []
    size = 0
    for p in paragraphs:
        if current and size + len(p) > config.CHUNK_SIZE:
            chunks.append("\n\n".join(current))
            overlap = current[-config.CHUNK_OVERLAP_PARAGRAPHS:] if config.CHUNK_OVERLAP_PARAGRAPHS else []
            current = list(overlap)
            size = sum(len(x) for x in current)
        current.append(p)
        size += len(p)
    if current:
        chunks.append("\n\n".join(current))
    return chunks


def main() -> None:
    rag.use_utf8_console()
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else config.KNOWLEDGE_PATH
    base = knowledge.Knowledge(path.read_text(encoding="utf-8"))

    ids, documents, metadatas = [], [], []
    for section in base.sections:
        for chunk_idx, chunk in enumerate(chunk_paragraphs(section["paragraphes"])):
            ids.append(f"{path.stem}-{section['index']:02d}-{chunk_idx:03d}")
            documents.append(chunk)
            metadatas.append({
                "source": path.name,
                "doc_title": base.doc_title,
                "section_id": section["id"],
                "numero": section["numero"],
                "chapter": section["chapter"],
                "chapter_index": section["index"],
                "chunk_index": chunk_idx,
                "type": section["type"],
                "entreprise": section["entreprise"],
                "sujet": section["sujet"],
                "twin": section["twin"],
            })

    print(f"Document : {base.doc_title[:100]}")
    print(f"{len(base.sections)} sections, {len(documents)} morceaux à indexer")
    print(f"Calcul des embeddings avec '{config.EMBED_MODEL}'...")

    # Le titre de la section est ajouté au texte embarqué pour aider la recherche
    to_embed = [f"{m['chapter']}\n{d}" for d, m in zip(documents, metadatas)]
    embeddings = rag.embed_documents(to_embed)

    collection = rag.get_collection()
    # Retire d'abord l'ancienne version de ce fichier : sinon les morceaux qui n'existent
    # plus (section supprimée ou redécoupée) resteraient dans la base.
    collection.delete(where={"source": path.name})
    collection.upsert(ids=ids, documents=documents, metadatas=metadatas, embeddings=embeddings)

    print(f"OK : {collection.count()} morceaux dans la collection '{config.COLLECTION_NAME}' ({config.CHROMA_DIR})")
    print(f"{len(base.entreprises)} entreprises, {len(base.prospects)} prospects, "
          f"{len(base.produits)} produits, {len(base.twins)} Digital Twins")


if __name__ == "__main__":
    main()
