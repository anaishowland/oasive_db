#!/usr/bin/env python3
"""
Run database migrations against Cloud SQL Postgres.

Usage:
    python scripts/run_migrations.py
    
Or with specific migration:
    python scripts/run_migrations.py --migration 001_fred_schema.sql
"""

import argparse
import logging
import os
import re
from pathlib import Path

from dotenv import load_dotenv
from google.cloud.sql.connector import Connector, IPTypes
import pg8000

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

MIGRATIONS_DIR = Path(__file__).parent.parent / "migrations"


def get_connection():
    """Get a pg8000 connection via Cloud SQL Connector."""
    connector = Connector()
    
    conn = connector.connect(
        os.getenv("CLOUDSQL_CONNECTION_NAME", "gen-lang-client-0343560978:us-central1:oasive-postgres"),
        "pg8000",
        user=os.getenv("POSTGRES_USER", "postgres"),
        password=os.getenv("POSTGRES_PASSWORD"),
        db=os.getenv("POSTGRES_DB", "postgres"),
        ip_type=IPTypes.PUBLIC,
    )
    return conn


def split_sql_statements(sql: str) -> list[str]:
    """
    Split SQL into executable statements.
    Handles:
    - Regular statements ending with ;
    - CREATE FUNCTION with $$ delimiters
    - Comments (removes them)
    """
    # Remove full-line comments
    lines = sql.split('\n')
    cleaned_lines = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith('--'):
            continue
        # Remove inline comments (but be careful not to break strings)
        if '--' in line and "'" not in line.split('--')[0]:
            line = line[:line.index('--')]
        cleaned_lines.append(line)
    
    cleaned_sql = '\n'.join(cleaned_lines)
    
    # State machine to split statements
    statements = []
    current_stmt = []
    in_dollar_quote = False
    
    i = 0
    while i < len(cleaned_sql):
        char = cleaned_sql[i]
        
        # Check for $$ (dollar quote start/end)
        if cleaned_sql[i:i+2] == '$$':
            current_stmt.append('$$')
            in_dollar_quote = not in_dollar_quote
            i += 2
            continue
        
        # If we're in a dollar quote, just add characters
        if in_dollar_quote:
            current_stmt.append(char)
            i += 1
            continue
        
        # Check for semicolon (statement end)
        if char == ';':
            current_stmt.append(char)
            stmt = ''.join(current_stmt).strip()
            if stmt and stmt != ';':
                statements.append(stmt)
            current_stmt = []
            i += 1
            continue
        
        current_stmt.append(char)
        i += 1
    
    # Add any remaining statement
    remaining = ''.join(current_stmt).strip()
    if remaining:
        statements.append(remaining)
    
    return statements


def run_migration(conn, migration_file: Path) -> None:
    """Execute a single migration file."""
    logger.info(f"Running migration: {migration_file.name}")
    
    sql_content = migration_file.read_text()
    statements = split_sql_statements(sql_content)
    
    logger.info(f"Found {len(statements)} statements to execute")
    
    cursor = conn.cursor()
    
    try:
        for i, stmt in enumerate(statements):
            logger.debug(f"Executing statement {i+1}: {stmt[:60]}...")
            try:
                cursor.execute(stmt)
            except Exception as e:
                logger.error(f"Error executing statement {i+1}:")
                logger.error(f"Statement: {stmt[:200]}...")
                raise
        
        conn.commit()
        logger.info(f"Completed migration: {migration_file.name}")
    except Exception as e:
        conn.rollback()
        logger.error(f"Error in migration {migration_file.name}: {e}")
        raise
    finally:
        cursor.close()


def main():
    parser = argparse.ArgumentParser(description="Run database migrations")
    parser.add_argument(
        "--migration",
        type=str,
        help="Specific migration file to run (e.g., 001_fred_schema.sql)"
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable verbose logging"
    )
    args = parser.parse_args()
    
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
    
    conn = get_connection()
    
    try:
        if args.migration:
            # Run specific migration
            migration_file = MIGRATIONS_DIR / args.migration
            if not migration_file.exists():
                logger.error(f"Migration file not found: {migration_file}")
                exit(1)
            run_migration(conn, migration_file)
        else:
            # Run all migrations in order
            migration_files = sorted(MIGRATIONS_DIR.glob("*.sql"))
            
            if not migration_files:
                logger.info("No migration files found")
                return
            
            logger.info(f"Found {len(migration_files)} migrations to run")
            
            for migration_file in migration_files:
                run_migration(conn, migration_file)
        
        logger.info("All migrations completed successfully")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
