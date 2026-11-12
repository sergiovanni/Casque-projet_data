# Givaudan AI Sales Copilot API (équipe Casque)

Copilote du commercial **Jordan Dubois** (secteur France Sud-Ouest et prospection cosmétique). L'API
FastAPI s'appuie sur une base de connaissances (`story.txt`), une base vectorielle (ChromaDB) et un LLM
local (Ollama). Tout tourne en local : pas de service payant, pas de clé API.

> **Règle d'or : l'IA ne calcule rien et ne décide rien de chiffré.** Les scores, remises, températures,
> minimums de commande et délais sont vérifiés en Python (`scoring.py`, `rules.py`). Le LLM rédige
> seulement du texte à partir de données déjà vérifiées.

## Ce que fait l'API

| Mode | Endpoints | Rôle |
|------|-----------|------|
| **Assistant** | `POST /ask`, `POST /pitch` | Préparer un RDV, répondre en direct, prospecter. Réponses à partir des documents, avec les « faits vérifiés » calculés par code. |
| **Prospection** | `POST /prospects/search`, `GET /prospects/{id}` | Prospects cosmétiques triés par score. **Sans IA.** |
| **Twin** | `POST /twin/start`, `POST /twin/message` | Le LLM joue le client (Digital Twin), le commercial s'entraîne. |
| **Coach** | `POST /coach/evaluate` | Note la simulation avec la grille (DISC, SPIN, CAP, objections, règles, engagement). |

## Fichiers du projet

