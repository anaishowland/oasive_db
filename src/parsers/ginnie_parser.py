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
    
    Layout Versions (Loan-Level Files):
    - V1.0 (Oct 2013): L record = 142 bytes
    - V1.6 (Apr 2015): L record = 154 bytes (added Loan Origination Date, Seller Issuer ID)
    - V1.7 (Dec 2017): L record = 192 bytes (added 10 ARM fields)
    - V1.8 (Feb 2021): Same layout, added Loan Purpose "5" for Re-Performing
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
    
    # Loan-Level (L record) field definitions by version
    # Format: (start, end, field_name, data_type)
    # Positions are 0-indexed after the record type indicator
    LOAN_FIELDS_V10 = [
        # V1.0: 142 bytes total (Oct 2013 - Mar 2015)
        (0, 6, "cusip", "str"),            # CUSIP (6 chars)
        (6, 16, "loan_id", "str"),          # Loan Sequence Number (10 chars)
        (16, 20, "current_upb", "decimal"), # Current UPB (4 digits implied decimal)
        (20, 21, "loan_purpose", "str"),    # Loan Purpose (1 char: P=Purchase, R=Refi, etc.)
        (21, 23, "property_type", "str"),   # Property Type (2 chars)
        (23, 31, "first_payment_date", "date"),  # First Payment Date (YYYYMMDD)
        (31, 39, "maturity_date", "date"),       # Maturity Date (YYYYMMDD)
        (39, 44, "original_interest_rate", "rate"),  # Original Interest Rate (5: XXX.XX)
        (44, 55, "original_upb", "decimal"),     # Original UPB (11 digits)
        (55, 66, "scheduled_principal", "decimal"),  # Scheduled Principal
        (66, 78, "current_balance", "decimal"),  # Current Balance
        (78, 81, "months_to_maturity", "int"),   # Remaining Term
        (81, 84, "loan_age", "int"),             # Loan Age
        (84, 86, "state", "str"),                # State (2 chars)
        (86, 91, "current_interest_rate", "rate"),  # Current Interest Rate
        (91, 92, "first_time_buyer", "str"),     # First Time Buyer Flag
        (92, 93, "channel", "str"),              # Origination Channel
        (93, 94, "occupancy", "str"),            # Occupancy Status
        (94, 97, "credit_score", "int"),         # Credit Score
        (97, 103, "dti", "decimal"),             # DTI Ratio
        (103, 109, "ltv", "decimal"),            # LTV Ratio
        (109, 115, "cltv", "decimal"),           # CLTV Ratio
        (115, 116, "num_borrowers", "int"),      # Number of Borrowers
        (116, 118, "num_units", "int"),          # Number of Units
        (118, 123, "zip_3", "str"),              # ZIP Code (3-digit)
        (123, 124, "mortgage_insurance_pct", "int"),  # MI Percentage
        (124, 125, "loan_status", "str"),        # Loan Status
        (125, 131, "delinquency_status", "str"), # Delinquency Status
        (131, 137, "mod_flag", "str"),           # Modification Flag
        (137, 141, "report_period", "date"),     # Report Period (YYYYMM)
    ]
    
    LOAN_FIELDS_V16_ADDITIONS = [
        # V1.6 adds 12 bytes (Apr 2015 - Nov 2017): Total 154 bytes
        (141, 149, "loan_origination_date", "date"),  # Loan Origination Date (YYYYMMDD)
        (149, 153, "seller_issuer_id", "str"),        # Seller Issuer ID (4 chars)
    ]
    
    LOAN_FIELDS_V17_ADDITIONS = [
        # V1.7 adds ARM fields (Dec 2017+): Total 192 bytes
        (153, 155, "index_type", "str"),              # Index Type (2 chars)
        (155, 157, "look_back_period", "int"),        # Look-Back Period
        (157, 165, "interest_rate_change_date", "date"),  # Interest Rate Change Date
        (165, 170, "initial_rate_cap", "rate"),       # Initial Interest Rate Cap
        (170, 175, "subsequent_rate_cap", "rate"),    # Subsequent Interest Rate Cap
        (175, 180, "lifetime_rate_cap", "rate"),      # Lifetime Interest Rate Cap
        (180, 185, "next_rate_ceiling", "rate"),      # Next Interest Rate Change Ceiling
        (185, 190, "lifetime_rate_ceiling", "rate"),  # Lifetime Interest Rate Ceiling
        (190, 195, "lifetime_rate_floor", "rate"),    # Lifetime Interest Rate Floor
        (195, 200, "prospective_rate", "rate"),       # Prospective Interest Rate
    ]
    
    # Pool (P record) field definitions
    POOL_FIELDS = [
        (0, 7, "pool_number", "str"),       # Pool Number (7 chars including suffix)
        (7, 16, "cusip", "str"),            # CUSIP (9 chars)
        (16, 18, "pool_type", "str"),       # Pool Type (2 chars: SF, AR, etc.)
        (18, 24, "issue_date", "date"),     # Issue Date (YYYYMM)
        (24, 30, "original_term", "int"),   # Original Term
        (30, 36, "report_period", "date"),  # Report Period (YYYYMM)
    ]
    
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
    
    def _get_file_date(self, source_file: str) -> tuple[int, int]:
        """Extract YYYYMM from filename."""
        match = re.search(r"(\d{6})", source_file)
        if match:
            date_str = match.group(1)
            return int(date_str[:4]), int(date_str[4:6])
        return 0, 0
    
    def _get_loan_version(self, year: int, month: int) -> str:
        """Determine loan file layout version from date."""
        if (year, month) < (2015, 4):
            return "V1.0"  # 142 bytes
        elif (year, month) < (2017, 12):
            return "V1.6"  # 154 bytes
        else:
            return "V1.7"  # 192 bytes
    
    def _get_loan_fields(self, version: str) -> list:
        """Get field definitions for a given version."""
        fields = list(self.LOAN_FIELDS_V10)
        if version in ("V1.6", "V1.7"):
            fields.extend(self.LOAN_FIELDS_V16_ADDITIONS)
        if version == "V1.7":
            fields.extend(self.LOAN_FIELDS_V17_ADDITIONS)
        return fields
    
    def _parse_loan_record(self, line: str, fields: list, pool_number: str) -> dict | None:
        """Parse a single L (loan) record."""
        if not line or line[0] != 'L':
            return None
        
        record = {"pool_number": pool_number}
        content = line[1:]  # Skip record type indicator
        
        for start, end, field_name, data_type in fields:
            if end > len(content):
                # Field not present in this version
                continue
            
            raw_value = content[start:end].strip()
            
            if not raw_value:
                record[field_name] = None
                continue
            
            try:
                if data_type == "str":
                    record[field_name] = raw_value
                elif data_type == "int":
                    record[field_name] = int(raw_value) if raw_value else None
                elif data_type == "decimal":
                    # Handle implied decimal (e.g., "12345" -> 123.45)
                    record[field_name] = float(raw_value) / 100 if raw_value else None
                elif data_type == "rate":
                    # Rate fields (e.g., "05250" -> 5.250)
                    record[field_name] = float(raw_value) / 1000 if raw_value else None
                elif data_type == "date":
                    # Date fields (YYYYMMDD or YYYYMM)
                    record[field_name] = raw_value
                else:
                    record[field_name] = raw_value
            except (ValueError, TypeError):
                record[field_name] = None
        
        return record
    
    def _parse_loan_file(self, file_path: str, source_file: str) -> int:
        """
        Parse loan-level file (llmon1, llmon2, dailyllmni) into database.
        
        File format: Fixed-width text with record types:
        - H: Header (41 bytes)
        - P: Pool info (37 bytes)
        - L: Loan detail (142-192 bytes depending on version)
        - T: Trailer/totals (44 bytes)
        
        Structure: H, then for each pool: P, L*, T
        """
        logger.info(f"Parsing loan file: {source_file}")
        
        # Determine version from filename date
        year, month = self._get_file_date(source_file)
        version = self._get_loan_version(year, month)
        fields = self._get_loan_fields(version)
        
        logger.info(f"Using layout version {version} for {year}-{month:02d}")
        
        records = []
        current_pool = None
        total_loans = 0
        
        try:
            with open(file_path, 'r', encoding='latin-1') as f:
                for line_num, line in enumerate(f, 1):
                    line = line.rstrip('\n\r')
                    
                    if not line:
                        continue
                    
                    record_type = line[0]
                    
                    if record_type == 'H':
                        # Header record - extract file info
                        logger.debug(f"Header: {line[:40]}...")
                        
                    elif record_type == 'P':
                        # Pool record - extract pool number
                        current_pool = line[1:8] if len(line) > 8 else None
                        
                    elif record_type == 'L':
                        # Loan record
                        if current_pool:
                            loan = self._parse_loan_record(line, fields, current_pool)
                            if loan:
                                loan["file_date"] = f"{year}-{month:02d}-01"
                                loan["source_file"] = source_file
                                loan["layout_version"] = version
                                records.append(loan)
                                total_loans += 1
                        
                    elif record_type == 'T':
                        # Trailer record - marks end of pool
                        pass
                    
                    # Batch insert every BATCH_SIZE records
                    if len(records) >= self.BATCH_SIZE:
                        self._insert_loan_batch(records)
                        records = []
                
                # Insert remaining records
                if records:
                    self._insert_loan_batch(records)
                    
        except Exception as e:
            logger.error(f"Error parsing file: {e}")
            raise
        
        logger.info(f"Parsed {total_loans} loan records from {source_file}")
        return total_loans
    
    def _insert_loan_batch(self, records: list[dict]) -> None:
        """Insert batch of loan records to database."""
        if not records:
            return
        
        # For now, just log - actual insert to be implemented
        # based on final schema
        logger.info(f"Would insert {len(records)} loan records")
        
        # TODO: Implement actual database insert
        # df = pd.DataFrame(records)
        # df.to_sql("ginnie_loans_staging", self.engine, if_exists="append", index=False)
    
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
