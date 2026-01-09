"""
Analyze Freddie Mac SFTP server structure and create file inventory.
Run this to understand what files are available before bulk downloading.
"""

import json
import os
import re
import stat
from collections import defaultdict
from datetime import datetime
from pathlib import PurePosixPath

import paramiko
from dotenv import load_dotenv

load_dotenv()


def get_sftp_client():
    """Create SFTP connection."""
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    
    client.connect(
        hostname="data.mbs-securities.com",
        port=22,
        username=os.getenv("FREDDIE_USERNAME"),
        password=os.getenv("FREDDIE_PASSWORD"),
        look_for_keys=False,
        allow_agent=False,
    )
    
    return client, client.open_sftp()


def list_directory(sftp, path, max_depth=3, current_depth=0):
    """Recursively list directory structure."""
    results = []
    
    if current_depth > max_depth:
        return results
    
    try:
        items = sftp.listdir_attr(path)
    except IOError as e:
        print(f"  Cannot list {path}: {e}")
        return results
    
    for item in items:
        full_path = str(PurePosixPath(path) / item.filename)
        
        is_dir = stat.S_ISDIR(item.st_mode)
        
        info = {
            "path": full_path,
            "name": item.filename,
            "is_dir": is_dir,
            "size": item.st_size if not is_dir else 0,
            "modified": datetime.fromtimestamp(item.st_mtime).isoformat() if item.st_mtime else None,
        }
        
        results.append(info)
        
        if is_dir and current_depth < max_depth:
            results.extend(list_directory(sftp, full_path, max_depth, current_depth + 1))
    
    return results


def analyze_files(files):
    """Analyze file inventory."""
    stats = {
        "total_files": 0,
        "total_dirs": 0,
        "total_size_bytes": 0,
        "by_extension": defaultdict(lambda: {"count": 0, "size": 0}),
        "by_directory": defaultdict(lambda: {"count": 0, "size": 0}),
        "by_year": defaultdict(lambda: {"count": 0, "size": 0}),
        "file_patterns": defaultdict(int),
    }
    
    for f in files:
        if f["is_dir"]:
            stats["total_dirs"] += 1
        else:
            stats["total_files"] += 1
            stats["total_size_bytes"] += f["size"]
            
            # By extension
            ext = PurePosixPath(f["name"]).suffix.lower() or "no_ext"
            stats["by_extension"][ext]["count"] += 1
            stats["by_extension"][ext]["size"] += f["size"]
            
            # By top-level directory
            parts = f["path"].split("/")
            if len(parts) > 1:
                top_dir = parts[1]
                stats["by_directory"][top_dir]["count"] += 1
                stats["by_directory"][top_dir]["size"] += f["size"]
            
            # Extract year from filename
            year_match = re.search(r"20\d{2}", f["name"])
            if year_match:
                year = year_match.group()
                stats["by_year"][year]["count"] += 1
                stats["by_year"][year]["size"] += f["size"]
            
            # Identify file patterns (prefix before date)
            pattern_match = re.match(r"([A-Za-z_]+)", f["name"])
            if pattern_match:
                stats["file_patterns"][pattern_match.group(1)] += 1
    
    return stats


def format_size(bytes_val):
    """Format bytes to human readable."""
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if bytes_val < 1024:
            return f"{bytes_val:.2f} {unit}"
        bytes_val /= 1024
    return f"{bytes_val:.2f} PB"


def main():
    print("=" * 60)
    print("Freddie Mac SFTP Server Analysis")
    print("=" * 60)
    
    print("\nConnecting to SFTP...")
    client, sftp = get_sftp_client()
    print("Connected!")
    
    # List root directory first
    print("\n--- ROOT DIRECTORY ---")
    root_items = sftp.listdir_attr("/")
    for item in root_items:
        is_dir = stat.S_ISDIR(item.st_mode)
        print(f"  {'[DIR]' if is_dir else '[FILE]'} {item.filename}")
    
    # Deep scan
    print("\n--- SCANNING FILE STRUCTURE (this may take a minute) ---")
    all_files = list_directory(sftp, "/", max_depth=4)
    
    print(f"\nScanned {len(all_files)} items")
    
    # Analyze
    print("\n--- ANALYSIS ---")
    stats = analyze_files(all_files)
    
    print(f"\nTotal files: {stats['total_files']:,}")
    print(f"Total directories: {stats['total_dirs']:,}")
    print(f"Total size: {format_size(stats['total_size_bytes'])}")
    
    print("\n--- BY EXTENSION ---")
    for ext, data in sorted(stats["by_extension"].items(), key=lambda x: -x[1]["count"])[:10]:
        print(f"  {ext}: {data['count']:,} files ({format_size(data['size'])})")
    
    print("\n--- BY TOP DIRECTORY ---")
    for dir_name, data in sorted(stats["by_directory"].items(), key=lambda x: -x[1]["count"]):
        print(f"  {dir_name}: {data['count']:,} files ({format_size(data['size'])})")
    
    print("\n--- BY YEAR ---")
    for year, data in sorted(stats["by_year"].items()):
        print(f"  {year}: {data['count']:,} files ({format_size(data['size'])})")
    
    print("\n--- FILE PATTERNS (top 20) ---")
    for pattern, count in sorted(stats["file_patterns"].items(), key=lambda x: -x[1])[:20]:
        print(f"  {pattern}: {count:,} files")
    
    # Save full inventory
    inventory_file = "/Users/anaishowland/oasive_db/docs/freddie_sftp_inventory.json"
    with open(inventory_file, "w") as f:
        json.dump({
            "scan_date": datetime.utcnow().isoformat(),
            "stats": {
                "total_files": stats["total_files"],
                "total_dirs": stats["total_dirs"],
                "total_size_bytes": stats["total_size_bytes"],
                "by_extension": dict(stats["by_extension"]),
                "by_directory": dict(stats["by_directory"]),
                "by_year": dict(stats["by_year"]),
                "file_patterns": dict(stats["file_patterns"]),
            },
            "files": all_files[:1000],  # First 1000 for reference
        }, f, indent=2, default=str)
    print(f"\nFull inventory saved to: {inventory_file}")
    
    sftp.close()
    client.close()
    print("\nDone!")


if __name__ == "__main__":
    main()
