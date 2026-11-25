# Codex CLI

## 📦 Official installation

Install resolves a stable Linux release for the native architecture, verifies the archive digest from the official release and compares the binary against the digest in the publisher's hash record. Existing third-party binaries require separate review. [Official releases](https://github.com/openai/codex/releases).

## ⚙️ Configuration

Choose user settings or an explicit Git checkout. Account-visible models and effort levels come from the client's read-only stdio model list. The plugin preserves other TOML keys and only merges engineering instructions when selected. Inherit preserves existing sandbox and approval choices. The built-in `:read-only` and `:workspace` profiles can be selected explicitly. Existing custom permission tables stay in the file. [Configuration reference](https://learn.chatgpt.com/docs/config-file/config-reference) · [Codex app server](https://learn.chatgpt.com/docs/app-server).

## 🔐 Accounts

Sign in through the native client. Its authentication files and keyring records remain under the client's control. [Authentication](https://learn.chatgpt.com/docs/auth).

## 🧩 MCP and skills

Register one reviewed HTTPS MCP endpoint for the user or a selected checkout. It starts disabled unless Enable is selected. Configure separate authentication through the native client. Verify registration with `codex mcp list`. Choose and review each skill in the client’s skill manager. [Codex MCP configuration](https://learn.chatgpt.com/docs/mcp).
