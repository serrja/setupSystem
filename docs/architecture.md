# 🧩 Architecture

The Textual shell composes discovery, the authorization broker, an asynchronous Make adapter, settings, the resource ledger and the structured journal. It discovers the reviewed distributions through Python package metadata. Backend code runs with the interpreter of its own activated environment. Each package owns its models, actions, Help, templates and native recipes. [Plugin author guide](plugin_authoring.md)

A read-only worker reports a plan bound to an immutable release, typed values and observed facts. Apply rechecks the release, observations and ownership, reserves resources and invokes a fixed trusted Make target. After native execution a worker verifies the result. Private mutation receipts and the user-visible journal serve different readers. A process interruption requires inspection before any further mutation. [Trust](plugin_trust.md) · [Recovery](recovery.md)

The package contains shared desktop style data and independently distributable plugin wheels. Normal-user file recovery uses inode-checked publication and retained before-images. Only protected package resources participate in administrative transactions. [Debian packaging](debian.md)
