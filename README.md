# Website Monitoring Dashboard

Dashboard statique de monitoring de disponibilité de sites web — HTML/CSS/JavaScript
vanilla, hébergé sur GitHub Pages, alimenté par un script Python exécuté
automatiquement une fois par jour via GitHub Actions.

Aucun backend, aucune base de données : `config/list_url.txt` est la seule source
de vérité pour la liste des sites, et `data/status.json` est la seule donnée
consommée par le frontend.

---

## 1. Fonctionnement général

```
config/list_url.txt
        │
        ▼
GitHub Actions (quotidien / manuel)
        │
        ▼
Python/monitor.py
        │
        ├── Vérification HTTP (statut + code)
        ├── Capture d'écran (Playwright + Chromium)
        ├── Écrase l'ancienne capture du site
        ├── Supprime les captures des sites retirés
        └── Génère data/status.json
                │
                ▼
         GitHub Pages
                │
                ▼
   index.html + CSS/style.css + JS/script.js
                │
        Dashboard généré dynamiquement
        (statistiques, tuiles, statuts, captures)
```

Chaque exécution est indépendante : le fichier `config/list_url.txt` est relu
intégralement à chaque fois, et `data/status.json` est régénéré en entier.

---

## 2. Architecture du projet

```
/
├── index.html                    Page unique du dashboard
├── CSS/style.css                 Styles (thème clair/sombre inclus)
├── JS/script.js                  Génération dynamique des tuiles + thème
├── config/list_url.txt           Liste des sites à surveiller (source de vérité)
├── data/status.json              Résultat du dernier monitoring (généré)
├── screenshots/                  Une capture par site (écrasée à chaque run)
├── Python/monitor.py             Script de monitoring
├── requirements.txt              Dépendances Python
├── .github/workflows/monitoring.yml   Automatisation GitHub Actions
└── README.md
```

---

## 3. Format de `config/list_url.txt`

Une ligne = un site, au format :

```
Nom du site, URL
```

Exemple :

```text
# Sites Algorel
Algorel, https://www.algorel.fr
Au Fil Du Bain, https://www.aufildubain.fr

# Autres sites
Site exemple, https://www.example.com
```

Règles :

- Les lignes vides sont ignorées.
- Les lignes commençant par `#` sont des commentaires.
- Les espaces autour du nom et de l'URL sont supprimés automatiquement.
- Les lignes invalides (virgule manquante, URL non valide, champ vide) sont
  ignorées et signalées dans les logs, sans interrompre le traitement des
  autres sites.

### Ajouter un site

Ajoutez une ligne dans `config/list_url.txt` :

```text
Nouveau site, https://www.example.com
```

Aucune modification de code n'est nécessaire. Le site apparaîtra dans le
dashboard après la prochaine exécution du monitoring (automatique ou manuelle).

### Supprimer un site

Supprimez simplement sa ligne dans `config/list_url.txt`. À la prochaine
exécution, son entrée disparaît de `data/status.json` et sa capture d'écran
est automatiquement supprimée de `screenshots/`.

---

## 4. Fonctionnement du monitoring (`Python/monitor.py`)

Pour chaque site listé :

1. Une requête HTTP GET est envoyée (timeout de 15 secondes).
2. Le code HTTP de la réponse est enregistré.
3. **Statut ONLINE / DOWN** : voir la logique ci-dessous.
4. La page principale est chargée avec **Playwright + Chromium** et une
   capture d'écran est prise (résolution 1440×900).
5. La capture écrase le fichier existant du même site
   (`screenshots/<slug>.png`) — aucun historique horodaté n'est conservé.
6. `data/status.json` est régénéré intégralement à partir des résultats.

Une erreur sur un site (DNS, connexion, timeout, certificat HTTPS, erreur
Playwright...) n'interrompt jamais le traitement des autres sites : chaque
site est isolé dans son propre bloc de gestion d'erreurs.

### Logique ONLINE / DOWN

Définie dans la fonction `is_online()` de `Python/monitor.py`, facilement
modifiable :

```python
def is_online(http_code):
    if http_code is None:
        return False          # DNS, connexion, timeout, SSL...
    return http_code < 500    # 2xx, 3xx, 4xx -> ONLINE ; 5xx -> DOWN
```

Cas gérés explicitement : erreurs DNS, erreurs de connexion, timeouts,
erreurs HTTPS/certificat, codes 4xx, codes 5xx, erreurs Playwright.

### Nommage des captures d'écran

Le nom de fichier est dérivé du nom du site (accents et caractères spéciaux
normalisés, espaces remplacés par des tirets) :

