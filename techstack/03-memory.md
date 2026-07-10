# Tech Stack: Memory

Design: [systemdesign/03-memory](../systemdesign/03-memory.md). Global stack: [00-stack-summary](00-stack-summary.md).

## Feature-specific
| Concern | Choice | Notes |
|---|---|---|
| Store | **SQLite** | single local file; beliefs + raw log + task state |
| Vector search | **sqlite-vec** extension | in-DB similarity; no separate vector service |
| Embeddings | **fastembed** (`bge-small-en-v1.5` or MiniLM) | local, free, ~fast on CPU |
| Extraction | light model (`gemma-4-26b-a4b-it`) | pulls durable facts at end of turn |
| Decay job | scheduled task in the Brain | lowers salience, soft-archives |

## Cost note
- **Fully local except extraction.** Embeddings, storage, search, decay = zero API cost.
- Extraction is one cheap light-model call per turn *only when* something durable might exist (skipped otherwise).

## Why not a cloud DB
- PRD requires data on-device. Supabase/cloud DB would put beliefs in the cloud — wrong for this product. SQLite keeps it local and free.
