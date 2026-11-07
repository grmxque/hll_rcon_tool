# Maintenance du fork

Ce dépôt (`grmxque/hll_rcon_tool`) est un fork de [CRCON](https://github.com/MarechJ/hll_rcon_tool).
`master` = dépôt officiel + nos modules (ex. [Anti-Steamroll](anti-steamroll.md)). Le VPS **construit ses
propres images Docker** à partir de ce dépôt au lieu de récupérer les images officielles `cericmathey/*`.

Les règles de développement qui gardent les mises à jour simples (code perso dans des fichiers à nous,
aucune migration Django, pas de modification des workflows/`default.env`) sont décrites dans `CLAUDE.md`
(section *This repository is a fork*).

## Remotes

| Remote | Dépôt | Usage |
| --- | --- | --- |
| `origin` | `grmxque/hll_rcon_tool` | notre fork, déployé sur le VPS |
| `upstream` | `MarechJ/hll_rcon_tool` | dépôt officiel, en lecture seule |

Mise en place sur un nouveau poste :

```bash
git remote add upstream https://github.com/MarechJ/hll_rcon_tool.git
git fetch upstream --tags
git config rerere.enabled true   # git rejoue les résolutions de conflits déjà faites
```

Les tags sont indispensables : la version affichée par le backend (`uv-dynamic-versioning`) et le build du
frontend (`git describe --tags` dans `Dockerfile-frontend`) en dépendent.

Les workflows GitHub hérités du dépôt officiel (`main.yml` qui crée des tags automatiquement,
`docker-images*.yml` qui publient sur Docker Hub) sont **désactivés sur le fork** dans *Settings → Actions*
(ou `gh workflow disable`). Les fichiers restent intacts pour ne pas créer de conflits.

## Développer un module

```bash
git checkout master && git pull
git checkout -b feature/<nom>
# ... code + tests ...
uv run pytest && (cd rcongui && npm run build)
git checkout master && git merge --no-ff feature/<nom>
git push origin master
```

Si le module ajoute des permissions, il faut les ajouter à `FORK_PERMISSIONS` dans
`rconweb/api/fork_permissions.py`. Elles sont créées au prochain `migrate` (conteneur `maintenance`), puis
attribuées aux groupes dans l'admin Django.

## Intégrer une mise à jour officielle

Avant de commencer, lire les notes de release officielles : les changements de `default.env` ou de
`docker-templates/*.yaml` sont à reporter **à la main** dans le `.env` et le `compose.yaml` du VPS, qui ne
sont pas versionnés.

En local :

```bash
git fetch upstream --tags
git checkout master
git merge upstream/master        # conflits éventuels : surtout dans les fichiers "points de branchement"
uv run pytest
(cd rcongui && npm ci && npm run build)
git push origin master --tags
```

Pour prévisualiser les conflits sans rien modifier :
`git merge --no-commit --no-ff upstream/master; git merge --abort`.

Pour voir tout ce qui diffère de l'officiel : `git diff upstream/master --stat`.

## Déployer sur le VPS

Dans le dossier CRCON du VPS :

```bash
git pull --ff-only
git fetch --tags
docker compose build
docker compose up -d --remove-orphans
docker compose ps                # le conteneur maintenance doit être "healthy"
```

Le build (surtout le frontend) consomme CPU et RAM : à faire de préférence quand le serveur est peu peuplé.

### Rollback

```bash
git log --oneline -5             # repérer le commit précédent
git checkout <sha>
docker compose build && docker compose up -d --remove-orphans
git checkout master              # une fois le problème corrigé sur master
```

Si une migration de base de données pose problème, restaurer la sauvegarde (voir ci-dessous).

## Bascule initiale du VPS (CRCON officiel → fork)

À faire une seule fois, sur un VPS qui fait tourner le CRCON officiel.

1. Sauvegarder la base et la configuration :

   ```bash
   source .env
   docker compose exec -T postgres pg_dump -U "$HLL_DB_USER" "$HLL_DB_NAME" > "backup_$(date +%F).sql"
   cp .env .env.bak && cp compose.yaml compose.yaml.bak && cp -r config config.bak
   ```

2. Pointer le clone vers le fork, en gardant l'officiel comme `upstream` :

   ```bash
   git status                     # vérifier qu'il n'y a pas de modifications locales versionnées
   git remote rename origin upstream
   git remote add origin https://github.com/grmxque/hll_rcon_tool.git
   git fetch --all --tags
   git checkout -B master origin/master
   ```

3. Dans `.env`, remplacer les images officielles par des noms locaux. Un `docker compose pull` ne pourra
   alors jamais remettre les images officielles par-dessus les nôtres :

   ```dotenv
   BACKEND_DOCKER_REPOSITORY=crcon-fork/backend
   FRONTEND_DOCKER_REPOSITORY=crcon-fork/frontend
   TAGGED_VERSION=local
   ```

4. Construire et démarrer :

   ```bash
   docker compose build
   docker compose up -d --remove-orphans
   docker compose ps
   docker compose logs maintenance | tail -50
   ```

5. Dans l'admin Django, attribuer les permissions `can_view_auto_mod_antisteamroll_config` et
   `can_change_auto_mod_antisteamroll_config` aux groupes concernés.

Restauration de la sauvegarde si nécessaire :

```bash
docker compose exec -T postgres psql -U "$HLL_DB_USER" "$HLL_DB_NAME" < backup_YYYY-MM-DD.sql
```

(sur une base vidée au préalable, ou après avoir recréé le volume `db_data`).
