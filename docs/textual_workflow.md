# 🧩 Using the configurator

Start `setup-system` in a terminal as your desktop user. Select an existing local account if you need administrative access. Browsing, saving preferences and inspecting a plugin are available immediately.

From a text console you can prepare the system and its graphical session first. After logging into the desktop, reopen the configurator whenever you want to use a program’s account actions. GitHub CLI, Codex and Telegram each have their own sign-in flow.

Search the catalog, choose a category, then open a card. **Overview** shows the extension release, source and permissions. Activate its backend before using **Inspect program**. An already installed program retains its existing ownership.

**Settings** stores the current preferences for this extension. **Accounts** appears for plugins that declare account actions. Choose a stable action in **Actions** and review **Run parameters** when the plugin declares flags or environment entries. Preview displays the exact changes and available recovery. Switch cards to work on another draft while an action runs.

An action may ask you to finish a step in the official client. Inspect the result before continuing after a reboot, lost connection or interrupted transaction. **Managed resources** shows verified ownership and available recovery. **History** filters the application journal to the selected plugin. Open **Help** for program-specific instructions or press F1 for the offline guides. [Configuration](configuration.md) · [Recovery](recovery.md)

If validation fails, correct the field indicated by the worker and preview again. A changed input invalidates its previous confirmation. When an outcome is unknown, inspect current state and the operation history before retrying.
