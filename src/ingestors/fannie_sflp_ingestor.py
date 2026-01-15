"""
Fannie Mae SFLP (Single-Family Loan Performance) Data Ingestor

Downloads and ingests Fannie Mae historical loan-level data (2000-2025).
Data source: Data Dynamics Platform (datadynamics.fanniemae.com)

The Performance_All.zip contains combined acquisition + performance data.
Each row has both loan characteristics AND monthly performance snapshot.

Usage:
    python -m src.ingestors.fannie_sflp_ingestor --status
    python -m src.ingestors.fannie_sflp_ingestor --process ~/Downloads
    python -m src.ingestors.fannie_sflp_ingestor --process-file ~/Downloads/Performance_All.zip
    python -m src.ingestors.fannie_sflp_ingestor --process-gcs gs://oasive-raw-data/fannie/sflp
"""

import os
import sys
import zipfile
import logging
import tempfile
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional, Set
from decimal import Decimal, InvalidOperation
import argparse
import io

# GCS imports
try:
    from google.cloud import storage
    HAS_GCS = True
except ImportError:
    HAS_GCS = False

from sqlalchemy import text
from sqlalchemy.engine import Engine

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.db.connection import get_engine

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# =============================================================================
# FANNIE MAE COMBINED FILE LAYOUT
# Performance_All.zip contains pipe-delimited files with ~108 columns
# Each row combines acquisition (static) + performance (monthly) data
# =============================================================================

# Column positions in the combined file (0-indexed)
# Based on Fannie Mae Single-Family Loan Performance Data File Layout
COMBINED_COLUMNS = {
    'loan_id': 0,
    'report_period': 1,           # MMYYYY
    'channel': 2,                 # R=Retail, B=Broker, C=Correspondent
    'seller_name': 3,
    'servicer_name': 4,
    'master_servicer': 5,
    'orig_rate': 6,
    'current_rate': 7,
    'orig_upb': 8,
    'issuance_upb': 9,
    'current_upb': 10,
    'orig_loan_term': 11,
    'orig_date': 12,              # MMYYYY
    'first_payment_date': 13,
    'loan_age': 14,
    'rem_months': 15,
    'adj_rem_months': 16,
    'maturity_date': 17,
    'ltv': 18,
    'cltv': 19,
    'num_borrowers': 20,
    'dti': 21,
    'fico': 22,
    'co_borrower_fico': 23,
    'first_time_buyer': 24,
    'loan_purpose': 25,           # P=Purchase, C=Cash-out Refi, N=No Cash-out Refi, R=Refi
    'property_type': 26,          # SF/PU/CO/MH/CP
    'num_units': 27,
    'occupancy': 28,              # P=Principal, I=Investment, S=Second Home
    'state': 29,
    'msa': 30,
    'zipcode': 31,
    'mi_pct': 32,
    'product_type': 33,           # FRM/ARM
    'prepay_penalty': 34,
    'io_indicator': 35,
    # ... more columns ...
    'dlq_status': 36,             # Current delinquency
    'modification_flag': 37,
    # ... 
    'zero_balance_code': 42,      # Prepay/default reason (varies by file version)
    'zero_balance_date': 43,
}

# Zero Balance Codes for prepay analysis
ZERO_BALANCE_CODES = {
    '01': 'Prepaid or Matured',
    '02': 'Third Party Sale',
    '03': 'Short Sale/Deed-in-Lieu',
    '06': 'Repurchased',
    '09': 'REO Disposition',
    '15': 'Note Sale',
    '16': 'Reperforming Loan Sale',
    '96': 'Inactive',
    '97': 'Removed',
}


# =============================================================================
# Helper Functions
# =============================================================================

def safe_decimal(value: str) -> Optional[Decimal]:
    """Convert string to Decimal."""
    if not value or value.strip() == '':
        return None
    try:
        return Decimal(value.strip())
    except (InvalidOperation, ValueError):
        return None


def safe_int(value: str) -> Optional[int]:
    """Convert string to int."""
    if not value or value.strip() == '':
        return None
    try:
        return int(float(value.strip()))
    except (ValueError, TypeError):
        return None


def parse_date_mmyyyy(value: str) -> Optional[str]:
    """Convert MMYYYY to YYYY-MM-01."""
    if not value or len(value.strip()) < 6:
        return None
    value = value.strip()
    try:
        if len(value) == 6:
            month = int(value[:2])
            year = int(value[2:6])
            if 1 <= month <= 12 and 1990 <= year <= 2030:
                return f"{year}-{month:02d}-01"
        return None
    except:
        return None


# =============================================================================
# Fannie Mae Combined Parser
# =============================================================================

