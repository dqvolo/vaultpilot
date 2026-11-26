"""Encrypted local vault. Everything lives in one Fernet-encrypted file on this machine.

Key storage: OS keychain via `keyring`; falls back to a 0600 key file if no keychain exists.
"""
import json, os, stat, time
from pathlib import Path
from cryptography.fernet import Fernet

SERVICE = "vaultpilot"
# Priority when merging per-document fields into one profile (first wins).
PROFILE_ORDER = ["aadhaar", "pan", "other"]


def _home() -> Path:  # read env each call so tests can redirect it
    return Path(os.environ.get("VAULTPILOT_HOME", Path.home() / ".vaultpilot"))


def _key() -> bytes:
    _home().mkdir(parents=True, exist_ok=True)
    keyfile = _home() / "key"
    if not os.environ.get("VAULTPILOT_NO_KEYRING"):
        try:
            import keyring
            k = keyring.get_password(SERVICE, "vault-key")
            if not k:
                k = Fernet.generate_key().decode()
                keyring.set_password(SERVICE, "vault-key", k)
            return k.encode()
        except Exception:
            pass
    if not keyfile.exists():
        keyfile.write_bytes(Fernet.generate_key())
        keyfile.chmod(stat.S_IRUSR | stat.S_IWUSR)
    return keyfile.read_bytes()


def _load() -> dict:
    f = _home() / "vault.bin"
    if not f.exists():
        return {"docs": {}, "audit": []}
    return json.loads(Fernet(_key()).decrypt(f.read_bytes()))


def _save(data: dict) -> None:
    _home().mkdir(parents=True, exist_ok=True)
    (_home() / "vault.bin").write_bytes(Fernet(_key()).encrypt(json.dumps(data).encode()))


def save_doc(doc_type: str, fields: dict) -> None:
    data = _load()
    data["docs"][doc_type] = {k: v for k, v in fields.items() if v}
    _save(data)


def docs() -> dict:
    return _load()["docs"]


def profile() -> dict:
    """Flat view of the vault: one value per field."""
    merged: dict = {}
    d = docs()
    for t in PROFILE_ORDER + [t for t in d if t not in PROFILE_ORDER]:
        for k, v in d.get(t, {}).items():
            merged.setdefault(k, v)
    return merged


def log(portal: str, fields_used: list) -> None:
    """Audit entry: which fields went to which portal. Never stores the values."""
    data = _load()
    data["audit"].append({"time": time.strftime("%Y-%m-%d %H:%M:%S"),
                          "portal": portal, "fields": sorted(fields_used)})
    _save(data)


def audit() -> list:
    return _load()["audit"]


def wipe() -> None:
    (_home() / "vault.bin").unlink(missing_ok=True)
    (_home() / "key").unlink(missing_ok=True)
    try:
        import keyring
        keyring.delete_password(SERVICE, "vault-key")
    except Exception:
        pass
