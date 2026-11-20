# Linux execution boundaries

The Debian-specific APT, service and login adapters own privileged operating-system
changes. Portable user configuration, projects and template use cases retain their
separate ownership. [Architecture](architecture.md) owns the complete boundary model.

Open Textual normally; only a confirmed system action invokes the finite privileged
executor. Source paths in HOME are not trusted root executable inputs. Runtime delivery
and Python builds always run under the normal account. Direct HTTPS and signed Debian
APT provide downloads; no temporary network client or subscription input exists.

See [configuration](configuration.md), [Debian support](debian.md) and
[recovery](recovery.md). Native mocks/containers do not prove real graphical installation.
