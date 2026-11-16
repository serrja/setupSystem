# ChatGPT in everyday work

These are suggested texts for manual ChatGPT customization and short workflows. This file does not change account settings or connect Google Drive or Obsidian.

## 🧭 Work areas

Separate conversations by purpose: **study**, **development**, **my projects** and **everyday tasks**. Keep a useful final decision in Obsidian when it needs to be found later. Source code and working commands stay in the project's Git repository, collaborative documents in Google Docs and attachments in Drive.

## ⚙️ Custom instructions

Review the following text before pasting it into an available account-settings interface. It grants no access to files, cloud services or computer actions.

> Reply in Russian while preserving technical names and code in the project's language. Give the practical answer first, then necessary explanation and checks. When a fact depends on a current product version or external state, verify a primary source or state the uncertainty. For a study topic, explain the reasoning and offer one small way to check understanding. For code, respect existing contracts, versions and project commands; never invent test runs or data access. Do not include secrets, personal documents or private-file contents without an explicit need. Before publishing externally or changing access, show exactly what would be sent and to whom.

Create the four work areas above only if the interface supports that organization. Their names provide navigation; they do not authorize automatic reading of other projects or cloud files.

## 📚 Workflows

**Study a topic.** “Explain *[topic]* at *[level]*. Give key definitions, one worked example, common mistakes and three self-check questions. Identify what should be compared with the course material.”

**Understand code.** “Explain this fragment's purpose and data flow. List external contracts and missing context. Wait until we understand the behavior before proposing a refactor.”

**Investigate an error.** “Here are the command, expected result and sanitized traceback. Rank testable hypotheses, then suggest minimal safe diagnostic steps. Do not treat a hypothesis as confirmed without evidence.”

**Prepare a document.** “Outline a *[document type]* for *[audience]*. Separate known facts from fields that need input, and give a short accuracy checklist before publication.”

**Plan a project.** “Split *[goal]* into verifiable stages, dependencies and risks. State the first small result and acceptance criterion without inventing schedules or resources.”

**Save a conversation result.** “Create a short Obsidian note with the goal, confirmed decision, open questions, next actions and primary-source links. Omit secrets and lengthy conversation history.” Review the text, create the note in the nearest relevant vault section and link it back to that section's index.

## 🔒 Data and verification

Remove tokens, `.env` content, personal data and private addresses before pasting code or logs. An integration available in web ChatGPT is not evidence that local Codex can access it. Inspect actual permissions in each product's interface; this file is an example, not proof that a configuration was applied.
