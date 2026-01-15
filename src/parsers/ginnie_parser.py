"""
Ginnie Mae Disclosure File Parser

Parses downloaded Ginnie Mae files and loads data into PostgreSQL.

File Types Supported:
- Pool/Security files (dailySFPS, monthlySFPS, nimonSFPS)
- Pool Supplemental files (dailySFS, monthlySFS, nimonSFS)  
- Loan-level files (dailyll_new, llmon1, llmon2)
- Factor files (factorA1, factorA2, factorB1, factorB2)
- Liquidation files (llmonliq)

File Layouts:
- See https://www.ginniemae.gov/data_and_reports/disclosure_data/Pages/bulk_data_download_layout.aspx
- Download layout PDFs and sample files for field definitions

Usage:
    python -m src.parsers.ginnie_parser --file-type pool
    python -m src.parsers.ginnie_parser --file-type loan
    python -m src.parsers.ginnie_parser --file-type factor
"""

import argparse
import io
import logging
import os
import re
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

import pandas as pd
from google.cloud import storage
from sqlalchemy import text

from src.config import GCSConfig, PostgresConfig
from src.db.connection import get_engine

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


class GinnieParser:
    """
    Parses Ginnie Mae disclosure files into database tables.
    
    Supports:
    - Pool-level data → dim_pool_ginnie
    - Loan-level data → dim_loan_ginnie  
    - Factor data → fact_pool_month_ginnie
    """
    
    # Batch size for database inserts
    BATCH_SIZE = 5000
    
    # File type to parser method mapping
    FILE_TYPE_PARSERS = {
        "daily_pool": "_parse_pool_file",
        "daily_pool_supp": "_parse_pool_supplemental",
        "monthly_new_pool": "_parse_pool_file",
        "monthly_new_pool_supp": "_parse_pool_supplemental",
        "portfolio_pool": "_parse_pool_file",
        "portfolio_pool_supp": "_parse_pool_supplemental",
        "daily_loan": "_parse_loan_file",
        "monthly_new_loan": "_parse_loan_file",
        "portfolio_loan_g1": "_parse_loan_file",
        "portfolio_loan_g2": "_parse_loan_file",
        "factor_a1": "_parse_factor_file",
        "factor_a2": "_parse_factor_file",
        "factor_b1": "_parse_factor_file",
        "factor_b2": "_parse_factor_file",
        "liquidations": "_parse_liquidation_file",
    }
    
    def __init__(
        self,
        postgres_config: PostgresConfig | None = None,
        gcs_config: GCSConfig | None = None,
    ):
        self.postgres_config = postgres_config or PostgresConfig.from_env()
        self.gcs_config = gcs_config or GCSConfig.from_env()
        
        self.engine = get_engine(self.postgres_config)
        self.storage_client = storage.Client(project=self.gcs_config.project_id)
    
    def get_files_to_parse(self, file_type: str | None = None) -> list[dict]:
        """Get downloaded files that haven't been parsed yet."""
        with self.engine.connect() as conn:
            query = """
                SELECT filename, file_type, local_gcs_path, file_date
                FROM ginnie_file_catalog
                WHERE download_status = 'downloaded'
                  AND processed_at IS NULL
            """
            params = {}
            
            if file_type:
                query += " AND file_type = :file_type"
                params["file_type"] = file_type
            
            query += " ORDER BY file_date DESC NULLS LAST"
            
            result = conn.execute(text(query), params)
            return [dict(row._mapping) for row in result]
    
    def download_from_gcs(self, gcs_path: str) -> str:
        """Download file from GCS to temp directory."""
        # Parse gs:// path
        if gcs_path.startswith("gs://"):
            gcs_path = gcs_path[5:]
        
        bucket_name, blob_path = gcs_path.split("/", 1)
        
        bucket = self.storage_client.bucket(bucket_name)
        blob = bucket.blob(blob_path)
        
        # Download to temp file
        suffix = Path(blob_path).suffix
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            blob.download_to_filename(tmp.name)
            return tmp.name
    
    def _extract_zip(self, zip_path: str) -> list[str]:
        """Extract ZIP file and return list of extracted file paths."""
        extract_dir = tempfile.mkdtemp()
        
        with zipfile.ZipFile(zip_path, 'r') as z:
            z.extractall(extract_dir)
        
        # Return all extracted files
        extracted = []
        for root, dirs, files in os.walk(extract_dir):
            for f in files:
                extracted.append(os.path.join(root, f))
        
        return extracted
    
    def _parse_pool_file(self, file_path: str, source_file: str) -> int:
        """
        Parse pool/security file into dim_pool_ginnie.
        
        TODO: Update column mapping based on actual file layout
        This is a placeholder - need to inspect actual file format.
        """
        logger.info(f"Parsing pool file: {source_file}")
        
        # Try to read as fixed-width or delimited
        try:
            # Most Ginnie files are pipe-delimited
            df = pd.read_csv(file_path, sep='|', dtype=str, low_memory=False)
        except Exception:
            try:
                # Try comma-delimited
                df = pd.read_csv(file_path, dtype=str, low_memory=False)
            except Exception as e:
                logger.error(f"Could not parse file: {e}")
                return 0
        
        if df.empty:
            logger.warning("File is empty")
            return 0
        
        logger.info(f"Read {len(df)} rows with columns: {list(df.columns)[:10]}...")
        
        # TODO: Map columns to dim_pool_ginnie schema
        # This requires inspecting the actual file layout
        # For now, just log what we found
        
        records_inserted = 0
        
        # Placeholder: Will implement actual parsing once we have file layout
        logger.warning("Pool parsing not yet implemented - need file layout specs")
        
        return records_inserted
    
    def _parse_pool_supplemental(self, file_path: str, source_file: str) -> int:
        """Parse pool supplemental file."""
        logger.info(f"Parsing pool supplemental: {source_file}")
        
        # Similar to pool file but with extended attributes
        # TODO: Implement based on file layout
        
        return 0
    
    def _parse_loan_file(self, file_path: str, source_file: str) -> int:
        """
        Parse loan-level file into dim_loan_ginnie.
        
        TODO: Update column mapping based on actual file layout
        """
        logger.info(f"Parsing loan file: {source_file}")
        
        try:
            df = pd.read_csv(file_path, sep='|', dtype=str, low_memory=False)
        except Exception:
            try:
                df = pd.read_csv(file_path, dtype=str, low_memory=False)
            except Exception as e:
                logger.error(f"Could not parse file: {e}")
                return 0
        
        if df.empty:
            logger.warning("File is empty")
            return 0
        
        logger.info(f"Read {len(df)} rows with columns: {list(df.columns)[:10]}...")
        
        # TODO: Map columns to dim_loan_ginnie schema
        
        return 0
    
    def _parse_factor_file(self, file_path: str, source_file: str) -> int:
        """
        Parse factor file into fact_pool_month_ginnie.
        
        Factor files contain monthly prepayment/factor data.
        """
        logger.info(f"Parsing factor file: {source_file}")
        
        try:
            df = pd.read_csv(file_path, sep='|', dtype=str, low_memory=False)
        except Exception:
            try:
                df = pd.read_csv(file_path, dtype=str, low_memory=False)
            except Exception as e:
                logger.error(f"Could not parse file: {e}")
                return 0
        
        if df.empty:
            logger.warning("File is empty")
            return 0
        
        logger.info(f"Read {len(df)} rows with columns: {list(df.columns)[:10]}...")
        
        # Extract date from filename
        match = re.search(r"(\d{6})", source_file)
        as_of_date = None
        if match:
            try:
                date_str = match.group(1)
                as_of_date = datetime(int(date_str[:4]), int(date_str[4:6]), 1).date()
            except ValueError:
                pass
        
        # TODO: Map columns to fact_pool_month_ginnie schema
        
        return 0
    
    def _parse_liquidation_file(self, file_path: str, source_file: str) -> int:
        """Parse loan liquidation file."""
        logger.info(f"Parsing liquidation file: {source_file}")
        
        # TODO: Implement based on file layout
        
        return 0
    
    def update_catalog_processed(self, filename: str, records: int) -> None:
        """Mark file as processed in catalog."""
        with self.engine.connect() as conn:
            conn.execute(
                text("""
                    UPDATE ginnie_file_catalog
                    SET processed_at = NOW(),
                        download_status = 'processed',
                        updated_at = NOW()
                    WHERE filename = :filename
                """),
                {"filename": filename}
            )
            conn.commit()
    
    def update_catalog_error(self, filename: str, error: str) -> None:
        """Mark file as error in catalog."""
        with self.engine.connect() as conn:
            conn.execute(
                text("""
                    UPDATE ginnie_file_catalog
                    SET download_status = 'error',
                        error_message = :error,
                        updated_at = NOW()
                    WHERE filename = :filename
                """),
                {"filename": filename, "error": error[:500]}
            )
            conn.commit()
    
    def parse_file(self, file_info: dict) -> int:
        """Parse a single file."""
        filename = file_info["filename"]
        file_type = file_info["file_type"]
        gcs_path = file_info["local_gcs_path"]
        
        logger.info(f"Processing {filename} (type={file_type})")
        
        # Get parser method
        parser_method_name = self.FILE_TYPE_PARSERS.get(file_type)
        if not parser_method_name:
            logger.warning(f"No parser for file type: {file_type}")
            return 0
        
        parser_method = getattr(self, parser_method_name)
        
        try:
            # Download from GCS
            local_path = self.download_from_gcs(gcs_path)
            
            try:
                # Extract if ZIP
                if local_path.endswith(".zip"):
                    extracted_files = self._extract_zip(local_path)
                    os.unlink(local_path)
                    
                    total_records = 0
                    for extracted_path in extracted_files:
                        records = parser_method(extracted_path, filename)
                        total_records += records
                        os.unlink(extracted_path)
                    
                    return total_records
                else:
                    records = parser_method(local_path, filename)
                    os.unlink(local_path)
                    return records
                    
            finally:
                # Clean up temp files
                if os.path.exists(local_path):
                    os.unlink(local_path)
                    
        except Exception as e:
            logger.error(f"Error parsing {filename}: {e}")
            raise
    
    def run(
        self,
        file_type: str | None = None,
        limit: int | None = None,
    ) -> dict[str, Any]:
        """
        Run parser on downloaded files.
        
        Args:
            file_type: Filter to specific file type
            limit: Maximum files to process
        
        Returns:
            Summary dictionary
        """
        logger.info(f"Starting Ginnie Mae parser (file_type={file_type})")
        
        results = {
            "files_processed": 0,
            "records_inserted": 0,
            "errors": [],
        }
        
        files = self.get_files_to_parse(file_type)
        
        if limit:
            files = files[:limit]
        
        logger.info(f"Found {len(files)} files to parse")
        
        for file_info in files:
            filename = file_info["filename"]
            
            try:
                records = self.parse_file(file_info)
                self.update_catalog_processed(filename, records)
                results["files_processed"] += 1
                results["records_inserted"] += records
                logger.info(f"Processed {filename}: {records} records")
                
            except Exception as e:
                error_msg = f"Error parsing {filename}: {e}"
                logger.error(error_msg)
                results["errors"].append(error_msg)
                self.update_catalog_error(filename, str(e))
        
        logger.info(
            f"Parser complete: {results['files_processed']} files, "
            f"{results['records_inserted']} records, "
            f"{len(results['errors'])} errors"
        )
        
        return results


def main():
    """Entry point for Cloud Run job."""
    parser = argparse.ArgumentParser(description="Ginnie Mae File Parser")
    parser.add_argument(
        "--file-type",
        help="Filter to specific file type (e.g., daily_pool, factor_a1)"
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="Maximum files to process"
    )
    
    args = parser.parse_args()
    
    parser_instance = GinnieParser()
    
    results = parser_instance.run(
        file_type=args.file_type,
        limit=args.limit,
    )
    
    if results["errors"]:
        logger.warning(f"Completed with {len(results['errors'])} errors")
        exit(1)
    
    logger.info("Ginnie Mae parser completed successfully")


if __name__ == "__main__":
    main()
