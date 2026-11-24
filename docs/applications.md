# Workspace applications

Select applications or languages in **Working environment**. Preview shows the packages, current upstream selections and managed files for that run. A change between Preview and Apply requires another confirmation. The [application catalog](../config/workspace/applications.toml) lists the available sources.

Firefox ESR comes from signed Debian packages. Its enterprise policy enables strict tracking protection, HTTPS-Only mode and uBlock Origin from Mozilla. An unrelated enterprise policy requires manual review. The configurator leaves the Firefox profile and its stored credentials to the browser.

The Codex CLI profile uses the official `@openai/codex` package. The settings merge preserves an existing model, permission profile and approval policy. It adds moderate defaults for missing choices. A managed block goes into the effective global instructions file, including an existing nonempty `AGENTS.override.md`. Project actions launch Codex in the selected checkout. Authentication remains in the application. [Codex configuration reference](https://developers.openai.com/codex/config-reference/) and [AGENTS.md discovery](https://developers.openai.com/codex/guides/agents-md/).

See the [ChatGPT](chatgpt_guide.md) and [Happ](happ_guide.md) guides for their package sources and account steps. Other optional applications are off by default.
