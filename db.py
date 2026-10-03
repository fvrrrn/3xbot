import logging
from datetime import datetime
from pathlib import Path

import aiosqlite
from aiosqlite import Connection

from models import Transaction

logger = logging.getLogger(__name__)


async def get_connection(path: str) -> Connection:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    connection = await aiosqlite.connect(path)
    connection.row_factory = aiosqlite.Row
    await connection.execute("PRAGMA journal_mode=WAL")
    await connection.execute("""
        CREATE TABLE IF NOT EXISTS transactions (
            id TEXT PRIMARY KEY,
            tg_id INTEGER NOT NULL,
            months INTEGER NOT NULL,
            amount REAL NOT NULL,
            message_id INTEGER,
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


async def create_transaction(tx: Transaction, connection: Connection) -> int:
    await connection.execute(
        "UPDATE transactions SET status = 'cancelled' WHERE tg_id = ? AND status = 'pending'",
        (tx.tg_id,),
    )
    cursor = await connection.execute(
        """INSERT INTO transactions (id, tg_id, months, amount, message_id, status)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (tx.id, tx.tg_id, tx.months, tx.amount, tx.message_id, tx.status),
    )
    await connection.commit()
    if cursor.lastrowid is None:
        raise RuntimeError(f"Could not insert transaction {tx.id!r}")
    return cursor.lastrowid


async def get_pending_transaction(
    transaction_id: str, connection: Connection
) -> Transaction:
    cursor = await connection.execute(
        "SELECT * FROM transactions WHERE id = ? AND status = 'pending'",
        (transaction_id,),
    )
    row = await cursor.fetchone()
    if row is None:
        raise ValueError(f"No pending transaction {transaction_id!r}")
    return Transaction(
        id=row["id"],
        tg_id=row["tg_id"],
        months=row["months"],
        amount=row["amount"],
        message_id=row["message_id"],
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
    row = await cursor.fetchone()
    transactions = row["c"] if row else 0
    cursor = await connection.execute(
        "SELECT COALESCE(SUM(amount), 0) as s FROM transactions WHERE status = 'completed'"
    )
    row = await cursor.fetchone()
    revenue = row["s"] if row else 0.0
    return {"transactions": transactions, "revenue": revenue}
