"""
Fannie Mae HARP (Home Affordable Refinance Program) Data Ingestor

HARP allowed underwater borrowers (LTV > 100%) to refinance 2009-2018.

Ingests:
1. HARPLPPub.csv - Full HARP loan performance data (25 GB uncompressed!)
2. Loan_Mapping.txt - Maps old loan IDs to new post-HARP loan IDs

Source: Data Dynamics Platform (datadynamics.fanniemae.com)

Usage:
    python -m src.ingestors.fannie_harp_ingestor --status
    python -m src.ingestors.fannie_harp_ingestor --process ~/Downloads/HARP_Files.zip
    python -m src.ingestors.fannie_harp_ingestor --process-mapping ~/Downloads/HARP_Files.zip
"""

import os
import sys
import zipfile
import csv
import logging
from pathlib import Path
from typing import Dict, List, Optional
from decimal import Decimal, InvalidOperation
import argparse
import io

from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from src.db.connection import get_engine

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


def safe_decimal(value: str) -> Optional[Decimal]:
    if not value or value.strip() == '' or value.strip().upper() in ('NA', 'N/A', ''):
        return None
    try:
        return Decimal(value.strip().replace(',', ''))
    except (InvalidOperation, ValueError):
        return None


def safe_int(value: str) -> Optional[int]:
    if not value or value.strip() == '' or value.strip().upper() in ('NA', 'N/A', ''):
        return None
    try:
        return int(float(value.strip().replace(',', '')))
    except (ValueError, TypeError):
        return None


def parse_yyyymm(value: str) -> Optional[str]:
    """Parse YYYYMM to YYYY-MM-01."""
    if not value or len(value.strip()) < 6:
        return None
    v = value.strip()[:6]
    try:
        year = int(v[:4])
        month = int(v[4:6])
        if 1999 <= year <= 2030 and 1 <= month <= 12:
            return f"{year}-{month:02d}-01"
    except:
        pass
    return None


class HARPLoanMappingParser:
    """Parse HARP Loan_Mapping.txt file."""
    
    def __init__(self, engine):
        self.engine = engine
        self.batch_size = 5000
        
    def process_zip(self, zip_path: Path) -> int:
        """Extract and process the mapping file."""
        logger.info(f"Processing loan mapping from {zip_path.name}...")
        
        total_mappings = 0
        
        with zipfile.ZipFile(zip_path, 'r') as zf:
            mapping_files = [f for f in zf.namelist() if 'mapping' in f.lower() and f.endswith('.txt')]
            
            if not mapping_files:
                logger.warning("No Loan_Mapping.txt found in archive")
                return 0
            
            for mapping_file in mapping_files:
                logger.info(f"  Processing {mapping_file}...")
                
                with zf.open(mapping_file) as f:
                    text_wrapper = io.TextIOWrapper(f, encoding='utf-8', errors='ignore')
                    reader = csv.reader(text_wrapper, delimiter='|')
                    
                    # Skip header
                    header = next(reader, None)
                    logger.info(f"    Header: {header}")
                    
                    batch = []
                    for row in reader:
                        if len(row) >= 2:
                            batch.append({
                                'original_loan_id': row[0].strip(),
                                'new_loan_id': row[1].strip()
                            })
                            
                            if len(batch) >= self.batch_size:
                                self._insert_batch(batch)
                                total_mappings += len(batch)
                                if total_mappings % 100000 == 0:
                                    logger.info(f"    Processed {total_mappings:,} mappings...")
                                batch = []
                    
                    if batch:
                        self._insert_batch(batch)
                        total_mappings += len(batch)
        
        logger.info(f"Loaded {total_mappings:,} loan mappings")
        return total_mappings
    
    def _insert_batch(self, batch: List[Dict]):
        """Insert batch into database."""
        with self.engine.connect() as conn:
            stmt = text("""
                INSERT INTO harp_loan_mapping (original_loan_id, new_loan_id)
                VALUES (:original_loan_id, :new_loan_id)
                ON CONFLICT (original_loan_id, new_loan_id) DO NOTHING
            """)
            conn.execute(stmt, batch)
            conn.commit()


