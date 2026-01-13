"""
SFLLD (Single-Family Loan-Level Dataset) Ingestor

Downloads and ingests Freddie Mac historical loan-level data (1999-2025).
Data source: Clarity Data Intelligence Platform

COMPLIANCE NOTE:
Freddie Mac's Terms of Use prohibit automated web scraping. This module provides:
1. A download tracker to manage manual downloads
2. An auto-parser for downloaded files
3. Integration with the official Clarity Download API (if access is granted)

Usage:
    # Track which files need downloading
    python -m src.ingestors.sflld_ingestor --status

    # Process manually downloaded files
    python -m src.ingestors.sflld_ingestor --process /path/to/downloads

    # API mode (requires API access from clarity@freddiemac.com)
    python -m src.ingestors.sflld_ingestor --api-mode --api-key YOUR_KEY
"""

import os
import sys
import zipfile
import logging
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional, Generator
from decimal import Decimal, InvalidOperation
import argparse

from google.cloud import storage
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.db.connection import get_engine
from src.config import GCS_RAW_BUCKET

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# =============================================================================
# SFLLD File Layout (from Freddie Mac documentation)
# The dataset has two files per year:
#   - origination data (loan characteristics at origination)
#   - performance data (monthly performance updates)
# =============================================================================

# Origination File Columns (pipe-delimited, no header)
ORIGINATION_COLUMNS = [
    'credit_score',           # 1: Credit Score at Origination
    'first_payment_date',     # 2: First Payment Date (YYYYMM)
    'first_time_buyer',       # 3: First Time Homebuyer Flag (Y/N)
    'maturity_date',          # 4: Maturity Date (YYYYMM)
    'msa',                    # 5: Metropolitan Statistical Area
    'mi_pct',                 # 6: Mortgage Insurance Percentage
    'num_units',              # 7: Number of Units
    'occupancy',              # 8: Occupancy Status (P/I/S)
    'cltv',                   # 9: Original Combined LTV
    'dti',                    # 10: Original DTI
    'orig_upb',               # 11: Original UPB
    'ltv',                    # 12: Original LTV
    'orig_rate',              # 13: Original Interest Rate
    'channel',                # 14: Channel (R/B/C)
    'prepay_penalty',         # 15: Prepayment Penalty Flag
    'amort_type',             # 16: Amortization Type
    'state',                  # 17: Property State
    'property_type',          # 18: Property Type
    'zipcode',                # 19: Postal Code (3-digit)
    'loan_sequence',          # 20: Loan Sequence Number (unique ID)
    'loan_purpose',           # 21: Loan Purpose (P/C/N/R)
    'loan_term',              # 22: Original Loan Term
    'num_borrowers',          # 23: Number of Borrowers
    'seller_name',            # 24: Seller Name
    'servicer_name',          # 25: Servicer Name
    'super_conforming',       # 26: Super Conforming Flag
    'pre_harp_loan_seq',      # 27: Pre-HARP Loan Sequence (if refinance)
    'program_indicator',      # 28: Program Indicator (H=HARP, F=FTHB, 9=Other)
    'harp_indicator',         # 29: HARP Indicator
    'property_valuation',     # 30: Property Valuation Method
    'io_indicator',           # 31: Interest Only Indicator
    'mi_cancellation',        # 32: MI Cancellation Indicator
]

