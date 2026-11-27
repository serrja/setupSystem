# 🏗️ Architecture

The Textual shell calls application services created by `bootstrap.runtime`. The host owns the database engine, HTTP client, task group, confirmation and operation handles. Workers load plugin handlers in their selected installation.

## 🧩 Plugins

The registry reads distribution metadata and resolves a compatible provider for each action requirement. A preview records the executor identities and contract versions used by the broker, the settings revision, inputs, permissions, resource preconditions and steps. A generation lease protects the environment during execution. A later compatible activation retains the previous release pointer. Recovery needs a retained receipt identifying the exact generation.

## 🔄 Execution and recovery

Apply revalidates the confirmed plan, reserves its resources and runs each handler outside the database transaction. A separate readback checks observed resources against the approved step and the applied receipt. The final transition publishes eligible changes and updates dependencies. A page requests cancellation through the operation UUID. The executor owns the safe stopping boundary for system transactions. [Database](database.md) · [Recovery](recovery.md)

## ⚙️ Configuration

The SDK provides typed worker requests, commands, capability calls, process services and file receipts. Each flat plugin package owns its settings, action models and native resources. Desktop consumers use the versioned appearance package from the greeter directory. The Python plugin owns the shared toolchain library used by its Dart/Flutter and TypeScript/Vue peers. The host grants only the worker environment fields declared for that release. Workers run with their account's OS privileges. [Plugin contracts](plugin_authoring.md) · [Plugin trust](plugin_trust.md)
