# Brief pour Claude Code — Refaire l'API « Givaudan AI Sales Copilot » (équipe Casque)

> À coller tel quel dans Claude Code, à la racine du dépôt (fork de `hmicn/example-rag`).

## 1. Contexte

Hackathon Givaudan AI Innovation Challenge, thématique « Relation commerciale & IA ». On construit un copilote pour le commercial **Jordan Dubois**. Le jury veut surtout voir un **Digital Twin du client** : une IA qui joue le client, avec sa personnalité, et à qui le commercial peut parler pour s'entraîner. Le jury a dit d'**éviter** de recréer un simple CRM ou un enregistreur de conversation.

Le dépôt de départ est un RAG simple : `ingest.py`, `rag.py`, `api.py` (FastAPI), `config.py`, ChromaDB, Ollama. **On garde cette base** et on l'étend.

## 2. Contraintes techniques (à respecter)

- Tout tourne **en local** avec Ollama : LLM `llama3.2:3b` (choisi dans notre benchmark), embeddings `nomic-embed-text`. Le changement de modèle se fait dans `config.py`.
- Python 3.11+, FastAPI, ChromaDB. Pas de service payant, pas de clé API.
- La seule source de données est `story.txt` (fourni, ne pas le modifier par code).
- **Règle d'or : l'IA ne calcule rien et ne décide rien de chiffré.** Les scores, remises, températures, minimums de commande et délais sont vérifiés en Python. Le LLM rédige seulement du texte à partir de données déjà vérifiées.
- CORS ouvert pour le front (`http://localhost:*`).
- Code simple et commenté en français : c'est un projet d'étudiants, il doit être compréhensible.

## 3. Format de `story.txt`

- Ligne 1 : titre. Ensuite, des sections dont le titre commence par un numéro romain : `XXVIII. Fiche prospect — Maison Alizé (score 95/100)`.
- Le type de section se lit dans le titre, après le numéro :
  - `Fiche client —`, `Fiche client perdu —`, `Customer profile —` → client
  - `Compte rendu de visite —` → compte rendu
  - `Digital Twin —` → persona d'un contact (le nom de l'entreprise est à la fin du titre)
  - `Fiche prospect —` → prospect (les prospects cosmétiques contiennent une ligne `Données de score : ...`)
  - `Catalogue ...` → produits ; `Grille de score ...`, `Récapitulatif ...`, `Alertes de veille`, `Veille ...`, `Tendances ...`, `Concurrence ...`, `Procédure ...`, `Scénarios ...`, `Techniques ...`, `Sources ...`, `Présentation du secteur ...` → sections de référence
- Les fiches prospects cosmétiques ont des lignes fixes : `Identité :`, `Accroche :`, `Signaux :`, `Pourquoi cette entreprise :`, `Produits Givaudan adaptés :`, `Contact :`, `Profil Digital Twin :`, `Données de score :`, `Prochaine étape :`.
- Les produits ont un code (`TW-104`, `FB-140`…), un dosage, une stabilité en °C (`Stable jusqu'à 180 °C`, `Non stable au-delà de 90 °C`), un prix au kilo et des allergènes.

## 4. Ce qu'il faut construire

