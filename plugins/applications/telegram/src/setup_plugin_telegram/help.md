# Telegram Desktop

## 📦 Installation

Install **Snap** first and start its service. Telegram uses the verified Telegram FZ-LLC publisher and the strict latest/stable revision for the machine architecture. Preview shows signed revision identities, SHA3-384 digests and shared prerequisites. On Debian, snapd can automatically install Canonical’s system snap. The plan includes its signed revision ahead of any base or content snaps. Applying this change can restart snapd. Apply rechecks the plan, requests each revision through snapd and reads its signed local assertion. The launcher uses `/usr/bin/snap run telegram-desktop`. Existing Debian installations and independently installed snaps require separate review. [Official Telegram snap](https://snapcraft.io/telegram-desktop)

## 📦 Migrating from the Debian package

Profiles and existing launchers keep their IDs. If an earlier Telegram plugin installed the Debian package, select **Remove previously managed Debian package** and inspect the APT closure. This removes only the recorded, unchanged package and retains configuration and user files. Then choose Install for the Snap version, and preview **Save application launcher** to update each existing managed shortcut. External Debian packages require your own migration. Full cleanup can also remove the previously managed package.

## ⚙️ Preferences

Open Telegram Desktop → Settings → Add Account. Complete the native sign-in and check Active Sessions. Open Settings → Notifications and Sounds to select desktop notification behavior. [Official documentation](https://desktop.telegram.org/).

## 🧹 User data

Uninstall requires the same verified snap revision originally managed by this plugin. Close Telegram first. Snap saves and checks a manual snapshot for all users before removal. Find the retained set ID with `snap saved`; after reinstalling Telegram, restore it with `sudo snap restore SET_ID telegram-desktop`. Shared runtimes and external installations are preserved. If the installation loses its confirmation channel, inspect the exact native operation with `snap changes` and `snap tasks CHANGE_ID`. The pending receipt cannot authorize the removal of an independently installed snap. If snapd refreshed a revision independently, review the changed identity in Snap. [Snap snapshots](https://snapcraft.io/docs/how-to-guides/manage-snaps/create-data-snapshots/)

## ⚙️ Named launch profiles

Choose a normal, tray or Settings launch in this plugin’s Settings. Select Save application launcher, enter a stable ID and menu label, and review the precise desktop entry. Each ID creates a separate application-menu shortcut in the user data directory. Its fixed command uses the selected program option. The managed-file receipt supports restore and detects later edits. [Telegram Desktop launch options](https://github.com/telegramdesktop/tdesktop/blob/dev/Telegram/SourceFiles/core/launcher.cpp).
