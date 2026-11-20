"""Pure conservative package removal policy."""

CLEANUP_CANDIDATES = frozenset(
    {"nano", "vim-tiny", "reportbug", "popularity-contest", "debian-faq", "doc-debian"}
)
PROTECTED_PACKAGES = frozenset(
    {
        "apt",
        "dpkg",
        "sudo",
        "systemd",
        "systemd-sysv",
        "openssh-server",
        "openssh-client",
        "cryptsetup",
        "cryptsetup-initramfs",
        "initramfs-tools",
        "grub-common",
        "grub-pc",
        "grub-efi-amd64",
        "grub-efi-arm64",
        "linux-image-amd64",
        "linux-image-arm64",
        "network-manager",
        "iproute2",
        "hyprland",
        "greetd",
        "neovim",
        "python3",
    }
)


def validate_removals(
    requested: frozenset[str],
    removed: frozenset[str],
    essential: frozenset[str],
    *,
    editor_verified: bool,
) -> None:
    """Reject every unexpected dependency removal, including seemingly optional ones."""
    if not requested or not requested <= CLEANUP_CANDIDATES:
        raise ValueError("Only explicitly selected cleanup candidates are allowed")
    if removed != requested:
        raise ValueError("APT removal closure changed; review a new plan")
    if removed & (PROTECTED_PACKAGES | essential):
        raise ValueError("Essential, Protected or workstation package removal is forbidden")
    if removed & {"nano", "vim-tiny"} and not editor_verified:
        raise ValueError("A working Neovim fallback and EDITOR/VISUAL must be verified first")
