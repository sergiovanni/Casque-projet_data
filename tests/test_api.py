"""Tests de l'API. Ollama est remplacé par de faux appels : les tests sont rapides et ne dépendent pas du LLM."""

import json

import pytest
from fastapi.testclient import TestClient

import api
import config
import rag


@pytest.fixture
def appels_llm():
    """Les messages envoyés au (faux) LLM, pour vérifier ce qu'il reçoit."""
    return []


@pytest.fixture
def client(monkeypatch, tmp_path, appels_llm):
    def faux_chat(messages, temperature=0.2, json_mode=False, max_tokens=None):
        appels_llm.append(messages)
        if json_mode:
            return json.dumps({"notes": {"disc": 4, "spin": 3, "cap": 3, "objections": 4, "regles": 5, "engagement": 4},
                               "point_fort": "Écoute", "a_ameliorer": "Questions SPIN", "phrase_plus_efficace": "…",
                               "infos_cachees_decouvertes": []})
        return "Réponse du faux LLM."

    monkeypatch.setattr(rag, "chat", faux_chat)
    monkeypatch.setattr(rag, "search", lambda question, top_k, entreprise=None: [])  # pas de base vectorielle
    monkeypatch.setattr(config, "ALERTS_PATH", tmp_path / "alerts.json")
    api.SESSIONS.clear()
    return TestClient(api.app)


# --- Prospection (sans IA) --------------------------------------------------------------

def test_prospects_search_renvoie_les_10_premiers(client):
    r = client.post("/prospects/search", json={"query": "Trouve-moi 10 marques de cosmétiques françaises qui pourraient "
                                                         "avoir besoin de nouvelles fragrances"}).json()
    assert [p["nom"] for p in r["prospects"]] == [
        "Maison Alizé", "Capilla Nova", "Lumen Derma", "Solenza", "Hommage Paris", "Ondine & Sel", "Petit Nuage",
        "Bulle d'Écume", "Laboratoire Sources de Cambo", "Terre de Lys"]
    assert [p["score"] for p in r["prospects"]] == [95, 93, 88, 85, 83, 80, 73, 68, 58, 53]
    assert r["total"] == 11  # Atelier Verdane (50) est 11e


def test_prospects_search_identique_a_chaque_appel(client):
    corps = {"query": "marques de cosmétiques"}
    assert client.post("/prospects/search", json=corps).json() == client.post("/prospects/search", json=corps).json()


def test_prospect_detail(client):
    r = client.get("/prospects/maison-alize").json()
    assert r["score"] == 95 and r["rang"] == 1
    assert r["contact"]["nom"] == "Élise Marchand"
    assert "rituels du soir" in r["accroche"].lower()
    assert "chef de projet parfum" in r["signaux"]
    assert r["pourquoi_cette_entreprise"]
    assert {d["critere"] for d in r["details_score"]} >= {"lancement", "porte_entree"}
    assert r["points_manquants"] == [{"critere": "porte_entree", "libelle": "porte d'entrée", "manque": 5}]
    assert r["codes_produits"] == ["FB-140"]


def test_prospect_inconnu(client):
    assert client.get("/prospects/nexiste-pas").status_code == 404


# --- Menus --------------------------------------------------------------------------------

def test_clients_et_solutions(client):
    clients = client.get("/clients").json()
    assert len(clients) == 7  # 6 actifs + Conserveries Basques (perdu)
    laiterie = client.get("/clients/laiterie-des-pyrenees").json()
    assert laiterie["remise_actuelle"] == 5 and len(laiterie["comptes_rendus"]) == 1
    assert client.get("/clients/maison-alize").status_code == 404  # un prospect n'est pas un client
    solutions = {p["code"]: p for p in client.get("/solutions").json()}
    assert solutions["TW-221"]["temperature_max"] == 90 and solutions["FB-112"]["bio"]


def test_insights(client):
    r = client.get("/insights").json()
    assert r["tendances"] and r["veille"] and r["alertes_recommandees"]


