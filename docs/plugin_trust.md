# Plugin trust

The host validates release metadata, publisher origin and wheel digests before activation. Workers use isolated environments. Review the publisher and release digest before confirming activation. A user-level worker can exercise the permissions of its account.

A privileged handler requires an administrative execution record that matches its identity and release digest. The record specifies a protected launcher and resource digests. The installed launcher validates the approved handler and plan again. Only the authorization channel carries the secret.
