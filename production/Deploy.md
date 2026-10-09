# Deploying Church-Management on a home server

Goal: run the app at `https://church-management.canzana.xyz` from a home server, using your existing
Caddy and a Cloudflare Tunnel. Your Tailscale lab (`*.lab.canzana.xyz`) is not changed.

```
Browser -> Cloudflare -> Tunnel (cloudflared, outbound only) -> Caddy :80 -> 127.0.0.1:8090 -> gunicorn (web)
                                                                  |-> /static and /media served from production/data
Docker network (internal only): web, scheduler, db
```

Why a tunnel: if your ISP uses CGNAT (your router's WAN IP differs from `curl -4 ifconfig.me`), port forwarding
cannot work. A tunnel needs no router changes and exposes nothing inbound, so lab services stay private.

## What each file does

| File | Purpose |
|---|---|
| `Dockerfile.prod` | Builds the production image: Tailwind CSS (via your `make tailwind-build`), Python deps, gunicorn, non-root user |
| `Dockerfile.prod.dockerignore` | Keeps `.git`, `.env`, local data and generated files out of the image |
| `docker-compose.prod.yml` | `db` (Postgres, no published ports), `web` (127.0.0.1:8090), `scheduler` (nightly jobs) |
| `env.production.example` | Template for `production/.env` (secret key, DB password, allowed hosts) |
| `deploy/entrypoint.sh` | On every start: `migrate`, `collectstatic`, then run the server |
| `deploy/scheduler.py` | Periodic jobs in Manila time; skips commands that do not exist yet |
| `deploy/settings.diff` | The `settings.py` changes needed for production |
| `deploy/Caddyfile.snippet` | Caddy config for the **port-forwarding** route. Not used on the tunnel route (use Phase 3 below) |

## Phase 0: Confirm CGNAT (2 minutes)

Read the WAN/Internet IP on your router's admin page and compare it with `curl -4 ifconfig.me` on the server.
If the router shows `100.64.x.x`-`100.127.x.x` or a private address (`10.x`, `192.168.x`, `172.16-31.x`), you are behind
CGNAT: continue with this guide. If it is a normal public IP that simply differs, check `tailscale status` for an
exit node or VPN on the server; if you do have a reachable public IP, use `deploy/Caddyfile.snippet` instead of
Phases 3-4 (and lock the `*.lab` block first, as that file explains).

## Phase 1: Prepare the repo (dev machine)

1. Copy the `production/` folder into the repo root.
2. Patch the settings from the repo root:
   ```bash
   [ -n "$(tail -c1 production/deploy/settings.diff)" ] && echo >> production/deploy/settings.diff
   git apply --check production/deploy/settings.diff && git apply production/deploy/settings.diff
   ```
   (The first line adds a missing final newline; `git apply` rejects a patch without one.) If it still fails, make the
   five edits in `church_management/settings.py` by hand. The diff shows exactly which: import `Csv`,
   `ALLOWED_HOSTS` and `CSRF_TRUSTED_ORIGINS` from env, `STATIC_ROOT`, the `if not DEBUG:` security block, and `LOGGING`.
3. Add to `.gitignore` (leading slashes matter: a plain `data/` would also hide `static/data/ph_address.json`):
   ```
   /production/data/
   /production/.env
   ```
4. Add `ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS` and `LOG_LEVEL` to `.env.example` (AGENTS.md asks to keep it in sync).
5. Check dev still works (`docker compose up`, `python manage.py check`, open `http://127.0.0.1:8001/`), then commit and push.

## Phase 2: Run the app on the server

```bash
cd ~/docker
git clone <your-repo-url> church-management
cd church-management/production

cp env.production.example .env && chmod 600 .env
python3 -c "import secrets; print(secrets.token_urlsafe(64))"   # paste into SECRET_KEY
id -u; id -g                                                    # set APP_UID / APP_GID if not 1000
nano .env                                                       # SECRET_KEY, POSTGRES_PASSWORD, hostname

mkdir -p data/static data/media
docker compose -f docker-compose.prod.yml up -d --build
docker compose -f docker-compose.prod.yml ps
docker compose -f docker-compose.prod.yml logs -f web
```

The first build takes several minutes. Wait until `web` is `healthy`, then:

```bash
curl -I -H "Host: church-management.canzana.xyz" http://127.0.0.1:8090/    # expect HTTP 200
docker compose -f docker-compose.prod.yml exec web python manage.py createsuperuser   # Enter at "Account id"
```

A request without the `Host` header returns 400; that is normal (`ALLOWED_HOSTS`). `seed_superadmin` is dev-only
(needs `DEBUG=True`), so use `createsuperuser`. If the build fails at the Tailwind step, check that
`make tailwind-build` works without Docker; that is the part most likely to need adjusting.

## Phase 3: Add the app to your existing Caddy

1. In `~/docker/caddy/docker-compose.yml`, add two volume lines (adjust the path to where you cloned the repo):
   ```yaml
   - /home/cool/docker/church-management/production/data/static:/srv/church/static:ro
   - /home/cool/docker/church-management/production/data/media:/srv/church/media:ro
   ```
2. Add this block to `~/docker/caddy/Caddyfile`, after the lab block:
   ```
   http://church-management.{$DOMAIN} {
       encode zstd gzip

       request_body {
           max_size 100MB
       }

       handle /static/* {
           root * /srv/church
           file_server
       }
       handle /media/* {
           root * /srv/church
           file_server
       }

       handle {
           reverse_proxy localhost:8090 {
               header_up X-Forwarded-Proto https
           }
       }
   }
   ```
   `http://` stops Caddy from requesting a certificate for this name (Cloudflare handles HTTPS). The
   `X-Forwarded-Proto https` line is required: without it Django thinks requests are plain HTTP and logins fail with CSRF errors.
3. Apply and test:
   ```bash
   cd ~/docker/caddy && docker compose up -d
   docker compose logs --tail 20 caddy
   curl -I -H "Host: church-management.canzana.xyz" http://127.0.0.1/
   curl -I -H "Host: church-management.canzana.xyz" http://127.0.0.1/static/css/output.css
   ```
   Both should return 200.

## Phase 4: Make it public with a Cloudflare Tunnel

1. Cloudflare dashboard -> Zero Trust -> Networks -> Tunnels -> Create a tunnel -> Cloudflared. Name it (for example
   `leaf`) and copy the token.
2. On the server:
   ```bash
   mkdir -p ~/docker/cloudflared && cd ~/docker/cloudflared
   echo 'TUNNEL_TOKEN=paste-token-here' > .env && chmod 600 .env
   ```
   Create `docker-compose.yml` there:
   ```yaml
   services:
     cloudflared:
       image: cloudflare/cloudflared:latest
       container_name: cloudflared
       restart: always
       network_mode: host
       env_file: .env
       command: tunnel --no-autoupdate run
   ```
   Then `docker compose up -d && docker compose logs -f`. You should see several "Registered tunnel connection" lines.
3. In the dashboard, add a public hostname to that tunnel: subdomain `church-management`, domain `canzana.xyz`,
   type `HTTP`, URL `localhost:80`. Cloudflare creates the DNS record itself, so delete any existing
   `church-management` record first. Menu wording varies between dashboard versions.
4. SSL/TLS -> Edge Certificates: turn on Always Use HTTPS.

## Phase 5: Verify

- Open `https://church-management.canzana.xyz`: the landing page should be styled and the dark/light toggle should work.
- Log in at `/accounts/login/` with the account ID from `createsuperuser`; you should land on `/superadmin/`.
- `docker compose -f docker-compose.prod.yml logs scheduler` (in `production/`) should show it started.
  "skip ... not found" lines for jobs that are not built yet are expected.

## Updating later

```bash
cd ~/docker/church-management && git pull
cd production && docker compose -f docker-compose.prod.yml up -d --build
```
Migrations and `collectstatic` run automatically on start.

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| 400 Bad Request on the real site | Hostname missing from `ALLOWED_HOSTS` in `production/.env` |
| "CSRF verification failed" on login | `CSRF_TRUSTED_ORIGINS` missing `https://church-management.canzana.xyz`, or the Caddy block lacks `header_up X-Forwarded-Proto https` |
| Unstyled pages | `static/css/output.css` not built (check the build logs) or Caddy volume lines missing |
| Uploads return 404 | Caddy `media` volume path wrong, or `production/data/media` not owned by `APP_UID` |
| 502 from Cloudflare | Tunnel URL wrong (should be `localhost:80`), or Caddy/cloudflared not running |
| "Permission denied" writing to `data/` | `APP_UID`/`APP_GID` in `.env` do not match the owner of `production/data`; fix and rebuild |
| Errors with no traceback | Settings patch not applied (the `LOGGING` block) |

## Things to know

- **Media is public by URL.** Caddy serves `/media/*` without a login. If resources are members-only, downloads
  should be gated through Django before real data goes in.
- **Pin versions later.** `requirements.txt` has `Django>=5.0` (unpinned) and Tailwind builds from `latest`
  (`TAILWIND_VERSION` build arg pins it).
- **Backups are not set up yet.** The data lives in the `postgres_data` volume and `production/data/media`.
- **Database shell:** `docker compose -f docker-compose.prod.yml exec web python manage.py dbshell` (no port is published).
- **Email** still uses the console backend; fine until forgot-password is built.
- **Untested:** the image build was never run by the author's tooling (no Docker available there); the compose file,
  shell scripts, scheduler logic and settings patch were checked separately.