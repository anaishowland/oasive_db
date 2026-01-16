#!/usr/bin/env python3
"""
Extract Freddie Mac SFLLD ZIP one file at a time and upload to GCS.

The SFLLD ZIP contains nested ZIPs (yearly), which contain quarterly ZIPs,
which contain the actual TXT files.

This script handles the 37GB+ ZIP file by:
1. Opening the main ZIP
2. Extracting ONE yearly ZIP at a time
3. Opening that yearly ZIP and extracting quarterly TXTs
4. Uploading each TXT to GCS
5. Cleaning up temp files

Your laptop only needs ~2GB of free space at a time.

Usage:
    python scripts/extract_and_upload_sflld.py ~/Downloads/full_set_standard_historical_data.zip
    python scripts/extract_and_upload_sflld.py ~/Downloads/non_std_historical_data.zip --prefix sflld/non_std
"""

import os
import sys
import zipfile
import argparse
import tempfile
from pathlib import Path
from google.cloud import storage

def main():
    parser = argparse.ArgumentParser(description='Extract and upload SFLLD ZIP to GCS')
    parser.add_argument('zip_path', type=str, help='Path to ZIP file')
    parser.add_argument('--bucket', type=str, default='oasive-raw-data', help='GCS bucket name')
    parser.add_argument('--prefix', type=str, default='sflld/extracted', help='GCS prefix for uploads')
    parser.add_argument('--start-year', type=int, default=0, help='Start from year (for resuming)')
    args = parser.parse_args()
    
    zip_path = Path(args.zip_path).expanduser()
    if not zip_path.exists():
        print(f"Error: ZIP file not found: {zip_path}")
        sys.exit(1)
    
    print(f"=== Freddie Mac SFLLD ZIP Extractor ===")
    print(f"ZIP: {zip_path}")
    print(f"Size: {zip_path.stat().st_size / 1e9:.1f} GB")
    print(f"Destination: gs://{args.bucket}/{args.prefix}/")
    print()
    
    # Initialize GCS client
    client = storage.Client()
    bucket = client.bucket(args.bucket)
    
    # Check what's already uploaded
    existing_blobs = set(b.name for b in bucket.list_blobs(prefix=args.prefix))
    print(f"Found {len(existing_blobs)} files already in GCS")
    
    with zipfile.ZipFile(zip_path, 'r') as main_zf:
        # Get list of inner files (should be yearly ZIPs or direct TXT files)
        all_files = sorted(main_zf.namelist())
        
        print(f"Found {len(all_files)} items in main ZIP")
        
        for filename in all_files:
            # Skip directories
            if filename.endswith('/'):
                continue
            
            # Check if it's a nested ZIP
            if filename.endswith('.zip'):
                # Extract year from filename
                try:
                    year = int(''.join(filter(str.isdigit, filename))[:4])
                    if year < args.start_year:
                        print(f"SKIP {filename} (before start year {args.start_year})")
                        continue
                except:
                    pass
                
                print(f"\n=== Processing nested ZIP: {filename} ===")
                
                # Extract the yearly ZIP to temp
                with tempfile.NamedTemporaryFile(suffix='.zip', delete=False) as tmp:
                    tmp_zip_path = tmp.name
                    print(f"Extracting {filename} to temp...")
                    with main_zf.open(filename) as src:
                        tmp.write(src.read())
                
                # Now process the yearly ZIP
                try:
                    with zipfile.ZipFile(tmp_zip_path, 'r') as yearly_zf:
                        inner_files = sorted(yearly_zf.namelist())
                        
                        for inner_file in inner_files:
                            if inner_file.endswith('/'):
                                continue
                            
                            # Check if it's another nested ZIP (quarterly)
                            if inner_file.endswith('.zip'):
                                print(f"  Processing quarterly ZIP: {inner_file}...")
                                
                                # Extract quarterly ZIP
                                with tempfile.NamedTemporaryFile(suffix='.zip', delete=False) as qtmp:
                                    qtmp_path = qtmp.name
                                    with yearly_zf.open(inner_file) as src:
                                        qtmp.write(src.read())
                                
                                # Process quarterly ZIP
                                try:
                                    with zipfile.ZipFile(qtmp_path, 'r') as qzf:
                                        for txt_file in qzf.namelist():
                                            if txt_file.endswith('.txt'):
                                                _upload_txt_file(qzf, txt_file, bucket, args.prefix, existing_blobs)
                                finally:
                                    os.unlink(qtmp_path)
                            
                            elif inner_file.endswith('.txt'):
                                _upload_txt_file(yearly_zf, inner_file, bucket, args.prefix, existing_blobs)
                finally:
                    os.unlink(tmp_zip_path)
            
            elif filename.endswith('.txt'):
                # Direct TXT file in main ZIP
                _upload_txt_file(main_zf, filename, bucket, args.prefix, existing_blobs)
    
    print("\n=== COMPLETE ===")
    print(f"All files uploaded to gs://{args.bucket}/{args.prefix}/")
    print()
    print("Now run the Cloud Run processor:")
    print(f"  gcloud run jobs execute sflld-processor \\")
    print(f"    --region=us-central1 --project=gen-lang-client-0343560978 \\")
    print(f"    --args='-m,src.ingestors.sflld_ingestor,--process-gcs,gs://{args.bucket}/{args.prefix}'")


def _upload_txt_file(zf, filename, bucket, prefix, existing_blobs):
    """Extract and upload a single TXT file."""
    # Construct GCS path
    gcs_path = f"{prefix}/{Path(filename).name}"
    
    if gcs_path in existing_blobs:
        print(f"    SKIP {filename} (already uploaded)")
        return
    
    print(f"    Extracting {filename}...")
    
    # Extract to temp
    with tempfile.NamedTemporaryFile(suffix='.txt', delete=False) as tmp:
        tmp_path = tmp.name
        with zf.open(filename) as src:
            # Copy in chunks
            while True:
                chunk = src.read(64 * 1024 * 1024)  # 64MB
                if not chunk:
                    break
                tmp.write(chunk)
    
    file_size = os.path.getsize(tmp_path)
    print(f"    Uploading {file_size / 1e6:.1f} MB to gs://{bucket.name}/{gcs_path}...")
    
    # Upload
    blob = bucket.blob(gcs_path)
    blob.upload_from_filename(tmp_path)
    
    # Clean up
    os.unlink(tmp_path)
    print(f"    ✅ Done")


if __name__ == '__main__':
    main()
