"""
Fannie Mae SFLP (Single-Family Loan Performance) Data Ingestor

Downloads and ingests Fannie Mae historical loan-level data (2000-2025).
Data source: Data Dynamics Platform (datadynamics.fanniemae.com)

Coverage: ~62 million loans over 25 years

Usage:
    # Show status of downloaded files
    python -m src.ingestors.fannie_sflp_ingestor --status

    # Process downloaded files from local directory
    python -m src.ingestors.fannie_sflp_ingestor --process ~/Downloads/fannie_sflp

    # Process files from GCS
    python -m src.ingestors.fannie_sflp_ingestor --process-gcs gs://oasive-raw-data/fannie/sflp
"""

import os
import sys
import zipfile
import logging
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional
from decimal import Decimal, InvalidOperation
import argparse
import io

from google.cloud import storage
from sqlalchemy import text
from sqlalchemy.engine import Engine

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.db.connection import get_engine
from src.config import GCSConfig

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# =============================================================================
# FANNIE MAE FILE LAYOUT (from Data Dynamics documentation)
# =============================================================================

# Acquisition File Columns (pipe-delimited, no header)
# Source: Fannie Mae Single-Family Loan Performance Data Glossary
ACQUISITION_COLUMNS = [
    'loan_id',                    # 1: Fannie Mae Loan Identifier
    'channel',                    # 2: Origination Channel
    'seller_name',                # 3: Seller Name
    'orig_rate',                  # 4: Original Interest Rate
    'orig_upb',                   # 5: Original UPB
    'orig_loan_term',             # 6: Original Loan Term
    'orig_date',                  # 7: Origination Date (MMYYYY or YYYYMM)
    'first_payment_date',         # 8: First Payment Date
    'ltv',                        # 9: Original LTV
    'cltv',                       # 10: Original CLTV
    'num_borrowers',              # 11: Number of Borrowers
    'dti',                        # 12: Original DTI
    'fico',                       # 13: Borrower Credit Score
    'first_time_buyer',           # 14: First-Time Homebuyer Flag
    'loan_purpose',               # 15: Loan Purpose
    'property_type',              # 16: Property Type
    'num_units',                  # 17: Number of Units
    'occupancy',                  # 18: Occupancy Status
    'state',                      # 19: Property State
    'zipcode',                    # 20: Zip Code (3-digit)
    'mi_pct',                     # 21: Mortgage Insurance Percentage
    'product_type',               # 22: Product Type (FRM/ARM)
    'co_borrower_fico',           # 23: Co-Borrower Credit Score
    'mi_type',                    # 24: Mortgage Insurance Type
    'relocation_mortgage',        # 25: Relocation Mortgage Indicator
    # Additional columns added in later versions:
    'property_valuation',         # 26: Property Valuation Method (if present)
    'io_indicator',               # 27: Interest Only Indicator (if present)
    'mi_cancellation',            # 28: MI Cancellation Indicator (if present)
]

