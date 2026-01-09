"""
Freddie Mac SFTP Data Ingestor

Downloads disclosure files from CSS SFTP server and stages them in GCS.
Designed to run as a Cloud Run job on a scheduled basis.

SFTP Details:
- Domain: data.mbs-securities.com
- Port: 22
- Credentials: Provided by CSS (format: svcfre-<vendor>)

File Types on SFTP:
- FRE_FISS_YYYYMMDD.zip - Intraday security issuance
- FRE_IS_YYYYMM.zip - Monthly security issuance
- Historical deal files (various patterns)

See: Freddie_CSS_SFTP_Connectivity_Instructions.pdf
"""

import argparse
import logging
import os
import re
import stat
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from io import BytesIO
from pathlib import PurePosixPath
from typing import Any

import paramiko
from google.cloud import storage
from sqlalchemy import text

from src.config import FreddieConfig, GCSConfig, PostgresConfig
from src.db.connection import get_engine

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


class FreddieIngestor:
    """
    Downloads Freddie Mac disclosure files from CSS SFTP to GCS.
    
    Supports multiple run modes:
    - catalog: List and catalog files without downloading
    - incremental: Download only new files since last run
    - backfill: Download all historical files in batches
    
    Features:
    - Batched downloads with automatic reconnection
    - Retry logic for failed downloads
    - Parallel uploads to GCS
    - Progress tracking and resumable downloads
    """
    
    # File type patterns for Freddie Mac disclosure files
    FILE_PATTERNS = {
        "intraday_issuance": re.compile(r"FRE_FISS_\d{8}\.zip$", re.IGNORECASE),
        "monthly_issuance": re.compile(r"FRE_IS_\d{6}\.zip$", re.IGNORECASE),
        "deal_files": re.compile(r"\d+[a-z]+\d*\.(zip|pdf)$", re.IGNORECASE),
        "factor": re.compile(r".*\.fac$", re.IGNORECASE),
        "type": re.compile(r".*\.typ$", re.IGNORECASE),
    }
    
    # Configuration for batching and retries
    BATCH_SIZE = 50  # Reconnect after this many files
    MAX_RETRIES = 3
    RETRY_DELAY = 5  # seconds
    DOWNLOAD_TIMEOUT = 300  # seconds
    LARGE_FILE_THRESHOLD = 50 * 1024 * 1024  # 50MB - use temp file for larger
    
    def __init__(
        self,
        freddie_config: FreddieConfig | None = None,
        postgres_config: PostgresConfig | None = None,
        gcs_config: GCSConfig | None = None,
    ):
        self.freddie_config = freddie_config or FreddieConfig.from_env()
        self.postgres_config = postgres_config or PostgresConfig.from_env()
        self.gcs_config = gcs_config or GCSConfig.from_env()
        
        self.engine = get_engine(self.postgres_config)
        self.storage_client = storage.Client(project=self.gcs_config.project_id)
        
        if not self.freddie_config.username or not self.freddie_config.password:
            raise ValueError("FREDDIE_USERNAME and FREDDIE_PASSWORD are required")
        
        self._sftp = None
        self._ssh_client = None
    
    def _connect(self) -> paramiko.SFTPClient:
        """Create a new SFTP connection."""
        logger.info(f"Connecting to SFTP: {self.freddie_config.host}")
        
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        
        client.connect(
            hostname=self.freddie_config.host,
            port=self.freddie_config.port,
            username=self.freddie_config.username,
            password=self.freddie_config.password,
            look_for_keys=False,
            allow_agent=False,
            timeout=60,
        )
        
        sftp = client.open_sftp()
        sftp.get_channel().settimeout(self.DOWNLOAD_TIMEOUT)
        
        self._ssh_client = client
        self._sftp = sftp
        
        logger.info("SFTP connection established")
        return sftp
    
    def _disconnect(self):
        """Close SFTP connection."""
        if self._sftp:
            try:
                self._sftp.close()
            except Exception:
                pass
            self._sftp = None
        if self._ssh_client:
            try:
                self._ssh_client.close()
            except Exception:
                pass
            self._ssh_client = None
    
    def _reconnect(self) -> paramiko.SFTPClient:
        """Reconnect to SFTP server."""
        self._disconnect()
        time.sleep(2)  # Brief pause before reconnecting
        return self._connect()
    
    def _get_sftp(self) -> paramiko.SFTPClient:
        """Get active SFTP connection, creating if needed."""
        if self._sftp is None:
            return self._connect()
        return self._sftp
    
    def _classify_file(self, filename: str) -> str:
        """Classify a file based on its name pattern."""
        for file_type, pattern in self.FILE_PATTERNS.items():
            if pattern.match(filename):
                return file_type
        
        # Additional heuristics
        ext = PurePosixPath(filename).suffix.lower()
        if ext == ".zip":
            return "archive"
        elif ext == ".pdf":
            return "document"
        elif ext == ".xlsx":
            return "spreadsheet"
        return "other"
    
    def _extract_date_from_filename(self, filename: str) -> datetime | None:
        """Extract date from filename."""
        patterns = [
            (r"(\d{4})(\d{2})(\d{2})", lambda m: datetime(int(m[0]), int(m[1]), int(m[2]))),
            (r"(\d{4})-(\d{2})-(\d{2})", lambda m: datetime(int(m[0]), int(m[1]), int(m[2]))),
            (r"(\d{6})", lambda m: datetime(int(m[0][:4]), int(m[0][4:6]), 1)),
        ]
        
        for pattern, parser in patterns:
            match = re.search(pattern, filename)
            if match:
                try:
                    return parser(match.groups() if len(match.groups()) > 1 else [match.group()])
                except (ValueError, IndexError):
                    continue
        return None
    
    def list_remote_files(
        self,
        remote_dir: str = "/",
        recursive: bool = True,
        max_depth: int = 5,
    ) -> list[dict[str, Any]]:
        """List all files in remote directory."""
        sftp = self._get_sftp()
        return self._list_dir_recursive(sftp, remote_dir, recursive, max_depth, 0)
    
    def _list_dir_recursive(
        self,
        sftp: paramiko.SFTPClient,
        remote_dir: str,
        recursive: bool,
        max_depth: int,
        current_depth: int,
    ) -> list[dict[str, Any]]:
        """Recursively list directory contents."""
        files = []
        
        if current_depth > max_depth:
            return files
        
        try:
            items = sftp.listdir_attr(remote_dir)
        except IOError as e:
            logger.warning(f"Cannot list {remote_dir}: {e}")
            return files
        
        for item in items:
            full_path = str(PurePosixPath(remote_dir) / item.filename)
            
            if stat.S_ISDIR(item.st_mode):
                if recursive:
                    files.extend(self._list_dir_recursive(
                        sftp, full_path, recursive, max_depth, current_depth + 1
                    ))
            else:
                files.append({
                    "remote_path": full_path,
                    "filename": item.filename,
                    "file_type": self._classify_file(item.filename),
                    "file_date": self._extract_date_from_filename(item.filename),
                    "remote_size": item.st_size,
                    "remote_modified_at": datetime.fromtimestamp(item.st_mtime) if item.st_mtime else None,
                })
        
        return files
    
    def get_cataloged_files(self) -> dict[str, dict]:
        """Get cataloged files with their status."""
        with self.engine.connect() as conn:
            result = conn.execute(text("""
                SELECT remote_path, download_status, local_gcs_path
                FROM freddie_file_catalog
            """))
            return {
                row.remote_path: {
                    "status": row.download_status,
                    "gcs_path": row.local_gcs_path,
                }
                for row in result
            }
    
    def add_to_catalog(self, file_info: dict[str, Any]) -> None:
        """Add file to catalog."""
        with self.engine.connect() as conn:
            conn.execute(
                text("""
                    INSERT INTO freddie_file_catalog 
                    (remote_path, filename, file_type, file_date, remote_size, remote_modified_at, download_status)
                    VALUES (:remote_path, :filename, :file_type, :file_date, :remote_size, :remote_modified_at, 'pending')
                    ON CONFLICT (remote_path) DO UPDATE SET
                        remote_size = EXCLUDED.remote_size,
                        remote_modified_at = EXCLUDED.remote_modified_at,
                        updated_at = NOW()
                """),
                file_info
            )
            conn.commit()
    
    def update_catalog_status(
        self,
        remote_path: str,
        status: str,
        gcs_path: str | None = None,
        error_message: str | None = None,
    ) -> None:
        """Update catalog entry status."""
        with self.engine.connect() as conn:
            if status == "downloaded":
                conn.execute(
                    text("""
                        UPDATE freddie_file_catalog 
                        SET download_status = :status,
                            local_gcs_path = :gcs_path,
                            downloaded_at = NOW(),
                            updated_at = NOW()
                        WHERE remote_path = :remote_path
                    """),
                    {"status": status, "gcs_path": gcs_path, "remote_path": remote_path}
                )
            else:
                conn.execute(
                    text("""
                        UPDATE freddie_file_catalog 
                        SET download_status = :status,
                            error_message = :error_message,
                            updated_at = NOW()
                        WHERE remote_path = :remote_path
                    """),
                    {"status": status, "error_message": error_message, "remote_path": remote_path}
                )
            conn.commit()
    
    def download_file(self, file_info: dict[str, Any]) -> str:
        """
        Download a single file with retry logic.
        
        Returns:
            GCS path of uploaded file
        """
        remote_path = file_info["remote_path"]
        filename = file_info["filename"]
        file_size = file_info.get("remote_size", 0)
        
        # Determine GCS path
        now = datetime.now(timezone.utc)
        gcs_path = f"freddie/raw/{now.year}/{now.month:02d}/{filename}"
        
        for attempt in range(self.MAX_RETRIES):
            try:
                sftp = self._get_sftp()
                
                logger.info(f"Downloading {remote_path} ({file_size / 1024 / 1024:.1f} MB)")
                
                # Use temp file for large files, BytesIO for small
                if file_size > self.LARGE_FILE_THRESHOLD:
                    with tempfile.NamedTemporaryFile(delete=False) as tmp:
                        sftp.get(remote_path, tmp.name)
                        tmp_path = tmp.name
                    
                    bucket = self.storage_client.bucket(self.gcs_config.raw_bucket)
                    blob = bucket.blob(gcs_path)
                    blob.upload_from_filename(tmp_path, timeout=self.DOWNLOAD_TIMEOUT)
                    os.unlink(tmp_path)
                else:
                    buffer = BytesIO()
                    sftp.getfo(remote_path, buffer)
                    buffer.seek(0)
                    
                    bucket = self.storage_client.bucket(self.gcs_config.raw_bucket)
                    blob = bucket.blob(gcs_path)
                    blob.upload_from_file(buffer, timeout=self.DOWNLOAD_TIMEOUT)
                
                logger.info(f"Uploaded to gs://{self.gcs_config.raw_bucket}/{gcs_path}")
                return f"gs://{self.gcs_config.raw_bucket}/{gcs_path}"
                
            except Exception as e:
                logger.warning(f"Download attempt {attempt + 1} failed for {remote_path}: {e}")
                if attempt < self.MAX_RETRIES - 1:
                    logger.info(f"Retrying in {self.RETRY_DELAY}s...")
                    time.sleep(self.RETRY_DELAY)
                    self._reconnect()
                else:
                    raise
    
    def log_ingest_run(
        self,
        status: str,
        files_discovered: int = 0,
        files_downloaded: int = 0,
        bytes_downloaded: int = 0,
        error_message: str | None = None,
        run_started_at: datetime | None = None,
    ) -> None:
        """Log ingestion run to database."""
        with self.engine.connect() as conn:
            conn.execute(
                text("""
                    INSERT INTO freddie_ingest_log 
                    (run_started_at, run_completed_at, status, files_discovered, 
                     files_downloaded, bytes_downloaded, error_message)
                    VALUES (:run_started_at, :run_completed_at, :status, :files_discovered,
                            :files_downloaded, :bytes_downloaded, :error_message)
                """),
                {
                    "run_started_at": run_started_at or datetime.now(timezone.utc),
                    "run_completed_at": datetime.now(timezone.utc),
                    "status": status,
                    "files_discovered": files_discovered,
                    "files_downloaded": files_downloaded,
                    "bytes_downloaded": bytes_downloaded,
                    "error_message": error_message,
                }
            )
            conn.commit()
    
    def run(
        self,
        mode: str = "incremental",
        file_types: list[str] | None = None,
        file_pattern: str | None = None,
        max_files: int | None = None,
        skip_catalog: bool = False,
    ) -> dict[str, Any]:
        """
        Run the Freddie Mac SFTP sync.
        
        Args:
            mode: 'catalog' (list only), 'incremental' (new files), 'backfill' (all pending)
            file_types: Filter by file type (intraday_issuance, monthly_issuance, etc.)
            file_pattern: Regex pattern to filter filenames
            max_files: Maximum files to download
            skip_catalog: Skip cataloging, download pending files directly
        
        Returns:
            Summary dictionary
        """
        run_started_at = datetime.now(timezone.utc)
        logger.info(f"Starting Freddie Mac sync (mode={mode})")
        
        results = {
            "mode": mode,
            "files_discovered": 0,
            "files_cataloged": 0,
            "files_downloaded": 0,
            "bytes_downloaded": 0,
            "errors": [],
        }
        
        try:
            # Step 1: List and catalog files (unless skipping)
            if not skip_catalog:
                logger.info("Scanning remote files...")
                remote_files = self.list_remote_files("/")
                results["files_discovered"] = len(remote_files)
                logger.info(f"Found {len(remote_files)} files on SFTP server")
                
                # Filter by type/pattern if specified
                if file_types:
                    remote_files = [f for f in remote_files if f["file_type"] in file_types]
                    logger.info(f"Filtered to {len(remote_files)} files of types: {file_types}")
                
                if file_pattern:
                    pattern = re.compile(file_pattern)
                    remote_files = [f for f in remote_files if pattern.search(f["filename"])]
                    logger.info(f"Filtered to {len(remote_files)} files matching pattern")
                
                # Catalog new files
                cataloged = self.get_cataloged_files()
                new_files = [f for f in remote_files if f["remote_path"] not in cataloged]
                
                for f in new_files:
                    self.add_to_catalog(f)
                results["files_cataloged"] = len(new_files)
                logger.info(f"Cataloged {len(new_files)} new files")
            
            # Step 2: Download files based on mode
            if mode == "catalog":
                logger.info("Catalog-only mode, skipping downloads")
            else:
                # Get files to download
                cataloged = self.get_cataloged_files()
                
                if mode == "incremental":
                    # Download new files (pending status)
                    to_download = [
                        {"remote_path": path, "filename": PurePosixPath(path).name, **info}
                        for path, info in cataloged.items()
                        if info["status"] == "pending"
                    ]
                elif mode == "backfill":
                    # Download all pending and error files
                    to_download = [
                        {"remote_path": path, "filename": PurePosixPath(path).name, **info}
                        for path, info in cataloged.items()
                        if info["status"] in ("pending", "error")
                    ]
                else:
                    to_download = []
                
                # Apply filters
                if file_types:
                    to_download = [f for f in to_download 
                                   if self._classify_file(f["filename"]) in file_types]
                if file_pattern:
                    pattern = re.compile(file_pattern)
                    to_download = [f for f in to_download if pattern.search(f["filename"])]
                if max_files:
                    to_download = to_download[:max_files]
                
                logger.info(f"Downloading {len(to_download)} files...")
                
                # Download in batches with reconnection
                for batch_idx in range(0, len(to_download), self.BATCH_SIZE):
                    batch = to_download[batch_idx:batch_idx + self.BATCH_SIZE]
                    logger.info(f"Processing batch {batch_idx // self.BATCH_SIZE + 1} "
                               f"({len(batch)} files)")
                    
                    # Reconnect at start of each batch
                    if batch_idx > 0:
                        self._reconnect()
                    
                    for file_info in batch:
                        try:
                            # Get file size from SFTP if not available
                            if "remote_size" not in file_info or file_info.get("remote_size") is None:
                                try:
                                    sftp = self._get_sftp()
                                    stat_info = sftp.stat(file_info["remote_path"])
                                    file_info["remote_size"] = stat_info.st_size
                                except Exception:
                                    file_info["remote_size"] = 0
                            
                            gcs_path = self.download_file(file_info)
                            self.update_catalog_status(
                                file_info["remote_path"],
                                "downloaded",
                                gcs_path=gcs_path,
                            )
                            results["files_downloaded"] += 1
                            results["bytes_downloaded"] += file_info.get("remote_size", 0)
                            
                        except Exception as e:
                            error_msg = f"Error downloading {file_info['remote_path']}: {e}"
                            logger.error(error_msg)
                            results["errors"].append(error_msg)
                            self.update_catalog_status(
                                file_info["remote_path"],
                                "error",
                                error_message=str(e)[:500],
                            )
            
            # Log successful run
            self.log_ingest_run(
                status="success" if not results["errors"] else "partial",
                files_discovered=results["files_discovered"],
                files_downloaded=results["files_downloaded"],
                bytes_downloaded=results["bytes_downloaded"],
                run_started_at=run_started_at,
            )
            
            logger.info(
                f"Sync complete: {results['files_discovered']} discovered, "
                f"{results['files_cataloged']} cataloged, "
                f"{results['files_downloaded']} downloaded, "
                f"{len(results['errors'])} errors"
            )
            
        except Exception as e:
            error_msg = str(e)
            logger.error(f"Sync failed: {error_msg}")
            results["errors"].append(error_msg)
            
            self.log_ingest_run(
                status="error",
                error_message=error_msg[:500],
                run_started_at=run_started_at,
            )
        
        finally:
            self._disconnect()
        
        return results


