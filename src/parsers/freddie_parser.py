#!/usr/bin/env python3
"""
Freddie Mac Disclosure File Parser

Parses downloaded Freddie Mac disclosure files and loads them into PostgreSQL.

File Types Supported:
- FRE_ILLD: Loan-Level Disclosure Data
- FRE_IS: Monthly Issuance Summary (Pool-Level)
- FRE_DPR_Fctr: Monthly Factor/Prepay Data
- FRE_FISS: Intraday Issuance

Usage:
    python -m src.parsers.freddie_parser --file-type illd --limit 10
    python -m src.parsers.freddie_parser --file-type issuance --all
    python -m src.parsers.freddie_parser --process-all
"""

import argparse
import io
import logging
import os
import sys
import zipfile
from dataclasses import dataclass
from datetime import datetime, date
from typing import Dict, List, Optional, Generator, Any
from decimal import Decimal, InvalidOperation

from google.cloud import storage
from sqlalchemy import text
from sqlalchemy.engine import Engine

# Add parent to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../..'))

from src.db.connection import get_engine

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


# =============================================================================
# Column Mappings
# =============================================================================

# FRE_IS (Pool/Security Issuance) -> dim_pool + freddie_security_issuance
ISSUANCE_COLUMNS = {
    'Prefix': 'prefix',
    'Security Identifier': 'security_id',
    'CUSIP': 'cusip',
    'Security Factor Date': 'factor_date',
    'Security Factor': 'factor',
    'Issue Date': 'issue_date',
    'Maturity Date': 'maturity_date',
    'Issuance Investor Security UPB': 'issuance_upb',
    'Current Investor Security UPB': 'current_upb',
    'WA Net Interest Rate': 'wa_net_rate',
    'WA Issuance Interest Rate': 'wa_issuance_rate',
    'WA Current Interest Rate': 'wa_current_rate',
    'WA Loan Term': 'wa_loan_term',
    'WA Current Remaining Months to Maturity': 'wa_rem_months',
    'WA Loan Age': 'wa_loan_age',
    'WA Mortgage Loan Amount': 'wa_loan_amount',
    'Average Mortgage Loan Amount': 'avg_loan_amount',
    'WA Loan-To-Value (LTV)': 'wa_ltv',
    'WA Combined Loan-To-Value (CLTV)': 'wa_cltv',
    'WA Debt-To-Income (DTI)': 'wa_dti',
    'WA Borrower Credit Score': 'wa_fico',
    'Loan Count': 'loan_count',
    'Servicer Name': 'servicer_name',
    'Servicer State': 'servicer_state',
    'Seller Name': 'seller_name',
}

# FRE_ILLD (Loan-Level Disclosure) -> dim_loan
LOAN_COLUMNS = {
    'Loan Identifier': 'loan_id',
    'Prefix': 'prefix',
    'Security Identifier': 'security_id',
    'CUSIP': 'cusip',
    'Mortgage Loan Amount': 'orig_upb',
    'Issuance Investor Loan UPB': 'issuance_upb',
    'Current Investor Loan UPB': 'current_upb',
    'Amortization Type': 'amort_type',
    'Original Interest Rate': 'orig_rate',
    'Issuance Interest Rate': 'issuance_rate',
    'Current Interest Rate': 'current_rate',
    'First Payment Date': 'first_pay_date',
    'Maturity Date': 'maturity_date',
    'Loan Term': 'loan_term',
    'Remaining Months to Maturity': 'rem_months',
    'Loan Age': 'loan_age',
    'Loan-To-Value (LTV)': 'ltv',
    'Combined Loan-To-Value (CLTV)': 'cltv',
    'Debt-To-Income (DTI)': 'dti',
    'Borrower Credit Score': 'fico',
    'Number of Borrowers': 'num_borrowers',
    'First Time Home Buyer Indicator': 'first_time_buyer',
    'Loan Purpose': 'loan_purpose',
    'Occupancy Status': 'occupancy',
    'Number of Units': 'num_units',
    'Property Type': 'property_type',
    'Channel': 'channel',
    'Property State': 'state',
    'Seller Name': 'seller_name',
    'Servicer Name': 'servicer_name',
    'Mortgage Insurance Percent': 'mi_pct',
    'Government Insured Guarantee': 'govt_insured',
    'Interest Only Loan Indicator': 'io_indicator',
}

