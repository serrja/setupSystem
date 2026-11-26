# 🔐 Plugin trust

The host validates release metadata, publisher origin and wheel digests before activation. Workers use isolated environments. Review the publisher and release digest before confirming activation. A user-level worker can exercise the permissions of its account.

A privileged handler requires an administrative execution record that matches its identity and release digest. The record specifies a protected launcher and resource digests. The installed launcher validates the approved handler and plan again. Only the authorization channel carries the secret.

## 🔐 Native fact providers

The installed executor record pins the provider distribution, version, release digest and contract version for each native handler that reads provider facts. Preview compares the selected generation against that record. The Debian launcher checks the installed action and catalog digest before revalidating the approved inputs. Update the installed executor package when its provider release differs.

An older record can still authorize a local action whose provider is evidenced by a separate confirmed step using the same protected launcher. Recovery keeps the original generation. A historical query without an identifiable provider requires independent inspection.
