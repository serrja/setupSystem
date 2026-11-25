"""Store a plugin-scoped secret only through the native Secret Service backend."""

import asyncio
import re
from uuid import UUID, uuid4

from keyring.backend import KeyringBackend
from keyring.backends.SecretService import Keyring
from keyring.errors import KeyringError
from pydantic import SecretStr


class SecretUnavailableError(RuntimeError):
    """The platform keyring is unavailable or locked."""


def _service(plugin: str) -> str:
    if not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", plugin):
        raise ValueError("Invalid secret namespace")
    return "setup-system/" + plugin


def _backend() -> KeyringBackend:
    try:
        implementation: type[KeyringBackend] = Keyring
        backend = implementation()
        if backend.priority <= 0:
            raise SecretUnavailableError("Unlock a Secret Service keyring")
        return backend
    except (ImportError, RuntimeError, KeyringError):
        raise SecretUnavailableError("Secret Service is unavailable") from None


async def store_secret(plugin: str, secret: SecretStr, reference: UUID | None = None) -> UUID:
    reference = reference or uuid4()

    def save() -> None:
        _backend().set_password(_service(plugin), str(reference), secret.get_secret_value())

    try:
        await asyncio.to_thread(save)
    except (KeyringError, RuntimeError):
        raise SecretUnavailableError("Could not save the secret in Secret Service") from None
    return reference


async def read_secret(plugin: str, reference: UUID) -> SecretStr:
    def read() -> str | None:
        return _backend().get_password(_service(plugin), str(reference))

    try:
        value = await asyncio.to_thread(read)
    except (KeyringError, RuntimeError):
        value = None
    if value is None:
        raise SecretUnavailableError("Unlock the system keyring or enter the secret again")
    return SecretStr(value)


async def forget_secret(plugin: str, reference: UUID) -> None:
    def delete() -> None:
        _backend().delete_password(_service(plugin), str(reference))

    try:
        await asyncio.to_thread(delete)
    except (KeyringError, RuntimeError):
        raise SecretUnavailableError("Could not remove the selected keyring entry") from None


async def secret_exists(plugin: str, reference: UUID) -> bool:
    def lookup() -> bool:
        return _backend().get_password(_service(plugin), str(reference)) is not None

    try:
        return await asyncio.to_thread(lookup)
    except (KeyringError, RuntimeError):
        raise SecretUnavailableError(
            "Unlock the system keyring before checking the reference"
        ) from None
