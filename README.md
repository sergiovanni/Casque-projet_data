# Givaudan AI Sales Copilot API (équipe Casque)

Assistant commercial pour le secteur France Sud-Ouest, via une API FastAPI. Les passages
pertinents de la base de connaissances (`story.txt`) sont retrouvés dans une base
vectorielle (ChromaDB), puis un LLM Ollama rédige la réponse.

Trois modes :

| Mode          | Route         | Rôle |
|---------------|---------------|------|
| **Assistant** | `POST /ask`   | Le commercial pose une question (préparer un RDV, répondre à une objection en direct, piloter le secteur, prospecter). L'IA répond à partir des documents. |
| **Twin**      | `POST /twin`  | L'IA joue un client (Digital Twin). Le commercial lui parle comme en rendez-vous. |
| **Coach**     | `POST /coach` | Après une simulation, l'IA note le commercial avec la grille d'évaluation (6 critères : DISC, SPIN, CAP, objections, règles, engagement). |

## Fichiers du projet

| Fichier            | Rôle |
|--------------------|------|
| `story.txt`        | La base de connaissances : règles commerciales, catalogue, méthodes de vente, fiches clients et prospects, comptes rendus, Digital Twins, veille, scénarios d'entraînement. Chaque section commence par un titre « I. … », « VIII. Digital Twin — … ». Données fictives. |
| `ingest.py`        | Charge `story.txt` dans la base : une entrée par section (les sections de moins de 3000 caractères restent entières, les autres sont découpées), avec ses métadonnées (type, client, Digital Twin) et ses embeddings Ollama. Peut être relancé : l'ancienne version du fichier est remplacée. Accepte un autre fichier : `python ingest.py autre.txt`. |
| `kb.py`            | Lecture structurée de la base : type de chaque section, liste des Digital Twins, repérage des clients et personnes cités dans une question. |
| `clean.py`         | Vide la base (supprime la collection ChromaDB). |
| `api.py`           | L'API FastAPI et son Swagger, avec les trois modes et leurs prompts. |
| `list.py`          | Affiche le contenu de la base : modèle d'embeddings, nombre de morceaux, puis chaque morceau groupé par section. Avec `--full`, affiche le texte complet. |
| `rag.py`           | Code partagé : connexion à ChromaDB, embeddings et appel du LLM via Ollama. |
| `config.py`        | Tous les réglages, modifiables par variables d'environnement. |
| `smoke_test.py`    | Pose à l'API une vingtaine de questions de `questions_test.md` et affiche réponse et réponse attendue. |
| `requirements.txt` | Dépendances Python : FastAPI, Uvicorn, Ollama, ChromaDB. |
| `chroma_db/`       | Base vectorielle créée par `ingest.py` (non versionnée). |

## Installation

Prérequis : Python 3.10+ et [Ollama](https://ollama.com) lancé en local.

### 1. Créer et activer un environnement virtuel

```bash
python -m venv .venv
```

| Terminal                  | Commande                          |
|---------------------------|-----------------------------------|
| Windows PowerShell        | `.venv\Scripts\Activate.ps1`      |
| Windows cmd               | `.venv\Scripts\activate.bat`      |
| Git Bash (Windows)        | `source .venv/Scripts/activate`   |
| macOS / Linux             | `source .venv/bin/activate`       |

Si PowerShell refuse d'exécuter le script d'activation : `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`.

### 2. Installer les dépendances

```bash
pip install -r requirements.txt
```

### 3. Télécharger les modèles Ollama

```bash
ollama pull nomic-embed-text   # modèle d'embeddings
ollama pull llama3.2:3b        # LLM (ou un autre, voir Configuration)
```

Un modèle plus gros (par exemple `llama3.1:8b`) suit mieux les consignes de jeu de rôle et d'évaluation.

## Utilisation

```bash
python ingest.py        # charge story.txt dans la base (chroma_db/)
python list.py          # affiche le contenu de la base (--full pour le texte complet)
python clean.py         # vide la base
python api.py           # lance l'API sur http://127.0.0.1:8000
python smoke_test.py    # pose des questions de test à l'API lancée
```

Swagger : http://127.0.0.1:8000/docs

Pour recharger après modification de `story.txt` : relancer `python ingest.py` (ou `python clean.py` puis `python ingest.py`).

## Endpoints

| Méthode | Route     | Rôle                                                                 |
|---------|-----------|----------------------------------------------------------------------|
| GET     | `/health` | Nombre de morceaux en base et modèles utilisés                       |
| GET     | `/twins`  | Liste des Digital Twins disponibles (nom, fonction, entreprise)      |
| POST    | `/search` | Passages retrouvés seulement (sans LLM), pour déboguer               |
| POST    | `/ask`    | **Assistant** : réponse du LLM + passages sources                    |
| POST    | `/twin`   | **Twin** : réplique du client simulé                                 |
| POST    | `/coach`  | **Coach** : évaluation d'une conversation                            |

### Assistant

```json
{"question": "Prépare mon rendez-vous du 3 décembre avec la Laiterie des Pyrénées.", "top_k": 6}
```

Quand la question nomme un client (« Laiterie des Pyrénées ») ou une personne (« Marc Etchegaray »),
leurs fiches sont ajoutées aux passages trouvés par la recherche sémantique.

### Twin

L'API ne garde pas la conversation en mémoire : le client renvoie les échanges précédents à chaque appel.
`user` est le commercial, `assistant` est le Digital Twin.

```json
{
  "twin": "Marc Etchegaray",
  "message": "Je vous propose un stock de sécurité réservé.",
  "history": [
    {"role": "user", "content": "Bonjour Monsieur Etchegaray."},
    {"role": "assistant", "content": "Avant toute chose, parlons du retard de mars."}
  ]
}
```

### Coach

```json
{
  "twin": "Marc Etchegaray",
  "history": [
    {"role": "user", "content": "Bonjour Monsieur Etchegaray."},
    {"role": "assistant", "content": "Avant toute chose, parlons du retard de mars."}
  ],
  "question": "Quelles erreurs ai-je faites ?"
}
```

`question` est facultatif : sans lui, le coach fait l'évaluation complète selon la grille.

## Configuration (variables d'environnement)

| Variable                   | Défaut                   |
|----------------------------|--------------------------|
| `LLM_MODEL`                | `llama3.2:3b`            |
| `EMBED_MODEL`              | `nomic-embed-text`       |
| `OLLAMA_HOST`              | `http://localhost:11434` |
| `NUM_CTX`                  | `8192` (contexte du LLM, en tokens) |
| `CHROMA_DIR`               | `./chroma_db`            |
| `COLLECTION_NAME`          | `knowledge`              |
| `KNOWLEDGE_PATH`           | `./story.txt`            |
| `CHUNK_SIZE`               | `1200` (caractères, sections longues) |
| `WHOLE_SECTION_MAX`        | `3000` (une section plus courte n'est pas découpée) |
| `CHUNK_OVERLAP_PARAGRAPHS` | `1`                      |
| `TOP_K`                    | `6`                      |
| `MAX_CONTEXT_CHARS`        | `16000` (texte envoyé au LLM) |
| `MAX_HISTORY`              | `30` (messages d'historique conservés) |

Si vous changez `EMBED_MODEL`, relancez `clean.py` puis `ingest.py` : les vecteurs
de deux modèles différents ne sont pas compatibles.