| Fichier | Rôle |
|---------|------|
| `story.txt` | **La seule source de données** : règles commerciales, catalogue, méthodes de vente, clients, prospects, comptes rendus, Digital Twins, veille. Données fictives. À ne pas modifier par code. |
| `knowledge.py` | Lit `story.txt` au démarrage : sections, entreprises, prospects, produits, Digital Twins, règles chiffrées. Repère aussi les clients, personnes et produits cités dans une question. |
| `scoring.py` | Score des prospects : additionne les points de « Données de score », vérifie la grille et le total écrit (alerte dans les logs si écart), trie par score. |
| `rules.py` | Règles métier (fonctions pures, testées) : `check_remise`, `check_temperature`, `check_bio`, `check_quantite`, `detect_infractions`. |
| `prompts.py` | Les prompts des modes Assistant, Twin, Coach et Pitch, et la bibliothèque de questions. |
| `ingest.py` | Charge `story.txt` dans ChromaDB, avec les métadonnées `type` et `entreprise`. Peut être relancé : l'ancienne version est remplacée. |
| `rag.py` | Embeddings, recherche (filtrée par entreprise si besoin) et appel du LLM via Ollama. |
| `api.py` | L'API FastAPI et son Swagger (tous les endpoints). |
| `config.py` | Les réglages, modifiables par variables d'environnement. |
| `clean.py` / `list.py` | Vider la base / afficher son contenu (`--full` pour le texte complet). |
| `eval_questions.py` | Lance toutes les questions de `questions_test.md` sur l'API et écrit `rapport_eval.md`. |
| `tests/` | `test_rules.py`, `test_scoring.py`, `test_api.py` (l'API est testée avec un faux LLM : rapide, sans Ollama). |
| `questions_test.md` | Questions de test et réponses attendues, par mode. |
| `BRIEF_CLAUDE_CODE.md` | Le cahier des charges de la refonte. |

## Installation

Prérequis : Python 3.11+ et [Ollama](https://ollama.com) lancé en local.

```bash
python -m venv .venv
```

| Terminal | Activation |
|----------|------------|
| Windows PowerShell | `.venv\Scripts\Activate.ps1` (si refusé : `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`) |
| Windows cmd | `.venv\Scripts\activate.bat` |
| Git Bash | `source .venv/Scripts/activate` |
| macOS / Linux | `source .venv/bin/activate` |

```bash
pip install -r requirements.txt
ollama pull nomic-embed-text    # embeddings
ollama pull llama3.2:3b         # LLM (choisi dans notre benchmark)
```

Sous Git Bash, si `ollama` n'est pas reconnu : `export PATH="$LOCALAPPDATA/Programs/Ollama:$PATH"`.

## Lancement

```bash
python ingest.py        # charge story.txt dans la base (à refaire quand story.txt change)
python api.py           # lance l'API sur http://127.0.0.1:8000
pytest                  # lance les tests (sans Ollama)
python eval_questions.py   # évalue l'API sur questions_test.md (Ollama et l'API lancés)
```

Swagger : http://127.0.0.1:8000/docs

Pour changer de modèle : variable d'environnement `LLM_MODEL` (ou valeur par défaut dans `config.py`).
Si vous changez `EMBED_MODEL`, relancez `python clean.py` puis `python ingest.py`.

## Endpoints, écran par écran (exemples curl)

Sous Windows, mettre le JSON dans un fichier (`-d @requete.json`) évite les soucis d'accents de Git Bash.

| Écran / bouton | Endpoint | IA ? |
|---|---|---|
| Écran 0 — chat « Go » | `POST /ask` | Oui |
| Bibliothèque de prompts | `GET /prompts` | Non |
| Message vocal (bonus) | `POST /voice/transcribe` | Non |
| Attach | `POST /attach` → 501 | — |
| Écran 1 — « Lancer la recherche » | `POST /prospects/search` | Non |
| Écran 2 et 3 — « Consulter », « Pourquoi cette entreprise ? » | `GET /prospects/{id}` | Non |
| Préparer un pitch | `POST /pitch` | Oui |
| Créer une alerte | `POST /alerts`, `GET /alerts` | Non |
| Simuler le RDV | `POST /twin/start`, `POST /twin/message` | Oui |
| Évaluation | `POST /coach/evaluate` | Oui + règles |
| Menu Mes clients | `GET /clients`, `GET /clients/{id}` | Non |
| Menu Insights | `GET /insights` | Non |
| Menu Solutions | `GET /solutions` | Non |
| Santé | `GET /health` | Non |

```bash
# Écran 0 : poser une question (entreprise_id est facultatif : il limite la recherche à cette entreprise)
curl -X POST localhost:8000/ask -H "Content-Type: application/json" \
  -d '{"question": "Puis-je accorder 7 % à Mathilde Garat ?"}'
# -> {"answer": "...", "faits_verifies": ["Remise de 7 % : possible seulement avec un contrat de deux ans..."], "sources": [...]}

# Écran 1 : les prospects cosmétiques, du meilleur au moins bon (toujours le même résultat)
curl -X POST localhost:8000/prospects/search -H "Content-Type: application/json" \
  -d '{"query": "marques de cosmétiques françaises", "limit": 10}'

# Écrans 2 et 3 : la fiche, le « pourquoi » et le détail du score (95 et pas 100 : porte d'entrée à 5/10)
curl localhost:8000/prospects/maison-alize

# Pitch CAP adapté au profil DISC du contact
curl -X POST localhost:8000/pitch -H "Content-Type: application/json" -d '{"entreprise_id": "capilla-nova"}'

# Alerte (les types possibles sont dans GET /alerts)
curl -X POST localhost:8000/alerts -H "Content-Type: application/json" \
  -d '{"entreprise_id": "atelier-verdane", "type": "fin d'\''un contrat exclusif"}'

# Simuler un rendez-vous, puis l'évaluer
curl -X POST localhost:8000/twin/start -H "Content-Type: application/json" \
  -d '{"entreprise_id": "laiterie-des-pyrenees", "contact": "Marc Etchegaray"}'
# -> {"session_id": "a1b2c3d4e5f6", "twin": {...}, "reply": "..."}
curl -X POST localhost:8000/twin/message -H "Content-Type: application/json" \
  -d '{"session_id": "a1b2c3d4e5f6", "message": "Je vous propose un stock de securite reserve."}'
curl -X POST localhost:8000/coach/evaluate -H "Content-Type: application/json" -d '{"session_id": "a1b2c3d4e5f6"}'
```

L'identifiant d'une entreprise est son nom sans accents, en minuscules, avec des tirets
(`laiterie-des-pyrenees`, `maison-alize`, `bulle-d-ecume`). La liste est dans `GET /clients` et `POST /prospects/search`.

### Choix à connaître

- **Le Twin est chargé par nom, jamais par recherche vectorielle** : un client ne peut pas être mélangé avec un autre.
- **Prospects cosmétiques** : leur Digital Twin vient de la ligne « Profil Digital Twin » de leur fiche. Le prospect ne connaît ni son score ni notre stratégie : ces lignes ne sont pas données au LLM. Atelier Verdane, Laboratoire Sources de Cambo et Miellerie des Landes n'ont pas de profil : `POST /twin/start` renvoie 404.
- **Évaluation** : `rules.detect_infractions` analyse chaque message du commercial (remise > 8 %, promesse de bio, délai impossible, « sans allergène » faux). Si une infraction est détectée, la note « règles » est plafonnée à 1/5 par le code, quoi que dise le LLM.
- **Sessions de simulation** : gardées en mémoire (perdues au redémarrage de l'API). Le LLM reçoit les 8 derniers échanges ; l'évaluation lit toute la conversation.
- **`POST /prospects/search`** : la requête ne sert qu'à filtrer si elle nomme un segment (bébé, solaire, homme, recharge...). Le score n'est jamais calculé par le LLM.
- **Alertes** : enregistrées dans `alerts.json` (non versionné).

## Configuration (variables d'environnement)

| Variable | Défaut |
|----------|--------|
| `LLM_MODEL` | `llama3.2:3b` |
| `EMBED_MODEL` | `nomic-embed-text` |
| `OLLAMA_HOST` | `http://localhost:11434` |
| `NUM_CTX` | `8192` (contexte du LLM, en tokens) |
| `CHROMA_DIR` / `COLLECTION_NAME` | `./chroma_db` / `knowledge` |
| `KNOWLEDGE_PATH` | `./story.txt` |
| `CHUNK_SIZE` / `WHOLE_SECTION_MAX` | `1000` / `3000` (caractères ; une section plus courte n'est pas découpée) |
| `TOP_K` | `5` (passages retrouvés par similarité) |
| `MAX_CONTEXT_CHARS` | `16000` (texte envoyé au LLM) |
| `TWIN_EXCHANGES` / `ASK_HISTORY` | `8` / `6` |
| `ALERTS_PATH` | `./alerts.json` |