# Performance File Columns (pipe-delimited, no header)
PERFORMANCE_COLUMNS = [
    'loan_id',                    # 1: Fannie Mae Loan Identifier
    'report_date',                # 2: Monthly Reporting Period (MMDDYYYY or YYYYMM)
    'servicer_name',              # 3: Current Servicer Name
    'current_rate',               # 4: Current Interest Rate
    'current_upb',                # 5: Current Actual UPB
    'loan_age',                   # 6: Loan Age
    'rem_months',                 # 7: Remaining Months to Legal Maturity
    'adj_rem_months',             # 8: Adjusted Remaining Months (for modifications)
    'maturity_date',              # 9: Maturity Date
    'msa',                        # 10: Metropolitan Statistical Area
    'dlq_status',                 # 11: Current Loan Delinquency Status
    'modification_flag',          # 12: Modification Flag
    'zero_balance_code',          # 13: Zero Balance Code
    'zero_balance_date',          # 14: Zero Balance Effective Date
    'last_paid_date',             # 15: Last Paid Installment Date
    'foreclosure_date',           # 16: Foreclosure Date
    'disposition_date',           # 17: Disposition Date
    'foreclosure_costs',          # 18: Foreclosure Costs
    'property_preservation',      # 19: Property Preservation Costs
    'asset_recovery',             # 20: Asset Recovery Costs
    'misc_expenses',              # 21: Miscellaneous Holding Expenses
    'taxes_held',                 # 22: Taxes and Insurance Held
    'net_proceeds',               # 23: Net Sale Proceeds
    'credit_enhancement',         # 24: Credit Enhancement Proceeds
    'reo_proceeds',               # 25: REO Management Expenses
    'other_fc_expenses',          # 26: Other Foreclosure Proceeds
    'non_interest_bearing_upb',   # 27: Non-Interest Bearing UPB
    'principal_forgiveness',      # 28: Principal Forgiveness Amount
    'repurchase_make_whole',      # 29: Repurchase Make Whole Proceeds
    'foreclosure_principal',      # 30: Foreclosure Principal Write-off
    'servicing_activity',         # 31: Servicing Activity Indicator
]

