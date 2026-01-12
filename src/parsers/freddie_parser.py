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
from typing import Dict, List, Optional, Generator, Any, TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine as EngineType
from decimal import Decimal, InvalidOperation

from google.cloud import storage
from sqlalchemy import text
from sqlalchemy.engine import Engine

# Add parent to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../..'))

from src.db.connection import get_engine
from src.tagging.pool_tagger import PoolTagger

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


# =============================================================================
# Tagging Integration
# =============================================================================

def tag_new_pools(engine, limit: Optional[int] = None) -> int:
    """Tag any pools that don't have tags yet."""
    tagger = PoolTagger(engine, current_mortgage_rate=6.5)
    return tagger.tag_all_pools(batch_size=1000, limit=limit)


# =============================================================================
# Column Mappings
# =============================================================================

# FRE_IS (Pool/Security Issuance) -> dim_pool + freddie_security_issuance
# Maps 91 columns from FRE_IS files to our schema
ISSUANCE_COLUMNS = {
    # Core identifiers
    'Prefix': 'prefix',
    'Security Identifier': 'security_id',
    'CUSIP': 'cusip',
    'Security Factor Date': 'factor_date',
    'Security Factor': 'factor',
    
    # Dates
    'Issue Date': 'issue_date',
    'Maturity Date': 'maturity_date',
    'Updated Longest Maturity Date': 'updated_maturity_date',
    
    # UPB/Size
    'Issuance Investor Security UPB': 'issuance_upb',
    'Current Investor Security UPB': 'current_upb',
    
    # Rates (critical for WAC/refi incentive)
    'WA Net Interest Rate': 'wa_net_rate',
    'WA Issuance Interest Rate': 'wa_issuance_rate',
    'WA Current Interest Rate': 'wa_current_rate',
    
    # Term/Age (critical for WALA/burnout)
    'WA Loan Term': 'wa_loan_term',
    'WA Current Remaining Months to Maturity': 'wa_rem_months',
    'WA Loan Age': 'wa_loan_age',  # WALA - key for burnout scoring
    
    # Loan size (critical for spec pool classification)
    'WA Mortgage Loan Amount': 'wa_loan_amount',
    'Average Mortgage Loan Amount': 'avg_loan_amount',
    'WA Origination Mortgage Loan Amount': 'wa_orig_loan_amount',
    'Average Origination Mortgage Loan Amount': 'avg_orig_loan_amount',
    
    # Credit characteristics (critical for AI tagging)
    'WA Loan-To-Value (LTV)': 'wa_ltv',
    'WA Combined Loan-To-Value (CLTV)': 'wa_cltv',
    'WA Debt-To-Income (DTI)': 'wa_dti',
    'WA Borrower Credit Score': 'wa_fico',  # Key for risk_profile
    'WA Updated Credit Score': 'wa_updated_fico',
    'WA Estimated Loan-To-Value (ELTV)': 'wa_eltv',
    
    # Origination credit (for historical comparison)
    'WA Origination Loan-To-Value (LTV)': 'wa_orig_ltv',
    'WA Origination Combined Loan-To-Value (CLTV)': 'wa_orig_cltv',
    'WA Origination Debt-To-Income (DTI)': 'wa_orig_dti',
    'WA Origination Credit Score': 'wa_orig_fico',
    
    # Loan count
    'Loan Count': 'loan_count',
    
    # Parties (critical for servicer_prepay_risk)
    'Servicer Name': 'servicer_name',
    'Servicer City': 'servicer_city',
    'Servicer State': 'servicer_state',
    'Seller Name': 'seller_name',
    'Seller City': 'seller_city',
    'Seller State': 'seller_state',
    
    # Product features
    'Interest Only Security Indicator': 'io_indicator',
    'Prepayment Penalty Indicator': 'prepay_penalty',
    'Subtype': 'subtype',
    
    # Delinquency
    'Delinquent Loans Purchased (Prior Month UPB)': 'dlq_purchased_upb',
    'Delinquent Loans Purchased (Loan Count)': 'dlq_purchased_count',
    
    # ARM fields (for is_arm flag)
    'Index': 'arm_index',
    'WA Mortgage Margin': 'wa_margin',
    'Initial Fixed Rate Period': 'init_fixed_period',
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
            # Get the first .txt or .csv file (2019 files use .csv)
            data_files = [f for f in zf.namelist() if f.endswith('.txt') or f.endswith('.csv')]
            if not data_files:
                raise ValueError(f"No .txt or .csv file found in {gcs_path}")
            
            with zf.open(data_files[0]) as f:
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
    
    def parse_csv_2019_format(self, content: str, column_map: Dict[str, str]) -> Generator[Dict, None, None]:
        """Parse 2019 CSV format with comma delimiter and 2-row header."""
        lines = content.strip().split('\n')
        if len(lines) < 3:  # Need at least 2 header rows + 1 data row
            return
        
        # 2019 format: Row 0 is category headers, Row 1 is actual column names
        # Use Row 1 as header
        header = [col.strip() for col in lines[1].split(',')]
        
        # Build column mapping for 2019 format (different column names)
        MAPPING_2019 = {
            'PREFIX': 'prefix',
            'POOL NUNBER': 'security_id',  # Note: typo in original files
            'POOL NUMBER': 'security_id',
            'CUSIP': 'cusip',
            'ISSUE DATE': 'issue_date',
            'MATURITY DATE': 'maturity_date',
            'ISSUANCE INVESTOR SECURITY UPB': 'issuance_upb',
            'WA NET INTEREST RATE': 'wa_net_rate',
            'WA ISSUANCE INTEREST RATE': 'wa_issuance_rate',
            'WA LOAN TERM': 'wa_loan_term',
            'WA LOAN AGE': 'wa_loan_age',
            'WA MORTGAGE LOAN AMOUNT': 'wa_loan_amount',
            'AVG MORTGAGE LOAN AMOUNT': 'avg_loan_amount',
            'WA LOAN-TO-VALUE (LTV)': 'wa_ltv',
            'WA COMBINED-LOAN-TO-VALUE (CLTV)': 'wa_cltv',
            'WA DEBT-TO-INCOME (DTI)': 'wa_dti',
            'BORROWER CREDIT SCORE': 'wa_fico',
            'LOAN COUNT': 'loan_count',
        }
        
        # Map header to our column names
        col_indices = {}
        for i, col in enumerate(header):
            col_clean = col.strip().upper()
            if col_clean in MAPPING_2019:
                col_indices[MAPPING_2019[col_clean]] = i
        
        # Parse data rows (skip first 2 header rows)
        for line in lines[2:]:
            if not line.strip():
                continue
            
            values = line.split(',')
            row = {}
            
            for our_col, idx in col_indices.items():
                if idx < len(values):
                    row[our_col] = values[idx].strip()
                else:
                    row[our_col] = None
            
            yield row
    
    def detect_and_parse(self, content: str, column_map: Dict[str, str]) -> Generator[Dict, None, None]:
        """Detect format and parse accordingly."""
        first_line = content.split('\n')[0] if content else ''
        
        # Check if it's comma-delimited (2019 format) or pipe-delimited
        if '|' in first_line:
            yield from self.parse_pipe_delimited(content, column_map)
        else:
            # 2019 CSV format
            yield from self.parse_csv_2019_format(content, column_map)
    
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
    """Parses FRE_IS (Monthly Issuance Summary) files into dim_pool with AI tagging fields."""
    
    def process_file(self, file_info: Dict) -> int:
        """Process a single FRE_IS file with comprehensive field mapping."""
        logger.info(f"Processing {file_info['filename']}")
        
        try:
            content = self.download_and_extract(file_info['local_gcs_path'])
            rows = list(self.detect_and_parse(content, ISSUANCE_COLUMNS))
            
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
                    
                    # Classify servicer prepay risk
                    servicer_name = row.get('servicer_name') or ''
                    servicer_risk = self._classify_servicer_prepay_risk(servicer_name)
                    
                    # Determine product flags
                    avg_loan = safe_decimal(row.get('avg_loan_amount'))
                    is_arm = row.get('arm_index') is not None and row.get('arm_index') != ''
                    is_io = row.get('io_indicator') == 'Y'
                    is_low_balance = avg_loan and avg_loan < 85000
                    is_high_balance = avg_loan and avg_loan >= 726200  # 2024 conforming limit
                    is_jumbo = avg_loan and avg_loan >= 1000000
                    
                    with self.engine.connect() as conn:
                        # Upsert into dim_pool with all fields for AI tagging
                        conn.execute(text("""
                            INSERT INTO dim_pool (
                                pool_id, cusip, prefix, product_type, coupon, issue_date,
                                maturity_date, orig_upb, servicer_name, servicer_id, orig_loan_count,
                                avg_fico, avg_ltv, avg_dti, avg_loan_size, wac, wam, wala,
                                is_arm, is_low_balance, is_high_balance, is_jumbo,
                                servicer_quality_tag, source_file
                            ) VALUES (
                                :pool_id, :cusip, :prefix, :product_type, :coupon, :issue_date,
                                :maturity_date, :orig_upb, :servicer_name, :servicer_id, :orig_loan_count,
                                :avg_fico, :avg_ltv, :avg_dti, :avg_loan_size, :wac, :wam, :wala,
                                :is_arm, :is_low_balance, :is_high_balance, :is_jumbo,
                                :servicer_prepay_risk, :source_file
                            )
                            ON CONFLICT (pool_id) DO UPDATE SET
                                cusip = COALESCE(EXCLUDED.cusip, dim_pool.cusip),
                                servicer_name = COALESCE(EXCLUDED.servicer_name, dim_pool.servicer_name),
                                servicer_quality_tag = COALESCE(EXCLUDED.servicer_quality_tag, dim_pool.servicer_quality_tag),
                                avg_fico = COALESCE(EXCLUDED.avg_fico, dim_pool.avg_fico),
                                avg_ltv = COALESCE(EXCLUDED.avg_ltv, dim_pool.avg_ltv),
                                avg_dti = COALESCE(EXCLUDED.avg_dti, dim_pool.avg_dti),
                                avg_loan_size = COALESCE(EXCLUDED.avg_loan_size, dim_pool.avg_loan_size),
                                wac = COALESCE(EXCLUDED.wac, dim_pool.wac),
                                wam = COALESCE(EXCLUDED.wam, dim_pool.wam),
                                wala = COALESCE(EXCLUDED.wala, dim_pool.wala),
                                is_arm = COALESCE(EXCLUDED.is_arm, dim_pool.is_arm),
                                is_low_balance = COALESCE(EXCLUDED.is_low_balance, dim_pool.is_low_balance),
                                is_high_balance = COALESCE(EXCLUDED.is_high_balance, dim_pool.is_high_balance),
                                is_jumbo = COALESCE(EXCLUDED.is_jumbo, dim_pool.is_jumbo),
                                source_file = EXCLUDED.source_file,
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
                            'servicer_name': servicer_name or None,
                            'servicer_id': self._extract_servicer_id(servicer_name),
                            'avg_fico': safe_int(row.get('wa_fico')),
                            'avg_ltv': safe_decimal(row.get('wa_ltv')),
                            'avg_dti': safe_decimal(row.get('wa_dti')),
                            'avg_loan_size': avg_loan,
                            'wac': safe_decimal(row.get('wa_current_rate')),
                            'wam': safe_int(row.get('wa_rem_months')),
                            'wala': safe_int(row.get('wa_loan_age')),
                            'orig_loan_count': safe_int(row.get('loan_count')),
                            'is_arm': is_arm,
                            'is_low_balance': is_low_balance,
                            'is_high_balance': is_high_balance,
                            'is_jumbo': is_jumbo,
                            'servicer_prepay_risk': servicer_risk,
                            'source_file': file_info['filename'],
                        })
                        
                        # Insert into fact_pool_month with factor/prepay data
                        factor_date = parse_date(row.get('factor_date')) or file_date
                        if factor_date:
                            conn.execute(text("""
                                INSERT INTO fact_pool_month (
                                    pool_id, as_of_date, loan_count, factor, curr_upb, wala, wac, wam,
                                    avg_loan_size, source_file
                                ) VALUES (
                                    :pool_id, :as_of_date, :loan_count, :factor, :curr_upb, :wala, :wac, :wam,
                                    :avg_loan_size, :source_file
                                )
                                ON CONFLICT (pool_id, as_of_date) DO UPDATE SET
                                    factor = COALESCE(EXCLUDED.factor, fact_pool_month.factor),
                                    curr_upb = COALESCE(EXCLUDED.curr_upb, fact_pool_month.curr_upb),
                                    wala = COALESCE(EXCLUDED.wala, fact_pool_month.wala),
                                    wac = COALESCE(EXCLUDED.wac, fact_pool_month.wac),
                                    source_file = EXCLUDED.source_file
                            """), {
                                'pool_id': pool_id,
                                'as_of_date': factor_date.replace(day=1),
                                'loan_count': safe_int(row.get('loan_count')),
                                'factor': safe_decimal(row.get('factor')),
                                'curr_upb': safe_decimal(row.get('current_upb')),
                                'wala': safe_int(row.get('wa_loan_age')),
                                'wac': safe_decimal(row.get('wa_current_rate')),
                                'wam': safe_int(row.get('wa_rem_months')),
                                'avg_loan_size': avg_loan,
                                'source_file': file_info['filename'],
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
    
    def _classify_servicer_prepay_risk(self, servicer_name: str) -> str:
        """
        Classify servicer by prepayment speed (investor perspective).
        Fast servicers = prepay_exposed (bad for investors)
        Slow servicers = prepay_protected (good for investors)
        """
        if not servicer_name:
            return 'neutral'
        
        name_lower = servicer_name.lower()
        
        # Fast servicers = BAD for investors (easy refi, automated)
        FAST_SERVICERS = [
            'rocket', 'quicken', 'better', 'loandepot', 'loan depot',
            'uwm', 'united wholesale', 'freedom mortgage', 'pennymac'
        ]
        if any(s in name_lower for s in FAST_SERVICERS):
            return 'prepay_exposed'
        
        # Slow servicers = GOOD for investors (bureaucratic, manual)
        SLOW_SERVICERS = [
            'wells fargo', 'chase', 'jpmorgan', 'bank of america', 'bofa',
            'ocwen', 'carrington', 'specialized loan', 'cenlar', 'nationstar',
            'mr. cooper', 'mr cooper', 'phh'
        ]
        if any(s in name_lower for s in SLOW_SERVICERS):
            return 'prepay_protected'
        
        return 'neutral'
    
    def _extract_servicer_id(self, servicer_name: str) -> Optional[str]:
        """Extract/generate servicer ID from name."""
        if not servicer_name:
            return None
        # Simple ID: first word, lowercased, no spaces
        return servicer_name.split()[0].lower()[:20] if servicer_name else None


# =============================================================================
# Loan-Level Parser (FRE_ILLD -> dim_loan)
# =============================================================================

class LoanParser(FreddieFileParser):
    """Parses FRE_ILLD (Loan-Level Disclosure) files."""
    
    def process_file(self, file_info: Dict, batch_size: int = 10000) -> int:
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
                        logger.info(f"Processing batch of {len(batch)} loans (total: {inserted + len(batch)})")
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
        """Insert a batch of loans using fast bulk insert."""
        if not batch:
            return 0
        
        # Get raw psycopg2 connection for execute_values
        from psycopg2.extras import execute_values
        
        raw_conn = self.engine.raw_connection()
        try:
            cursor = raw_conn.cursor()
            
            # First, collect unique pool_ids and create stubs
            pool_ids = set(loan['pool_id'] for loan in batch if loan.get('pool_id'))
            
            # Batch create stub pools using execute_values
            if pool_ids:
                pool_tuples = [
                    (pid, pid.split('-')[0] if '-' in pid else None, 'stub_from_illd')
                    for pid in pool_ids
                ]
                execute_values(
                    cursor,
                    """INSERT INTO dim_pool (pool_id, prefix, source_file)
                       VALUES %s ON CONFLICT (pool_id) DO NOTHING""",
                    pool_tuples,
                    page_size=1000
                )
            
            # Prepare loan tuples for bulk insert
            loan_tuples = [
                (
                    loan.get('loan_id'),
                    loan.get('pool_id'),
                    loan.get('first_pay_date'),
                    loan.get('orig_rate'),
                    loan.get('orig_upb'),
                    loan.get('orig_term'),
                    loan.get('fico'),
                    loan.get('ltv'),
                    loan.get('cltv'),
                    loan.get('dti'),
                    loan.get('occupancy'),
                    loan.get('property_type'),
                    loan.get('purpose'),
                    loan.get('state'),
                    loan.get('channel'),
                    loan.get('first_time_buyer'),
                    loan.get('num_units'),
                    loan.get('num_borrowers')
                )
                for loan in batch
            ]
            
            # Bulk insert loans using execute_values (10-50x faster)
            execute_values(
                cursor,
                """INSERT INTO dim_loan (
                    loan_id, pool_id, first_pay_date, orig_rate, orig_upb,
                    orig_term, fico, ltv, cltv, dti, occupancy,
                    property_type, purpose, state, channel,
                    first_time_buyer, num_units, num_borrowers
                ) VALUES %s
                ON CONFLICT (loan_id) DO UPDATE SET
                    pool_id = EXCLUDED.pool_id,
                    updated_at = NOW()""",
                loan_tuples,
                page_size=5000
            )
            
            raw_conn.commit()
            return len(batch)
            
        except Exception as e:
            raw_conn.rollback()
            logger.error(f"Bulk insert failed: {e}")
            return 0
        finally:
            raw_conn.close()


# =============================================================================
# Factor Parser (FRE_DPR_Fctr -> cohort prepay data)
# =============================================================================

# =============================================================================
# FISS Parser (FRE_FISS -> freddie_security_issuance)
# =============================================================================

class FissParser(FreddieFileParser):
    """
    Parses FRE_FISS (Intraday Security Issuance) files.
    These are headerless pipe-delimited files with 9 columns:
    0: Product type code
    1: Pool prefix (CL, etc.)
    2: Security identifier
    3: CUSIP
    4: Coupon (or code)
    5: UPB
    6: Price (usually 100)
    7: Loan count
    8: Additional field
    """
    
    def process_file(self, file_info: Dict) -> int:
        """Process a single FRE_FISS file (headerless format)."""
        logger.info(f"Processing {file_info['filename']}")
        
        try:
            content = self.download_and_extract(file_info['local_gcs_path'])
            
            # Extract date from filename (FRE_FISS_YYYYMMDD.zip)
            date_str = file_info['filename'].replace('FRE_FISS_', '').replace('.zip', '')
            try:
                issuance_date = datetime.strptime(date_str, '%Y%m%d').date()
            except ValueError:
                issuance_date = None
            
            lines = content.strip().split('\n')
            inserted = 0
            
            with self.engine.connect() as conn:
                for line in lines:
                    if not line.strip():
                        continue
                    
                    cols = line.split('|')
                    if len(cols) < 8:
                        continue
                    
                    try:
                        # Parse headerless format
                        prefix = cols[1].strip() if len(cols) > 1 else None
                        security_id = cols[2].strip() if len(cols) > 2 else None
                        cusip = cols[3].strip() if len(cols) > 3 else None
                        
                        if not security_id:
                            continue
                        
                        pool_id = f"{prefix}-{security_id}" if prefix else security_id
                        
                        # Insert into freddie_security_issuance
                        conn.execute(text("""
                            INSERT INTO freddie_security_issuance (
                                issuance_date, pool_id, cusip, prefix, 
                                orig_face, source_file
                            ) VALUES (
                                :issuance_date, :pool_id, :cusip, :prefix,
                                :orig_face, :source_file
                            )
                        """), {
                            'issuance_date': issuance_date,
                            'pool_id': pool_id,
                            'cusip': cusip,
                            'prefix': prefix,
                            'orig_face': safe_decimal(cols[5]) if len(cols) > 5 else None,
                            'source_file': file_info['filename'],
                        })
                        
                        # Also create stub in dim_pool if needed
                        conn.execute(text("""
                            INSERT INTO dim_pool (pool_id, cusip, prefix, source_file)
                            VALUES (:pool_id, :cusip, :prefix, :source_file)
                            ON CONFLICT (pool_id) DO UPDATE SET
                                cusip = COALESCE(EXCLUDED.cusip, dim_pool.cusip)
                        """), {
                            'pool_id': pool_id,
                            'cusip': cusip,
                            'prefix': prefix,
                            'source_file': file_info['filename'],
                        })
                        
                        inserted += 1
                        
                    except Exception as e:
                        logger.warning(f"Error parsing FISS line: {e}")
                        continue
                
                conn.commit()
            
            self.mark_processed(file_info['id'], True)
            logger.info(f"Inserted {inserted} securities from {file_info['filename']}")
            return inserted
            
        except Exception as e:
            logger.error(f"Error processing {file_info['filename']}: {e}")
            self.mark_processed(file_info['id'], False, str(e))
            return 0


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

def process_files(file_type: str, limit: Optional[int] = None, engine: Optional[Engine] = None, 
                  auto_tag: bool = True) -> int:
    """
    Process files of a specific type and optionally tag new pools.
    
    Args:
        file_type: Type of file to process (issuance, illd, factor, fiss)
        limit: Maximum number of files to process
        engine: SQLAlchemy engine (created if not provided)
        auto_tag: Whether to automatically tag new pools after processing
        
    Returns:
        Number of records processed
    """
    if engine is None:
        engine = get_engine()
    
    parsers = {
        'issuance': (IssuanceParser, 'FRE_IS_%'),
        'illd': (LoanParser, 'FRE_ILLD_%'),
        'factor': (FactorParser, 'FRE_DPR_Fctr%'),
        'fiss': (FissParser, 'FRE_FISS_%'),  # Headerless format
    }
    
    if file_type not in parsers:
        logger.error(f"Unknown file type: {file_type}")
        logger.info(f"Available types: {list(parsers.keys())}")
        return 0
    
    parser_class, pattern = parsers[file_type]
    parser = parser_class(engine)
    
    files = parser.get_pending_files(pattern, limit)
    logger.info(f"Found {len(files)} pending {file_type} files")
    
    total_processed = 0
    for file_info in files:
        count = parser.process_file(file_info)
        total_processed += count
    
    logger.info(f"Total records processed: {total_processed}")
    
    # Auto-tag new pools after parsing (if enabled and pools were created)
    if auto_tag and total_processed > 0 and file_type in ('issuance', 'fiss'):
        logger.info("Running AI tagger on new pools...")
        tagged = tag_new_pools(engine)
        logger.info(f"Tagged {tagged} new pools")
    
    return total_processed


def main():
    parser = argparse.ArgumentParser(description='Parse Freddie Mac disclosure files')
    parser.add_argument('--file-type', type=str, 
                       choices=['issuance', 'illd', 'factor', 'fiss'],
                       help='Type of file to process')
    parser.add_argument('--limit', type=int, help='Limit number of files to process')
    parser.add_argument('--process-all', action='store_true', 
                       help='Process all pending files of all types')
    parser.add_argument('--no-tag', action='store_true',
                       help='Skip automatic AI tagging after parsing')
    parser.add_argument('--tag-only', action='store_true',
                       help='Only run AI tagging (no file parsing)')
    args = parser.parse_args()
    
    engine = get_engine()
    auto_tag = not args.no_tag
    
    if args.tag_only:
        logger.info("Running AI tagger on untagged pools...")
        tagged = tag_new_pools(engine)
        logger.info(f"Tagged {tagged} pools")
    elif args.process_all:
        for file_type in ['issuance', 'fiss', 'illd', 'factor']:
            logger.info(f"\n=== Processing {file_type} files ===")
            process_files(file_type, args.limit, engine, auto_tag=False)
        # Tag once at the end if auto_tag enabled
        if auto_tag:
            logger.info("\n=== Running AI Tagging ===")
            tagged = tag_new_pools(engine)
            logger.info(f"Tagged {tagged} new pools")
    elif args.file_type:
        process_files(args.file_type, args.limit, engine, auto_tag=auto_tag)
    else:
        parser.print_help()


if __name__ == '__main__':
    main()
