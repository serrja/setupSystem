# Workspace applications

In **Working environment**, select the applications or languages you need. **Preview** shows which packages, versions and managed files the selection would change. A change between Preview and Apply requires another confirmation. The application catalog at `/usr/lib/setup-system/config/workspace/applications.toml` lists the available sources.

## 🦊 Firefox ESR

Firefox ESR comes from signed Debian packages. Its enterprise policy enables strict tracking protection, HTTPS-Only mode and uBlock Origin from Mozilla. An unrelated enterprise policy requires manual review. The configurator leaves the Firefox profile and its stored credentials to the browser.

## 🧑‍💻 Codex CLI

The Codex CLI profile uses the official `@openai/codex` package. The settings merge preserves an existing model, permission profile and approval policy. It adds moderate defaults for missing choices. A managed block goes into the effective global instructions file, including an existing nonempty `AGENTS.override.md`. Project actions launch Codex in the selected checkout. Authentication remains in the application. [Codex configuration reference](https://developers.openai.com/codex/config-reference/) and [AGENTS.md discovery](https://developers.openai.com/codex/guides/agents-md/).

Other applications are off by default. Their guides cover package sources and signing in. [ChatGPT Desktop](chatgpt_guide.md) · [Happ](happ_guide.md)

## Compatible releases

An explicit preview resolves candidates from the approved source for the target architecture and runtime. APT uses the configured stable Debian suites. Where a plugin tracks a reviewed maintenance branch, Preview records its commit and displays that identity. A compatibility series in the editor catalog represents the supported plugin or server API. Within that series the resolver selects a stable release and records its identity for confirmation. If the source has no matching signed package or verified artifact, the plan reports the block. A change in the resolved identity requires a fresh preview. The engineering uv.lock and full Git SHAs on release workflows govern project builds.