# Zero Balance Codes (same across GSEs)
ZERO_BALANCE_CODES = {
    '01': 'Prepaid or Matured',
    '02': 'Third Party Sale',
    '03': 'Short Sale',
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
    except (InvalidOperation, ValueError):
        return None


def safe_int(value: str) -> Optional[int]:
    """Convert string to int, handling empty/invalid values."""
    if not value or value.strip() == '':
        return None
    try:
        return int(float(value.strip()))
    except (ValueError, TypeError):
        return None


def parse_date(value: str) -> Optional[str]:
    """Convert various date formats to YYYY-MM-01."""
    if not value or len(value.strip()) < 6:
        return None
    
    value = value.strip()
    try:
        # Try MMYYYY format (Fannie Mae acquisition files)
        if len(value) == 6:
            try:
                month = int(value[:2])
                year = int(value[2:6])
                if 1 <= month <= 12 and 1990 <= year <= 2030:
                    return f"{year}-{month:02d}-01"
            except:
                pass
            
            # Try YYYYMM format
            try:
                year = int(value[:4])
                month = int(value[4:6])
                if 1 <= month <= 12 and 1990 <= year <= 2030:
                    return f"{year}-{month:02d}-01"
            except:
                pass
        
        # Try MMDDYYYY format (Fannie Mae performance files)
        if len(value) == 8:
            try:
                month = int(value[:2])
                year = int(value[4:8])
                if 1 <= month <= 12 and 1990 <= year <= 2030:
                    return f"{year}-{month:02d}-01"
            except:
                pass
            
            # Try YYYYMMDD format
            try:
                year = int(value[:4])
                month = int(value[4:6])
                if 1 <= month <= 12 and 1990 <= year <= 2030:
                    return f"{year}-{month:02d}-01"
            except:
                pass
        
        return None
    except:
        return None


# =============================================================================
# Fannie Mae SFLP Parser
# =============================================================================

class FannieSFLPParser:
    """
    Parser for Fannie Mae Single-Family Loan Performance data files.
    
    Each quarterly dataset contains:
    - Acquisition_YYYYQX.txt (loan characteristics at origination)
    - Performance_YYYYQX.txt (monthly performance updates)
    """
    
    def __init__(self, engine: Engine):
        self.engine = engine
        self.batch_size = 10000
        
    def parse_acquisition_line(self, line: str) -> Optional[Dict]:
        """Parse a single acquisition (origination) record."""
        fields = line.strip().split('|')
        if len(fields) < 20:
            return None
        
        # Get field with safe indexing
        def get_field(idx: int) -> str:
            return fields[idx] if idx < len(fields) else ''
        
        return {
            'loan_id': get_field(0),
            'channel': get_field(1),
            'seller_name': get_field(2),
            'orig_rate': safe_decimal(get_field(3)),
            'orig_upb': safe_decimal(get_field(4)),
            'orig_loan_term': safe_int(get_field(5)),
            'orig_date': parse_date(get_field(6)),
            'first_payment_date': parse_date(get_field(7)),
            'ltv': safe_decimal(get_field(8)),
            'cltv': safe_decimal(get_field(9)),
            'num_borrowers': safe_int(get_field(10)),
            'dti': safe_decimal(get_field(11)),
            'fico': safe_int(get_field(12)),
            'first_time_buyer': get_field(13) if get_field(13) in ['Y', 'N'] else None,
            'loan_purpose': get_field(14),
            'property_type': get_field(15),
            'num_units': safe_int(get_field(16)),
            'occupancy': get_field(17),
            'state': get_field(18)[:2] if get_field(18) else None,
            'zipcode': get_field(19)[:3] if get_field(19) else None,
            'mi_pct': safe_decimal(get_field(20)),
            'product_type': get_field(21) if len(fields) > 21 else None,
            'co_borrower_fico': safe_int(get_field(22)) if len(fields) > 22 else None,
            'mi_type': get_field(23) if len(fields) > 23 else None,
            'relocation_mortgage': get_field(24) if len(fields) > 24 else None,
            'source': 'FANNIE_SFLP',
        }
    
    def parse_performance_line(self, line: str) -> Optional[Dict]:
        """Parse a single monthly performance record."""
        fields = line.strip().split('|')
        if len(fields) < 10:
            return None
        
        def get_field(idx: int) -> str:
            return fields[idx] if idx < len(fields) else ''
        
        return {
            'loan_id': get_field(0),
            'report_date': parse_date(get_field(1)),
            'servicer_name': get_field(2) if len(fields) > 2 else None,
            'current_rate': safe_decimal(get_field(3)),
            'current_upb': safe_decimal(get_field(4)),
            'loan_age': safe_int(get_field(5)),
            'rem_months': safe_int(get_field(6)),
            'dlq_status': get_field(10) if len(fields) > 10 else None,
            'modification_flag': get_field(11) if len(fields) > 11 else None,
            'zero_balance_code': get_field(12) if len(fields) > 12 else None,
            'zero_balance_date': parse_date(get_field(13)) if len(fields) > 13 else None,
        }
    
    def process_zip_file(self, zip_path: Path) -> Dict[str, int]:
        """Process a single Fannie Mae ZIP file."""
        logger.info(f"Processing {zip_path.name}")
        
        counts = {'acquisition': 0, 'performance': 0, 'errors': 0}
        
        with zipfile.ZipFile(zip_path, 'r') as zf:
            file_list = zf.namelist()
            
            # Check for nested ZIPs (quarterly structure)
            nested_zips = [f for f in file_list if f.endswith('.zip')]
            
            if nested_zips:
                # Process each nested ZIP
                for nested_zip_name in sorted(nested_zips):
                    logger.info(f"  Processing: {nested_zip_name}")
                    try:
                        with zf.open(nested_zip_name) as nz_data:
                            nested_bytes = io.BytesIO(nz_data.read())
                            
                            with zipfile.ZipFile(nested_bytes, 'r') as inner_zf:
                                for inner_file in inner_zf.namelist():
                                    if inner_file.endswith('.txt'):
                                        counts = self._process_txt_file(
                                            inner_zf, inner_file, counts
                                        )
                    except Exception as e:
                        logger.error(f"    Error processing {nested_zip_name}: {e}")
                        counts['errors'] += 1
            else:
                # Direct TXT files
                for f in file_list:
                    if f.endswith('.txt'):
                        counts = self._process_txt_file(zf, f, counts)
        
        return counts
    
    def _process_txt_file(self, zf: zipfile.ZipFile, filename: str, counts: Dict) -> Dict:
        """Process a single TXT file from within a ZIP."""
        filename_lower = filename.lower()
        
        if 'acquisition' in filename_lower or 'acq' in filename_lower:
            logger.info(f"    Loading acquisition: {filename}")
            with zf.open(filename) as f:
                loaded = self._load_acquisition_data(f)
                counts['acquisition'] += loaded
        elif 'performance' in filename_lower or 'perf' in filename_lower:
            # Skip performance files for initial load (very large)
            logger.info(f"    Skipping performance: {filename} (load separately)")
        else:
            # Unknown file type - try to determine from content
            logger.info(f"    Unknown file type: {filename}")
        
        return counts
    
    def _load_acquisition_data(self, file_handle) -> int:
        """Load acquisition data into dim_loan_fannie_historical."""
        batch = []
        total = 0
        
        for line_bytes in file_handle:
            try:
                line = line_bytes.decode('utf-8', errors='ignore')
            except:
                continue
                
            record = self.parse_acquisition_line(line)
            if record and record.get('loan_id'):
                batch.append(record)
                
                if len(batch) >= self.batch_size:
                    self._insert_acquisition_batch(batch)
                    total += len(batch)
                    if total % 100000 == 0:
                        logger.info(f"      Loaded {total:,} loans...")
                    batch = []
        
        if batch:
            self._insert_acquisition_batch(batch)
            total += len(batch)
        
        logger.info(f"      Total: {total:,} loans loaded")
        return total
    
    def _insert_acquisition_batch(self, batch: List[Dict]):
        """Insert a batch of acquisition records."""
        if not batch:
            return
        
        try:
            with self.engine.connect() as conn:
                stmt = text("""
                    INSERT INTO dim_loan_fannie_historical (
                        loan_id, channel, seller_name, orig_rate, orig_upb,
                        orig_loan_term, orig_date, first_payment_date,
                        ltv, cltv, num_borrowers, dti, fico,
                        first_time_buyer, loan_purpose, property_type,
                        num_units, occupancy, state, zipcode,
                        mi_pct, product_type, co_borrower_fico, mi_type,
                        relocation_mortgage, source
                    ) VALUES (
                        :loan_id, :channel, :seller_name, :orig_rate, :orig_upb,
                        :orig_loan_term, :orig_date, :first_payment_date,
                        :ltv, :cltv, :num_borrowers, :dti, :fico,
                        :first_time_buyer, :loan_purpose, :property_type,
                        :num_units, :occupancy, :state, :zipcode,
                        :mi_pct, :product_type, :co_borrower_fico, :mi_type,
                        :relocation_mortgage, :source
                    )
                    ON CONFLICT (loan_id) DO NOTHING
                """)
                conn.execute(stmt, batch)
                conn.commit()
        except Exception as e:
            logger.error(f"Batch insert failed: {e}")
    
    def process_from_bytes(self, content: bytes, file_type: str = 'acquisition') -> int:
        """Process file content from bytes."""
        batch = []
        total = 0
        
        for line in content.decode('utf-8', errors='ignore').split('\n'):
            if not line.strip():
                continue
            
            if file_type == 'acquisition':
                record = self.parse_acquisition_line(line)
                if record and record.get('loan_id'):
                    batch.append(record)
            elif file_type == 'performance':
                record = self.parse_performance_line(line)
                if record and record.get('loan_id'):
                    batch.append(record)
            
            if len(batch) >= self.batch_size:
                if file_type == 'acquisition':
                    self._insert_acquisition_batch(batch)
                else:
                    self._insert_performance_batch(batch)
                total += len(batch)
                if total % 100000 == 0:
                    logger.info(f"    Loaded {total:,} records...")
                batch = []
        
        if batch:
            if file_type == 'acquisition':
                self._insert_acquisition_batch(batch)
            else:
                self._insert_performance_batch(batch)
            total += len(batch)
        
        return total
    
    def _insert_performance_batch(self, batch: List[Dict]):
        """Insert a batch of performance records."""
        if not batch:
            return
        
        try:
            with self.engine.connect() as conn:
                stmt = text("""
                    INSERT INTO fact_loan_month_fannie_historical (
                        loan_id, report_date, current_rate, current_upb,
                        loan_age, rem_months, dlq_status, modification_flag,
                        zero_balance_code, zero_balance_date
                    ) VALUES (
                        :loan_id, :report_date, :current_rate, :current_upb,
                        :loan_age, :rem_months, :dlq_status, :modification_flag,
                        :zero_balance_code, :zero_balance_date
                    )
                    ON CONFLICT (loan_id, report_date) DO NOTHING
                """)
                conn.execute(stmt, batch)
                conn.commit()
        except Exception as e:
            logger.error(f"Performance batch insert failed: {e}")


# =============================================================================
# GCS Processing Support
# =============================================================================

class GCSFannieSFLPProcessor:
    """Process Fannie Mae SFLP files from Google Cloud Storage."""
    
    def __init__(self, engine: Engine, gcs_path: str):
        self.engine = engine
        self.gcs_path = gcs_path
        self.storage_client = storage.Client()
        self.parser = FannieSFLPParser(engine)
        
        # Parse bucket and prefix from gs:// path
        path_parts = gcs_path.replace("gs://", "").split("/", 1)
        self.bucket_name = path_parts[0]
        self.prefix = path_parts[1] if len(path_parts) > 1 else ""
        self.bucket = self.storage_client.bucket(self.bucket_name)
    
    def process_all(self):
        """Process all Fannie Mae SFLP files from GCS."""
        import tempfile
        
        logger.info(f"Processing Fannie Mae SFLP from: gs://{self.bucket_name}/{self.prefix}")
        
        # Step 1: Process pre-extracted TXT files
        logger.info("Step 1: Looking for extracted TXT files...")
        self._process_extracted_files()
        
        # Step 2: Process ZIP files
        logger.info("Step 2: Processing ZIP files...")
        self._process_zip_files()
    
    def _process_extracted_files(self):
        """Process TXT files already extracted in GCS."""
        blobs = list(self.bucket.list_blobs(prefix=self.prefix))
        
        # Find acquisition files
        acq_files = [b for b in blobs 
                     if b.name.endswith('.txt') 
                     and ('acquisition' in b.name.lower() or 'acq' in b.name.lower())]
        
        logger.info(f"  Found {len(acq_files)} acquisition files")
        
        for blob in sorted(acq_files, key=lambda b: b.name):
            try:
                logger.info(f"  Processing: {blob.name}")
                content = blob.download_as_bytes()
                total = self.parser.process_from_bytes(content, 'acquisition')
                logger.info(f"    Loaded {total:,} loans")
            except Exception as e:
                logger.error(f"    Error: {e}")
    
    def _process_zip_files(self):
        """Process ZIP files from GCS."""
        blobs = list(self.bucket.list_blobs(prefix=self.prefix))
        
        zip_files = [b for b in blobs if b.name.endswith('.zip')]
        logger.info(f"  Found {len(zip_files)} ZIP files")
        
        for blob in sorted(zip_files, key=lambda b: b.name):
            try:
                logger.info(f"  Processing: {blob.name}")
                
                import tempfile
                with tempfile.NamedTemporaryFile(suffix='.zip', delete=False) as tmp:
                    blob.download_to_filename(tmp.name)
                    
                    counts = self.parser.process_zip_file(Path(tmp.name))
                    logger.info(f"    Completed: {counts}")
                    
                    os.unlink(tmp.name)
            except Exception as e:
                logger.error(f"    Error: {e}")


# =============================================================================
# Download Status Tracker
# =============================================================================

class FannieSFLPTracker:
    """Track download status for Fannie Mae SFLP files."""
    
    def __init__(self, engine: Engine, download_dir: str = None):
        self.engine = engine
        self.download_dir = download_dir or os.path.expanduser("~/Downloads/fannie_sflp")
    
    def print_status(self):
        """Print current download and processing status."""
        print("\n" + "=" * 60)
        print("Fannie Mae SFLP Download Status")
        print("=" * 60)
        print(f"\nDownload directory: {self.download_dir}")
        
        # Check local files
        download_path = Path(self.download_dir)
        if download_path.exists():
            zip_files = list(download_path.glob("*.zip"))
            txt_files = list(download_path.glob("*.txt"))
            print(f"\n📁 Local files:")
            print(f"  ZIP files: {len(zip_files)}")
            print(f"  TXT files: {len(txt_files)}")
        else:
            print(f"\n⚠️  Download directory not found")
        
        # Check database
        try:
            with self.engine.connect() as conn:
                result = conn.execute(text("SELECT COUNT(*) FROM dim_loan_fannie_historical"))
                loan_count = result.fetchone()[0]
                print(f"\n📊 Database status:")
                print(f"  Loans loaded: {loan_count:,}")
        except Exception as e:
            print(f"\n📊 Database status: Table not yet created")
        
        print("\n📥 To download data:")
        print("  1. Visit: https://datadynamics.fanniemae.com/data-dynamics/#/downloadLoanData/Single-Family")
        print("  2. Download the '2000Q1-2025Q2 Acquisition and Performance File'")
        print(f"  3. Save to: {self.download_dir}/")
        print("  4. Run: python -m src.ingestors.fannie_sflp_ingestor --process ~/Downloads/fannie_sflp")
        print("=" * 60)


# =============================================================================
# Main
# =============================================================================

def main():
    parser = argparse.ArgumentParser(description='Fannie Mae SFLP Historical Data Ingestor')
    parser.add_argument('--status', action='store_true', help='Show download status')
    parser.add_argument('--process', type=str, help='Process downloaded files from local directory')
    parser.add_argument('--process-file', type=str, help='Process a single ZIP file')
    parser.add_argument('--process-gcs', type=str, help='Process files from GCS path (gs://bucket/path)')
    parser.add_argument('--download-dir', type=str, default='~/Downloads/fannie_sflp',
                        help='Directory for downloaded files')
    args = parser.parse_args()
    
    engine = get_engine()
    download_dir = os.path.expanduser(args.download_dir)
    
    if args.status:
        tracker = FannieSFLPTracker(engine, download_dir)
        tracker.print_status()
    
    elif args.process_gcs:
        logger.info(f"Processing Fannie Mae SFLP from GCS: {args.process_gcs}")
        processor = GCSFannieSFLPProcessor(engine, args.process_gcs)
        processor.process_all()
    
    elif args.process:
        process_dir = Path(args.process)
        if not process_dir.exists():
            logger.error(f"Directory not found: {process_dir}")
            return
        
        parser_instance = FannieSFLPParser(engine)
        
        # Process TXT files first
        txt_files = list(process_dir.glob("*.txt"))
        acq_files = [f for f in txt_files 
                     if 'acquisition' in f.name.lower() or 'acq' in f.name.lower()]
        
        if acq_files:
            logger.info(f"Found {len(acq_files)} acquisition TXT files")
            for txt_file in sorted(acq_files):
                try:
                    logger.info(f"Processing {txt_file.name}")
                    with open(txt_file, 'rb') as f:
                        content = f.read()
                        total = parser_instance.process_from_bytes(content, 'acquisition')
                        logger.info(f"  Loaded {total:,} loans")
                except Exception as e:
                    logger.error(f"Error processing {txt_file.name}: {e}")
        
        # Then process ZIP files
        zip_files = list(process_dir.glob("*.zip"))
        if zip_files:
            logger.info(f"Found {len(zip_files)} ZIP files")
            for zip_file in sorted(zip_files):
                try:
                    counts = parser_instance.process_zip_file(zip_file)
                    logger.info(f"Completed {zip_file.name}: {counts}")
                except Exception as e:
                    logger.error(f"Error processing {zip_file.name}: {e}")
    
    elif args.process_file:
        zip_path = Path(args.process_file)
        if not zip_path.exists():
            logger.error(f"File not found: {zip_path}")
            return
        
        parser_instance = FannieSFLPParser(engine)
        counts = parser_instance.process_zip_file(zip_path)
        logger.info(f"Completed: {counts}")
    
    else:
        parser.print_help()


if __name__ == '__main__':
    main()