class FannieCombinedParser:
    """
    Parser for Fannie Mae combined acquisition + performance files.
    
    Strategy:
    1. First pass: Extract unique loans (origination data from first occurrence)
    2. Track zero_balance_code to identify prepay events
    """
    
    def __init__(self, engine: Engine):
        self.engine = engine
        self.batch_size = 10000
        self.seen_loans: Set[str] = set()
        self.loan_batch: List[Dict] = []
        self.perf_batch: List[Dict] = []
        self.total_loans = 0
        self.total_perf = 0
        
    def parse_line(self, line: str) -> Optional[Dict]:
        """Parse a single combined record."""
        # Handle leading pipe
        if line.startswith('|'):
            line = line[1:]
        
        fields = line.strip().split('|')
        if len(fields) < 30:
            return None
        
        def get(idx: int) -> str:
            return fields[idx] if idx < len(fields) else ''
        
        loan_id = get(0)
        if not loan_id:
            return None
        
        return {
            'loan_id': loan_id,
            'report_period': get(1),
            'channel': get(2) if get(2) in ['R', 'B', 'C', 'T'] else None,
            'seller_name': get(3) if get(3) != 'Other' else None,
            'servicer_name': get(4) if get(4) != 'Other' else None,
            'orig_rate': safe_decimal(get(6)),
            'current_rate': safe_decimal(get(7)),
            'orig_upb': safe_decimal(get(8)),
            'current_upb': safe_decimal(get(10)),
            'orig_loan_term': safe_int(get(11)),
            'orig_date': parse_date_mmyyyy(get(12)),
            'first_payment_date': parse_date_mmyyyy(get(13)),
            'loan_age': safe_int(get(14)),
            'rem_months': safe_int(get(15)),
            'maturity_date': parse_date_mmyyyy(get(17)),
            'ltv': safe_int(get(18)),
            'cltv': safe_int(get(19)),
            'num_borrowers': safe_int(get(20)),
            'dti': safe_int(get(21)),
            'fico': safe_int(get(22)),
            'co_borrower_fico': safe_int(get(23)),
            'first_time_buyer': get(24) if get(24) in ['Y', 'N'] else None,
            'loan_purpose': get(25) if get(25) in ['P', 'C', 'N', 'R'] else None,
            'property_type': get(26),
            'num_units': safe_int(get(27)),
            'occupancy': get(28) if get(28) in ['P', 'I', 'S'] else None,
            'state': get(29)[:2] if len(get(29)) >= 2 else None,
            'msa': get(30),
            'zipcode': get(31)[:3] if get(31) else None,
            'mi_pct': safe_decimal(get(32)),
            'product_type': get(33) if get(33) in ['FRM', 'ARM'] else None,
            'dlq_status': get(36) if len(fields) > 36 else None,
            'modification_flag': get(37) if len(fields) > 37 else None,
            # Zero balance fields - position varies, try common positions
            'zero_balance_code': self._find_zero_balance_code(fields),
        }
    
    def _find_zero_balance_code(self, fields: List[str]) -> Optional[str]:
        """Find zero balance code in the record (position varies by file version)."""
        # Common positions: 42, 43, 44
        for idx in [42, 43, 44, 45]:
            if idx < len(fields):
                val = fields[idx].strip()
                if val in ZERO_BALANCE_CODES:
                    return val
        return None
    
    def process_zip(self, zip_path: Path) -> Dict[str, int]:
        """Process a Fannie Mae ZIP file."""
        logger.info(f"Processing {zip_path.name} ({zip_path.stat().st_size / 1e9:.1f} GB)")
        
        counts = {'loans_new': 0, 'loans_existing': 0, 'performance': 0, 'prepays': 0}
        
        # Load existing loan IDs to avoid duplicates
        logger.info("Loading existing loan IDs from database...")
        with self.engine.connect() as conn:
            result = conn.execute(text("SELECT loan_id FROM dim_loan_fannie_historical"))
            self.seen_loans = {r[0] for r in result}
        logger.info(f"  Found {len(self.seen_loans):,} existing loans")
        
        with zipfile.ZipFile(zip_path, 'r') as zf:
            files = sorted([f for f in zf.namelist() if f.endswith('.csv')],
                          key=lambda x: x)  # Process in chronological order
            
            logger.info(f"Found {len(files)} quarterly files")
            
            for filename in files:
                logger.info(f"  Processing {filename}...")
                file_counts = self._process_quarterly_file(zf, filename)
                counts['loans_new'] += file_counts.get('loans_new', 0)
                counts['performance'] += file_counts.get('performance', 0)
                counts['prepays'] += file_counts.get('prepays', 0)
        
        return counts
    
    def _process_quarterly_file(self, zf: zipfile.ZipFile, filename: str) -> Dict[str, int]:
        """Process a single quarterly CSV file."""
        counts = {'loans_new': 0, 'performance': 0, 'prepays': 0}
        
        with zf.open(filename) as f:
            line_count = 0
            for line_bytes in f:
                try:
                    line = line_bytes.decode('utf-8', errors='ignore')
                except:
                    continue
                
                record = self.parse_line(line)
                if not record:
                    continue
                
                loan_id = record['loan_id']
                line_count += 1
                
                # New loan - extract origination data
                if loan_id not in self.seen_loans:
                    self.seen_loans.add(loan_id)
                    self.loan_batch.append({
                        'loan_id': loan_id,
                        'channel': record['channel'],
                        'seller_name': record['seller_name'],
                        'servicer_name': record['servicer_name'],
                        'orig_rate': record['orig_rate'],
                        'orig_upb': record['orig_upb'],
                        'orig_loan_term': record['orig_loan_term'],
                        'orig_date': record['orig_date'],
                        'first_payment_date': record['first_payment_date'],
                        'ltv': record['ltv'],
                        'cltv': record['cltv'],
                        'num_borrowers': record['num_borrowers'],
                        'dti': record['dti'],
                        'fico': record['fico'],
                        'co_borrower_fico': record['co_borrower_fico'],
                        'first_time_buyer': record['first_time_buyer'],
                        'loan_purpose': record['loan_purpose'],
                        'property_type': record['property_type'],
                        'num_units': record['num_units'],
                        'occupancy': record['occupancy'],
                        'state': record['state'],
                        'zipcode': record['zipcode'],
                        'mi_pct': record['mi_pct'],
                        'product_type': record['product_type'],
                        'source': 'FANNIE_SFLP',
                    })
                    counts['loans_new'] += 1
                
                # Track prepay events (zero_balance_code = 01)
                if record.get('zero_balance_code') == '01':
                    counts['prepays'] += 1
                
                counts['performance'] += 1
                
                # Flush batches
                if len(self.loan_batch) >= self.batch_size:
                    self._flush_loan_batch()
                
                if line_count % 1000000 == 0:
                    logger.info(f"    Processed {line_count:,} lines, {counts['loans_new']:,} new loans")
        
        # Final flush
        self._flush_loan_batch()
        
        logger.info(f"    {filename}: {counts['loans_new']:,} new loans, {counts['prepays']:,} prepays")
        return counts
    
    def _flush_loan_batch(self):
        """Insert loan batch into database."""
        if not self.loan_batch:
            return
        
        try:
            with self.engine.connect() as conn:
                stmt = text("""
                    INSERT INTO dim_loan_fannie_historical (
                        loan_id, channel, seller_name, servicer_name,
                        orig_rate, orig_upb, orig_loan_term, orig_date,
                        first_payment_date, ltv, cltv, num_borrowers,
                        dti, fico, co_borrower_fico, first_time_buyer,
                        loan_purpose, property_type, num_units, occupancy,
                        state, zipcode, mi_pct, product_type, source
                    ) VALUES (
                        :loan_id, :channel, :seller_name, :servicer_name,
                        :orig_rate, :orig_upb, :orig_loan_term, :orig_date,
                        :first_payment_date, :ltv, :cltv, :num_borrowers,
                        :dti, :fico, :co_borrower_fico, :first_time_buyer,
                        :loan_purpose, :property_type, :num_units, :occupancy,
                        :state, :zipcode, :mi_pct, :product_type, :source
                    )
                    ON CONFLICT (loan_id) DO NOTHING
                """)
                conn.execute(stmt, self.loan_batch)
                conn.commit()
                self.total_loans += len(self.loan_batch)
        except Exception as e:
            logger.error(f"Loan batch insert failed: {e}")
        
        self.loan_batch = []


