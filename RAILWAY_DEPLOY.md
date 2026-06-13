# Déploiement MetalFlow Pro sur Railway

Architecture : **3 services Railway** dans un seul projet.

```
Railway Project: MetalFlow Pro
├── [DB]       MongoDB          ← addon Railway
├── [Service]  metalflow-backend  ← dossier /backend
└── [Service]  metalflow-frontend ← dossier /frontend
```

---

## Prérequis

- Compte Railway → https://railway.app
- Railway CLI (optionnel) : `npm install -g @railway/cli`
- Repo Git avec ce code poussé sur GitHub/GitLab

---

## Étape 1 — Créer le projet Railway

1. Aller sur https://railway.app → **New Project**
2. Choisir **Deploy from GitHub repo** → sélectionner ce repo

---

## Étape 2 — Ajouter MongoDB

Dans le projet Railway :
1. **+ New** → **Database** → **MongoDB**
2. Une fois créé, cliquer sur la base de données → onglet **Variables**
3. Copier la valeur de `MONGO_URL` (sera utilisée à l'étape 4)

---

## Étape 3 — Service Backend

1. **+ New** → **GitHub Repo** → même repo
2. Dans les paramètres du service → **Source** → **Root Directory** : `backend`
3. Railway détecte automatiquement Python via `requirements.txt` et utilise `railway.toml`

### Variables d'environnement du backend

Dans l'onglet **Variables** du service backend, ajouter :

| Variable | Valeur |
|---|---|
| `MONGO_URL` | *Copier depuis le service MongoDB* |
| `DB_NAME` | `metalflow_pro` |
| `JWT_SECRET` | *Générer une clé forte : `openssl rand -base64 64`* |
| `JWT_ALGO` | `HS256` |
| `JWT_EXP_HOURS` | `24` |
| `REFRESH_DAYS` | `7` |
| `APP_ENV` | `production` |
| `LOG_LEVEL` | `INFO` |
| `SEED_ADMIN` | `true` |
| `ADMIN_EMAIL` | *votre email admin* |
| `ADMIN_PASSWORD` | *mot de passe fort (min 12 chars)* |
| `COOKIE_SECURE` | `true` |
| `COOKIE_SAMESITE` | `none` |
| `CORS_ALLOW_ORIGIN_REGEX` | `https://.*\.railway\.app` |
| `EMERGENT_LLM_KEY` | *votre clé LLM (optionnel)* |
| `LLM_MODEL_PROVIDER` | `anthropic` |
| `LLM_MODEL` | `claude-sonnet-4-6` |
| `GOLD_PRICE_USD_PER_OZ` | `2100` |
| `SILVER_PRICE_USD_PER_OZ` | `24` |
| `ENERGY_COST_USD_PER_KWH` | `0.08` |
| `CO2_KG_PER_KWH` | `0.42` |
| `DISCOUNT_RATE` | `0.08` |
| `NPV_HORIZON_YEARS` | `10` |
| `OPERATING_HOURS_PER_YEAR` | `8000` |

4. **Deploy** → attendre que le service démarre
5. Dans **Settings** → copier l'URL publique du backend (ex: `https://metalflow-backend-xxxx.railway.app`)

---

## Étape 4 — Service Frontend

1. **+ New** → **GitHub Repo** → même repo
2. **Root Directory** : `frontend`

### Variables d'environnement du frontend

> ⚠️ `REACT_APP_BACKEND_URL` est intégré AU BUILD. Il doit être défini **avant** le premier déploiement.

| Variable | Valeur |
|---|---|
| `REACT_APP_BACKEND_URL` | *URL publique du backend copiée à l'étape 3* |
| `ENABLE_HEALTH_CHECK` | `false` |

3. **Deploy** — Railway exécute `yarn install && yarn build` puis sert depuis `build/`

---

## Étape 5 — Vérification

| Check | URL |
|---|---|
| Health backend | `https://<backend-url>/api/health` → `{"status":"ok"}` |
| API docs (dev uniquement) | `https://<backend-url>/docs` |
| Frontend | `https://<frontend-url>/` |

---

## Mise à jour du CORS après déploiement

Une fois l'URL frontend connue, mettre à jour la variable backend :

```
CORS_ALLOW_ORIGIN_REGEX=https://(metalflow-frontend-xxxx\.railway\.app|votre-domaine\.com)
```

Puis **Redeploy** le backend.

---

## Domaine personnalisé (optionnel)

Dans chaque service → **Settings** → **Custom Domain** → ajouter votre domaine et configurer le DNS.

---

## Commandes CLI Railway (alternative)

```bash
# Login
railway login

# Lier le projet
railway link

# Déployer le backend
cd backend && railway up --service metalflow-backend

# Déployer le frontend
cd ../frontend && railway up --service metalflow-frontend
```

---

## Notes de sécurité production

- Changer `ADMIN_PASSWORD` immédiatement après le premier déploiement
- `JWT_SECRET` doit être unique et jamais partagé — minimum 64 caractères
- Ne jamais committer `.env` sur Git (déjà dans `.gitignore`)
- Activer **Private Networking** sur Railway pour que le frontend appelle le backend sans passer par l'internet public (utiliser `RAILWAY_PRIVATE_DOMAIN`)
