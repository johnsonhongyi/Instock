# Agent Quick Context

For work involving PVE, Docker deployment, or TDX market files, read [ENVIRONMENT_HANDOFF.md](ENVIRONMENT_HANDOFF.md) first.

- The Windows checkout is not the production Docker host. Docker runs on PVE inside LXC 102.
- Verify live container, image, and mount state before changing production; the handoff records a dated snapshot and can become stale.
- Use a deployment script whose expected base matches the live image. Preserve its lock, health-check, and rollback steps.
- Keep credentials and full environment dumps out of source, logs, and handoff notes.
- After deployment or a data repair, update the dated status in `ENVIRONMENT_HANDOFF.md` with observed results.
