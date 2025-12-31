"""
Freddie Mac SFTP Data Ingestor

Downloads disclosure files from CSS SFTP server and stages them in GCS.
Designed to run as a Cloud Run job on a scheduled basis.

SFTP Details:
- Domain: data.mbs-securities.com
- Port: 22
- Credentials: Provided by CSS (format: svcfre-<vendor>)

See: Freddie_CSS_SFTP_Connectivity_Instructions.pdf
"""

import logging
import os
import re
import stat
from datetime import datetime
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
    
    The CSS SFTP contains various disclosure files including:
    - Pool-level data
    - Loan-level data  
    - Factor files
    - Monthly updates
    
    This ingestor:
    1. Connects to SFTP and lists available files
    2. Compares against local catalog to find new files
    3. Downloads new files to GCS
    4. Updates the file catalog in Postgres
    """
    
    # Common file patterns for Freddie Mac disclosure files
    FILE_PATTERNS = {
        "loan_level": re.compile(r".*loan.*\.zip$", re.IGNORECASE),
        "pool": re.compile(r".*pool.*\.zip$", re.IGNORECASE),
        "factor": re.compile(r".*factor.*\.zip$", re.IGNORECASE),
        "disclosure": re.compile(r".*disclosure.*\.zip$", re.IGNORECASE),
    }
    
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
    
    def _get_sftp_client(self) -> paramiko.SFTPClient:
        """Create an SFTP connection to CSS server."""
        # Debug: Log connection details (not password)
        logger.info(f"SFTP Connection Details:")
        logger.info(f"  Host: {self.freddie_config.host}")
        logger.info(f"  Port: {self.freddie_config.port}")
        logger.info(f"  Username: '{self.freddie_config.username}'")
        logger.info(f"  Username length: {len(self.freddie_config.username)}")
        logger.info(f"  Password length: {len(self.freddie_config.password)}")
        
        transport = paramiko.Transport((
            self.freddie_config.host,
            self.freddie_config.port,
        ))
        
        # Disable host key checking as per CSS documentation
        # "Use of remote host key validation is not recommended"
        transport.connect(
            username=self.freddie_config.username,
            password=self.freddie_config.password,
        )
        
        sftp = paramiko.SFTPClient.from_transport(transport)
        logger.info(f"Connected to SFTP: {self.freddie_config.host}")
        return sftp
    
    def _classify_file(self, filename: str) -> str | None:
        """Classify a file based on its name pattern."""
        for file_type, pattern in self.FILE_PATTERNS.items():
            if pattern.match(filename):
                return file_type
        return "other"
    
    def _extract_date_from_filename(self, filename: str) -> datetime | None:
        """Try to extract a date from the filename."""
        # Common patterns: YYYYMMDD, YYYY-MM-DD, YYYY_MM
        patterns = [
            r"(\d{4})(\d{2})(\d{2})",  # YYYYMMDD
            r"(\d{4})-(\d{2})-(\d{2})",  # YYYY-MM-DD
            r"(\d{4})_(\d{2})",  # YYYY_MM
        ]
        
        for pattern in patterns:
            match = re.search(pattern, filename)
            if match:
                groups = match.groups()
                try:
                    if len(groups) == 3:
                        return datetime(int(groups[0]), int(groups[1]), int(groups[2]))
                    elif len(groups) == 2:
                        return datetime(int(groups[0]), int(groups[1]), 1)
                except ValueError:
                    continue
        return None
    
    def list_remote_files(
        self,
        sftp: paramiko.SFTPClient,
        remote_dir: str = "/",
        recursive: bool = True,
    ) -> list[dict[str, Any]]:
        """
        List all files in the remote directory.
        
        Args:
            sftp: Active SFTP client
            remote_dir: Directory to list
            recursive: Whether to recurse into subdirectories
        
        Returns:
            List of file metadata dictionaries
        """
        files = []
        
        try:
            items = sftp.listdir_attr(remote_dir)
        except IOError as e:
            logger.warning(f"Cannot list directory {remote_dir}: {e}")
            return files
        
        for item in items:
            full_path = str(PurePosixPath(remote_dir) / item.filename)
            
            if stat.S_ISDIR(item.st_mode):
                if recursive:
                    files.extend(self.list_remote_files(sftp, full_path, recursive))
            else:
                file_info = {
                    "remote_path": full_path,
                    "filename": item.filename,
                    "file_type": self._classify_file(item.filename),
                    "file_date": self._extract_date_from_filename(item.filename),
                    "remote_size": item.st_size,
                    "remote_modified_at": datetime.fromtimestamp(item.st_mtime) if item.st_mtime else None,
                }
                files.append(file_info)
        
        return files
    
    def get_cataloged_files(self) -> set[str]:
        """Get set of remote paths already in the catalog."""
        with self.engine.connect() as conn:
            result = conn.execute(text("""
                SELECT remote_path FROM freddie_file_catalog
            """))
            return {row.remote_path for row in result}
    
    def add_to_catalog(self, file_info: dict[str, Any]) -> None:
        """Add a new file to the catalog."""
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
        """Update the download status of a cataloged file."""
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
            elif status == "error":
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
    
    def download_to_gcs(
        self,
        sftp: paramiko.SFTPClient,
        remote_path: str,
        filename: str,
    ) -> str:
        """
        Download a file from SFTP and upload to GCS.
        
        Returns:
            GCS path (gs://bucket/path)
        """
        # Create GCS path: freddie/raw/YYYY/MM/filename
        today = datetime.utcnow()
        gcs_path = f"freddie/raw/{today.year}/{today.month:02d}/{filename}"
        
        bucket = self.storage_client.bucket(self.gcs_config.raw_bucket)
        blob = bucket.blob(gcs_path)
        
        # Stream download to avoid loading large files in memory
        logger.info(f"Downloading {remote_path} to gs://{self.gcs_config.raw_bucket}/{gcs_path}")
        
        # Use BytesIO for smaller files, or temp file for larger ones
        buffer = BytesIO()
        sftp.getfo(remote_path, buffer)
        buffer.seek(0)
        
        blob.upload_from_file(buffer, timeout=300)
        
        return f"gs://{self.gcs_config.raw_bucket}/{gcs_path}"
    
    def log_ingest_run(
        self,
        status: str,
        files_discovered: int = 0,
        files_downloaded: int = 0,
        bytes_downloaded: int = 0,
        error_message: str | None = None,
        run_started_at: datetime | None = None,
    ) -> None:
        """Log an ingestion run."""
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
                    "run_started_at": run_started_at or datetime.utcnow(),
                    "run_completed_at": datetime.utcnow(),
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
        download: bool = True,
        file_types: list[str] | None = None,
        max_files: int | None = None,
    ) -> dict[str, Any]:
        """
        Run the Freddie Mac SFTP sync job.
        
        Args:
            download: Whether to download new files (False = catalog only)
            file_types: Optional list of file types to process
            max_files: Maximum number of files to download (for testing)
        
        Returns:
            Summary dictionary with results
        """
        run_started_at = datetime.utcnow()
        logger.info("Starting Freddie Mac SFTP sync")
        
        results = {
            "files_discovered": 0,
            "new_files": 0,
            "files_downloaded": 0,
            "bytes_downloaded": 0,
            "errors": [],
        }
        
        try:
            sftp = self._get_sftp_client()
            
            # List all remote files
            logger.info("Listing remote files...")
            remote_files = self.list_remote_files(sftp, "/")
            results["files_discovered"] = len(remote_files)
            logger.info(f"Found {len(remote_files)} files on SFTP server")
            
            # Filter by file type if specified
            if file_types:
                remote_files = [f for f in remote_files if f["file_type"] in file_types]
                logger.info(f"Filtered to {len(remote_files)} files of types: {file_types}")
            
            # Find new files not in catalog
            cataloged = self.get_cataloged_files()
            new_files = [f for f in remote_files if f["remote_path"] not in cataloged]
            results["new_files"] = len(new_files)
            logger.info(f"Found {len(new_files)} new files to process")
            
            # Add new files to catalog
            for file_info in new_files:
                self.add_to_catalog(file_info)
            
            # Download files if requested
            if download and new_files:
                files_to_download = new_files[:max_files] if max_files else new_files
                
                for file_info in files_to_download:
                    try:
                        gcs_path = self.download_to_gcs(
                            sftp,
                            file_info["remote_path"],
                            file_info["filename"],
                        )
                        self.update_catalog_status(
                            file_info["remote_path"],
                            "downloaded",
                            gcs_path=gcs_path,
                        )
                        results["files_downloaded"] += 1
                        results["bytes_downloaded"] += file_info["remote_size"] or 0
                        
                    except Exception as e:
                        error_msg = f"Error downloading {file_info['remote_path']}: {e}"
                        logger.error(error_msg)
                        results["errors"].append(error_msg)
                        self.update_catalog_status(
                            file_info["remote_path"],
                            "error",
                            error_message=str(e),
                        )
            
            sftp.close()
            
            self.log_ingest_run(
                status="success" if not results["errors"] else "partial",
                files_discovered=results["files_discovered"],
                files_downloaded=results["files_downloaded"],
                bytes_downloaded=results["bytes_downloaded"],
                run_started_at=run_started_at,
            )
            
            logger.info(
                f"Freddie sync complete: {results['files_discovered']} discovered, "
                f"{results['new_files']} new, {results['files_downloaded']} downloaded"
            )
            
        except Exception as e:
            error_msg = str(e)
            logger.error(f"Freddie sync failed: {error_msg}")
            results["errors"].append(error_msg)
            
            self.log_ingest_run(
                status="error",
                error_message=error_msg,
                run_started_at=run_started_at,
            )
        
        return results


def main():
    """Entry point for Cloud Run job."""
    ingestor = FreddieIngestor()
    
    # For initial run, catalog only (set download=True when ready)
    results = ingestor.run(download=True)
    
    if results["errors"]:
        logger.warning(f"Completed with {len(results['errors'])} errors")
        exit(1)
    
    logger.info("Freddie Mac sync completed successfully")


if __name__ == "__main__":
    main()
