"""Carry operational evidence from the versioned normalized store into the ledger."""

import json
from uuid import UUID

from setup_system_sdk.enums.operation import OperationState, Outcome
from setup_system_sdk.resources import Resource
from sqlalchemy.engine import Connection

from setup_system.domain.operations import SUCCESS, OperationPhase


def _phases(connection: Connection) -> dict[str, OperationPhase]:
    rows = connection.exec_driver_sql("""
        SELECT o.operation_id, o.status, o.outcome, o.receipt,
          EXISTS(SELECT 1 FROM active_operations WHERE operation=o.seq) OR
          EXISTS(SELECT 1 FROM resource_reservations WHERE operation=o.seq) OR
          EXISTS(SELECT 1 FROM restores WHERE operation=o.seq AND NOT complete) OR
          EXISTS(SELECT 1 FROM program_dependencies
                 WHERE operation=o.seq AND NOT confirmed) AS pending,
          EXISTS(SELECT 1 FROM resource_changes WHERE operation=o.seq
                 AND (native IS NOT NULL OR confirmed IS NOT NULL)) AS effects,
          EXISTS(SELECT 1 FROM journal_events WHERE operation=o.seq
                 AND message_code='operation_reserved') AND
          EXISTS(SELECT 1 FROM resource_changes
                 WHERE operation=o.seq AND confirmed IS NULL) AS incomplete
        FROM v10_operations o
    """)
    phases = {}
    for row in rows:
        status, outcome = OperationState(row.status), Outcome(row.outcome) if row.outcome else None
        success = status is OperationState.SUCCEEDED and outcome in SUCCESS and not row.incomplete
        refused = (status is OperationState.CANCELLED and outcome is Outcome.CANCELLED) or (
            status is OperationState.FAILED and outcome in (Outcome.BLOCKED, Outcome.FAILED)
        )
        resolved = not row.pending and (
            success or (refused and not row.receipt and not row.effects)
        )
        phases[str(UUID(row.operation_id))] = (
            OperationPhase.VERIFIED if resolved else OperationPhase.UNRESOLVED
        )
    return phases


def review_converted(connection: Connection) -> None:
    """Recheck classifications made by the initial v10 importer using its retained tables."""
    _copy_approvals(connection, _observations(connection))
    for key, phase in _phases(connection).items():
        connection.exec_driver_sql(
            "UPDATE operations SET historical=1, "
            "phase=CASE WHEN phase='verified' THEN ? ELSE phase END "
            "WHERE id=? AND EXISTS(SELECT 1 FROM confirmations c WHERE c.operation=operations.id "
            "AND json_valid(c.snapshot) AND json_extract(c.snapshot,'$.revision')=10)",
            (phase.value, key),
        )


def _observations(connection: Connection) -> dict[int, str]:
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
    return evidence


def _copy_approvals(connection: Connection, evidence: dict[int, str]) -> None:
    sql = connection.exec_driver_sql
    for op in sql("SELECT seq, operation_id FROM v10_operations"):
        approved = [
            json.loads(evidence[i])
            for i in sql(
                "SELECT proposed FROM resource_changes WHERE operation=? ORDER BY position",
                (op.seq,),
            ).scalars()
        ]
        holdings = [
            json.loads(evidence[i])
            for i in sql(
                "SELECT observation FROM previous_ownership WHERE operation=? ORDER BY resource",
                (op.seq,),
            ).scalars()
        ]
        key = str(UUID(op.operation_id))
        sql(
            "UPDATE confirmations SET historical_resources=? WHERE operation=?",
            (json.dumps({"resources": approved, "holdings": holdings}), key),
        )


def transfer(connection: Connection) -> None:
    """Retain source rows and their foreign keys, including native-only receipt fields."""
    sql = connection.exec_driver_sql
    keys = {row.id: (row.kind, row.identity) for row in sql("SELECT * FROM resources")}
    evidence = _observations(connection)
    operations: dict[int, str] = {}
    phases = _phases(connection)
    for row in sql(
        "SELECT o.*, r.plugin_id, r.digest, r.version "
        "FROM v10_operations o JOIN extension_releases r ON r.id=o.release_id "
    ).mappings():
        key = str(UUID(row["operation_id"]))
        operations[row["seq"]] = key
        phase = phases[key]
        sql(
            "INSERT INTO operations(id,plugin,action,release,phase,outcome,started,historical) "
            "VALUES(?,?,?,?,?,?,?,1)",
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
        sql(
            "INSERT INTO confirmations(operation,snapshot,recovery) VALUES(?,?,?)",
            (key, snapshot, row["receipt"]),
        )
    _copy_approvals(connection, evidence)
    for record in sql("SELECT * FROM resource_changes"):
        kind, identity = keys[record.resource]
        change_phase = (
            "verified"
            if record.confirmed is not None
            else "applied"
            if record.native is not None
            else "prepared"
        )
        observation = record.confirmed or record.native or record.proposed
        sql(
            "INSERT INTO changes VALUES(?,?,?,?,?)",
            (operations[record.operation], kind, identity, change_phase, evidence[observation]),
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
