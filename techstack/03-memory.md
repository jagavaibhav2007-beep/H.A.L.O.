# Tech Stack: Memory

Design: [systemdesign/03-memory](../systemdesign/03-memory.md). Global stack: [00-stack-summary](00-stack-summary.md).

## Feature-specific
| Concern | Choice | Notes |
|---|---|---|
| Store | **SQLite** | single local file; beliefs + raw log + task state |
| Vector search | **sqlite-vec** extension | in-DB similarity; no separate vector service |
| Embeddings | **fastembed** (`bge-small-en-v1.5` or MiniLM) | local, free, ~fast on CPU |
| Extraction | light model (`gemma-4-26b-a4b-it`) | v2: one consolidation pass per session segment (idle/shutdown/pressure-triggered), plus per-candidate ADD/UPDATE/INVALIDATE/NOOP decision calls |
| Decay job | scheduled task in the Brain | lowers salience, soft-archives |

## Cost note
- **Fully local except extraction.** Embeddings, storage, search, decay = zero API cost.
- Extraction (v2) is one cheap light-model call per session *segment*, not per turn — an N-turn session costs 1 extraction + 0–3 decision calls + 1 summary instead of N extraction calls (~10× fewer).

## Why not a cloud DB
- PRD requires data on-device. Supabase/cloud DB would put beliefs in the cloud — wrong for this product. SQLite keeps it local and free.
