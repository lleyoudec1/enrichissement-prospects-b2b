# Pipeline d'enrichissement prospects B2B

Script Python qui enrichit automatiquement un fichier de prospects B2B avec le **site web** et l'**email de contact** de chaque entreprise — sans clé API ni abonnement payant.

---

## Ce que fait le script

À partir d'un fichier Excel contenant `SIREN`, `Nom de l'entreprise` et `Ville`, le pipeline :

1. **Trouve le site web** de chaque entreprise via :
   - L'API publique [Recherche Entreprises](https://recherche-entreprises.api.gouv.fr) (data.gouv.fr)
   - En fallback : recherche DuckDuckGo avec filtrage des annuaires (Pages Jaunes, Societe.com, Infogreffe…)

2. **Extrait l'email de contact** en scrapant les pages clés du site trouvé :
   `/contact`, `/mentions-legales`, `/nous-contacter`, `/about`, page d'accueil…
   - Filtrage des emails inutiles (`noreply@`, `webmaster@`, `exemple@`…)
   - Priorité : `contact@` > `info@` > `direction@` > premier trouvé

3. **Exporte trois fichiers** :
   - `enrichi_*.xlsx` — fichier complet avec colonnes ajoutées
   - `a_traiter_manuellement_*.xlsx` — entreprises sans email trouvé
   - `log_*.txt` — log horodaté du traitement

4. **Reprend là où il s'est arrêté** grâce à un fichier de progression JSON — sans retraiter ce qui est déjà fait.

---

## Installation

```bash
pip install requests beautifulsoup4 pandas openpyxl
```

---

## Utilisation

1. Placez votre fichier source dans le même dossier que le script
2. Ajustez les paramètres en tête de fichier :

```python
INPUT_FILE  = "mon_fichier_prospects.xlsx"
OUTPUT_FILE = "enrichi_mon_fichier_prospects.xlsx"
```

3. Lancez :

```bash
python enrichir_entreprises.py
```

---

## Format du fichier d'entrée

| Colonne | Description |
|---|---|
| `SIREN` | Numéro SIREN à 9 chiffres |
| `Nom de l'entreprise` | Raison sociale |
| `Ville` | Ville du siège (utilisée pour affiner la recherche web) |

Les autres colonnes présentes dans le fichier sont conservées telles quelles.

---

## Colonnes ajoutées en sortie

| Colonne | Description |
|---|---|
| `Site_Web` | URL du site officiel trouvé |
| `Source_Site` | Source : `api_gouv` ou `duckduckgo` |
| `Email_Contact` | Email de contact extrait |
| `Source_Email` | Page où l'email a été trouvé (`accueil`, `contact`, `mentions-legales`…) |

---

## Paramètres configurables

```python
PAUSE_BETWEEN = 2      # Pause entre chaque entreprise (secondes)
TIMEOUT       = 10     # Timeout par requête HTTP
MAX_RETRIES   = 2      # Nombre de tentatives en cas d'échec
```

---

## Contraintes techniques respectées

- ✅ Timeout 10s par requête, retry ×2 avec backoff exponentiel
- ✅ Pause de 2s entre chaque entreprise (respect du rate limiting)
- ✅ User-Agent navigateur réaliste
- ✅ Gestion silencieuse des erreurs (échec → champ vide, on continue)
- ✅ Logging horodaté dans un fichier `.txt`
- ✅ Reprise automatique sans retraitement
- ✅ `requests` + `beautifulsoup4` uniquement — pas de Selenium

---

## Fichiers générés

```
enrichi_*.xlsx                  ← résultat complet enrichi
a_traiter_manuellement_*.xlsx   ← entreprises sans email (traitement manuel)
enrichissement_progress_*.json  ← état de progression (reprise)
log_*.txt                       ← log détaillé du traitement
```

---

## Performances observées

- ~60–70 % de sites trouvés (selon notoriété des entreprises)
- ~90–95 % d'emails trouvés parmi les sites identifiés
- Durée : environ **3–4 secondes par entreprise** (pause + scraping)

---

## Dépendances

```
requests>=2.31
beautifulsoup4>=4.12
pandas>=2.0
openpyxl>=3.1
```
