# Applications

In Working environment, select the components to include in the next plan. The released `manifests/applications.toml` supplies display names, package requirements, adapters and supported artifact architectures.

| Selection | Delivery and verification |
| --- | --- |
| Firefox ESR | Signed Debian package, tracking protection set to Strict, HTTPS-Only and uBlock Origin from Mozilla Add-ons. Existing profiles, passwords and bookmarks stay under Firefox's control. |
| Codex CLI | Official archive pinned per architecture. Setup verifies the executable version, preserves existing model, reasoning, approval and permission choices, and merges instructions into the active user AGENTS file. |
| ChatGPT Desktop and Obsidian | Reviewed upstream Debian archives with exact SHA-256, package identity and architecture. Availability appears beside the selection when the release lacks an artifact for the machine. |
| Happ | Verified vendor Debian package and native service. Open Happ to complete account or subscription setup. |
| Git and shell tools, Telegram, Docker and LibreOffice | Selected Debian packages acquired from signed Debian sources. Docker group membership requires a separate owner decision. |
| Neovim and language profiles | Choose editor and language tools independently. See the [editor guide](neovim_guide.md). |

In Tools → Projects, select a discovered checkout to launch Codex in its exact Git root. Setup places the verified engineering contract and its pinned upstream URL in the effective global instructions file. It updates its own block in a nonempty `AGENTS.override.md` when present, otherwise in `AGENTS.md`. The local policy directory retains the contract and its upstream license. Codex loads the global instructions before the repository and directory instructions. The native client owns authentication. [Codex instructions](https://developers.openai.com/codex/guides/agents-md/) · [Pinned Codex configuration schema](https://github.com/openai/codex/blob/rust-v0.170.0/codex-rs/core/config.schema.json)

On the selected target, inspect Firefox's `about:policies` and extension list after first launch. For ChatGPT, complete the native sign-in after package verification. [Mozilla policy documentation](https://mozilla.github.io/policy-templates/) · [ChatGPT Desktop](chatgpt_guide.md)