# FRE_DPR_Fctr (Factor/Prepay) -> fact_pool_month
FACTOR_COLUMNS = {
    'Type of Security': 'security_type',
    'Year': 'vintage_year',
    'WA Net Interest Rate': 'wa_net_rate',
    'Cohort Current UPB': 'cohort_upb',
    'Cohort WA Current Interest Rate': 'wa_current_rate',
    'Cohort WA Current Remaining Months to Maturity': 'wa_rem_months',
    'Cohort WA Current Loan Age': 'wa_loan_age',
    'Date': 'record_date',
    'Factor Date': 'factor_date',
    'Principal Reduction Amount': 'prin_reduction',
    'Cumulative Principal Reduction Amount': 'cum_prin_reduction',
    'Unscheduled Principal Reduction Amount': 'unsched_prin',
    'SMM': 'smm',
    'Cumulative SMM': 'cum_smm',
    'CPR': 'cpr',
    'Cumulative CPR': 'cum_cpr',
}


# =============================================================================
# Helper Functions
# =============================================================================

def safe_decimal(value: str, default: Optional[Decimal] = None) -> Optional[Decimal]:
    """Safely convert string to Decimal."""
    if not value or value.strip() == '':
        return default
    try:
        return Decimal(value.strip())
    except InvalidOperation:
        return default


def safe_int(value: str, default: Optional[int] = None) -> Optional[int]:
    """Safely convert string to int."""
    if not value or value.strip() == '':
        return default
    try:
        return int(value.strip())
    except ValueError:
        return default


def parse_date(value: str) -> Optional[date]:
    """Parse date from various Freddie Mac formats."""
    if not value or value.strip() == '':
        return None
    value = value.strip()
    
    # Try different formats
    formats = [
        '%m%Y',      # 122020 (MMYYYY)
        '%Y%m%d',    # 20201201 (YYYYMMDD)
        '%m%d%Y',    # 12012020 (MMDDYYYY)
        '%Y%m',      # 202012 (YYYYMM)
    ]
    
    for fmt in formats:
        try:
            dt = datetime.strptime(value, fmt)
            return dt.date()
        except ValueError:
            continue
    
    return None


def parse_yyyymm(value: str) -> Optional[date]:
    """Parse YYYYMM format to first of month."""
    if not value or len(value) != 6:
        return None
    try:
        year = int(value[:4])
        month = int(value[4:6])
        return date(year, month, 1)
    except (ValueError, IndexError):
        return None


# =============================================================================
# File Parser
# =============================================================================

class FreddieFileParser:
    """Parses Freddie Mac disclosure files from GCS."""
    
    def __init__(self, engine: Engine):
        self.engine = engine
        self.storage_client = storage.Client()
        self.bucket = self.storage_client.bucket('oasive-raw-data')
        
    def get_pending_files(self, file_pattern: str, limit: Optional[int] = None) -> List[Dict]:
        """Get files that have been downloaded but not processed."""
        query = """
            SELECT id, filename, local_gcs_path, remote_size
            FROM freddie_file_catalog
            WHERE downloaded_at IS NOT NULL 
              AND processed_at IS NULL
              AND filename LIKE :pattern
            ORDER BY filename
        """
        if limit:
            query += f" LIMIT {limit}"
            
        with self.engine.connect() as conn:
            result = conn.execute(text(query), {'pattern': file_pattern})
            return [dict(row._mapping) for row in result]
    
    def download_and_extract(self, gcs_path: str) -> str:
        """Download ZIP from GCS and extract text content."""
        # Parse bucket and blob path
        # gs://oasive-raw-data/freddie/raw/2026/01/filename.zip
        path_parts = gcs_path.replace('gs://oasive-raw-data/', '')
        blob = self.bucket.blob(path_parts)
        
        # Download to memory
        content = blob.download_as_bytes()
        
        # Extract from ZIP
        with zipfile.ZipFile(io.BytesIO(content)) as zf:
            # Get the first .txt file
            txt_files = [f for f in zf.namelist() if f.endswith('.txt')]
            if not txt_files:
                raise ValueError(f"No .txt file found in {gcs_path}")
            
            with zf.open(txt_files[0]) as f:
                return f.read().decode('utf-8', errors='replace')
    
    def parse_pipe_delimited(self, content: str, column_map: Dict[str, str]) -> Generator[Dict, None, None]:
        """Parse pipe-delimited content with header row."""
        lines = content.strip().split('\n')
        if not lines:
            return
        
        # Parse header
        header = [col.strip() for col in lines[0].split('|')]
        
        # Map header to our column names
        col_indices = {}
        for i, col in enumerate(header):
            if col in column_map:
                col_indices[column_map[col]] = i
        
        # Parse data rows
        for line in lines[1:]:
            if not line.strip():
                continue
            
            values = line.split('|')
            row = {}
            
            for our_col, idx in col_indices.items():
                if idx < len(values):
                    row[our_col] = values[idx].strip()
                else:
                    row[our_col] = None
            
            yield row
    
    def mark_processed(self, file_id: int, success: bool, error_msg: Optional[str] = None):
        """Mark file as processed in catalog."""
        with self.engine.connect() as conn:
            if success:
                conn.execute(text("""
                    UPDATE freddie_file_catalog 
                    SET processed_at = NOW(), error_message = NULL
                    WHERE id = :id
                """), {'id': file_id})
            else:
                conn.execute(text("""
                    UPDATE freddie_file_catalog 
                    SET error_message = :error
                    WHERE id = :id
                """), {'id': file_id, 'error': error_msg})
            conn.commit()


