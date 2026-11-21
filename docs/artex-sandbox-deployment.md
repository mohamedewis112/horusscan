# HorusScan / Horus Security Lab — ARTEX isolated sandbox

This document records the real Preview-only ARTEX experiment configured on the existing **Vercel project `horusscan-web`**. No live website or Supabase project was changed.

## Assets (as of 2026-11-21)

- Existing GitHub repository: `mohamedewis112/horusscan`
- Feature branch: `feat/security-lab-repurpose`
- Existing Vercel web project: `horusscan-web`
- Named Vercel Sandbox: `horusscan-artex-lab` (persistent; sandbox sessions have a finite timeout)
- Pinned ARTEX upstream commit: `5876d517f80571a6bbe9ecbef43c7c5d75f2d05d` from `BaaRai/ARTEX`
- Docker image built from the pinned source: `artex:local`
- PostgreSQL: `postgres:16-alpine`
- Drive: `horus-artex-data` (1 GiB), mounted read/write at `/vercel/persist`
- Data paths: `/vercel/persist/postgres`, `/vercel/persist/artex-data`, `/vercel/persist/artex-keys`; credentials and local mock test data are stored in the Drive.
- Current URL: resolve port 8787 from the named Sandbox's routes in Vercel; do not assume the URL or Sandbox session will stay active forever.

The sandbox includes a locally reachable `demo-target` service using only fake rows, mounted from `/vercel/persist/lab`. It is not exposed publicly. The test endpoint `POST /admin/delete-all` deletes synthetic records only; `POST /admin/reset` reseeds them.

## Verified execution

- Vercel Sandbox is running with route for port 8787.
- `artex` container: running.
- `postgres` container: healthy.
- `demo-target` container: running.
- Local ARTEX `/setup`: HTTP 200.
- Connection from ARTEX container to `http://demo-target:8080/health`: success.
- Demonstrated fake-data deletion and full restore (3 → 0 → 3 records).
- Horus Security Lab Preview `/api/lab/status`: checks the live sandbox and exposes a console link.
- All outbound external network destinations are restricted to `api.groq.com`; no unrestricted internet scanning and no public target authorization has been granted.

## Model configuration

The ARTEX container has **non-secret** defaults:
`ARTEX_LLM_PROVIDER=openai`,
`ARTEX_LLM_MODEL=openai/gpt-oss-120b`,
`ARTEX_LLM_BASE_URL=https://api.groq.com/openai/v1`,
and a deliberately aggressive noise profile **inside the isolated sandbox**.

The Groq API key already configured for the Next.js website is **not automatically passed to ARTEX**. To use agent reasoning, create the admin account at `/setup`, open ARTEX's System / LLM settings, choose OpenAI-compatible format, and add an appropriate rotated API key through ARTEX's secret management UI. **Never commit or print credentials, or publish the first-run setup token.**

## Security and lifecycle limits

- Public console access requires ARTEX setup/login. Set up a strong administrator credential promptly and avoid sharing the Sandbox URL and setup token publicly.
- The code is third-party offensive security software; this Preview setup is for disposable, controlled and strictly scoped testing only.
- The running sandbox does **not** have unrestricted outbound connectivity. There is no permission to scan, exploit or destroy `twsilla.com` or any other live site.
- The Vercel Sandbox session will stop on its configured timeout (currently set to 1 hour). A future session may resume from snapshots, but may require restarting the Docker daemon and Compose services.
- The Drive persists data independently, but Docker image/build cache and the daemon may need restoration or rebuilding after a new session.
- The original Supabase HorusScan project `rnrpbzdommxfzyuqjraf` remains INACTIVE due to active Free-project quota. It was **not** deleted or restored. No other Supabase project was touched.
- No changes have been merged to Production.
