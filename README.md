# Church-Management

A church management system for **Jesus Is Lord Church**, built to manage the church's **extensions** (local congregations/branches). It handles attendance, tithes and offerings, events, member accounts, and prayer requests, and gives members daily-engagement tools such as a Bible reader, streaks, habits, and an accountability buddy.

## Tech Stack

- **Backend:** Django
- **Database:** PostgreSQL 16
- **Frontend:** Tailwind CSS (standalone CLI, no Node required) and Alpine.js
- **Infrastructure:** Docker & Docker Compose
- **DB GUI:** pgAdmin 4 (dev)

## Getting Started

### Prerequisites

- Docker & Docker Compose
- `make`

### First-time setup

1. Clone the repo.
2. Copy `.env.example` to `.env` and fill in the values (Postgres credentials, Django secret key, and optionally `PGADMIN_DEFAULT_EMAIL` / `PGADMIN_DEFAULT_PASSWORD`).
3. Download the Tailwind CLI (one-time):
   ```bash
   curl -sLO https://github.com/tailwindlabs/tailwindcss/releases/latest/download/tailwindcss-linux-x64
   chmod +x tailwindcss-linux-x64
   mv tailwindcss-linux-x64 theme/tailwindcss
   ```
4. Download Alpine.js (one-time):
   ```bash
   mkdir -p static/js
   curl -sLo static/js/alpine.min.js https://cdn.jsdelivr.net/npm/alpinejs@3.14.9/dist/cdn.min.js
   ```
5. Build and start the containers, then migrate and create a superuser:
   ```bash
   docker compose up --build -d
   docker compose exec web python manage.py migrate
   docker compose exec web python manage.py createsuperuser
   ```

### Day-to-day development

```bash
docker compose up
make tailwind-watch    # in a separate terminal
```

### Local URLs

| Service | URL |
|---|---|
| App | http://localhost:8002 |
| Django admin | http://localhost:8002/admin |
| pgAdmin (DB GUI) | http://localhost:5050 |
| PostgreSQL (host access) | `localhost:5433` |

All ports are bound to `127.0.0.1` only.

### Using the database GUI (pgAdmin)

1. Open http://localhost:5050 and log in with `PGADMIN_DEFAULT_EMAIL` / `PGADMIN_DEFAULT_PASSWORD` (defaults: `admin@example.com` / `admin`, so change them in `.env`).
2. Right-click **Servers → Register → Server**.
3. Under **Connection**, use host `db`, port `5432`, and the `POSTGRES_USER` / `POSTGRES_PASSWORD` values from `.env`.

Use `db:5432` from inside Docker (pgAdmin, Django) and `localhost:5433` from your host machine.

## Concepts

- **Extension:** a local congregation. Members, attendance, tithes, and events belong to an Extension, not to a Coordinator account, so history persists when a Coordinator is reassigned.
- **Roles:** Super-admin → Admin → Coordinator → Member. Members can also hold a special role: *Tithes Member* or *Attendance Member*.
- **Account status:** active, archived, inactive, suspended, or not activated.

## Contributing

See [AGENTS.md](AGENTS.md) for conventions, commands, and domain rules. They apply to human contributors as well as AI coding agents.