# =============================================================================
# Issuance Parser (FRE_IS -> dim_pool + freddie_security_issuance)
# =============================================================================

class IssuanceParser(FreddieFileParser):
    """Parses FRE_IS (Monthly Issuance Summary) files."""
    
    def process_file(self, file_info: Dict) -> int:
        """Process a single FRE_IS file."""
        logger.info(f"Processing {file_info['filename']}")
        
        try:
            content = self.download_and_extract(file_info['local_gcs_path'])
            rows = list(self.parse_pipe_delimited(content, ISSUANCE_COLUMNS))
            
            if not rows:
                logger.warning(f"No data rows in {file_info['filename']}")
                self.mark_processed(file_info['id'], True)
                return 0
            
            # Extract file date from filename (FRE_IS_YYYYMM.zip)
            file_date = parse_yyyymm(file_info['filename'].replace('FRE_IS_', '').replace('.zip', ''))
            
            inserted = 0
            errors = 0
            
            for row in rows:
                try:
                    # Build pool_id
                    pool_id = f"{row.get('prefix', '')}-{row.get('security_id', '')}"
                    
                    if not pool_id or pool_id == '-':
                        continue
                    
                    with self.engine.connect() as conn:
                        # Upsert into dim_pool
                        conn.execute(text("""
                            INSERT INTO dim_pool (
                                pool_id, cusip, prefix, product_type, coupon, issue_date,
                                maturity_date, orig_upb, servicer_name, orig_loan_count,
                                avg_fico, avg_ltv, avg_dti, avg_loan_size, wac
                            ) VALUES (
                                :pool_id, :cusip, :prefix, :product_type, :coupon, :issue_date,
                                :maturity_date, :orig_upb, :servicer_name, :orig_loan_count,
                                :avg_fico, :avg_ltv, :avg_dti, :avg_loan_size, :wac
                            )
                            ON CONFLICT (pool_id) DO UPDATE SET
                                cusip = EXCLUDED.cusip,
                                servicer_name = EXCLUDED.servicer_name,
                                avg_fico = EXCLUDED.avg_fico,
                                avg_ltv = EXCLUDED.avg_ltv,
                                updated_at = NOW()
                        """), {
                            'pool_id': pool_id,
                            'cusip': row.get('cusip') or None,
                            'prefix': row.get('prefix') or None,
                            'product_type': self._derive_product(row),
                            'coupon': safe_decimal(row.get('wa_net_rate')),
                            'issue_date': parse_date(row.get('issue_date')),
                            'maturity_date': parse_date(row.get('maturity_date')),
                            'orig_upb': safe_decimal(row.get('issuance_upb')),
                            'servicer_name': row.get('servicer_name') or None,
                            'avg_fico': safe_int(row.get('wa_fico')),
                            'avg_ltv': safe_decimal(row.get('wa_ltv')),
                            'avg_dti': safe_decimal(row.get('wa_dti')),
                            'avg_loan_size': safe_decimal(row.get('avg_loan_amount')),
                            'wac': safe_decimal(row.get('wa_current_rate')),
                            'orig_loan_count': safe_int(row.get('loan_count')),
                        })
                        
                        # Insert into fact_pool_month
                        factor_date = parse_date(row.get('factor_date')) or file_date
                        if factor_date:
                            conn.execute(text("""
                                INSERT INTO fact_pool_month (
                                    pool_id, as_of_date, loan_count, factor, curr_upb
                                ) VALUES (
                                    :pool_id, :as_of_date, :loan_count, :factor, :curr_upb
                                )
                                ON CONFLICT (pool_id, as_of_date) DO UPDATE SET
                                    factor = EXCLUDED.factor,
                                    curr_upb = EXCLUDED.curr_upb
                            """), {
                                'pool_id': pool_id,
                                'as_of_date': factor_date.replace(day=1),
                                'loan_count': safe_int(row.get('loan_count')),
                                'factor': safe_decimal(row.get('factor')),
                                'curr_upb': safe_decimal(row.get('current_upb')),
                            })
                        
                        conn.commit()
                        inserted += 1
                        
                except Exception as e:
                    errors += 1
                    if errors <= 3:  # Only log first few errors
                        logger.warning(f"Error processing row {pool_id}: {e}")
                    continue
            
            if errors > 3:
                logger.warning(f"Suppressed {errors - 3} additional errors")
            
            self.mark_processed(file_info['id'], True)
            logger.info(f"Inserted/updated {inserted} pools from {file_info['filename']}")
            return inserted
            
        except Exception as e:
            logger.error(f"Error processing {file_info['filename']}: {e}")
            self.mark_processed(file_info['id'], False, str(e))
            return 0
    
    def _derive_product(self, row: Dict) -> str:
        """Derive product type from row data."""
        term = safe_int(row.get('wa_loan_term'))
        if term:
            if term >= 350:
                return '30yr'
            elif term >= 170:
                return '20yr'
            elif term >= 160:
                return '15yr'
            elif term >= 110:
                return '10yr'
        return 'Other'


