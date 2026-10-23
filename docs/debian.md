# Debian: install, then configure

Use [official Debian downloads](https://www.debian.org/distrib/) and the
[installation manual](https://www.debian.org/releases/stable/installmanual).
The desktop profile targets Debian 13 (trixie), amd64 or arm64. The general installer
also supports Debian 12; that does not imply Hyprland support on Debian 12.

## Image and architecture

`uname -m` reports the running machine: x86_64 normally selects amd64, aarch64
selects arm64. When preparing another machine, check its hardware instead.
Choose the stable netinst image from [Debian CD](https://cdimage.debian.org/debian-cd/current/).
Download the ISO, `SHA512SUMS` and `SHA512SUMS.sign` from the same architecture's
`iso-cd` directory into a new empty directory. Do not substitute a third-party image.

Verify authenticity before using the image, following
[Debian's signature instructions](https://www.debian.org/CD/verify).
On an existing Debian system with `debian-keyring`:

```bash
gpgv --keyring /usr/share/keyrings/debian-role-keys.pgp SHA512SUMS.sign SHA512SUMS
sha512sum --check --ignore-missing SHA512SUMS
```

If that keyring is unavailable, retrieve the stable CD signing key in a temporary
keyring and compare the full fingerprint against the official page before trusting it:

```bash
key_dir=$(mktemp -d)
gpg --homedir "$key_dir" --keyserver hkps://keyring.debian.org --recv-keys DF9B9C49EAA9298432589D76DA87E80D6294BE9B
gpg --homedir "$key_dir" --fingerprint DF9B9C49EAA9298432589D76DA87E80D6294BE9B
gpg --homedir "$key_dir" --verify SHA512SUMS.sign SHA512SUMS
sha512sum --check --ignore-missing SHA512SUMS
```

Stop on a failed signature, fingerprint mismatch or checksum error. Confirm the
chosen ISO appears with `OK`, not just some other downloaded file. A checksum from
an unauthenticated file proves consistency, not Debian authorship. Keep the signed
checksums with the image. Remove the temporary keyring after verification if desired.

## Bootable USB — destructive manual operation

Back up the USB and identify the entire device by model, size and serial:

```bash
lsblk -o NAME,PATH,SIZE,MODEL,SERIAL,TRAN,MOUNTPOINTS
```

Unmount each mounted partition of the selected USB explicitly. Set the two values
below only after checking them; the device placeholder deliberately does not exist.
The command overwrites the selected whole device, including its partition table:

```bash
iso_path=./selected-debian-netinst.iso
usb_device=/dev/disk/by-id/usb-REPLACE_AFTER_LSBLK
test -f "$iso_path" && test -b "$usb_device"
# Run only after the preceding checks and your explicit device review:
sudo dd if="$iso_path" of="$usb_device" bs=4M status=progress conv=fsync
sync
```

These are instructions, not an automatic setupSystem step. No ISO writing,
partitioning or OS reinstallation is performed by the profile. Boot the USB from
the firmware menu, preserve recovery keys, choose storage/encryption deliberately,
create a regular user, configure networking and retain a working fallback desktop
for the first Hyprland login. Never repartition from an unattended agent run.

## Configuration

After installation, inspect `/etc/os-release`, `uname -m`, `lspci -nnk`, monitor
connectors under `/sys/class/drm` and the distro's recommended firmware/driver.
Use Debian updates/security repositories. Do not mix stable with sid or replace
system libraries. Reboot for kernel/driver changes only at a chosen maintenance time.

```bash
python3 bootstrap.py plan
python3 bootstrap.py plan --profile terminalDev
# Explicit system/user installation only after reviewing the plan:
python3 bootstrap.py apply --profile terminalDev --yes
setup-system doctor --profile terminalDev
```

For the current development CLI from its exact checkout, use
`uv run --locked setup-system --help`. This explicit command prepares the locked
project CLI environment when absent; it does not run tests or the OS installer.
It is distinct from an older globally installed command. To replace the global CLI,
back up its existing tool environment and use the existing bootstrap on supported
Debian; do not replace the system Python. See [templates](templates.md) for explicit
version selection and creation without hidden tests, commits or remote creation.

Optional desktop setup continues in [Hyprland](hyprland.md); portable font setup is
[separate](fonts.md). Ubuntu may use the portable profile only: never run Debian APT
commands there and never force the general installer past its OS guard.
