"""
Fannie Mae Multifamily (Commercial MBS) Data Ingestor

Ingests:
1. Multifamily.zip - Loan performance data for apartment/commercial buildings
2. Multifamily_DSCR.zip - Debt Service Coverage Ratio metrics

Source: Data Dynamics Platform (datadynamics.fanniemae.com)

Usage:
    python -m src.ingestors.fannie_multifamily_ingestor --status
    python -m src.ingestors.fannie_multifamily_ingestor --process-performance ~/Downloads/Multifamily.zip
    python -m src.ingestors.fannie_multifamily_ingestor --process-dscr ~/Downloads/Multifamily_DSCR.zip
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

from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from src.db.connection import get_engine

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


def safe_decimal(value: str) -> Optional[Decimal]:
    if not value or value.strip() == '':
        return None
    try:
        return Decimal(value.strip().replace(',', ''))
    except (InvalidOperation, ValueError):
        return None


def safe_int(value: str) -> Optional[int]:
    if not value or value.strip() == '':
        return None
    try:
        return int(float(value.strip().replace(',', '')))
    except (ValueError, TypeError):
        return None


def parse_date(value: str) -> Optional[str]:
    """Parse various date formats to YYYY-MM-DD."""
    if not value or value.strip() == '':
        return None
    value = value.strip()
    
    # Try common formats
    import re
    
    # MM/DD/YYYY
    match = re.match(r'(\d{1,2})/(\d{1,2})/(\d{4})', value)
    if match:
        m, d, y = match.groups()
        return f"{y}-{int(m):02d}-{int(d):02d}"
    
    # YYYY-MM-DD
    match = re.match(r'(\d{4})-(\d{2})-(\d{2})', value)
    if match:
        return value
    
    # YYYYMMDD
    match = re.match(r'(\d{4})(\d{2})(\d{2})', value)
    if match:
        y, m, d = match.groups()
        return f"{y}-{m}-{d}"
    
    return None


class MultifamilyPerformanceParser:
    """Parse Fannie Mae Multifamily loan performance data."""
    
    def __init__(self, engine):
        self.engine = engine
        self.batch_size = 1000
        
    def process_zip(self, zip_path: Path) -> int:
        """Process multifamily performance ZIP file."""
        logger.info(f"Processing {zip_path.name}...")
        
        total_loans = 0
        
        with zipfile.ZipFile(zip_path, 'r') as zf:
            csv_files = [f for f in zf.namelist() if f.endswith('.csv')]
            
            for csv_name in csv_files:
                logger.info(f"  Processing {csv_name}...")
                
                with zf.open(csv_name) as f:
                    # Read as text
                    import io
                    text_wrapper = io.TextIOWrapper(f, encoding='utf-8', errors='ignore')
                    reader = csv.DictReader(text_wrapper)
                    
                    batch = []
                    for row in reader:
                        record = self._parse_row(row)
                        if record:
                            batch.append(record)
                            
                            if len(batch) >= self.batch_size:
                                self._insert_batch(batch)
                                total_loans += len(batch)
                                batch = []
                    
                    if batch:
                        self._insert_batch(batch)
                        total_loans += len(batch)
        
        logger.info(f"Loaded {total_loans:,} multifamily loans")
        return total_loans
    
    def _parse_row(self, row: Dict) -> Optional[Dict]:
        """Parse a single CSV row - Fannie Mae MF format."""
        # Fannie Mae MF column names
        loan_id = row.get('Loan Number')
        if not loan_id:
            return None
        
        # Parse UPB values (they have $ and commas)
        def parse_upb(val):
            if not val:
                return None
            return safe_decimal(val.replace('$', '').replace(',', ''))
        
        return {
            'loan_id': loan_id.strip(),
            'deal_name': row.get('MCIRT Deal ID', '').strip() or row.get('MCAS Deal ID', '').strip() or None,
            'property_name': None,  # Not in this file
            'orig_date': parse_date(row.get('Note Date')),
            'maturity_date': parse_date(row.get('Maturity Date - Current') or row.get('Maturity Date at Acquisition')),
            'orig_upb': parse_upb(row.get('Original UPB')),
            'current_upb': parse_upb(row.get('UPB - Current')),
            'orig_rate': safe_decimal(row.get('Original Interest Rate')),
            'current_rate': safe_decimal(row.get('Note Rate')),
            'property_type': row.get('Specific Property Type', '').strip() or None,
            'property_city': row.get('Property City', '').strip() or None,
            'property_state': row.get('Property State', '').strip()[:2] if row.get('Property State') else None,
            'property_zip': row.get('Property Zip Code', '').strip()[:10] if row.get('Property Zip Code') else None,
            'units': safe_int(row.get('Property Acquisition Total Unit Count')),
            'orig_ltv': safe_decimal(row.get('Loan Acquisition LTV')),
            'orig_dscr': safe_decimal(row.get('Underwritten DSCR')),
            'current_dscr': None,  # Would need separate DSCR file
            'noi': None,
            'dlq_status': row.get('Loan Payment Status', '').strip() or None,
        }
    
    def _insert_batch(self, batch: List[Dict]):
        """Insert batch into database."""
        with self.engine.connect() as conn:
            stmt = text("""
                INSERT INTO dim_loan_fannie_multifamily (
                    loan_id, deal_name, property_name, orig_date, maturity_date,
                    orig_upb, current_upb, orig_rate, current_rate,
                    property_type, property_city, property_state, property_zip,
                    units, orig_ltv, orig_dscr, current_dscr, noi, dlq_status
                ) VALUES (
                    :loan_id, :deal_name, :property_name, :orig_date, :maturity_date,
                    :orig_upb, :current_upb, :orig_rate, :current_rate,
                    :property_type, :property_city, :property_state, :property_zip,
                    :units, :orig_ltv, :orig_dscr, :current_dscr, :noi, :dlq_status
                )
                ON CONFLICT (loan_id) DO UPDATE SET
                    current_upb = EXCLUDED.current_upb,
                    current_rate = EXCLUDED.current_rate,
                    current_dscr = EXCLUDED.current_dscr,
                    noi = EXCLUDED.noi,
                    dlq_status = EXCLUDED.dlq_status,
                    updated_at = NOW()
            """)
            conn.execute(stmt, batch)
            conn.commit()


class DSCRParser:
    """Parse Fannie Mae Multifamily DSCR file."""
    
    def __init__(self, engine):
        self.engine = engine
        self.batch_size = 1000
        
    def process_zip(self, zip_path: Path) -> int:
        """Process DSCR ZIP file."""
        logger.info(f"Processing {zip_path.name}...")
        
        total_records = 0
        
        with zipfile.ZipFile(zip_path, 'r') as zf:
            csv_files = [f for f in zf.namelist() if f.endswith('.csv')]
            
            for csv_name in csv_files:
                logger.info(f"  Processing {csv_name}...")
                
                with zf.open(csv_name) as f:
                    import io
                    text_wrapper = io.TextIOWrapper(f, encoding='utf-8', errors='ignore')
                    reader = csv.DictReader(text_wrapper)
                    
                    batch = []
                    for row in reader:
                        record = self._parse_row(row)
                        if record:
                            batch.append(record)
                            
                            if len(batch) >= self.batch_size:
                                self._insert_batch(batch)
                                total_records += len(batch)
                                batch = []
                    
                    if batch:
                        self._insert_batch(batch)
                        total_records += len(batch)
        
        logger.info(f"Loaded {total_records:,} DSCR records")
        return total_records
    
    def _parse_row(self, row: Dict) -> Optional[Dict]:
        """Parse a single CSV row."""
        loan_id = row.get('Loan Identifier') or row.get('LOAN_ID') or row.get('loan_id')
        if not loan_id:
            return None
        
        return {
            'loan_id': loan_id.strip(),
            'as_of_date': parse_date(row.get('As of Date') or row.get('PERIOD')),
            'dscr': safe_decimal(row.get('DSCR') or row.get('Current DSCR')),
            'noi': safe_decimal(row.get('NOI') or row.get('Net Operating Income')),
            'debt_service': safe_decimal(row.get('Debt Service')),
            'occupancy_rate': safe_decimal(row.get('Occupancy Rate') or row.get('Occupancy')),
        }
    
    def _insert_batch(self, batch: List[Dict]):
        """Insert batch into database."""
        with self.engine.connect() as conn:
            stmt = text("""
                INSERT INTO fact_multifamily_dscr (
                    loan_id, as_of_date, dscr, noi, debt_service, occupancy_rate
                ) VALUES (
                    :loan_id, :as_of_date, :dscr, :noi, :debt_service, :occupancy_rate
                )
                ON CONFLICT (loan_id, as_of_date) DO UPDATE SET
                    dscr = EXCLUDED.dscr,
                    noi = EXCLUDED.noi,
                    debt_service = EXCLUDED.debt_service,
                    occupancy_rate = EXCLUDED.occupancy_rate
            """)
            conn.execute(stmt, batch)
            conn.commit()


def print_status(engine):
    """Print current status."""
    print("\n" + "=" * 60)
    print("Fannie Mae Multifamily Status")
    print("=" * 60)
    
    with engine.connect() as conn:
        try:
            result = conn.execute(text("SELECT COUNT(*) FROM dim_loan_fannie_multifamily"))
            count = result.fetchone()[0]
            print(f"\n📊 Multifamily loans: {count:,}")
        except:
            print("\n📊 Multifamily table not created yet")
        
        try:
            result = conn.execute(text("SELECT COUNT(*) FROM fact_multifamily_dscr"))
            count = result.fetchone()[0]
            print(f"📊 DSCR records: {count:,}")
        except:
            print("📊 DSCR table not created yet")
    
    print("\n📥 To load data:")
    print("   --process-performance ~/Downloads/Multifamily.zip")
    print("   --process-dscr ~/Downloads/Multifamily_DSCR.zip")
    print("=" * 60)


def main():
    parser = argparse.ArgumentParser(description='Fannie Mae Multifamily Ingestor')
    parser.add_argument('--status', action='store_true', help='Show status')
    parser.add_argument('--process-performance', type=str, help='Process performance ZIP')
    parser.add_argument('--process-dscr', type=str, help='Process DSCR ZIP')
    args = parser.parse_args()
    
    engine = get_engine()
    
    if args.status:
        print_status(engine)
    
    elif args.process_performance:
        zip_path = Path(args.process_performance).expanduser()
        if not zip_path.exists():
            logger.error(f"File not found: {zip_path}")
            return
        
        parser_instance = MultifamilyPerformanceParser(engine)
        count = parser_instance.process_zip(zip_path)
        logger.info(f"✅ Completed: {count:,} loans loaded")
    
    elif args.process_dscr:
        zip_path = Path(args.process_dscr).expanduser()
        if not zip_path.exists():
            logger.error(f"File not found: {zip_path}")
            return
        
        dscr_parser = DSCRParser(engine)
        count = dscr_parser.process_zip(zip_path)
        logger.info(f"✅ Completed: {count:,} DSCR records loaded")
    
    else:
        parser.print_help()


if __name__ == '__main__':
    main()