# =============================================================================
# Loan-Level Parser (FRE_ILLD -> dim_loan)
# =============================================================================

class LoanParser(FreddieFileParser):
    """Parses FRE_ILLD (Loan-Level Disclosure) files."""
    
    def process_file(self, file_info: Dict, batch_size: int = 5000) -> int:
        """Process a single FRE_ILLD file."""
        logger.info(f"Processing {file_info['filename']} (may contain many loans)")
        
        try:
            content = self.download_and_extract(file_info['local_gcs_path'])
            
            # Extract file date from filename (FRE_ILLD_YYYYMM.zip)
            file_date = parse_yyyymm(file_info['filename'].replace('FRE_ILLD_', '').replace('.zip', ''))
            
            inserted = 0
            batch = []
            
            for row in self.parse_pipe_delimited(content, LOAN_COLUMNS):
                try:
                    # Build pool_id for foreign key
                    pool_id = f"{row.get('prefix', '')}-{row.get('security_id', '')}"
                    
                    loan_data = {
                        'loan_id': row.get('loan_id'),
                        'pool_id': pool_id,
                        'first_pay_date': parse_date(row.get('first_pay_date')),
                        'orig_rate': safe_decimal(row.get('orig_rate')),
                        'orig_upb': safe_decimal(row.get('orig_upb')),
                        'orig_term': safe_int(row.get('loan_term')),
                        'fico': safe_int(row.get('fico')),
                        'ltv': safe_decimal(row.get('ltv')),
                        'cltv': safe_decimal(row.get('cltv')),
                        'dti': safe_decimal(row.get('dti')),
                        'occupancy': row.get('occupancy'),
                        'property_type': row.get('property_type'),
                        'purpose': row.get('loan_purpose'),
                        'state': row.get('state'),
                        'channel': row.get('channel'),
                        'first_time_buyer': row.get('first_time_buyer') == 'Y',
                        'num_units': safe_int(row.get('num_units')),
                        'num_borrowers': safe_int(row.get('num_borrowers')),
                    }
                    
                    batch.append(loan_data)
                    
                    if len(batch) >= batch_size:
                        inserted += self._insert_loan_batch(batch)
                        batch = []
                        
                except Exception as e:
                    logger.warning(f"Error processing loan row: {e}")
                    continue
            
            # Insert remaining batch
            if batch:
                inserted += self._insert_loan_batch(batch)
            
            self.mark_processed(file_info['id'], True)
            logger.info(f"Inserted/updated {inserted} loans from {file_info['filename']}")
            return inserted
            
        except Exception as e:
            logger.error(f"Error processing {file_info['filename']}: {e}")
            self.mark_processed(file_info['id'], False, str(e))
            return 0
    
    def _insert_loan_batch(self, batch: List[Dict]) -> int:
        """Insert a batch of loans."""
        if not batch:
            return 0
            
        inserted = 0
        with self.engine.connect() as conn:
            for loan in batch:
                try:
                    conn.execute(text("""
                        INSERT INTO dim_loan (
                            loan_id, pool_id, first_pay_date, orig_rate, orig_upb,
                            orig_term, fico, ltv, cltv, dti, occupancy,
                            property_type, purpose, state, channel,
                            first_time_buyer, num_units, num_borrowers
                        ) VALUES (
                            :loan_id, :pool_id, :first_pay_date, :orig_rate, :orig_upb,
                            :orig_term, :fico, :ltv, :cltv, :dti, :occupancy,
                            :property_type, :purpose, :state, :channel,
                            :first_time_buyer, :num_units, :num_borrowers
                        )
                        ON CONFLICT (loan_id) DO UPDATE SET
                            pool_id = EXCLUDED.pool_id,
                            updated_at = NOW()
                    """), loan)
                    inserted += 1
                except Exception as e:
                    # Log but continue - might be FK constraint if pool doesn't exist yet
                    pass
                    
            conn.commit()
            
        return inserted


