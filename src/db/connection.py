"""Database connection utilities for Cloud SQL Postgres."""

import os
from contextlib import contextmanager
from typing import Generator

import pg8000
from google.cloud.sql.connector import Connector, IPTypes
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.pool import QueuePool

from src.config import PostgresConfig


def _get_conn(connector: Connector, config: PostgresConfig) -> pg8000.Connection:
    """Create a connection to Cloud SQL Postgres via the connector."""
    return connector.connect(
        config.instance_connection_name,
        "pg8000",
        user=config.user,
        password=config.password,
        db=config.db_name,
        ip_type=IPTypes.PUBLIC,  # Use PUBLIC for Cloud Run, PRIVATE for internal
    )


def get_engine(config: PostgresConfig | None = None) -> Engine:
    """
    Create a SQLAlchemy engine connected to Cloud SQL Postgres.
    
    Uses the Cloud SQL Python Connector for secure connections
    without needing to manage SSL certs or allowlist IPs.
    """
    if config is None:
        config = PostgresConfig.from_env()
    
    connector = Connector()
    
    engine = create_engine(
        "postgresql+pg8000://",
        creator=lambda: _get_conn(connector, config),
        poolclass=QueuePool,
        pool_size=5,
        max_overflow=2,
        pool_timeout=30,
        pool_recycle=1800,
    )
    
    return engine


@contextmanager
def get_db_connection(config: PostgresConfig | None = None) -> Generator:
    """
    Context manager for database connections.
    
    Usage:
        with get_db_connection() as conn:
            result = conn.execute(text("SELECT 1"))
    """
    engine = get_engine(config)
    connection = engine.connect()
    try:
        yield connection
    finally:
        connection.close()
        engine.dispose()