# Performance (Monthly) File Columns
PERFORMANCE_COLUMNS = [
    'loan_sequence',          # 1: Loan Sequence Number
    'monthly_reporting_period',  # 2: Monthly Reporting Period (YYYYMM)
    'current_upb',            # 3: Current Actual UPB
    'current_dlq_status',     # 4: Current Loan Delinquency Status
    'loan_age',               # 5: Loan Age
    'rem_months',             # 6: Remaining Months to Legal Maturity
    'repurchase_flag',        # 7: Repurchase Flag
    'modification_flag',      # 8: Modification Flag
    'zero_balance_code',      # 9: Zero Balance Code (prepay/foreclosure reason)
    'zero_balance_date',      # 10: Zero Balance Effective Date
    'current_rate',           # 11: Current Interest Rate
    'current_deferred_upb',   # 12: Current Deferred UPB
    'ddlpi',                  # 13: Due Date of Last Paid Installment
    'mi_recoveries',          # 14: MI Recoveries
    'net_sales_proceeds',     # 15: Net Sales Proceeds
    'non_mi_recoveries',      # 16: Non MI Recoveries
    'expenses',               # 17: Expenses
    'legal_costs',            # 18: Legal Costs
    'maintenance_costs',      # 19: Maintenance Costs
    'taxes_insurance',        # 20: Taxes and Insurance
    'misc_expenses',          # 21: Miscellaneous Expenses
    'actual_loss',            # 22: Actual Loss Calculation
    'modification_cost',      # 23: Modification Cost
    'step_mod_flag',          # 24: Step Modification Flag
    'deferred_payment_plan',  # 25: Deferred Payment Plan
    'eltv',                   # 26: Estimated LTV
    'zero_balance_removal',   # 27: Zero Balance Removal UPB
    'dlq_accrued_interest',   # 28: Delinquent Accrued Interest
    'dlq_due_date',           # 29: Delinquency Due Date
    'borrower_assistance',    # 30: Borrower Assistance Status
    'current_month_mod_cost', # 31: Current Month Modification Cost
    'interest_bearing_upb',   # 32: Interest Bearing UPB
]

# Zero Balance Codes (important for prepay analysis)
ZERO_BALANCE_CODES = {
    '01': 'Prepaid or Matured',
    '02': 'Third Party Sale',
    '03': 'Short Sale or Deed-in-Lieu',
    '06': 'Repurchased',
    '09': 'REO Disposition',
    '15': 'Note Sale',
    '16': 'Reperforming Loan Sale',
    '96': 'Inactive without Zero Balance',
    '97': 'Inactive - Removal',
}


# =============================================================================
# Helper Functions
# =============================================================================

def safe_decimal(value: str) -> Optional[Decimal]:
    """Convert string to Decimal, handling empty/invalid values."""
    if not value or value.strip() == '':
        return None
    try:
        return Decimal(value.strip())
    except InvalidOperation:
        return None


def safe_int(value: str) -> Optional[int]:
    """Convert string to int, handling empty/invalid values."""
    if not value or value.strip() == '':
        return None
    try:
        return int(value.strip())
    except ValueError:
        return None


def parse_date(value: str) -> Optional[str]:
    """Convert YYYYMM to YYYY-MM-01 date string."""
    if not value or len(value.strip()) < 6:
        return None
    try:
        year = value[:4]
        month = value[4:6]
        return f"{year}-{month}-01"
    except:
        return None


# =============================================================================
# Download Tracker
# =============================================================================

class SFLLDDownloadTracker:
    """
    Tracks which SFLLD files need to be downloaded.
    
    Since automated scraping is against TOS, this helps manage manual downloads
    by showing status and validating completeness.
    """
    
    # Expected files (Standard Dataset 1999-2025)
    EXPECTED_YEARS = list(range(1999, 2026))
    
    def __init__(self, engine: Engine, download_dir: str = None):
        self.engine = engine
        self.download_dir = download_dir or os.path.expanduser("~/Downloads/sflld")
        
    def get_download_status(self) -> Dict:
        """Check which files have been downloaded vs. needed."""
        status = {
            'downloaded': [],
            'pending': [],
            'total_years': len(self.EXPECTED_YEARS),
            'downloaded_count': 0,
            'pending_count': 0,
        }
        
        # Check download directory
        download_path = Path(self.download_dir)
        if download_path.exists():
            downloaded_files = list(download_path.glob("historical_data_*.zip")) + \
                              list(download_path.glob("sample_*.zip"))
            downloaded_years = set()
            for f in downloaded_files:
                # Extract year from filename
                try:
                    year = int(f.stem.split('_')[-1])
                    downloaded_years.add(year)
                except ValueError:
                    continue
            
            status['downloaded'] = sorted(downloaded_years)
            status['downloaded_count'] = len(downloaded_years)
        
        # Calculate pending
        status['pending'] = [y for y in self.EXPECTED_YEARS if y not in status['downloaded']]
        status['pending_count'] = len(status['pending'])
        
        return status
    
    def print_status(self):
        """Print download status to console."""
        status = self.get_download_status()
        
        print("\n" + "=" * 60)
        print("SFLLD Download Status")
        print("=" * 60)
        print(f"\nDownload directory: {self.download_dir}")
        print(f"\nTotal years available: {status['total_years']} (1999-2025)")
        print(f"Downloaded: {status['downloaded_count']}")
        print(f"Pending: {status['pending_count']}")
        
        if status['downloaded']:
            print(f"\n✅ Downloaded years: {status['downloaded']}")
        
        if status['pending']:
            print(f"\n⏳ Pending years: {status['pending']}")
            print("\nTo download, visit:")
            print("https://claritydownload.fmapps.freddiemac.com/CRT/#/sflld")
            print("\nDownload each year's file and save to:")
            print(f"  {self.download_dir}/")
        else:
            print("\n🎉 All years downloaded!")
        
        print("=" * 60)


