import json
import logging
from datetime import datetime
from pathlib import Path

import aiosqlite
from aiosqlite import Connection

from models import Host, Plan, Transaction

logger = logging.getLogger(__name__)


async def get_connection(path: str) -> Connection:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    connection = await aiosqlite.connect(path)
    connection.row_factory = aiosqlite.Row
    await connection.execute("PRAGMA journal_mode=WAL")
    await connection.execute("""
        CREATE TABLE IF NOT EXISTS hosts (
            host_name TEXT PRIMARY KEY,
            host_url TEXT NOT NULL,
            api_token TEXT NOT NULL,
            inbound_id INTEGER NOT NULL,
            public_hostname TEXT,
            public_url TEXT,
            additional_inbound_ids TEXT DEFAULT '[]'
        )
    """)
    await connection.execute("""
        CREATE TABLE IF NOT EXISTS plans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            host_name TEXT NOT NULL,
            plan_name TEXT NOT NULL,
            months INTEGER NOT NULL,
            price REAL NOT NULL,
            FOREIGN KEY (host_name) REFERENCES hosts (host_name)
        )
    """)
    await connection.execute("""
        CREATE TABLE IF NOT EXISTS transactions (
            id TEXT PRIMARY KEY,
            tg_id INTEGER NOT NULL,
            plan_id INTEGER NOT NULL,
            amount REAL NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    await connection.execute("""
        CREATE TABLE IF NOT EXISTS support_threads (
            id INTEGER PRIMARY KEY,
            topic_id INTEGER NOT NULL
        )
    """)
    await connection.commit()
    return connection


def _row_to_host(r: aiosqlite.Row) -> Host:
    return Host(
        host_name=r["host_name"],
        host_url=r["host_url"],
        api_token=r["api_token"],
        inbound_id=r["inbound_id"],
        public_hostname=r["public_hostname"],
        public_url=r["public_url"],
        additional_inbound_ids=json.loads(r["additional_inbound_ids"] or "[]"),
    )


async def get_hosts(connection: Connection) -> list[Host]:
    cursor = await connection.execute("SELECT * FROM hosts")
    rows = await cursor.fetchall()
    if not rows:
        raise ValueError("No hosts configured")
    return [_row_to_host(r) for r in rows]


async def get_host(host_name: str, connection: Connection) -> Host:
    cursor = await connection.execute(
        "SELECT * FROM hosts WHERE host_name = ?", (host_name,)
    )
    row = await cursor.fetchone()
    if row is None:
        raise ValueError(f"Host {host_name!r} not found")
    return _row_to_host(row)


async def add_host(host: Host, connection: Connection) -> int:
    cursor = await connection.execute(
        """INSERT INTO hosts
           (host_name, host_url, api_token, inbound_id,
            public_hostname, public_url, additional_inbound_ids)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (
            host.host_name,
            host.host_url,
            host.api_token,
            host.inbound_id,
            host.public_hostname,
            host.public_url,
            json.dumps(host.additional_inbound_ids),
        ),
    )
    await connection.commit()
    if cursor.lastrowid is None:
        raise RuntimeError(f"Could not insert host {host.host_name!r}")
    return cursor.lastrowid


async def delete_host(host_name: str, connection: Connection) -> int:
    await connection.execute("DELETE FROM plans WHERE host_name = ?", (host_name,))
    cursor = await connection.execute(
        "DELETE FROM hosts WHERE host_name = ?", (host_name,)
    )
    await connection.commit()
    if cursor.rowcount == 0:
        raise RuntimeError(f"Host {host_name!r} not found")
    return cursor.rowcount


async def get_plans(connection: Connection, host_name: str | None = None) -> list[Plan]:
    if host_name:
        cursor = await connection.execute(
            "SELECT * FROM plans WHERE host_name = ? ORDER BY price", (host_name,)
        )
    else:
        cursor = await connection.execute(
            "SELECT * FROM plans ORDER BY host_name, price"
        )
    rows = await cursor.fetchall()
    if not rows:
        raise ValueError("No plans found")
    return [
        Plan(
            id=r["id"],
            host_name=r["host_name"],
            plan_name=r["plan_name"],
            months=r["months"],
            price=r["price"],
        )
        for r in rows
    ]


