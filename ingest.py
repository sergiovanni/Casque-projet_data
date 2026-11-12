"""Charge la base de connaissances (story.txt) dans la base vectorielle.

Usage :
    python ingest.py                  # charge story.txt
    python ingest.py autre_fichier.txt

Chaque section du document (« I. … », « VIII. Digital Twin — … ») est indexée avec
des métadonnées (type de section, client, Digital Twin) calculées par `kb.py`.
Les sections courtes restent d'un seul tenant, pour qu'une fiche client ou un
Digital Twin ne soit jamais coupé en deux.
"""

import re
import sys
from pathlib import Path

import config
import kb
import rag

# Un titre de section ressemble à "IV. Le Sanctuaire Brisé" ou "Chapitre 1 : Le Grand Départ"
CHAPTER_RE = re.compile(r"^(?:[IVXLC]+\.\s+\S|chapitre\s+\w+\b)", re.IGNORECASE)


def split_into_sections(text: str) -> tuple[str, list[tuple[str, list[str]]]]:
    """Retourne (titre du document, [(titre de la section, [paragraphes])])."""
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    title = ""
    if paragraphs and not CHAPTER_RE.match(paragraphs[0]):
        title = paragraphs.pop(0)

    sections: list[tuple[str, list[str]]] = []
    current_title, current = "Introduction", []
    for p in paragraphs:
        if CHAPTER_RE.match(p) and len(p) < 120:
            if current:
                sections.append((current_title, current))
            current_title, current = p, []
        else:
            current.append(p)
    if current:
        sections.append((current_title, current))
    return title, sections


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
    text = path.read_text(encoding="utf-8")
    doc_title, sections = split_into_sections(text)

    ids, documents, metadatas = [], [], []
    for chap_idx, (chapter, paragraphs) in enumerate(sections):
        section_meta = kb.section_metadata(chapter)
        for chunk_idx, chunk in enumerate(chunk_paragraphs(paragraphs)):
            ids.append(f"{path.stem}-{chap_idx:02d}-{chunk_idx:03d}")
            documents.append(chunk)
            metadatas.append({
                "source": path.name,
                "doc_title": doc_title,
                "chapter": chapter,
                "chapter_index": chap_idx,
                "chunk_index": chunk_idx,
                **section_meta,
            })

    print(f"Document : {doc_title or path.name}")
    print(f"{len(sections)} sections, {len(documents)} morceaux à indexer")
    print(f"Calcul des embeddings avec '{config.EMBED_MODEL}'...")

    # Le titre de la section est ajouté au texte embarqué pour aider la recherche
    to_embed = [f"{m['chapter']}\n{d}" for d, m in zip(documents, metadatas)]
    embeddings = rag.embed_documents(to_embed)

    collection = rag.get_collection()
    # Retire d'abord l'ancienne version de ce fichier : sinon les morceaux qui n'existent
    # plus (section supprimée ou redécoupée) resteraient dans la base.
    collection.delete(where={"source": path.name})
    collection.upsert(ids=ids, documents=documents, metadatas=metadatas, embeddings=embeddings)

    twins = sorted({m["twin"] for m in metadatas if m["twin"]})
    print(f"OK : {collection.count()} morceaux dans la collection '{config.COLLECTION_NAME}' "
          f"({config.CHROMA_DIR})")
    print(f"{len(twins)} Digital Twins : {', '.join(twins)}")


if __name__ == "__main__":
    main()
