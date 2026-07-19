"""Secrets store for sensitive credentials (e.g., OpenRouter API key).

Design (D8): The key value never crosses the IPC boundary. It lives exclusively in
Windows Credential Manager via keyring and is never logged, printed, or included in
any returned status. Status only: "set" or "missing".

Test seam: if HALO_KEYRING_DIR env var is set, bypass keyring and store the key in
a plain file under that directory (test-only, no real app uses this).
"""

import os
import keyring
import keyring.errors


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
        # Swallow keyring backend exceptions (rare: disabled Credential Manager, etc.)
        return None


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
    """Return the status of the stored key.

    Returns:
        "set" if a key exists, "missing" if not.
        The actual key value is never included in the status.
    """
    return "set" if get_key() else "missing"