class HARPLoanParser:
    """Parse HARP loan performance data (HARPLPPub.csv - 25 GB!)."""
    
    # Expected column layout (similar to standard SFLP but with HARP-specific fields)
    COLUMNS = [
        'loan_id', 'channel', 'seller_name', 'orig_rate', 'orig_upb', 'orig_loan_term',
        'orig_date', 'first_payment_date', 'ltv', 'cltv', 'num_borrowers', 'dti',
        'fico', 'co_borrower_fico', 'first_time_buyer', 'loan_purpose', 'property_type',
        'num_units', 'occupancy', 'state', 'zipcode', 'mi_pct', 'product_type',
        'co_borrower_credit_score', 'mi_type', 'relocation_mortgage'
    ]
    
    def __init__(self, engine):
        self.engine = engine
        self.batch_size = 5000
        self.seen_loans = set()
        
    def process_zip(self, zip_path: Path) -> Dict[str, int]:
        """Process HARP loan data ZIP file."""
        logger.info(f"Processing HARP loans from {zip_path.name}...")
        logger.info("⚠️  This is a large file (25 GB uncompressed) - will take time!")
        
        counts = {'loans': 0, 'skipped': 0}
        
        # Load existing loan IDs
        logger.info("Loading existing HARP loan IDs...")
        with self.engine.connect() as conn:
            try:
                result = conn.execute(text("SELECT loan_id FROM dim_loan_fannie_harp"))
                self.seen_loans = {r[0] for r in result}
                logger.info(f"  Found {len(self.seen_loans):,} existing loans")
            except:
                self.seen_loans = set()
        
        with zipfile.ZipFile(zip_path, 'r') as zf:
            csv_files = [f for f in zf.namelist() if f.endswith('.csv') and 'HARP' in f.upper()]
            
            if not csv_files:
                # Try any CSV
                csv_files = [f for f in zf.namelist() if f.endswith('.csv')]
            
            for csv_name in csv_files:
                logger.info(f"  Processing {csv_name}...")
                
                with zf.open(csv_name) as f:
                    text_wrapper = io.TextIOWrapper(f, encoding='utf-8', errors='ignore')
                    reader = csv.reader(text_wrapper, delimiter='|')
                    
                    batch = []
                    for i, row in enumerate(reader):
                        if i == 0 and 'LOAN' in str(row).upper():
                            # Skip header if present
                            continue
                        
                        record = self._parse_row(row)
                        if record and record['loan_id'] not in self.seen_loans:
                            batch.append(record)
                            self.seen_loans.add(record['loan_id'])
                            
                            if len(batch) >= self.batch_size:
                                self._insert_batch(batch)
                                counts['loans'] += len(batch)
                                if counts['loans'] % 100000 == 0:
                                    logger.info(f"    Processed {counts['loans']:,} loans...")
                                batch = []
                        else:
                            counts['skipped'] += 1
                    
                    if batch:
                        self._insert_batch(batch)
                        counts['loans'] += len(batch)
        
        logger.info(f"Loaded {counts['loans']:,} new HARP loans (skipped {counts['skipped']:,} existing)")
        return counts
    
    def _parse_row(self, row: List[str]) -> Optional[Dict]:
        """Parse a single pipe-delimited row."""
        if len(row) < 10:
            return None
        
        loan_id = row[0].strip() if row[0] else None
        if not loan_id:
            return None
        
        return {
            'loan_id': loan_id,
            'channel': row[1].strip() if len(row) > 1 and row[1] else None,
            'seller_name': row[2].strip()[:100] if len(row) > 2 and row[2] else None,
            'orig_rate': safe_decimal(row[3]) if len(row) > 3 else None,
            'orig_upb': safe_decimal(row[4]) if len(row) > 4 else None,
            'orig_loan_term': safe_int(row[5]) if len(row) > 5 else None,
            'orig_date': parse_yyyymm(row[6]) if len(row) > 6 else None,
            'first_payment_date': parse_yyyymm(row[7]) if len(row) > 7 else None,
            'ltv': safe_decimal(row[8]) if len(row) > 8 else None,
            'cltv': safe_decimal(row[9]) if len(row) > 9 else None,
            'num_borrowers': safe_int(row[10]) if len(row) > 10 else None,
            'dti': safe_decimal(row[11]) if len(row) > 11 else None,
            'fico': safe_int(row[12]) if len(row) > 12 else None,
            'co_borrower_fico': safe_int(row[13]) if len(row) > 13 else None,
            'first_time_buyer': row[14].strip() if len(row) > 14 and row[14] else None,
            'loan_purpose': row[15].strip() if len(row) > 15 and row[15] else None,
            'property_type': row[16].strip() if len(row) > 16 and row[16] else None,
            'num_units': safe_int(row[17]) if len(row) > 17 else None,
            'occupancy': row[18].strip() if len(row) > 18 and row[18] else None,
            'state': row[19].strip()[:2] if len(row) > 19 and row[19] else None,
            'zipcode': row[20].strip()[:5] if len(row) > 20 and row[20] else None,
            'mi_pct': safe_decimal(row[21]) if len(row) > 21 else None,
        }
    
    def _insert_batch(self, batch: List[Dict]):
        """Insert batch into database."""
        with self.engine.connect() as conn:
            stmt = text("""
                INSERT INTO dim_loan_fannie_harp (
                    loan_id, channel, seller_name, orig_rate, orig_upb, orig_loan_term,
                    orig_date, first_payment_date, ltv, cltv, num_borrowers, dti,
                    fico, co_borrower_fico, first_time_buyer, loan_purpose, property_type,
                    num_units, occupancy, state, zipcode, mi_pct
                ) VALUES (
                    :loan_id, :channel, :seller_name, :orig_rate, :orig_upb, :orig_loan_term,
                    :orig_date, :first_payment_date, :ltv, :cltv, :num_borrowers, :dti,
                    :fico, :co_borrower_fico, :first_time_buyer, :loan_purpose, :property_type,
                    :num_units, :occupancy, :state, :zipcode, :mi_pct
                )
                ON CONFLICT (loan_id) DO UPDATE SET
                    updated_at = NOW()
            """)
            conn.execute(stmt, batch)
            conn.commit()


