"""Configuration management for Oasive data ingestion."""

import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()


@dataclass
class PostgresConfig:
    """Cloud SQL Postgres configuration."""
    instance_connection_name: str  # e.g., "project:region:instance"
    db_name: str
    user: str
    password: str
    
    @classmethod
    def from_env(cls) -> "PostgresConfig":
        return cls(
            instance_connection_name=os.getenv("CLOUDSQL_CONNECTION_NAME", "gen-lang-client-0343560978:us-central1:oasive-postgres"),
            db_name=os.getenv("POSTGRES_DB", "postgres"),
            user=os.getenv("POSTGRES_USER", "postgres"),
            password=os.getenv("POSTGRES_PASSWORD", ""),
        )


@dataclass
class FREDConfig:
    """FRED API configuration."""
    api_key: str
    base_url: str = "https://api.stlouisfed.org/fred"
    
    @classmethod
    def from_env(cls) -> "FREDConfig":
        return cls(
            api_key=os.getenv("FRED_API_KEY", ""),
        )


@dataclass
class FreddieConfig:
    """Freddie Mac SFTP configuration."""
    host: str
    port: int
    username: str
    password: str
    
    @classmethod
    def from_env(cls) -> "FreddieConfig":
        return cls(
            host="data.mbs-securities.com",
            port=22,
            username=os.getenv("FREDDIE_USERNAME", ""),
            password=os.getenv("FREDDIE_PASSWORD", ""),
        )


@dataclass
class GCSConfig:
    """Google Cloud Storage configuration."""
    project_id: str
    raw_bucket: str
    
    @classmethod
    def from_env(cls) -> "GCSConfig":
        return cls(
            project_id=os.getenv("GCP_PROJECT_ID", "gen-lang-client-0343560978"),
            raw_bucket=os.getenv("GCS_RAW_BUCKET", "oasive-raw-data"),
        )