def test_prompts_et_attach(client):
    assert set(client.get("/prompts").json()) == {"preparation", "pendant_le_rdv", "entrainement", "prospection"}
    assert client.post("/attach").status_code == 501


# --- Alertes ------------------------------------------------------------------------------

def test_creer_et_lister_une_alerte(client):
    r = client.post("/alerts", json={"entreprise_id": "atelier-verdane", "type": "fin d'un contrat exclusif"})
    assert r.status_code == 201
    assert client.get("/alerts").json()["alertes"][0]["entreprise"] == "Atelier Verdane"


def test_alerte_type_inconnu_ou_entreprise_inconnue(client):
    assert client.post("/alerts", json={"entreprise_id": "atelier-verdane", "type": "bidon"}).status_code == 422
    assert client.post("/alerts", json={"entreprise_id": "inconnue", "type": "levée de fonds"}).status_code == 404


# --- Assistant ----------------------------------------------------------------------------

def test_ask_donne_les_faits_verifies_au_llm(client, appels_llm):
    r = client.post("/ask", json={"question": "Puis-je accorder 7 % à Mathilde Garat ?"}).json()
    assert r["answer"] == "Réponse du faux LLM."
    assert any("contrat de deux ans" in f for f in r["faits_verifies"])
    envoye = appels_llm[-1][-1]["content"]
    assert "FAITS VÉRIFIÉS" in envoye and "Mathilde Garat" in envoye  # sa fiche est chargée par nom


def test_ask_charge_la_fiche_du_client_cite(client):
    r = client.post("/ask", json={"question": "Prépare mon rendez-vous avec la Laiterie des Pyrénées."}).json()
    titres = " ".join(s["chapter"] for s in r["sources"])
    assert "Marc Etchegaray" in titres and "Hélène Bordes" in titres and "Compte rendu" in titres


def test_ask_entreprise_inconnue(client):
    assert client.post("/ask", json={"question": "Bonjour", "entreprise_id": "inconnue"}).status_code == 404


# --- Twin et Coach ------------------------------------------------------------------------

def demarrer(client, entreprise_id="laiterie-des-pyrenees", contact=None):
    return client.post("/twin/start", json={"entreprise_id": entreprise_id, "contact": contact})


def test_twin_charge_le_bon_profil(client, appels_llm):
    r = demarrer(client, contact="Hélène Bordes").json()
    assert r["twin"]["nom"] == "Hélène Bordes"
    systeme = appels_llm[-1][0]["content"]
    assert "Tu joues Hélène Bordes" in systeme
    assert "25 % du sucre" in systeme       # son information cachée est dans le prompt...
    assert "Marc Etchegaray, acheteur, négocie" in systeme  # ...avec la fiche de l'entreprise
    assert "Tu joues Marc Etchegaray" not in systeme        # un seul personnage à la fois


def test_twin_prospect_sans_donnees_internes(client, appels_llm):
    r = demarrer(client, "maison-alize")
    assert r.json()["twin"]["nom"] == "Élise Marchand"
    systeme = appels_llm[-1][0]["content"]
    assert "cinq semaines de retard" in systeme
    assert "Score total" not in systeme and "Prochaine étape" not in systeme  # le prospect ne connaît pas notre scoring


def test_twin_anglais(client, appels_llm):
    demarrer(client, "atlantic-drinks-ltd")
    assert "UNIQUEMENT anglais" in appels_llm[-1][0]["content"]


def test_twin_introuvable(client):
    assert demarrer(client, "atelier-verdane").status_code == 404   # pas de Digital Twin
    assert demarrer(client, "laiterie-des-pyrenees", "Inconnu").status_code == 404
    assert client.post("/twin/message", json={"session_id": "nope", "message": "Bonjour"}).status_code == 404


