"""Entry point for `python -m brain`.

Phase 0 Steps 3-4: starts the WS server (session.json handshake, auth gate,
stub echo turn). See brain/server.py.
"""

from brain.server import main

if __name__ == "__main__":
    main()