async def get_plan(plan_id: int, connection: Connection) -> Plan:
    cursor = await connection.execute("SELECT * FROM plans WHERE id = ?", (plan_id,))
    row = await cursor.fetchone()
    if row is None:
        raise ValueError(f"Plan {plan_id} not found")
    return Plan(
        id=row["id"],
        host_name=row["host_name"],
        plan_name=row["plan_name"],
        months=row["months"],
        price=row["price"],
    )


async def add_plan(plan: Plan, connection: Connection) -> int:
    cursor = await connection.execute(
        "INSERT INTO plans (host_name, plan_name, months, price) VALUES (?, ?, ?, ?)",
        (plan.host_name, plan.plan_name, plan.months, plan.price),
    )
    await connection.commit()
    if cursor.lastrowid is None:
        raise RuntimeError(f"Could not insert plan {plan.plan_name!r}")
    return cursor.lastrowid


async def delete_plan(plan_id: int, connection: Connection) -> int:
    cursor = await connection.execute("DELETE FROM plans WHERE id = ?", (plan_id,))
    await connection.commit()
    if cursor.rowcount == 0:
        raise RuntimeError(f"Plan {plan_id} not found")
    return cursor.rowcount


async def create_transaction(tx: Transaction, connection: Connection) -> int:
    cursor = await connection.execute(
        "INSERT INTO transactions (id, tg_id, plan_id, amount, status) VALUES (?, ?, ?, ?, ?)",
        (tx.id, tx.tg_id, tx.plan_id, tx.amount, tx.status),
    )
    await connection.commit()
    if cursor.lastrowid is None:
        raise RuntimeError(f"Could not insert transaction {tx.id!r}")
    return cursor.lastrowid


async def find_pending_transaction(
    tg_id: int, amount: float, connection: Connection
) -> Transaction:
    cursor = await connection.execute(
        """SELECT * FROM transactions
           WHERE tg_id = ? AND status = 'pending'
           ORDER BY created_at DESC LIMIT 1""",
        (tg_id,),
    )
    row = await cursor.fetchone()
    if row is None:
        raise ValueError(f"No pending transaction for user {tg_id}")
    if abs(row["amount"] - amount) > row["amount"] * 0.10:
        raise ValueError(f"Amount mismatch: expected ~{amount}, got {row['amount']}")
    return Transaction(
        id=row["id"],
        tg_id=row["tg_id"],
        plan_id=row["plan_id"],
        amount=row["amount"],
        status=row["status"],
        created_at=datetime.fromisoformat(row["created_at"]),
    )


async def complete_transaction(transaction_id: str, connection: Connection) -> int:
    cursor = await connection.execute(
        "UPDATE transactions SET status = 'completed' WHERE id = ?", (transaction_id,)
    )
    await connection.commit()
    if cursor.rowcount == 0:
        raise RuntimeError(f"Transaction {transaction_id!r} not found")
    return cursor.rowcount


async def get_support_thread(tg_id: int, connection: Connection) -> int:
    cursor = await connection.execute(
        "SELECT topic_id FROM support_threads WHERE id = ?", (tg_id,)
    )
    row = await cursor.fetchone()
    if row is None:
        raise ValueError(f"No support thread for user {tg_id}")
    return row["topic_id"]


async def save_support_thread(tg_id: int, topic_id: int, connection: Connection) -> int:
    cursor = await connection.execute(
        "INSERT OR REPLACE INTO support_threads (id, topic_id) VALUES (?, ?)",
        (tg_id, topic_id),
    )
    await connection.commit()
    if cursor.lastrowid is None:
        raise RuntimeError(f"Could not save support thread for user {tg_id}")
    return cursor.lastrowid


async def get_user_by_thread(topic_id: int, connection: Connection) -> int:
    cursor = await connection.execute(
        "SELECT id FROM support_threads WHERE topic_id = ?", (topic_id,)
    )
    row = await cursor.fetchone()
    if row is None:
        raise ValueError(f"No user for thread {topic_id}")
    return row["id"]


async def get_stats(connection: Connection) -> dict:
    cursor = await connection.execute(
        "SELECT COUNT(*) as c FROM transactions WHERE status = 'completed'"
    )
    # TODO: Object of type "None" is not subscriptable [reportOptionalSubscript]
    transactions = (await cursor.fetchone())["c"]
    cursor = await connection.execute(
        "SELECT COALESCE(SUM(amount), 0) as s FROM transactions WHERE status = 'completed'"
    )
    # TODO: Object of type "None" is not subscriptable [reportOptionalSubscript]
    revenue = (await cursor.fetchone())["s"]
    return {"transactions": transactions, "revenue": revenue}
