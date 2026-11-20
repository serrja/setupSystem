# Happ

Select Happ explicitly in Textual's Working environment. It is independent of Base
system and never provides prerequisite connectivity. The reviewed upstream source and
per-architecture package hashes are in `manifests/happ.toml`.

The privileged package adapter verifies the official package digest and Debian metadata,
then applies its native helper with service-state checks. Sign in or import a subscription
through Happ's own UI after login. setupSystem does not request a subscription or read
its private account database. Preserve existing application data and routing preferences.

The native helper and GUI require a separately selected disposable Debian target test.
Package/catalog presence alone is not a working tunnel or application acceptance result.
