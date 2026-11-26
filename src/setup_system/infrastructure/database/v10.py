"""Carry operational evidence from the versioned normalized store into the ledger."""

import json
from uuid import UUID

from setup_system_sdk.resources import Resource
from sqlalchemy.engine import Connection


def transfer(connection: Connection) -> None:
    """Retain source rows and their foreign keys, including native-only receipt fields."""
    sql = connection.exec_driver_sql
    keys = {row.id: (row.kind, row.identity) for row in sql("SELECT * FROM resources")}
    consumers: dict[int, list[str]] = {}
    for entry in sql("SELECT * FROM resource_consumers ORDER BY observation, position"):
        consumers.setdefault(entry.observation, []).append(entry.plugin_id)
    evidence = {}
    for row in sql("SELECT * FROM observations").mappings():
        kind, identity = keys[row["resource"]]
        evidence[row["id"]] = Resource(
            kind=kind,
            identity=identity,
            consumers=tuple(consumers.get(row["id"], ())),
            **{key: value for key, value in row.items() if key not in {"id", "resource"}},
        ).model_dump_json()
    operations: dict[int, str] = {}
    for row in sql(
        "SELECT o.*, r.plugin_id, r.digest, r.version, a.operation IS NOT NULL AS pending "
        "FROM v10_operations o JOIN extension_releases r ON r.id=o.release_id "
        "LEFT JOIN active_operations a ON a.operation=o.seq"
    ).mappings():
        key = str(UUID(row["operation_id"]))
        operations[row["seq"]] = key
        phase = "unresolved" if row["pending"] or row["status"] == "running" else "verified"
        sql(
            "INSERT INTO operations VALUES(?,?,?,?,?,?,?)",
            (
                key,
                row["plugin_id"],
                row["action_id"],
                row["digest"],
                phase,
                row["outcome"],
                row["started_at"],
            ),
        )
        snapshot = json.dumps(
            {
                "revision": 10,
                "operation_id": key,
                "release": row["digest"],
                "version": row["version"],
                "source_operation": row["seq"],
                "settings": json.loads(row["settings"]),
                "inputs": json.loads(row["inputs"]),
            }
        )
        sql("INSERT INTO confirmations VALUES(?,?,?)", (key, snapshot, row["receipt"]))
    for record in sql("SELECT * FROM resource_changes"):
        kind, identity = keys[record.resource]
        phase = (
            "verified"
            if record.confirmed is not None
            else "applied"
            if record.native is not None
            else "prepared"
        )
        observation = record.confirmed or record.native or record.proposed
        sql(
            "INSERT INTO changes VALUES(?,?,?,?,?)",
            (operations[record.operation], kind, identity, phase, evidence[observation]),
        )
        if record.native is not None:
            sql(
                "INSERT INTO native_readbacks VALUES(?,?,?,?)",
                (operations[record.operation], kind, identity, evidence[record.native]),
            )
    for record in sql("SELECT * FROM ownership"):
        sql(
            "INSERT INTO holdings VALUES(?,?,?,?)",
            (*keys[record.resource], record.plugin_id, evidence[record.observation]),
        )
    for record in sql("SELECT * FROM resource_reservations"):
        sql(
            "INSERT INTO reservations VALUES(?,?,?)",
            (*keys[record.resource], operations[record.operation]),
        )
    for record in sql("SELECT * FROM program_dependencies"):
        if record.confirmed:
            sql(
                "INSERT OR IGNORE INTO requirements VALUES(?,?)", (record.consumer, record.provider)
            )
        else:
            sql(
                "INSERT INTO planned_requirements VALUES(?,?,?)",
                (operations[record.operation], record.consumer, record.provider),
            )
    for record in sql("SELECT * FROM restores"):
        sql(
            "INSERT INTO rollbacks VALUES(?,?)",
            (operations[record.operation], "verified" if record.complete else "unresolved"),
        )
