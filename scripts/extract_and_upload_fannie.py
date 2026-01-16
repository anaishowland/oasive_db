#!/usr/bin/env python3
"""
Extract Fannie Mae ZIP one file at a time and upload to GCS.

This script handles the 60GB+ ZIP file by:
1. Opening the ZIP without fully extracting
2. Extracting ONE quarterly CSV at a time to a temp file
3. Uploading that CSV to GCS
4. Deleting the temp file immediately
5. Repeating for each file

Your laptop only needs ~3GB of free space at a time.

Usage:
    python scripts/extract_and_upload_fannie.py ~/Downloads/Performance_All.zip
    python scripts/extract_and_upload_fannie.py ~/Downloads/non_std_historical_data.zip --prefix fannie/sflp/non_std
"""

import os
import sys
import zipfile
import argparse
import tempfile
from pathlib import Path
from google.cloud import storage

def main():
    parser = argparse.ArgumentParser(description='Extract and upload Fannie Mae ZIP to GCS')
    parser.add_argument('zip_path', type=str, help='Path to ZIP file')
    parser.add_argument('--bucket', type=str, default='oasive-raw-data', help='GCS bucket name')
    parser.add_argument('--prefix', type=str, default='fannie/sflp/extracted', help='GCS prefix for uploads')
    parser.add_argument('--start-from', type=int, default=0, help='Start from file index (for resuming)')
    args = parser.parse_args()
    
    zip_path = Path(args.zip_path).expanduser()
    if not zip_path.exists():
        print(f"Error: ZIP file not found: {zip_path}")
        sys.exit(1)
    
    print(f"=== Fannie Mae ZIP Extractor ===")
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
    
    with zipfile.ZipFile(zip_path, 'r') as zf:
        # Get list of files
        all_files = sorted(zf.namelist())
        csv_files = [f for f in all_files if f.endswith('.csv') or f.endswith('.txt')]
        
        print(f"Found {len(csv_files)} data files in ZIP")
        print()
        
        for i, filename in enumerate(csv_files):
            if i < args.start_from:
                continue
            
            # Check if already uploaded
            gcs_path = f"{args.prefix}/{filename}"
            if gcs_path in existing_blobs:
                print(f"[{i+1}/{len(csv_files)}] SKIP {filename} (already uploaded)")
                continue
            
            print(f"[{i+1}/{len(csv_files)}] Extracting {filename}...")
            
            # Get file info
            info = zf.getinfo(filename)
            compressed_size = info.compress_size / 1e9
            uncompressed_size = info.file_size / 1e9
            
            print(f"    Compressed: {compressed_size:.2f} GB -> Uncompressed: {uncompressed_size:.2f} GB")
            
            # Extract to temp file
            with tempfile.NamedTemporaryFile(suffix='.csv', delete=False) as tmp:
                tmp_path = tmp.name
                
                print(f"    Extracting to {tmp_path}...")
                with zf.open(filename) as src:
                    # Copy in chunks
                    chunk_size = 64 * 1024 * 1024  # 64MB
                    bytes_written = 0
                    while True:
                        chunk = src.read(chunk_size)
                        if not chunk:
                            break
                        tmp.write(chunk)
                        bytes_written += len(chunk)
                        # Progress indicator
                        if bytes_written % (256 * 1024 * 1024) == 0:
                            print(f"    Extracted {bytes_written / 1e9:.1f} GB...")
            
            print(f"    Uploading to gs://{args.bucket}/{gcs_path}...")
            
            # Upload to GCS
            blob = bucket.blob(gcs_path)
            blob.upload_from_filename(tmp_path)
            
            # Verify upload
            blob.reload()
            print(f"    ✅ Uploaded {blob.size / 1e9:.2f} GB")
            
            # Delete temp file
            os.unlink(tmp_path)
            print(f"    Cleaned up temp file")
            print()
    
    print("=== COMPLETE ===")
    print(f"All files uploaded to gs://{args.bucket}/{args.prefix}/")
    print()
    print("Now run the Cloud Run processor:")
    print(f"  gcloud run jobs execute fannie-sflp-processor \\")
    print(f"    --region=us-central1 --project=gen-lang-client-0343560978 \\")
    print(f"    --args='-m,src.ingestors.fannie_sflp_ingestor,--process-gcs-extracted,gs://{args.bucket}/{args.prefix}'")


if __name__ == '__main__':
    main()