def main():
    """Entry point for Cloud Run job."""
    parser = argparse.ArgumentParser(description="Freddie Mac SFTP Ingestor")
    parser.add_argument(
        "--mode",
        choices=["catalog", "incremental", "backfill"],
        default="incremental",
        help="Run mode: catalog (list only), incremental (new files), backfill (all pending)"
    )
    parser.add_argument(
        "--file-types",
        nargs="+",
        help="Filter by file types (e.g., intraday_issuance monthly_issuance)"
    )
    parser.add_argument(
        "--file-pattern",
        help="Regex pattern to filter filenames"
    )
    parser.add_argument(
        "--max-files",
        type=int,
        help="Maximum number of files to download"
    )
    parser.add_argument(
        "--skip-catalog",
        action="store_true",
        help="Skip cataloging, just download pending files"
    )
    
    args = parser.parse_args()
    
    ingestor = FreddieIngestor()
    
    results = ingestor.run(
        mode=args.mode,
        file_types=args.file_types,
        file_pattern=args.file_pattern,
        max_files=args.max_files,
        skip_catalog=args.skip_catalog,
    )
    
    if results["errors"]:
        logger.warning(f"Completed with {len(results['errors'])} errors")
        exit(1)
    
    logger.info("Freddie Mac sync completed successfully")


if __name__ == "__main__":
    main()
