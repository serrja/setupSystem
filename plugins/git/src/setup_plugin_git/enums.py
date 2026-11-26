from enum import StrEnum


class GitAction(StrEnum):
    CONFIGURE_STYLE = "configure-style"
    IMPORT_IDENTITY = "import-identity"
    USE_IDENTITY = "use-identity"
    UPLOAD_KEY = "upload-key"
    VERIFY_SSH = "verify-ssh"
    REVOKE_KEY = "revoke-key"
    REMOVE_OWNED_KEYS = "remove-owned-keys"
    GENERATE_KEY = "generate-key"
    REMOVE_LOCAL_KEYS = "remove-local-keys"
