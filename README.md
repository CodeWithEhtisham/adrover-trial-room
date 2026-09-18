# AdRover — AI Virtual Try-On

Spec: [`Adrover virtual tryon mvp.md`](./Adrover%20virtual%20tryon%20mvp.md) · **[How to run it](./RUNNING.md)**

| | | |
|---|---|---|
| [`spike/`](./spike) | Garment compatibility spike (§10 "build first") | persons × garments × {kling, fashn} → an HTML grid. Throwaway. |
| [`api/`](./api) | Phase 1 working demo (§7) | upload → pick garment → render → before/after. |

Both read `FAL_KEY` from the repo-root `.env` (copy `.env.example`); it is gitignored. Each directory has its own README and a dependency-free self-check
(`python test_run.py` / `python test_app.py`) that costs nothing to run.

**Order of operations:** the spike decides the demo catalog. Phase 1 consumes whatever
column comes out clean — copy those garments into `api/catalog/<category>/`.