# =============================================================================
# Status Tracker
# =============================================================================

class FannieSFLPTracker:
    """Track Fannie Mae SFLP processing status."""
    
    def __init__(self, engine: Engine):
        self.engine = engine
    
    def print_status(self):
        """Print current status."""
        print("\n" + "=" * 60)
        print("Fannie Mae SFLP Status")
        print("=" * 60)
        
        try:
            with self.engine.connect() as conn:
                result = conn.execute(text("SELECT COUNT(*) FROM dim_loan_fannie_historical"))
                loan_count = result.fetchone()[0]
                print(f"\n📊 Loans loaded: {loan_count:,}")
                
                if loan_count > 0:
                    # Sample stats
                    result = conn.execute(text("""
                        SELECT 
                            MIN(orig_date) as earliest,
                            MAX(orig_date) as latest,
                            COUNT(DISTINCT state) as states,
                            AVG(fico) as avg_fico
                        FROM dim_loan_fannie_historical
                        WHERE orig_date IS NOT NULL
                    """))
                    row = result.fetchone()
                    print(f"   Date range: {row[0]} to {row[1]}")
                    print(f"   States: {row[2]}")
                    print(f"   Avg FICO: {row[3]:.0f}" if row[3] else "")
        except Exception as e:
            print(f"   Table not created or empty")
        
        print("\n📥 To load data:")
        print("   python -m src.ingestors.fannie_sflp_ingestor --process-file ~/Downloads/Performance_All.zip")
        print("=" * 60)


