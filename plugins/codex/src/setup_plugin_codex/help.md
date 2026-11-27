# 🧩 Codex CLI

Codex CLI workstation plugin.

## 📦 Official installation

Install resolves a stable Linux release for the native architecture, verifies the archive digest from the official release and compares the binary against the digest in the publisher's hash record. Existing third-party binaries require separate review. [Official releases](https://github.com/openai/codex/releases).

## ⚙️ Configuration

Choose user settings or an explicit Git checkout. Account-visible models and effort levels come from the client's read-only stdio model list. The plugin preserves other TOML keys and only merges engineering instructions when selected. Inherit preserves existing sandbox and approval choices. The built-in `:read-only` and `:workspace` profiles can be selected explicitly. Existing custom permission tables stay in the file. [Configuration reference](https://learn.chatgpt.com/docs/config-file/config-reference) · [Codex app server](https://learn.chatgpt.com/docs/app-server).

## 🔐 Accounts

Choose Sign in for the official binary installed by this plugin. The action verifies its installed file digest before opening the native terminal and checks the client’s login status afterwards. For a separately installed binary or a session without a supported local terminal, follow the displayed command in your trusted terminal. The managed client passes the documented `cli_auth_credentials_store="keyring"` override to login, status and logout. For subsequent interactive sessions, select the same keyring mode in your Codex configuration. Setup System leaves this persistent choice to you. [Authentication](https://learn.chatgpt.com/docs/auth).

## 🧩 MCP and skills

Register one reviewed HTTPS MCP endpoint for the user or a selected checkout. It starts disabled unless Enable is selected. Configure separate authentication through the native client. Verify registration with `codex mcp list`. Choose and review each skill in the client’s skill manager. [Codex MCP configuration](https://learn.chatgpt.com/docs/mcp).
