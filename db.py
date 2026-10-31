"""Acceso a SQLite. Una conexión por petición."""
import sqlite3

from flask import g

import config


def connect():
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(config.DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def get():
    if "db" not in g:
        g.db = connect()
    return g.db


def close(_exc=None):
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


def init():
    conn = connect()
    conn.executescript((config.BASE / "schema.sql").read_text(encoding="utf-8"))
    conn.commit()
    conn.close()