```
Algorel        → screenshots/algorel.png
Au Fil Du Bain → screenshots/au-fil-du-bain.png
Mon Site 2026  → screenshots/mon-site-2026.png
```

En cas de collision entre deux noms générant le même slug, un suffixe
numérique est ajouté automatiquement (`site-1`, `site-2`, ...).

---

## 5. `data/status.json`

Généré entièrement à chaque exécution à partir de `config/list_url.txt` :

```json
{
  "last_update": "2026-08-19 06:00:00",
  "sites": [
    {
      "name": "Algorel",
      "url": "https://www.algorel.fr",
      "status": "online",
      "http_code": 200,
      "screenshot": "screenshots/algorel.png",
      "render_time_ms": 842,
      "last_check": "2026-08-19 06:00:00"
    }
  ]
}
```

Ce fichier est la seule source de données du frontend : aucun site n'est
codé en dur dans `index.html` ou `JS/script.js`.

`render_time_ms` est le temps de rendu complet de la page (navigation →
événement `load`, DOM + ressources + styles + images chargés), mesuré par
Playwright au moment même de la capture d'écran. Il vaut `null` si la page
n'a pas pu être chargée.

### Cache des captures d'écran

Le nom de fichier d'une capture reste identique d'un run à l'autre
(`screenshots/algorel.png`), donc les navigateurs la mettent en cache. Pour
éviter d'afficher une ancienne capture après une mise à jour, `JS/script.js`
ajoute automatiquement un paramètre `?v=<last_check>` à l'URL de l'image, ce
qui force le rechargement dès que `data/status.json` indique une nouvelle
heure de vérification — sans jamais renommer le fichier sur le disque.

---

## 6. GitHub Actions

Le workflow `.github/workflows/monitoring.yml` :

- s'exécute automatiquement **une fois par jour** (`cron: "0 6 * * *"`, 06:00 UTC) ;
- peut être déclenché manuellement via **Actions → Website Monitoring → Run workflow**
  (`workflow_dispatch`) ;
- installe Python, les dépendances (`requirements.txt`) et Chromium
  (`playwright install --with-deps chromium`) ;
- exécute `Python/monitor.py`, qui régénère les captures et `data/status.json` ;
- **ne crée un commit que si des fichiers ont changé** (captures, JSON) ;
- pousse directement sur la branche du repository.

### Modifier la fréquence

Éditez l'expression cron dans `monitoring.yml` :

```yaml
on:
  schedule:
    - cron: "0 6 * * *"   # tous les jours à 06:00 UTC
```

### Lancement manuel

Onglet **Actions** du repository GitHub → sélectionner **Website Monitoring**
→ **Run workflow**.

---

## 7. Déploiement GitHub Pages

1. Poussez ce projet sur un repository GitHub.
2. Dans **Settings → Pages**, choisissez la branche de déploiement (ex. `main`)
   et le dossier racine (`/`).
3. Le dashboard est servi statiquement depuis `index.html`, aucune étape de
   build n'est nécessaire.

Le dashboard va simplement lire `data/status.json` à chaque chargement de
page, donc il reflète toujours le résultat du dernier run GitHub Actions.

---

## 8. Thème clair / sombre

Un bouton dans l'en-tête permet de basculer entre les thèmes `light` et
`dark`. Le choix est sauvegardé dans `localStorage` et restauré au chargement
suivant. Si aucun choix n'a été fait, le thème du système d'exploitation est
utilisé (sinon le thème clair par défaut).

Les couleurs sont définies via des variables CSS dans `CSS/style.css`
(`:root` pour le thème clair, `[data-theme="dark"]` pour le thème sombre),
ce qui permet d'ajuster la palette sans toucher au reste du CSS.

---

## 9. Développement local

Le dashboard étant purement statique, il suffit de le servir via un petit
serveur HTTP local (le chargement de `data/status.json` via `fetch()`
nécessite `http://`, pas `file://`) :

```bash
python -m http.server 8000
# puis ouvrir http://localhost:8000
```

Pour exécuter le monitoring localement :

```bash
pip install -r requirements.txt
python -m playwright install --with-deps chromium
python Python/monitor.py
```

---

## 10. Sécurité

- Aucun secret (mot de passe, token, clé API) n'est stocké dans le repository.
- Le script n'interroge que les URLs présentes dans `config/list_url.txt`,
  validées avant utilisation.
- Le contenu de `list_url.txt` n'est jamais exécuté, uniquement lu comme
  donnée texte.
