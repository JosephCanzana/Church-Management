# AGENTS.md

## Project overview
Church-Management is a Django-based church management system for Jesus Is Lord Church, designed to manage multiple church extensions. Core areas: attendance, tithes/offering, events, resource center, prayer requests, streaks/habits with a buddy system, and a donation page. The UI supports Filipino/English and light/dark themes.

## Tech stack
- Django (PostgreSQL, not SQLite)
- PostgreSQL 16
- Tailwind CSS via standalone CLI
- Alpine.js
- Docker + Docker Compose
- Adminer (dev-only database GUI)

## Repository layout
- `manage.py`: Django entry point
- `static/`: project-wide static assets (`css/input.css`, generated `css/output.css`, `js/alpine.min.js`)
- `theme/`: Tailwind CLI binary lives here (gitignored, downloaded once)
- `docker-compose.yml`: `db`, `web`, and `adminer` services
- `Makefile`: Tailwind build/watch commands
- `.env.example`: template for required environment variables
- `.env`: local values, never committed

Inspect the tree before assuming where code lives; add new Django apps at the repo root.

## Running the project
Docker Compose is the default development path.

```bash
docker compose up --build -d
docker compose exec web python manage.py migrate
docker compose exec web python manage.py createsuperuser
```

Day-to-day:

```bash
docker compose up
make tailwind-watch   # separate terminal, runs continuously
```

| Service | URL |
|---|---|
| App | http://127.0.0.1:8002/ |
| Admin | http://127.0.0.1:8002/admin/ |
| pgAdmin (DB GUI) | http://127.0.0.1:5050/ |
| Postgres (host) | 127.0.0.1:5433 |

Adminer login: System `PostgreSQL`, Server `db`, credentials from `.env`.

## Environment
Settings are read from `.env`. Keep it consistent with `.env.example`. Expected variables include `DEBUG`, `SECRET_KEY`, and the `POSTGRES_*` values (`DB`, `USER`, `PASSWORD`, `HOST`, `PORT`). Inside Docker, Postgres is at host `db`, port `5432`. From the host it is on port `5433`.

## Common commands
```bash
docker compose exec web python manage.py makemigrations
docker compose exec web python manage.py migrate
docker compose exec web python manage.py test
docker compose exec web python manage.py shell
make tailwind-build
make tailwind-watch
```


## Working rules
- Prefer Docker-based commands; run Django commands with `docker compose exec web ...`.
- Create migrations for every model change and commit them.
- Use Tailwind utility classes and Alpine.js for interactivity. Don't add heavier frontend frameworks without discussion.
- Don't hand-edit `static/css/output.css`; rebuild it with `make tailwind-build`.
- Wrap user-facing strings for translation (Filipino/English) and support both light and dark themes in new UI.
- Never commit `.env`, secrets, or database dumps.
- Adminer is for local development only. Don't expose it publicly or ship it to production.
- If Postgres auth fails after changing `.env`: `docker compose down -v && docker compose up --build` (wipes local dev data).

## Before finishing a change
- Run the app or the smallest relevant Django command to verify it works.
- Run `docker compose exec web python manage.py check` and the relevant tests.
- Confirm migrations are generated and `.env.example` is updated if new variables were added.