# =============================================================================
# Factor Parser (FRE_DPR_Fctr -> cohort prepay data)
# =============================================================================

class FactorParser(FreddieFileParser):
    """Parses FRE_DPR_Fctr (Factor/Prepay) files."""
    
    def process_file(self, file_info: Dict) -> int:
        """Process a single FRE_DPR_Fctr file."""
        logger.info(f"Processing {file_info['filename']}")
        
        try:
            content = self.download_and_extract(file_info['local_gcs_path'])
            rows = list(self.parse_pipe_delimited(content, FACTOR_COLUMNS))
            
            if not rows:
                self.mark_processed(file_info['id'], True)
                return 0
            
            # For now, just log the factor data - we'll use this for CPR analysis
            # The factor data is at cohort level (security type + vintage + coupon)
            # This feeds into our servicer_prepay_metrics calculations
            
            inserted = 0
            # TODO: Insert into a cohort_factor table for CPR analysis
            
            self.mark_processed(file_info['id'], True)
            logger.info(f"Processed {len(rows)} factor records from {file_info['filename']}")
            return len(rows)
            
        except Exception as e:
            logger.error(f"Error processing {file_info['filename']}: {e}")
            self.mark_processed(file_info['id'], False, str(e))
            return 0


# =============================================================================
# Main Entry Point
# =============================================================================

def process_files(file_type: str, limit: Optional[int] = None, engine: Optional[Engine] = None):
    """Process files of a specific type."""
    if engine is None:
        engine = get_engine()
    
    parsers = {
        'issuance': (IssuanceParser, 'FRE_IS_%'),
        'illd': (LoanParser, 'FRE_ILLD_%'),
        'factor': (FactorParser, 'FRE_DPR_Fctr%'),
        'fiss': (IssuanceParser, 'FRE_FISS_%'),  # Same format as FRE_IS
    }
    
    if file_type not in parsers:
        logger.error(f"Unknown file type: {file_type}")
        logger.info(f"Available types: {list(parsers.keys())}")
        return
    
    parser_class, pattern = parsers[file_type]
    parser = parser_class(engine)
    
    files = parser.get_pending_files(pattern, limit)
    logger.info(f"Found {len(files)} pending {file_type} files")
    
    total_processed = 0
    for file_info in files:
        count = parser.process_file(file_info)
        total_processed += count
    
    logger.info(f"Total records processed: {total_processed}")


def main():
    parser = argparse.ArgumentParser(description='Parse Freddie Mac disclosure files')
    parser.add_argument('--file-type', type=str, 
                       choices=['issuance', 'illd', 'factor', 'fiss'],
                       help='Type of file to process')
    parser.add_argument('--limit', type=int, help='Limit number of files to process')
    parser.add_argument('--process-all', action='store_true', 
                       help='Process all pending files of all types')
    args = parser.parse_args()
    
    if args.process_all:
        for file_type in ['issuance', 'illd', 'factor']:
            logger.info(f"\n=== Processing {file_type} files ===")
            process_files(file_type, args.limit)
    elif args.file_type:
        process_files(args.file_type, args.limit)
    else:
        parser.print_help()


if __name__ == '__main__':
    main()
