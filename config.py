"""Configuration partagée par les scripts (surcharge possible via variables d'environnement)."""

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

# Ollama
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")
LLM_MODEL = os.getenv("LLM_MODEL", "llama3.2:3b")
EMBED_MODEL = os.getenv("EMBED_MODEL", "nomic-embed-text")
NUM_CTX = int(os.getenv("NUM_CTX", "8192"))  # taille du contexte du LLM, en tokens

# Base vectorielle (ChromaDB persistante sur disque)
CHROMA_DIR = Path(os.getenv("CHROMA_DIR", BASE_DIR / "chroma_db"))
COLLECTION_NAME = os.getenv("COLLECTION_NAME", "knowledge")

# Source et découpage
KNOWLEDGE_PATH = Path(os.getenv("KNOWLEDGE_PATH", BASE_DIR / "story.txt"))
CHUNK_SIZE = int(os.getenv("CHUNK_SIZE", "1000"))  # en caractères, pour les sections longues
WHOLE_SECTION_MAX = int(os.getenv("WHOLE_SECTION_MAX", "3000"))  # une section plus courte reste d'un seul tenant
CHUNK_OVERLAP_PARAGRAPHS = int(os.getenv("CHUNK_OVERLAP_PARAGRAPHS", "1"))

# Recherche
TOP_K = int(os.getenv("TOP_K", "5"))
MAX_CONTEXT_CHARS = int(os.getenv("MAX_CONTEXT_CHARS", "16000"))  # budget de texte envoyé au LLM

# Modes Twin / Coach
TWIN_EXCHANGES = int(os.getenv("TWIN_EXCHANGES", "8"))  # derniers échanges envoyés au LLM pendant une simulation
ASK_HISTORY = int(os.getenv("ASK_HISTORY", "6"))  # derniers messages de l'historique du chat Assistant

# Alertes de veille (créées depuis l'application)
ALERTS_PATH = Path(os.getenv("ALERTS_PATH", BASE_DIR / "alerts.json"))