# =============================================================================
# GCS Processor
# =============================================================================

class GCSFannieProcessor:
    """Process Fannie Mae files directly from GCS."""
    
    def __init__(self, engine, gcs_path: str):
        self.engine = engine
        self.gcs_path = gcs_path
        
        if not HAS_GCS:
            raise RuntimeError("google-cloud-storage not installed")
        
        # Parse GCS path
        if gcs_path.startswith('gs://'):
            parts = gcs_path[5:].split('/', 1)
            self.bucket_name = parts[0]
            self.prefix = parts[1] if len(parts) > 1 else ''
        else:
            raise ValueError(f"Invalid GCS path: {gcs_path}")
        
        self.client = storage.Client()
        self.bucket = self.client.bucket(self.bucket_name)
        
    def process(self):
        """Process all ZIP files in the GCS path."""
        logger.info(f"Processing files from {self.gcs_path}")
        
        # List all ZIP files
        blobs = list(self.bucket.list_blobs(prefix=self.prefix))
        zip_blobs = [b for b in blobs if b.name.endswith('.zip')]
        
        if not zip_blobs:
            logger.warning(f"No ZIP files found in {self.gcs_path}")
            return
        
        logger.info(f"Found {len(zip_blobs)} ZIP files")
        
        for blob in zip_blobs:
            self._process_gcs_zip(blob)
    
    def _process_gcs_zip(self, blob):
        """Download and process a single ZIP from GCS."""
        logger.info(f"Downloading {blob.name} ({blob.size / 1e9:.1f} GB)...")
        
        # Download to temp file (ZIP is too large for memory)
        with tempfile.NamedTemporaryFile(suffix='.zip', delete=False) as tmp:
            tmp_path = tmp.name
            blob.download_to_filename(tmp_path)
        
        logger.info(f"Downloaded to {tmp_path}")
        
        try:
            # Process the ZIP
            parser = FannieCombinedParser(self.engine)
            counts = parser.process_zip(Path(tmp_path))
            
            logger.info(f"Completed {blob.name}:")
            logger.info(f"  New loans: {counts['loans_new']:,}")
            logger.info(f"  Prepay events: {counts['prepays']:,}")
            logger.info(f"  Total records: {counts['performance']:,}")
        finally:
            # Clean up temp file
            os.unlink(tmp_path)
            logger.info(f"Cleaned up temp file")


# =============================================================================
# Main
# =============================================================================

def main():
    parser = argparse.ArgumentParser(description='Fannie Mae SFLP Ingestor')
    parser.add_argument('--status', action='store_true', help='Show status')
    parser.add_argument('--process', type=str, help='Process files from directory')
    parser.add_argument('--process-file', type=str, help='Process a single ZIP file')
    parser.add_argument('--process-gcs', type=str, help='Process files from GCS (gs://bucket/path)')
    args = parser.parse_args()
    
    engine = get_engine()
    
    if args.status:
        tracker = FannieSFLPTracker(engine)
        tracker.print_status()
    
    elif args.process_gcs:
        if not HAS_GCS:
            logger.error("google-cloud-storage not installed. Run: pip install google-cloud-storage")
            return
        
        processor = GCSFannieProcessor(engine, args.process_gcs)
        processor.process()
    
    elif args.process_file:
        zip_path = Path(args.process_file)
        if not zip_path.exists():
            logger.error(f"File not found: {zip_path}")
            return
        
        parser_instance = FannieCombinedParser(engine)
        counts = parser_instance.process_zip(zip_path)
        
        logger.info(f"\n{'='*60}")
        logger.info(f"COMPLETED")
        logger.info(f"  New loans: {counts['loans_new']:,}")
        logger.info(f"  Prepay events: {counts['prepays']:,}")
        logger.info(f"  Total records: {counts['performance']:,}")
        logger.info(f"{'='*60}")
    
    elif args.process:
        process_dir = Path(args.process)
        if not process_dir.exists():
            logger.error(f"Directory not found: {process_dir}")
            return
        
        parser_instance = FannieCombinedParser(engine)
        
        zip_files = list(process_dir.glob("*.zip"))
        for zip_file in sorted(zip_files):
            if 'Performance' in zip_file.name or 'Acquisition' in zip_file.name:
                counts = parser_instance.process_zip(zip_file)
                logger.info(f"Completed {zip_file.name}: {counts}")
    
    else:
        parser.print_help()


if __name__ == '__main__':
    main()
