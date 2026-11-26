# 🧩 Architecture

The Textual shell composes discovery, the authorization broker, an asynchronous Make adapter, settings, SQLAlchemy repositories and their operation journal. It discovers the reviewed distributions through Python package metadata. Backend code runs with the interpreter of its own activated environment. Each package owns its models, actions, Help, templates and native recipes. [Plugin author guide](plugin_authoring.md)

A read-only worker reports a plan bound to an immutable release, typed values and observed facts. Apply rechecks the release, observations and ownership, reserves resources and invokes a fixed trusted Make target. After native execution a worker verifies the result. Private mutation receipts and the user-visible journal serve different readers. A process interruption requires inspection before any further mutation. [Trust](plugin_trust.md) · [Recovery](recovery.md)

The package contains shared desktop style data and independently distributable plugin wheels. Normal-user file recovery uses inode-checked publication and retained before-images. Only protected package resources participate in administrative transactions. [Debian packaging](debian.md)

The public SDK separates schema definitions, protocol enums and the backend contract. Each package under `plugins/` contributes a named entry point, a manifest, a category for navigation and its own actions. The UI reads release metadata before importing a trusted UI module. Planning and execution use the activated interpreter. Workers receive bounded JSON messages and resource observations.

The host opens the database after Alembic has upgraded it. Services create independent short-lived sessions through the session factory. Reservations survive process exit. On shutdown, the application drains its jobs and disposes the engine. [Database](database.md)