### 4.1 `knowledge.py` — lecture structurée de `story.txt`
Parser `story.txt` au démarrage et construire en mémoire :
- `sections` : liste `{id, numero, titre, type, entreprise, texte}`
- `entreprises` : clients et prospects, avec leurs sections liées (fiche, compte rendu, Digital Twins)
- `prospects` : champs ci-dessus + détail des points de score (lancement, recrutement, tendance, fournisseur, taille, produit adapté, porte d'entrée)
- `produits` : code, nom, division, dosage, température max (si connue), allergènes, prix, certifications
- `regles` : remises (3 %, 5 %, 6–8 % sur contrat de 2 ans, validation au-delà de 8 %), 5 échantillons par trimestre, délais (5 jours, 3 semaines, 10 jours, 6 semaines), commande minimum 25 kg

### 4.2 `ingest.py` — indexation améliorée
- Garder le découpage actuel par section, mais ajouter les métadonnées `type` et `entreprise` à chaque morceau.
- Permettre la recherche filtrée (`where={"entreprise": ...}`) en plus de la recherche par similarité.

### 4.3 `scoring.py` — score des prospects (sans IA)
- Recalculer le score à partir des points listés dans `Données de score`, selon la grille de la section « Grille de score des prospects ».
- Vérifier que le total recalculé correspond au « Score total » écrit ; sinon, logguer une alerte.
- Trier les prospects par score décroissant.

### 4.4 `rules.py` — règles métier (sans IA)
Fonctions pures, testées :
- `check_remise(pourcentage, contrat_annees)` → autorisé / validation de Thomas Lefèvre requise
- `check_temperature(code_produit, temperature_procede)` → compatible / non compatible / inconnu (TW-310 : « tenue à la cuisson inconnue »)
- `check_bio(code_produit)` → aucun arôme alimentaire n'est bio ; seul FB-112 est COSMOS
- `check_quantite(kg)` → minimum 25 kg par référence ; stock de sécurité possible dès 1 tonne par an
- `detect_infractions(texte)` → repère dans un message du commercial une remise > 8 %, une promesse de bio, une promesse de délai impossible, une affirmation « sans allergène » fausse

### 4.5 Les trois modes du LLM (`prompts.py`)

**Assistant** (préparer, répondre en direct, prospecter) :
```
Tu es l'AI Sales Copilot de Givaudan. Tu aides le commercial Jordan Dubois.
Règles :
- Réponds UNIQUEMENT à partir des extraits fournis. Cite l'entreprise, le produit ou la section utilisée.
- Si l'information n'est pas dans les extraits, dis : « Je n'ai pas cette information dans les documents. » N'invente jamais.
- Ne donne jamais un score, un prix ou une remise qui n'est pas écrit dans les extraits ou dans les FAITS VÉRIFIÉS.
- Adapte tes conseils au profil DISC et aux mobiles SONCAS du contact quand ils sont fournis.
- Réponds dans la langue de la question. Sois concis et orienté action.
```

**Twin** (le LLM joue le client) :
```
Tu joues {nom}, {poste} chez {entreprise}. Tu n'es PAS un assistant : tu es ce client, en rendez-vous avec Jordan Dubois, commercial de Givaudan.
Ton profil complet : {texte intégral du Digital Twin + fiche entreprise}
Règles :
- Reste toujours dans ton personnage : ton, vocabulaire, phrases typiques, objections.
- Réponds en 1 à 4 phrases, comme à l'oral.
- Les informations de « Ce qu'il/elle ne dit que si on lui pose les bonnes questions » : ne les révèle QUE si le commercial pose une question précise qui y mène. Sinon, reste vague.
- N'invente aucun chiffre ni aucun fait absent de ton profil : esquive (« Je ne communique pas ce chiffre. »).
- Réagis aux erreurs du commercial selon « Ce qui l'agace ».
- Parle la langue du profil (Sean O'Brien : anglais uniquement).
```

**Coach** (évaluer une simulation) :
```
Tu es coach commercial. Évalue la conversation entre Jordan Dubois et le Digital Twin selon la grille :
DISC adapté, questions SPIN d'implication, présentation CAP, traitement des objections, respect des règles, engagement obtenu.
Utilise les INFRACTIONS DÉTECTÉES fournies (vérifiées par programme) sans les contredire.
Réponds en JSON : {"notes": {critère: 0-5}, "point_fort": "...", "a_ameliorer": "...", "phrase_plus_efficace": "...", "infos_cachees_decouvertes": [...]}
```

### 4.6 Endpoints, écran par écran (maquette Figma)

| Écran / bouton | Endpoint | Ce qu'il fait | IA ? |
|---|---|---|---|
| Écran 0 — chat « Go » | `POST /ask` `{question, history?}` | RAG Assistant ; renvoie `answer`, `sources` | Oui |
| Bibliothèque prompts | `GET /prompts` | Liste fixe de questions par catégorie (préparation, pendant le RDV, entraînement, prospection) | Non |
| Message vocal (bonus) | `POST /voice/transcribe` | Audio → texte avec `faster-whisper` en local | Non |
| Attach | — | Hors périmètre : renvoyer 501 avec un message clair | — |
| Écran 1 — « Lancer la recherche » | `POST /prospects/search` `{query, limit=10}` | Prospects cosmétiques triés par score Python ; renvoie `id, nom, ville, score, accroche` | Non |
| Écran 2 — « Consulter » | `GET /prospects/{id}` | Fiche détaillée + détail du score | Non |
| Écran 3 — « Pourquoi cette entreprise ? » | inclus dans `GET /prospects/{id}` | Champ `Pourquoi cette entreprise` + signaux | Non |
| Préparer un pitch | `POST /pitch` `{entreprise_id}` | Pitch avec la méthode CAP, adapté au DISC du contact, basé sur les produits adaptés | Oui |
| Créer une alerte | `POST /alerts` `{entreprise_id, type}` ; `GET /alerts` | Enregistre dans `alerts.json` ; types listés dans « Alertes de veille » | Non |
| Autre demande / Posez votre question | `POST /ask` avec `entreprise_id` | RAG filtré sur l'entreprise | Oui |
| **Nouveau : Simuler le RDV** | `POST /twin/start` `{entreprise_id, contact?}` → `session_id` + première réplique ; `POST /twin/message` `{session_id, message}` | Le LLM joue le client. Le profil est chargé **en entier par nom** (pas par recherche vectorielle). Historique gardé en mémoire (limiter aux 8 derniers échanges) | Oui |
| **Nouveau : Évaluation** | `POST /coach/evaluate` `{session_id}` | `rules.detect_infractions` sur chaque message + LLM Coach ; renvoie le JSON de notes | Oui + règles |
| Menu Mes clients | `GET /clients`, `GET /clients/{id}` | Liste et fiche client | Non |
| Menu Insights | `GET /insights` | Sections Tendances + Veille + alertes | Non |
| Menu Solutions | `GET /solutions` | Catalogue produits structuré | Non |
| Santé | `GET /health` | Vérifie Ollama et le nombre de morceaux indexés | Non |

Pour `/ask` et `/pitch`, ajouter au prompt un bloc **FAITS VÉRIFIÉS** calculé par `rules.py` quand la question parle de remise, de température, de bio ou de quantité.

### 4.7 Fichiers à livrer
```
config.py        (modèle llama3.2:3b, TOP_K=5, CHUNK_SIZE=1000)
knowledge.py     (parsing structuré)
ingest.py        (métadonnées type/entreprise)
rag.py           (recherche filtrée)
scoring.py
rules.py
prompts.py
api.py           (tous les endpoints ci-dessus)
tests/test_rules.py, tests/test_scoring.py, tests/test_api.py
eval_questions.py  (lance toutes les questions de questions_test.md sur l'API et écrit un rapport)
README.md        (installation, lancement, exemples curl par écran)
```

## 5. Critères d'acceptation

1. `POST /prospects/search` avec « Trouve-moi 10 marques de cosmétiques françaises qui pourraient avoir besoin de nouvelles fragrances » renvoie 10 prospects : Maison Alizé (95), Capilla Nova (93), Lumen Derma (88), Solenza (85), Hommage Paris (83), Ondine & Sel (80), Petit Nuage (73), Bulle d'Écume (68), Laboratoire Sources de Cambo (58), Terre de Lys (53). Atelier Verdane (50) est 11e. Mêmes résultats à chaque appel.
2. `GET /prospects/maison-alize` renvoie l'accroche, les signaux, le « Pourquoi », le contact Élise Marchand et le détail des points.
3. `check_remise(12, 0)` → validation requise ; `check_temperature("TW-221", 180)` → non compatible ; `check_temperature("TW-618", 121)` → compatible ; `check_temperature("TW-310", 180)` → inconnu.
4. En mode Twin avec Marc Etchegaray, le message « Je vous propose 12 % de remise » est signalé par `/coach/evaluate` comme infraction.
5. En mode Twin, le Twin ne révèle l'information cachée qu'après une question qui y mène (tester avec Hélène Bordes : objectif de 25 % de sucre en moins).
6. Les questions pièges de `questions_test.md` obtiennent « Je n'ai pas cette information dans les documents ».
7. `pytest` passe. `eval_questions.py` produit un rapport avec le taux de bonnes réponses.

## 6. À ne pas faire

- Ne pas laisser le LLM classer les prospects ni calculer un score.
- Ne pas utiliser la recherche vectorielle pour charger un Digital Twin (risque de mélanger deux clients).
- Ne pas ajouter de vraies marques ni de données extérieures : tout vient de `story.txt`.
- Ne pas ajouter de dépendance lourde (LangChain, base SQL) : le projet doit rester lisible.
