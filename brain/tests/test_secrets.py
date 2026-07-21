"""Tests for brain.secrets_store (plain asyncio+assert, no framework)."""

import os
import sys
import tempfile

# Add brain to path so we can import
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from brain.secrets_store import get_key, set_key, delete_key, key_status, keystore_available


def test_missing_key():
    """Test that get_key returns None when key is missing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        os.environ["HALO_KEYRING_DIR"] = tmpdir
        try:
            result = get_key()
            assert result is None, f"Expected None, got {result}"

            status = key_status()
            assert status == "missing", f"Expected 'missing', got {status}"
        finally:
            del os.environ["HALO_KEYRING_DIR"]


def test_set_and_get_roundtrip():
    """Test that set_key and get_key round-trip correctly."""
    with tempfile.TemporaryDirectory() as tmpdir:
        os.environ["HALO_KEYRING_DIR"] = tmpdir
        try:
            test_key = "test_openrouter_key_12345"
            set_key(test_key)

            result = get_key()
            assert result == test_key, f"Expected '{test_key}', got '{result}'"

            status = key_status()
            assert status == "set", f"Expected 'set', got '{status}'"
        finally:
            del os.environ["HALO_KEYRING_DIR"]


def test_set_with_whitespace_strips():
    """Test that set_key strips leading and trailing whitespace."""
    with tempfile.TemporaryDirectory() as tmpdir:
        os.environ["HALO_KEYRING_DIR"] = tmpdir
        try:
            test_key = "  my_key_value  "
            set_key(test_key)

            result = get_key()
            assert result == "my_key_value", f"Expected 'my_key_value', got '{result}'"
        finally:
            del os.environ["HALO_KEYRING_DIR"]


def test_set_empty_raises_valueerror():
    """Test that set_key raises ValueError on empty string."""
    with tempfile.TemporaryDirectory() as tmpdir:
        os.environ["HALO_KEYRING_DIR"] = tmpdir
        try:
            try:
                set_key("")
                assert False, "Expected ValueError"
            except ValueError as e:
                assert "empty" in str(e).lower(), f"ValueError message: {e}"

            # Also test whitespace-only
            try:
                set_key("   ")
                assert False, "Expected ValueError for whitespace-only"
            except ValueError as e:
                assert "empty" in str(e).lower(), f"ValueError message: {e}"
        finally:
            del os.environ["HALO_KEYRING_DIR"]


def test_delete_key():
    """Test that delete_key removes the key."""
    with tempfile.TemporaryDirectory() as tmpdir:
        os.environ["HALO_KEYRING_DIR"] = tmpdir
        try:
            # Set a key
            set_key("test_key")
            assert get_key() == "test_key"

            # Delete it
            delete_key()
            assert get_key() is None, "Expected key to be deleted"
            assert key_status() == "missing"
        finally:
            del os.environ["HALO_KEYRING_DIR"]


def test_double_delete_doesnt_raise():
    """Test that delete_key doesn't raise when key is already deleted."""
    with tempfile.TemporaryDirectory() as tmpdir:
        os.environ["HALO_KEYRING_DIR"] = tmpdir
        try:
            # Delete when nothing exists
            delete_key()  # Should not raise
            delete_key()  # Should not raise again
        finally:
            del os.environ["HALO_KEYRING_DIR"]


def test_broken_keystore_is_not_reported_as_missing():
    """A keystore we can't reach must NOT read as "no key stored".

    Those are different facts with different fixes, and rendering the first as
    the second is what makes someone rotate a key that was never lost (see
    mem/Bugs.md). Runs against the real keyring path -- the file seam can't
    fail the way a vault can -- with the backend patched to blow up.
    """
    import unittest.mock

    saved = os.environ.pop("HALO_KEYRING_DIR", None)  # force the production path
    try:
        with unittest.mock.patch("keyring.get_password", side_effect=RuntimeError("vault unavailable")):
            assert not keystore_available(), "a raising backend must not report itself available"
            status = key_status()
            assert status == "invalid", f"broken keystore reported as {status!r}, must be 'invalid'"
            assert get_key() is None, "get_key must still degrade to None for value callers"
    finally:
        if saved is not None:
            os.environ["HALO_KEYRING_DIR"] = saved


if __name__ == "__main__":
    test_missing_key()
    test_set_and_get_roundtrip()
    test_set_with_whitespace_strips()
    test_set_empty_raises_valueerror()
    test_delete_key()
    test_double_delete_doesnt_raise()
    test_broken_keystore_is_not_reported_as_missing()
    print("[brain.secrets_store] self-check OK")
