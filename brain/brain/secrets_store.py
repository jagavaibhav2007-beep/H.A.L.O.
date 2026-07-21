"""Secrets store for sensitive credentials (e.g., OpenRouter API key).

Design (D8): The key value never crosses the IPC boundary. It lives exclusively in
Windows Credential Manager via keyring and is never logged, printed, or included in
any returned status. Status only: "set" or "missing".

Test seam: if HALO_KEYRING_DIR env var is set, bypass keyring and store the key in
a plain file under that directory (test-only, no real app uses this).
"""

import logging
import os
import keyring
import keyring.errors

logger = logging.getLogger("brain.secrets")

SERVICE = "halo"
KEY_NAME = "openrouter"


def get_key() -> str | None:
    """Retrieve the key from the keystore.

    Returns:
        The key value if set, None if missing or if keyring backend fails.
        Never raises into a turn.
    """
    # ponytail: test seam — bypass keyring for testing
    keyring_dir = os.environ.get("HALO_KEYRING_DIR")
    if keyring_dir:
        key_file = os.path.join(keyring_dir, f"{SERVICE}_{KEY_NAME}.key")
        if os.path.exists(key_file):
            try:
                with open(key_file, "r") as f:
                    return f.read()
            except (OSError, IOError):
                return None
        return None

    # Production path: Windows Credential Manager via keyring
    try:
        return keyring.get_password(SERVICE, KEY_NAME)
    except Exception:
        # A backend failure is NOT the same as "no key stored" -- see
        # keystore_available(). Callers that only need the value still get
        # None, but key_status() reports the difference so the UI can't tell
        # the user "not set" when the truth is "I couldn't ask".
        logger.exception("keyring read failed (backend unavailable?)")
        return None


def keystore_available() -> bool:
    """Can we actually reach the keystore? Distinguishes 'no key stored' from
    'the vault itself is broken' -- conflating those is what makes a user go
    buy a replacement key they didn't need."""
    if os.environ.get("HALO_KEYRING_DIR"):
        return True
    try:
        keyring.get_password(SERVICE, KEY_NAME)
        return True
    except Exception:
        return False


def set_key(value: str) -> None:
    """Store the key in the keystore.

    Args:
        value: the key to store (stripped of leading/trailing whitespace)

    Raises:
        ValueError: if value is empty after stripping
    """
    # Strip whitespace
    value = value.strip()

    if not value:
        raise ValueError("Key cannot be empty")

    # ponytail: test seam — bypass keyring for testing
    keyring_dir = os.environ.get("HALO_KEYRING_DIR")
    if keyring_dir:
        os.makedirs(keyring_dir, exist_ok=True)
        key_file = os.path.join(keyring_dir, f"{SERVICE}_{KEY_NAME}.key")
        with open(key_file, "w") as f:
            f.write(value)
        return

    # Production path: Windows Credential Manager via keyring
    keyring.set_password(SERVICE, KEY_NAME, value)


def delete_key() -> None:
    """Delete the key from the keystore.

    Swallows keyring.errors.PasswordDeleteError if the key doesn't exist.
    """
    # ponytail: test seam — bypass keyring for testing
    keyring_dir = os.environ.get("HALO_KEYRING_DIR")
    if keyring_dir:
        key_file = os.path.join(keyring_dir, f"{SERVICE}_{KEY_NAME}.key")
        try:
            os.remove(key_file)
        except FileNotFoundError:
            pass  # Already deleted
        return

    # Production path: Windows Credential Manager via keyring
    try:
        keyring.delete_password(SERVICE, KEY_NAME)
    except keyring.errors.PasswordDeleteError:
        # Already deleted or never existed
        pass


def key_status() -> str:
    """Status only -- the key value itself never leaves this module (D8).

    "set"     a key is stored
    "missing" no key is stored (and the keystore answered, so this is the truth)
    "invalid" the keystore is unreachable, so we genuinely don't know

    The third case used to be reported as "missing", which reads as "your key
    is gone" and sends people off to rotate a key that was never lost.
    """
    if get_key():
        return "set"
    return "missing" if keystore_available() else "invalid"
