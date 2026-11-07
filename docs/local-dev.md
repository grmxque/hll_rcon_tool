# Environnement de développement local

Pour faire tourner CRCON en local (WSL/Linux) et tester l'interface avant la prod : PostgreSQL et Redis
dans Docker, Django et le frontend Vite en natif. Par défaut, le CRCON local n'est relié à **aucun serveur
de jeu** (identifiants factices). Les pages de réglages (dont Automods → Anti-Steamroll) fonctionnent,
mais les pages live (joueurs, logs, scoreboard) affichent des erreurs, et aucun automod n'agit.

Prérequis : `uv`, Node + npm et Docker Engine.

## 1. Docker Engine (une seule fois)

Sous Debian/Ubuntu en WSL avec systemd activé (`/etc/wsl.conf` : `[boot] systemd=true`), suivre la
[procédure officielle Docker](https://docs.docker.com/engine/install/debian/) (dépôt apt `download.docker.com`,
paquets `docker-ce docker-ce-cli containerd.io docker-compose-plugin`), puis :

```bash
sudo usermod -aG docker "$USER"   # puis fermer et rouvrir le terminal WSL
docker run --rm hello-world
```

## 2. Configuration (une seule fois)

```bash
cp example.dev.env dev.env          # dev.env est ignoré par git
cp rcongui/example.env rcongui/.env # cible du proxy Vite : http://localhost:8010
```

Dans `dev.env`, renseigner :

```bash
export HLL_HOST=127.0.0.1   # aucun serveur de jeu : valeurs factices,
export HLL_PORT=1           # la connexion RCON n'est tentée qu'à l'usage
export HLL_PASSWORD=dummy
export HLL_GAME=hll
export HLL_DB_PASSWORD=<un mot de passe local>
export DJANGO_DEBUG=1
export DEBUG=1
```

## 3. Démarrer les dépendances et initialiser la base

```bash
source dev.env
docker compose -f docker-compose.dev.yml up -d postgres redis

uv sync
PYTHONPATH=. uv run alembic upgrade head
PYTHONPATH=. uv run ./manage.py init_db
uv run python -m rcon.user_config.seed_db
PYTHONPATH=. uv run ./rconweb/manage.py migrate --noinput   # crée aussi les permissions du fork
PYTHONPATH=. uv run ./rconweb/manage.py createsuperuser
```

## 4. Lancer l'application

Deux terminaux, chacun après `source dev.env` :

```bash
PYTHONPATH=. uv run ./rconweb/manage.py runserver 8010
```

```bash
cd rcongui && npm ci && npm run dev   # http://localhost:3000
```

## Tests

Avec Postgres et Redis démarrés (comme la CI officielle, `.github/workflows/pr.yml`) :

```bash
source dev.env && uv run pytest tests/
```

Sans base, la plupart des tests passent tout de même. Ceux qui ont besoin de la base échouent avec
`No $HLL_DB_URL specified` :

```bash
DEBUG=1 SERVER_NUMBER=1 HLL_GAME=hll uv run pytest tests/
```

## Remise à zéro

```bash
docker compose -f docker-compose.dev.yml down
sudo rm -rf db_data redis_data   # données locales créées par docker-compose.dev.yml
```
