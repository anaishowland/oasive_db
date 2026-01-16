"""
Freddie Mac RPL (Re-performing Loans) / SCRT / SLST Data Ingestor

SCRT = Seasoned Credit Risk Transfer
SLST = Seasoned Loan Structured Transactions

These are formerly distressed loans that Freddie Mac sells to investors.
The mapping file links SFLLD loan IDs to their SCRT/SLST transaction.

Source: Clarity Platform (freddiemac.embs.com/FLoan)

Usage:
    python -m src.ingestors.freddie_rpl_ingestor --status
    python -m src.ingestors.freddie_rpl_ingestor --process ~/Downloads/rpl_historical_data.zip
"""

import os
import sys
import zipfile
import csv
import logging
from pathlib import Path
from typing import Dict, List, Optional
import argparse
import io
import tempfile

from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from src.db.connection import get_engine

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


class RPLMappingParser:
    """Parse RPL loan ID mapping files (SCRT/SLST transaction mappings)."""
    
    def __init__(self, engine):
        self.engine = engine
        self.batch_size = 2000
        
    def process_zip(self, zip_path: Path) -> Dict[str, int]:
        """Process RPL mapping ZIP (which contains nested ZIPs)."""
        logger.info(f"Processing RPL data from {zip_path.name}...")
        
        counts = {'standard': 0, 'excluded': 0}
        
        with zipfile.ZipFile(zip_path, 'r') as zf:
            inner_zips = [f for f in zf.namelist() if f.endswith('.zip')]
            
            for inner_zip_name in inner_zips:
                logger.info(f"  Extracting {inner_zip_name}...")
                
                with tempfile.TemporaryDirectory() as temp_dir:
                    temp_path = Path(temp_dir)
                    
                    # Extract inner ZIP
                    with zf.open(inner_zip_name) as inner_zip_data:
                        inner_zip_path = temp_path / inner_zip_name
                        inner_zip_path.write_bytes(inner_zip_data.read())
                    
                    # Process inner ZIP
                    with zipfile.ZipFile(inner_zip_path, 'r') as inner_zf:
                        for csv_name in inner_zf.namelist():
                            if csv_name.endswith('.csv'):
                                logger.info(f"    Processing {csv_name}...")
                                
                                is_excluded = 'excl' in inner_zip_name.lower() or 'excl' in csv_name.lower()
                                
                                with inner_zf.open(csv_name) as f:
                                    text_wrapper = io.TextIOWrapper(f, encoding='utf-8', errors='ignore')
                                    count = self._process_csv(text_wrapper, is_excluded, csv_name)
                                    
                                    if is_excluded:
                                        counts['excluded'] += count
                                    else:
                                        counts['standard'] += count
        
        logger.info(f"Loaded {counts['standard']:,} standard mappings, {counts['excluded']:,} excluded mappings")
        return counts
    
    def _process_csv(self, file_obj, is_excluded: bool, source_file: str) -> int:
        """Process a single CSV file."""
        reader = csv.DictReader(file_obj)
        
        batch = []
        total = 0
        
        for row in reader:
            record = self._parse_row(row, source_file)
            if record:
                batch.append(record)
                
                if len(batch) >= self.batch_size:
                    self._insert_batch(batch, is_excluded)
                    total += len(batch)
                    batch = []
        
        if batch:
            self._insert_batch(batch, is_excluded)
            total += len(batch)
        
        return total
    
    def _parse_row(self, row: Dict, source_file: str) -> Optional[Dict]:
        """Parse a single CSV row."""
        # Common column names - the mapping file links SFLLD IDs to transactions
        loan_seq = (
            row.get('LOAN_SEQUENCE') or 
            row.get('Loan Sequence Number') or 
            row.get('loan_sequence') or
            row.get('SFLLD_LOAN_SEQUENCE') or
            list(row.values())[0] if row else None
        )
        
        if not loan_seq:
            return None
        
        return {
            'sflld_loan_sequence': loan_seq.strip(),
            'transaction_type': row.get('TRANSACTION_TYPE', '').strip() or None,
            'transaction_name': row.get('TRANSACTION_NAME', '').strip() or row.get('Deal Name', '').strip() or None,
            'deal_id': row.get('DEAL_ID', '').strip() or row.get('Deal ID', '').strip() or None,
            'source_file': source_file
        }
    
    def _insert_batch(self, batch: List[Dict], is_excluded: bool):
        """Insert batch into appropriate table."""
        table = 'rpl_loan_id_mapping_excl' if is_excluded else 'rpl_loan_id_mapping'
        
        with self.engine.connect() as conn:
            stmt = text(f"""
                INSERT INTO {table} (
                    sflld_loan_sequence, transaction_type, transaction_name, deal_id, source_file
                ) VALUES (
                    :sflld_loan_sequence, :transaction_type, :transaction_name, :deal_id, :source_file
                )
                ON CONFLICT DO NOTHING
            """)
            conn.execute(stmt, batch)
            conn.commit()


def print_status(engine):
    """Print current status."""
    print("\n" + "=" * 60)
    print("Freddie Mac RPL (SCRT/SLST) Status")
    print("=" * 60)
    
    with engine.connect() as conn:
        try:
            result = conn.execute(text("SELECT COUNT(*) FROM rpl_loan_id_mapping"))
            count = result.fetchone()[0]
            print(f"\n📊 Standard RPL mappings: {count:,}")
        except:
            print("\n📊 RPL mapping table not created yet")
        
        try:
            result = conn.execute(text("SELECT COUNT(*) FROM rpl_loan_id_mapping_excl"))
            count = result.fetchone()[0]
            print(f"📊 Excluded RPL mappings: {count:,}")
        except:
            print("📊 Excluded RPL mapping table not created yet")
    
    print("\n📖 What is RPL/SCRT/SLST?")
    print("   SCRT = Seasoned Credit Risk Transfer")
    print("   SLST = Seasoned Loan Structured Transactions")
    print("   These are formerly distressed loans Freddie Mac sold to investors.")
    print("   The mapping links SFLLD loan IDs to their transaction/deal.")
    print("\n📥 To load data:")
    print("   --process ~/Downloads/rpl_historical_data.zip")
    print("=" * 60)


def main():
    parser = argparse.ArgumentParser(description='Freddie Mac RPL/SCRT/SLST Ingestor')
    parser.add_argument('--status', action='store_true', help='Show status')
    parser.add_argument('--process', type=str, help='Process RPL ZIP file')
    args = parser.parse_args()
    
    engine = get_engine()
    
    if args.status:
        print_status(engine)
    
    elif args.process:
        zip_path = Path(args.process).expanduser()
        if not zip_path.exists():
            logger.error(f"File not found: {zip_path}")
            return
        
        rpl_parser = RPLMappingParser(engine)
        counts = rpl_parser.process_zip(zip_path)
        
        logger.info(f"✅ Completed: {counts['standard']:,} standard, {counts['excluded']:,} excluded mappings")
    
    else:
        parser.print_help()


if __name__ == '__main__':
    main()