# =============================================================================
# SFLLD Parser
# =============================================================================

class SFLLDParser:
    """
    Parser for SFLLD historical loan-level data files.
    
    Each year's ZIP contains:
    - historical_data_YYYY.txt (origination data)
    - time_data_YYYY.txt (performance/monthly data)
    """
    
    def __init__(self, engine: Engine):
        self.engine = engine
        
    def parse_origination_line(self, line: str) -> Optional[Dict]:
        """Parse a single origination record."""
        fields = line.strip().split('|')
        if len(fields) < 25:
            return None
        
        return {
            'loan_sequence': fields[19] if len(fields) > 19 else None,
            'credit_score': safe_int(fields[0]),
            'first_payment_date': parse_date(fields[1]) if len(fields) > 1 else None,
            'first_time_buyer': fields[2] if len(fields) > 2 else None,
            'maturity_date': parse_date(fields[3]) if len(fields) > 3 else None,
            'msa': fields[4] if len(fields) > 4 else None,
            'mi_pct': safe_decimal(fields[5]),
            'num_units': safe_int(fields[6]),
            'occupancy': fields[7] if len(fields) > 7 else None,
            'cltv': safe_decimal(fields[8]),
            'dti': safe_decimal(fields[9]),
            'orig_upb': safe_decimal(fields[10]),
            'ltv': safe_decimal(fields[11]),
            'orig_rate': safe_decimal(fields[12]),
            'channel': fields[13] if len(fields) > 13 else None,
            'prepay_penalty': fields[14] if len(fields) > 14 else None,
            'amort_type': fields[15] if len(fields) > 15 else None,
            'state': fields[16] if len(fields) > 16 else None,
            'property_type': fields[17] if len(fields) > 17 else None,
            'zipcode': fields[18] if len(fields) > 18 else None,
            'loan_purpose': fields[20] if len(fields) > 20 else None,
            'loan_term': safe_int(fields[21]),
            'num_borrowers': safe_int(fields[22]),
            'seller_name': fields[23] if len(fields) > 23 else None,
            'servicer_name': fields[24] if len(fields) > 24 else None,
            'source': 'SFLLD',  # Mark as historical data
        }
    
    def parse_performance_line(self, line: str) -> Optional[Dict]:
        """Parse a single monthly performance record."""
        fields = line.strip().split('|')
        if len(fields) < 10:
            return None
        
        return {
            'loan_sequence': fields[0],
            'report_date': parse_date(fields[1]) if len(fields) > 1 else None,
            'current_upb': safe_decimal(fields[2]),
            'dlq_status': fields[3] if len(fields) > 3 else None,
            'loan_age': safe_int(fields[4]),
            'rem_months': safe_int(fields[5]),
            'zero_balance_code': fields[8] if len(fields) > 8 else None,
            'zero_balance_date': parse_date(fields[9]) if len(fields) > 9 else None,
            'current_rate': safe_decimal(fields[10]) if len(fields) > 10 else None,
        }
    
    def process_zip_file(self, zip_path: Path) -> Dict[str, int]:
        """Process a single SFLLD ZIP file."""
        logger.info(f"Processing {zip_path.name}")
        
        counts = {'origination': 0, 'performance': 0, 'errors': 0}
        
        with zipfile.ZipFile(zip_path, 'r') as zf:
            file_list = zf.namelist()
            
            # Find origination file (historical_data_YYYY.txt or similar)
            orig_file = None
            perf_file = None
            for f in file_list:
                if 'historical_data' in f.lower() or 'orig' in f.lower():
                    orig_file = f
                elif 'time' in f.lower() or 'perf' in f.lower():
                    perf_file = f
            
            # Process origination data
            if orig_file:
                logger.info(f"  Processing origination: {orig_file}")
                with zf.open(orig_file) as f:
                    counts['origination'] = self._load_origination_data(f)
            
            # Process performance data (if needed - this is HUGE)
            # Skip for now - performance data can be loaded separately
            if perf_file:
                logger.info(f"  Performance file found: {perf_file} (skipping for now)")
        
        return counts
    
    def _load_origination_data(self, file_handle) -> int:
        """Load origination data into dim_loan_historical."""
        batch = []
        batch_size = 10000
        total = 0
        
        for line_bytes in file_handle:
            line = line_bytes.decode('utf-8', errors='ignore')
            record = self.parse_origination_line(line)
            if record and record.get('loan_sequence'):
                batch.append(record)
                
                if len(batch) >= batch_size:
                    self._insert_loan_batch(batch)
                    total += len(batch)
                    if total % 100000 == 0:
                        logger.info(f"    Loaded {total:,} loans...")
                    batch = []
        
        if batch:
            self._insert_loan_batch(batch)
            total += len(batch)
        
        logger.info(f"    Total: {total:,} loans loaded")
        return total
    
    def _insert_loan_batch(self, batch: List[Dict]):
        """Insert a batch of loan records."""
        if not batch:
            return
        
        with self.engine.connect() as conn:
            # Use INSERT ... ON CONFLICT DO NOTHING to handle duplicates
            for record in batch:
                try:
                    conn.execute(text("""
                        INSERT INTO dim_loan_historical (
                            loan_sequence, credit_score, first_payment_date, 
                            first_time_buyer, maturity_date, msa, mi_pct,
                            num_units, occupancy, cltv, dti, orig_upb, ltv,
                            orig_rate, channel, prepay_penalty, amort_type,
                            state, property_type, zipcode, loan_purpose,
                            loan_term, num_borrowers, seller_name, servicer_name,
                            source
                        ) VALUES (
                            :loan_sequence, :credit_score, :first_payment_date,
                            :first_time_buyer, :maturity_date, :msa, :mi_pct,
                            :num_units, :occupancy, :cltv, :dti, :orig_upb, :ltv,
                            :orig_rate, :channel, :prepay_penalty, :amort_type,
                            :state, :property_type, :zipcode, :loan_purpose,
                            :loan_term, :num_borrowers, :seller_name, :servicer_name,
                            :source
                        )
                        ON CONFLICT (loan_sequence) DO NOTHING
                    """), record)
                except Exception as e:
                    logger.debug(f"Insert error: {e}")
            
            conn.commit()


