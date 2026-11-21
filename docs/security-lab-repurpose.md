# Horus Security Lab — same HorusScan project

## Scope of this conversion
This conversion deliberately REUSES `mohamedewis112/horusscan` and its Vercel projects. It does not create another GitHub repository or Vercel project.

This is a **staged UI conversion**, not a running penetration-test service.

- The web UI collects an **unverified candidate staging domain** and a preferred model provider.
- It does **not** issue HTTP requests to the target, run scanners or exploits, change any DNS records, or deploy an execution engine.
- The AI model, ARTEX executor, and database are not connected; the UI says so explicitly.
- Old archaeology code and historical data are preserved on the original `develop` branch until replacement is accepted and recovery is verified.
- Do not delete `TriConsensus AI`; it is a separate, active Supabase project.
- HorusScan Supabase project `rnrpbzdommxfzyuqjraf` was reported INACTIVE; its tables could not be inspected. No SQL cleanup has occurred.

## Required before any scanning
1. Verify ownership/authorization of the exact target domain and permitted test scope.
2. Provision an **isolated staging lab**, not a live customer website. Use synthetic accounts/data and keep backups.
3. Implement real authentication, least-privilege roles, audit events, rate limits, and a kill switch.
4. Put the model API key and ARTEX credentials exclusively in server-side secrets.
5. Review and pin the source of any ARTEX container and dependencies; never import an unverified public Docker image.
6. Add a server-side allowlist and deny unexpected redirects, localhost, RFC1918, link-local, internal metadata IPs, and all out-of-scope hosts.
7. Implement non-destructive checks first; only enable destructive simulations within the disposable lab.
8. Validate deployment configuration and run automated tests before merging.

## Proposed existing resources
- GitHub `mohamedewis112/horusscan`
- Vercel `horusscan-web` for the dashboard
- Vercel `horusscan-api` for tightly scoped API/control plane
- Existing worker projects can be repurposed after identifying which runtimes they can support; a permanent executor is not assumed possible on Vercel.
- Supabase `rnrpbzdommxfzyuqjraf` after safe recovery and explicit schema/data reset approval.

## Environment variables (future, server-side only)
```dotenv
SECURITY_LAB_ALLOWED_ORIGINS=
SECURITY_LAB_EXECUTION_MODE=disabled
SECURITY_LAB_REQUIRE_HUMAN_APPROVAL=true
MODEL_PROVIDER=groq
MODEL_API_BASE_URL=https://api.groq.com/openai/v1
MODEL_ID=
MODEL_API_KEY=
ARTEX_EXECUTOR_URL=
ARTEX_EXECUTOR_TOKEN=
```

**No exploit functionality, destructive automation, target verification or model execution is implemented by this change.**