def test_twin_historique_limite_aux_8_derniers_echanges(client, appels_llm):
    session_id = demarrer(client).json()["session_id"]
    for i in range(12):
        client.post("/twin/message", json={"session_id": session_id, "message": f"Message {i}"})
    envoye = appels_llm[-1]
    assert len(envoye) <= 1 + 2 * config.TWIN_EXCHANGES + 2   # système + historique + rappel + message
    assert envoye[-1]["content"] == "Message 11"
    assert len(api.SESSIONS[session_id]["messages"]) == 1 + 2 * 12   # tout est gardé pour l'évaluation


def test_evaluation_signale_la_remise_de_12_pourcent(client):
    session_id = demarrer(client).json()["session_id"]
    client.post("/twin/message", json={"session_id": session_id, "message": "Je vous propose 12 % de remise"})
    r = client.post("/coach/evaluate", json={"session_id": session_id}).json()
    assert [i["type"] for i in r["infractions"]] == ["remise"]
    assert r["notes"]["regles"] == 1            # le LLM avait mis 5 : le code plafonne la note
    assert set(r["notes"]) == {"disc", "spin", "cap", "objections", "regles", "engagement"}
    assert r["total"] == sum(r["notes"].values())


def test_evaluation_sans_infraction_garde_la_note_du_llm(client):
    session_id = demarrer(client).json()["session_id"]
    client.post("/twin/message", json={"session_id": session_id, "message": "Je propose un stock de sécurité réservé."})
    r = client.post("/coach/evaluate", json={"session_id": session_id}).json()
    assert r["infractions"] == [] and r["notes"]["regles"] == 5


def test_evaluation_sans_message_du_commercial(client):
    session_id = demarrer(client).json()["session_id"]
    assert client.post("/coach/evaluate", json={"session_id": session_id}).status_code == 400


def test_evaluation_json_invalide(client, monkeypatch):
    session_id = demarrer(client).json()["session_id"]
    client.post("/twin/message", json={"session_id": session_id, "message": "Bonjour"})
    monkeypatch.setattr(rag, "chat", lambda *a, **k: "pas du json")
    assert client.post("/coach/evaluate", json={"session_id": session_id}).status_code == 502


def test_health(client, monkeypatch):
    monkeypatch.setattr(rag, "ollama_available", lambda: False)
    assert client.get("/health").json()["ollama"] is False


# --- Réponses de l'Assistant ---------------------------------------------------------------

def test_refus_de_tete_retire_quand_une_reponse_suit():
    brut = "Je n'ai pas cette information dans les documents. Le TW-502 SweetBalance coûte 310 € le kilo."
    assert api.remove_leading_refusal(brut) == "Le TW-502 SweetBalance coûte 310 € le kilo."


def test_refus_seul_reste_intact():
    refus = "Je n'ai pas cette information dans les documents."
    assert api.remove_leading_refusal(refus) == refus
    assert api.remove_leading_refusal("Le TW-502 coûte 310 € le kilo.") == "Le TW-502 coûte 310 € le kilo."


def test_contexte_court_sans_sections_inutiles(client):
    r = client.post("/ask", json={"question": "Quel est le prix du TW-502 ?"}).json()
    titres = " ".join(s["chapter"] for s in r["sources"])
    assert "Fiche produit TW-502" in titres
    assert "Concurrence" not in titres and "Présentation du secteur" not in titres
    assert sum(len(s["text"]) for s in r["sources"]) <= config.MAX_CONTEXT_CHARS


def test_nom_propre_retrouve_les_paragraphes_qui_le_citent(client):
    r = client.post("/ask", json={"question": "Quels clients pourraient acheter SweetBalance ?"}).json()
    titres = " ".join(s["chapter"] for s in r["sources"])
    assert "BoissonSud" in titres and "Atlantic Drinks" in titres and "Brasserie du Béarn" in titres


def test_pitch_dans_ask_utilise_la_logique_du_pitch(client, appels_llm):
    r = client.post("/ask", json={"question": "Prépare un pitch pour Capilla Nova"}).json()
    assert r["sources"][0]["chapter"].startswith("Pitch")
    assert "Capilla Nova" in appels_llm[-1][-1]["content"]
