"""
FRED Data Ingestor

Fetches economic indicator data from the FRED API and stores it in Postgres.
Designed to run as a Cloud Run job on a daily schedule.

FRED API Docs: https://fred.stlouisfed.org/docs/api/fred/
"""

import json
import logging
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

import requests
from sqlalchemy import text
from tenacity import retry, stop_after_attempt, wait_exponential

from src.config import FREDConfig, PostgresConfig
from src.db.connection import get_engine

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


class FREDIngestor:
    """Handles fetching and storing FRED economic data."""
    
    OBSERVATIONS_URL = "https://api.stlouisfed.org/fred/series/observations"
    
    def __init__(
        self,
        fred_config: FREDConfig | None = None,
        postgres_config: PostgresConfig | None = None,
    ):
        self.fred_config = fred_config or FREDConfig.from_env()
        self.postgres_config = postgres_config or PostgresConfig.from_env()
        self.engine = get_engine(self.postgres_config)
        
        if not self.fred_config.api_key:
            raise ValueError("FRED_API_KEY is required")
    
    def get_active_series(self) -> list[dict[str, Any]]:
        """Fetch list of active FRED series from the database."""
        with self.engine.connect() as conn:
            result = conn.execute(text("""
                SELECT series_id, name, frequency 
                FROM fred_series 
                WHERE is_active = TRUE
                ORDER BY series_id
            """))
            return [dict(row._mapping) for row in result]
    
    def get_latest_obs_date(self, series_id: str) -> datetime | None:
        """Get the most recent observation date for a series."""
        with self.engine.connect() as conn:
            result = conn.execute(
                text("""
                    SELECT MAX(obs_date) as latest_date 
                    FROM fred_observation 
                    WHERE series_id = :series_id
                """),
                {"series_id": series_id}
            )
            row = result.fetchone()
            return row.latest_date if row and row.latest_date else None
    
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
    )
    def fetch_observations(
        self,
        series_id: str,
        observation_start: str | None = None,
    ) -> list[dict[str, Any]]:
        """
        Fetch observations from FRED API.
        
        Args:
            series_id: FRED series identifier (e.g., "UNRATE")
            observation_start: Start date in YYYY-MM-DD format (optional)
        
        Returns:
            List of observation dictionaries with 'date' and 'value' keys
        """
        params = {
            "series_id": series_id,
            "api_key": self.fred_config.api_key,
            "file_type": "json",
        }
        
        if observation_start:
            params["observation_start"] = observation_start
        
        logger.info(f"Fetching FRED data for {series_id} starting from {observation_start or 'beginning'}")
        
        response = requests.get(self.OBSERVATIONS_URL, params=params, timeout=30)
        response.raise_for_status()
        
        data = response.json()
        observations = data.get("observations", [])
        
        logger.info(f"Fetched {len(observations)} observations for {series_id}")
        return observations
    
    def _parse_value(self, value_str: str) -> Decimal | None:
        """Parse FRED value string to Decimal, handling missing data markers."""
        if value_str in (".", "", None):
            return None
        try:
            return Decimal(value_str)
        except InvalidOperation:
            logger.warning(f"Could not parse value: {value_str}")
            return None
    
    def insert_observations(
        self,
        series_id: str,
        observations: list[dict[str, Any]],
    ) -> int:
        """
        Insert observations using optimized bulk INSERT.
        
        Uses large batch multi-row INSERT for speed (~10x faster than row-by-row).
        """
        if not observations:
            return 0
        
        import time
        start_time = time.time()
        logger.info(f"Inserting {len(observations)} observations for {series_id}...")
        
        # Prepare all rows - escape single quotes for SQL
        def escape_sql(s: str) -> str:
            return s.replace("'", "''")
        
        rows = []
        for obs in observations:
            value = self._parse_value(obs.get("value"))
            rows.append({
                "series_id": series_id,
                "obs_date": obs["date"],
                "value": float(value) if value is not None else None,
                "vintage_date": "0001-01-01",
                "raw_payload": escape_sql(json.dumps(obs)),
            })
        
        # Use raw connection for bulk insert
        with self.engine.connect() as conn:
            raw_conn = conn.connection.dbapi_connection
            cursor = raw_conn.cursor()
            
            # Large batch size for fewer round-trips (500 rows per INSERT)
            batch_size = 500
            inserted = 0
            total_batches = (len(rows) + batch_size - 1) // batch_size
            
            for batch_num, i in enumerate(range(0, len(rows), batch_size), 1):
                batch = rows[i:i + batch_size]
                
                # Build multi-row VALUES clause
                values_list = []
                for r in batch:
                    val = r['value'] if r['value'] is not None else 'NULL'
                    values_list.append(
                        f"('{r['series_id']}', '{r['obs_date']}', {val}, "
                        f"'{r['vintage_date']}', '{r['raw_payload']}'::jsonb)"
                    )
                
                sql = f"""
                    INSERT INTO fred_observation (series_id, obs_date, value, vintage_date, raw_payload)
                    VALUES {', '.join(values_list)}
                    ON CONFLICT (series_id, obs_date, vintage_date) DO NOTHING
                """
                
                try:
                    cursor.execute(sql)
                    inserted += len(batch)
                    if batch_num % 2 == 0 or batch_num == total_batches:
                        logger.debug(f"  Batch {batch_num}/{total_batches} complete")
                except Exception as e:
                    logger.error(f"Batch {batch_num} insert error: {e}")
                    # Log first few chars of SQL for debugging
                    logger.debug(f"Failed SQL (first 200 chars): {sql[:200]}")
            
            raw_conn.commit()
            cursor.close()
        
        elapsed = time.time() - start_time
        logger.info(f"Inserted {inserted} observations for {series_id} in {elapsed:.1f}s")
        return inserted
    
    def log_ingest_run(
        self,
        series_id: str,
        status: str,
        rows_inserted: int = 0,
        error_message: str | None = None,
        run_started_at: datetime | None = None,
    ) -> None:
        """Log an ingestion run for audit purposes."""
        with self.engine.connect() as conn:
            conn.execute(
                text("""
                    INSERT INTO fred_ingest_log 
                    (series_id, run_started_at, run_completed_at, status, rows_inserted, error_message)
                    VALUES (:series_id, :run_started_at, :run_completed_at, :status, :rows_inserted, :error_message)
                """),
                {
                    "series_id": series_id,
                    "run_started_at": run_started_at or datetime.utcnow(),
                    "run_completed_at": datetime.utcnow(),
                    "status": status,
                    "rows_inserted": rows_inserted,
                    "error_message": error_message,
                }
            )
            conn.commit()
    
    def ingest_series(self, series_id: str) -> tuple[int, str]:
        """
        Ingest data for a single series.
        
        Returns:
            Tuple of (rows_inserted, status)
        """
        run_started_at = datetime.utcnow()
        
        try:
            # Get the latest observation date to do incremental fetch
            latest_date = self.get_latest_obs_date(series_id)
            
            if latest_date:
                # Start from the day after the latest observation
                observation_start = (latest_date + timedelta(days=1)).strftime("%Y-%m-%d")
            else:
                # First run - fetch all historical data
                observation_start = None
            
            # Fetch from FRED API
            observations = self.fetch_observations(series_id, observation_start)
            
            # Insert into database
            rows_inserted = self.insert_observations(series_id, observations)
            
            # Log success
            self.log_ingest_run(
                series_id=series_id,
                status="success",
                rows_inserted=rows_inserted,
                run_started_at=run_started_at,
            )
            
            logger.info(f"Successfully ingested {rows_inserted} rows for {series_id}")
            return rows_inserted, "success"
            
        except Exception as e:
            error_msg = str(e)
            logger.error(f"Error ingesting {series_id}: {error_msg}")
            
            self.log_ingest_run(
                series_id=series_id,
                status="error",
                error_message=error_msg,
                run_started_at=run_started_at,
            )
            
            return 0, f"error: {error_msg}"
    
    def run(self, series_ids: list[str] | None = None) -> dict[str, Any]:
        """
        Run the full ingestion job.
        
        Args:
            series_ids: Optional list of specific series to ingest.
                       If None, ingests all active series.
        
        Returns:
            Summary dictionary with results
        """
        logger.info("Starting FRED ingestion job")
        
        if series_ids:
            series_list = [{"series_id": s} for s in series_ids]
        else:
            series_list = self.get_active_series()
        
        logger.info(f"Processing {len(series_list)} series")
        
        results = {
            "total_series": len(series_list),
            "successful": 0,
            "failed": 0,
            "total_rows": 0,
            "details": [],
        }
        
        for series in series_list:
            series_id = series["series_id"]
            rows, status = self.ingest_series(series_id)
            
            if status == "success":
                results["successful"] += 1
                results["total_rows"] += rows
            else:
                results["failed"] += 1
            
            results["details"].append({
                "series_id": series_id,
                "rows_inserted": rows,
                "status": status,
            })
        
        logger.info(
            f"FRED ingestion complete: {results['successful']}/{results['total_series']} "
            f"series successful, {results['total_rows']} total rows"
        )
        
        return results


def main():
    """Entry point for Cloud Run job."""
    ingestor = FREDIngestor()
    results = ingestor.run()
    
    # Only exit with error if ALL series failed (total failure)
    if results["successful"] == 0 and results["total_series"] > 0:
        logger.error("FRED ingestion failed: no series were successfully ingested")
        exit(1)
    
    if results["failed"] > 0:
        logger.warning(f"{results['failed']} series failed to ingest (partial success)")
    
    logger.info("FRED ingestion job completed successfully")


if __name__ == "__main__":
    main()