def print_status(engine):
    """Print current status."""
    print("\n" + "=" * 60)
    print("Fannie Mae HARP Data Status")
    print("=" * 60)
    
    with engine.connect() as conn:
        try:
            result = conn.execute(text("SELECT COUNT(*) FROM dim_loan_fannie_harp"))
            count = result.fetchone()[0]
            print(f"\n📊 HARP loans: {count:,}")
        except:
            print("\n📊 HARP loan table not created yet")
        
        try:
            result = conn.execute(text("SELECT COUNT(*) FROM harp_loan_mapping"))
            count = result.fetchone()[0]
            print(f"📊 Loan mappings: {count:,}")
        except:
            print("📊 Loan mapping table not created yet")
    
    print("\n📥 To load data:")
    print("   --process ~/Downloads/HARP_Files.zip (all data)")
    print("   --process-mapping ~/Downloads/HARP_Files.zip (mappings only)")
    print("=" * 60)


def main():
    parser = argparse.ArgumentParser(description='Fannie Mae HARP Ingestor')
    parser.add_argument('--status', action='store_true', help='Show status')
    parser.add_argument('--process', type=str, help='Process full HARP ZIP (loans + mapping)')
    parser.add_argument('--process-mapping', type=str, help='Process only loan mapping')
    args = parser.parse_args()
    
    engine = get_engine()
    
    if args.status:
        print_status(engine)
    
    elif args.process:
        zip_path = Path(args.process).expanduser()
        if not zip_path.exists():
            logger.error(f"File not found: {zip_path}")
            return
        
        # First load mappings
        mapping_parser = HARPLoanMappingParser(engine)
        mapping_count = mapping_parser.process_zip(zip_path)
        
        # Then load loans
        loan_parser = HARPLoanParser(engine)
        loan_counts = loan_parser.process_zip(zip_path)
        
        logger.info(f"✅ Completed: {loan_counts['loans']:,} loans, {mapping_count:,} mappings")
    
    elif args.process_mapping:
        zip_path = Path(args.process_mapping).expanduser()
        if not zip_path.exists():
            logger.error(f"File not found: {zip_path}")
            return
        
        mapping_parser = HARPLoanMappingParser(engine)
        count = mapping_parser.process_zip(zip_path)
        logger.info(f"✅ Completed: {count:,} mappings loaded")
    
    else:
        parser.print_help()


if __name__ == '__main__':
    main()
