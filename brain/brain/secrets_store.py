"""Secrets store for sensitive credentials (e.g., OpenRouter API key).

Design (D8): The key value never crosses the IPC boundary. It lives exclusively in
Windows Credential Manager via keyring and is never logged, printed, or included in
any returned status. Status is one of "set", "missing", "invalid", or
"unverified"; validation state is non-secret metadata.

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
STATUS_NAME = "openrouter_status"
_VALID_STATUSES = {"set", "invalid", "unverified"}


# ponytail: the HALO_KEYRING_DIR test seam vs keyring fork lives here once.
# Backend failures propagate (get_key/keystore_available depend on that).
def _backend_get(name: str) -> str | None:
    keyring_dir = os.environ.get("HALO_KEYRING_DIR")
    if keyring_dir:
        try:
            with open(os.path.join(keyring_dir, f"{SERVICE}_{name}.key"), "r") as f:
                return f.read()
        except FileNotFoundError:
            return None
    return keyring.get_password(SERVICE, name)


def _backend_set(name: str, value: str) -> None:
    keyring_dir = os.environ.get("HALO_KEYRING_DIR")
    if keyring_dir:
        os.makedirs(keyring_dir, exist_ok=True)
        with open(os.path.join(keyring_dir, f"{SERVICE}_{name}.key"), "w") as f:
            f.write(value)
        return
    keyring.set_password(SERVICE, name, value)


def _backend_del(name: str) -> None:
    keyring_dir = os.environ.get("HALO_KEYRING_DIR")
    if keyring_dir:
        try:
            os.remove(os.path.join(keyring_dir, f"{SERVICE}_{name}.key"))
        except FileNotFoundError:
            pass  # Already deleted
        return
    try:
        keyring.delete_password(SERVICE, name)
    except keyring.errors.PasswordDeleteError:
        pass  # Already deleted or never existed


def _read_key() -> str | None:
    """Read the credential, allowing backend failures to reach status callers."""
    return _backend_get(KEY_NAME)


def get_key() -> str | None:
    """Retrieve the key from the keystore.

    Returns:
        The key value if set, None if missing or if keyring backend fails.
        Never raises into a turn.
    """
    # ponytail: test seam — bypass keyring for testing
    try:
        return _read_key()
    except Exception as exc:
        # A backend failure is NOT the same as "no key stored" -- see
        # keystore_available(). Callers that only need the value still get
        # None, but key_status() reports the difference so the UI can't tell
        # the user "not set" when the truth is "I couldn't ask".
        # Credential backends can expose machine/user paths in exception
        # details. Value callers only need the degraded result; status callers
        # use the strict path below for an actionable UI error.
        logger.warning("keyring read failed (%s); backend unavailable", type(exc).__name__)
        return None


def keystore_available() -> bool:
    """Can we actually reach the keystore? Distinguishes 'no key stored' from
    'the vault itself is broken' -- conflating those is what makes a user go
    buy a replacement key they didn't need."""
    if os.environ.get("HALO_KEYRING_DIR"):
        return True
    try:
        _read_key()
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

    _backend_set(KEY_NAME, value)


def set_validation_status(status: str) -> None:
    """Persist non-secret validation state beside the key.

    Without this, a rejected or offline-unverified key is incorrectly reported
    as connected after the next UI snapshot merely because a value exists.
    """
    if status not in _VALID_STATUSES:
        raise ValueError(f"invalid key status: {status}")
    _backend_set(STATUS_NAME, status)


def _validation_status() -> str | None:
    value = _backend_get(STATUS_NAME)
    if value is not None:
        value = value.strip()
    return value if value in _VALID_STATUSES else None


def delete_key() -> None:
    """Delete the key from the keystore.

    Swallows keyring.errors.PasswordDeleteError if the key doesn't exist.
    """
    _backend_del(KEY_NAME)
    _backend_del(STATUS_NAME)


def key_status() -> str:
    """Status only -- the key value itself never leaves this module (D8).

    Returns persisted validation metadata for a stored key, or ``missing``
    when the keystore confirms there is no key. Backend failures deliberately
    propagate so the snapshot layer can send ``invalid`` plus a recoverable
    error instead of presenting an unavailable vault as an absent credential.
    """
    # Unlike get_key(), status reads are strict: the snapshot caller must be
    # able to distinguish an unavailable vault from a confirmed missing key
    # and emit both invalid state and a recoverable error.
    key = _read_key()
    if not key:
        return "missing"
    return _validation_status() or "set"  # legacy keys predate status metadata