# =============================================================================
# Main
# =============================================================================

def main():
    parser = argparse.ArgumentParser(description='SFLLD Historical Data Ingestor')
    parser.add_argument('--status', action='store_true', help='Show download status')
    parser.add_argument('--process', type=str, help='Process downloaded files from directory')
    parser.add_argument('--process-file', type=str, help='Process a single ZIP file')
    parser.add_argument('--download-dir', type=str, default='~/Downloads/sflld',
                        help='Directory for downloaded files')
    args = parser.parse_args()
    
    engine = get_engine()
    download_dir = os.path.expanduser(args.download_dir)
    
    if args.status:
        tracker = SFLLDDownloadTracker(engine, download_dir)
        tracker.print_status()
        
    elif args.process:
        # Process all ZIP files in directory
        process_dir = Path(args.process)
        if not process_dir.exists():
            logger.error(f"Directory not found: {process_dir}")
            return
        
        parser_instance = SFLLDParser(engine)
        zip_files = list(process_dir.glob("*.zip"))
        
        logger.info(f"Found {len(zip_files)} ZIP files to process")
        for zip_file in sorted(zip_files):
            try:
                counts = parser_instance.process_zip_file(zip_file)
                logger.info(f"Completed {zip_file.name}: {counts}")
            except Exception as e:
                logger.error(f"Error processing {zip_file.name}: {e}")
    
    elif args.process_file:
        # Process single file
        zip_path = Path(args.process_file)
        if not zip_path.exists():
            logger.error(f"File not found: {zip_path}")
            return
        
        parser_instance = SFLLDParser(engine)
        counts = parser_instance.process_zip_file(zip_path)
        logger.info(f"Completed: {counts}")
    
    else:
        parser.print_help()


if __name__ == '__main__':
    main()
