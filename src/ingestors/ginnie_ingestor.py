"""
Ginnie Mae Bulk Download Ingestor

Downloads disclosure files from bulk.ginniemae.gov using Playwright (headless browser).
The site requires JavaScript execution to pass bot protection, so simple HTTP won't work.

File Types:
- Daily: dailySFPS.zip, dailySFS.zip, dailyll_new.zip
- Monthly New Issues: nimonSFPS_YYYYMM.zip, nimonSFS_YYYYMM.zip, dailyllmni.zip  
- Portfolio: monthlySFPS_YYYYMM.zip, monthlySFS_YYYYMM.zip, llmon1/2_YYYYMM.zip
- Factor: factorA1/A2_YYYYMM.zip, factorB1/B2_YYYYMM.zip
- Liquidations: llmonliq_YYYYMM.zip

Authentication Strategy:
- Primary: No auth required - site is public, just needs JS execution for bot check
- Fallback: If login required, automate via Gmail API to capture magic link
- Account: anais@oasive.ai

Reliability:
- Explicit waits for page loads (networkidle)
- Retry logic with exponential backoff (3 attempts)
- Screenshot capture on ALL failures (uploaded to GCS for debugging)
- Health check detects login walls
- Email alert on auth_required status

Gmail API Setup (for automated magic link capture):
1. Enable Gmail API in GCP console
2. Create OAuth2 credentials or use service account with domain-wide delegation
3. Store credentials in Secret Manager as 'gmail-api-credentials'
4. Grant read access to anais@oasive.ai inbox
"""

import argparse
import base64
import json
import logging
import os
import re
import smtplib
import tempfile
import time
from datetime import datetime, timezone
from email.mime.text import MIMEText
from pathlib import Path
from typing import Any

import requests
from google.cloud import secretmanager, storage
from sqlalchemy import text
from tenacity import retry, stop_after_attempt, wait_exponential

from src.config import GCSConfig, PostgresConfig
from src.db.connection import get_engine

# Playwright import with fallback
try:
    from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False
    PlaywrightTimeout = Exception

# Gmail API import with fallback
try:
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    GMAIL_API_AVAILABLE = True
except ImportError:
    GMAIL_API_AVAILABLE = False

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


class GinnieIngestor:
    """
    Downloads Ginnie Mae disclosure files using Playwright.
    
    Supports multiple run modes:
    - daily: Download daily new issue files
    - monthly: Download monthly portfolio and new issue files  
    - factor: Download factor files
    - backfill: Download all available files
    - catalog: List files without downloading
    
    Features:
    - Playwright for JavaScript-heavy site
    - Cookie-based session persistence
    - Retry logic with screenshots on failure
    - GCS upload with streaming
    """
    
    BULK_URL = "https://bulk.ginniemae.gov/"
    
    # File patterns by category
    FILE_PATTERNS = {
        # Daily files
        "daily_pool": re.compile(r"^dailySFPS\.zip$"),
        "daily_pool_supp": re.compile(r"^dailySFS\.zip$"),
        "daily_loan": re.compile(r"^dailyll_new\.zip$"),
        
        # Monthly new issues
        "monthly_new_pool": re.compile(r"^nimonSFPS_\d{6}\.zip$"),
        "monthly_new_pool_supp": re.compile(r"^nimonSFS_\d{6}\.zip$"),
        "monthly_new_loan": re.compile(r"^dailyllmni\.zip$"),
        
        # Monthly portfolio
        "portfolio_pool": re.compile(r"^monthlySFPS_\d{6}\.zip$"),
        "portfolio_pool_supp": re.compile(r"^monthlySFS_\d{6}\.zip$"),
        "portfolio_loan_g1": re.compile(r"^llmon1_\d{6}\.zip$"),
        "portfolio_loan_g2": re.compile(r"^llmon2_\d{6}\.zip$"),
        
        # Liquidations
        "liquidations": re.compile(r"^llmonliq_\d{6}\.zip$"),
        
        # Factor files
        "factor_a1": re.compile(r"^factorA1_\d{6}\.zip$"),
        "factor_a2": re.compile(r"^factorA2_\d{6}\.zip$"),
        "factor_b1": re.compile(r"^factorB1_\d{6}\.zip$"),
        "factor_b2": re.compile(r"^factorB2_\d{6}\.zip$"),
        "factor_a_plat": re.compile(r"^factorAplat_\d{6}\.txt$"),
        "factor_a_add": re.compile(r"^factorAAdd_\d{6}\.zip$"),
        
        # HMBS files
        "hmbs_daily": re.compile(r"^hdaily.*\.(zip|txt)$"),
        "hmbs_monthly": re.compile(r"^h(monthly|ni|llmon).*\.(zip|txt)$"),
    }
    
    # Mode to file type mapping
    MODE_FILE_TYPES = {
        "daily": ["daily_pool", "daily_pool_supp", "daily_loan"],
        "monthly": [
            "monthly_new_pool", "monthly_new_pool_supp", "monthly_new_loan",
            "portfolio_pool", "portfolio_pool_supp", 
            "portfolio_loan_g1", "portfolio_loan_g2",
            "liquidations",
        ],
        "factor": ["factor_a1", "factor_a2", "factor_b1", "factor_b2", "factor_a_plat", "factor_a_add"],
        "backfill": None,  # All files from current page
        "historical": None,  # Generate historical URLs programmatically
    }
    
    # Timeouts and retries
    PAGE_TIMEOUT = 60000  # 60 seconds
    DOWNLOAD_TIMEOUT = 300000  # 5 minutes for large files
    MAX_RETRIES = 3
    RETRY_DELAY = 5
    
    def __init__(
        self,
        postgres_config: PostgresConfig | None = None,
        gcs_config: GCSConfig | None = None,
    ):
        if not PLAYWRIGHT_AVAILABLE:
            raise ImportError(
                "Playwright not installed. Run: pip install playwright && playwright install chromium"
            )
        
        self.postgres_config = postgres_config or PostgresConfig.from_env()
        self.gcs_config = gcs_config or GCSConfig.from_env()
        
        self.engine = get_engine(self.postgres_config)
        self.storage_client = storage.Client(project=self.gcs_config.project_id)
        
        self._browser = None
        self._context = None
        self._page = None
        self._cookies_loaded = False
    
    def _get_secret(self, secret_id: str) -> str | None:
        """Get a secret from Secret Manager."""
        try:
            client = secretmanager.SecretManagerServiceClient()
            name = f"projects/{self.gcs_config.project_id}/secrets/{secret_id}/versions/latest"
            response = client.access_secret_version(request={"name": name})
            return response.payload.data.decode("UTF-8")
        except Exception as e:
            logger.debug(f"Could not load secret {secret_id}: {e}")
            return None
    
    def _save_secret(self, secret_id: str, value: str) -> bool:
        """Save a secret to Secret Manager (creates if doesn't exist)."""
        try:
            client = secretmanager.SecretManagerServiceClient()
            parent = f"projects/{self.gcs_config.project_id}"
            
            # Try to create secret if it doesn't exist
            try:
                client.create_secret(
                    request={
                        "parent": parent,
                        "secret_id": secret_id,
                        "secret": {"replication": {"automatic": {}}},
                    }
                )
                logger.info(f"Created secret: {secret_id}")
            except Exception:
                pass  # Secret already exists
            
            # Add new version
            secret_name = f"{parent}/secrets/{secret_id}"
            client.add_secret_version(
                request={
                    "parent": secret_name,
                    "payload": {"data": value.encode("UTF-8")},
                }
            )
            logger.info(f"Saved secret version: {secret_id}")
            return True
        except Exception as e:
            logger.error(f"Could not save secret {secret_id}: {e}")
            return False
    
    def _get_cookies_from_secret(self) -> list[dict] | None:
        """Load session cookies from Secret Manager."""
        cookies_json = self._get_secret("ginnie-session-cookies")
        if cookies_json:
            try:
                return json.loads(cookies_json)
            except json.JSONDecodeError:
                return None
        return None
    
    def _save_cookies_to_secret(self, cookies: list[dict]) -> None:
        """Save session cookies to Secret Manager."""
        self._save_secret("ginnie-session-cookies", json.dumps(cookies))
    
    def _send_alert_email(self, subject: str, body: str) -> None:
        """
        Send alert email via SendGrid or SMTP.
        Falls back to logging if email not configured.
        """
        alert_email = os.environ.get("ALERT_EMAIL", "anais@oasive.ai")
        sendgrid_key = self._get_secret("sendgrid-api-key")
        
        if sendgrid_key:
            try:
                import requests
                response = requests.post(
                    "https://api.sendgrid.com/v3/mail/send",
                    headers={
                        "Authorization": f"Bearer {sendgrid_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "personalizations": [{"to": [{"email": alert_email}]}],
                        "from": {"email": "alerts@oasive.ai", "name": "Oasive Alerts"},
                        "subject": f"[Oasive Alert] {subject}",
                        "content": [{"type": "text/plain", "value": body}],
                    },
                    timeout=10,
                )
                if response.status_code == 202:
                    logger.info(f"Alert email sent to {alert_email}")
                else:
                    logger.warning(f"SendGrid returned {response.status_code}")
            except Exception as e:
                logger.warning(f"Could not send alert email: {e}")
        else:
            # Just log if no email configured
            logger.warning(f"ALERT: {subject}\n{body}")
    
    def _check_for_magic_link_email(self, since_minutes: int = 10) -> str | None:
        """
        Check Gmail for magic link from Ginnie Mae.
        Returns the magic link URL if found.
        
        Requires Gmail API credentials in Secret Manager.
        """
        if not GMAIL_API_AVAILABLE:
            logger.warning("Gmail API not available - install google-api-python-client")
            return None
        
        creds_json = self._get_secret("gmail-api-credentials")
        if not creds_json:
            logger.warning("Gmail API credentials not configured")
            return None
        
        try:
            creds_data = json.loads(creds_json)
            credentials = service_account.Credentials.from_service_account_info(
                creds_data,
                scopes=['https://www.googleapis.com/auth/gmail.readonly'],
                subject='anais@oasive.ai'  # Email to impersonate
            )
            
            service = build('gmail', 'v1', credentials=credentials)
            
            # Search for recent emails from Ginnie Mae
            query = f'from:ginniemae.gov newer_than:{since_minutes}m'
            results = service.users().messages().list(
                userId='me', q=query, maxResults=5
            ).execute()
            
            messages = results.get('messages', [])
            
            for msg in messages:
                # Get full message
                full_msg = service.users().messages().get(
                    userId='me', id=msg['id'], format='full'
                ).execute()
                
                # Decode body
                payload = full_msg.get('payload', {})
                body = ""
                
                if 'parts' in payload:
                    for part in payload['parts']:
                        if part['mimeType'] == 'text/plain':
                            body = base64.urlsafe_b64decode(
                                part['body']['data']
                            ).decode('utf-8')
                            break
                elif 'body' in payload and 'data' in payload['body']:
                    body = base64.urlsafe_b64decode(
                        payload['body']['data']
                    ).decode('utf-8')
                
                # Look for magic link
                link_match = re.search(
                    r'https://[^\s]*(?:login|auth|verify|confirm)[^\s]*',
                    body,
                    re.IGNORECASE
                )
                if link_match:
                    logger.info("Found magic link in email")
                    return link_match.group(0)
            
            logger.info("No magic link email found")
            return None
            
        except Exception as e:
            logger.error(f"Error checking Gmail: {e}")
            return None
    
    def _attempt_automated_login(self) -> bool:
        """
        Attempt to complete login automatically.
        
        Authentication flow:
        1. Enter email address
        2. Submit -> redirects to security question page
        3. Answer security question
        4. Click Verify -> authenticated, can download files
        
        Returns True if successful.
        """
        logger.info("Attempting automated login...")
        
        page_content = self._page.content().lower()
        
        # Step 1: Check if we need to answer security question
        if "secret question" in page_content or "welcome back" in page_content:
            logger.info("Security question page detected")
            return self._answer_security_question()
        
        # Step 2: Check if we need to enter email first
        email_field = self._page.query_selector('input[name*="email" i], input[type="email"]')
        
        if email_field:
            logger.info("Email entry page detected")
            
            # Enter email
            email_field.fill("anais@oasive.ai")
            
            # Look for submit button
            submit_btn = self._page.query_selector(
                'input[type="submit"][value*="Submit" i], '
                'button[type="submit"], '
                'input[value="Submit"]'
            )
            if submit_btn:
                submit_btn.click()
                logger.info("Submitted email")
                
                # Wait for page transition
                self._page.wait_for_load_state("networkidle", timeout=30000)
                time.sleep(2)
                
                # Check if we're now on security question page
                page_content = self._page.content().lower()
                if "secret question" in page_content or "welcome back" in page_content:
                    return self._answer_security_question()
        
        # Step 3: Fallback to magic link if configured
        magic_link = self._check_for_magic_link_email(since_minutes=5)
        if magic_link:
            self._page.goto(magic_link, wait_until="networkidle")
            time.sleep(2)
            if "login" not in self._page.url.lower():
                logger.info("Magic link login successful!")
                cookies = self._context.cookies()
                self._save_cookies_to_secret(cookies)
                return True
        
        return False
    
    def _handle_download_auth(self) -> bool:
        """
        Handle full authentication flow for downloads.
        
        Steps:
        1. Enter email address if prompted
        2. Click Submit
        3. Answer security question if prompted
        4. Click Verify
        
        Returns True if authentication succeeded.
        """
        import time
        
        page_content = self._page.content().lower()
        
        # Step 1: Check if email entry is needed
        email_input = self._page.query_selector('input[type="text"][name*="Email" i], input[type="email"]')
        
        if email_input or "enter your e-mail" in page_content:
            logger.info("Email entry required - filling email...")
            
            # Get the email to use
            email = os.environ.get("GINNIE_EMAIL", "anais@oasive.ai")
            
            if not email_input:
                # Try other selectors
                email_input = self._page.query_selector('input[type="text"]')
            
            if email_input:
                email_input.fill(email)
                logger.info(f"Filled email: {email}")
                
                # Click Submit button
                submit_btn = self._page.query_selector(
                    'input[type="submit"][value*="Submit" i], '
                    'button:has-text("Submit"), '
                    'input[value="Submit"]'
                )
                
                if submit_btn:
                    submit_btn.click()
                    logger.info("Clicked Submit button")
                    
                    # Wait for page to load
                    self._page.wait_for_load_state("networkidle", timeout=30000)
                    time.sleep(2)
                else:
                    logger.error("Could not find Submit button")
                    self._take_screenshot("no_submit_button")
                    return False
            else:
                logger.error("Could not find email input")
                self._take_screenshot("no_email_input")
                return False
        
        # Step 2: Check if security question is now visible
        return self._answer_security_question()
    
    def _answer_security_question(self) -> bool:
        """
        Answer the security question to complete authentication.
        
        Security answer is stored in Secret Manager as 'ginnie-security-answer'.
        """
        logger.info("Answering security question...")
        
        # Get security answer from Secret Manager
        security_answer = self._get_secret("ginnie-security-answer")
        if not security_answer:
            logger.error("Security answer not found in Secret Manager")
            self._send_alert_email(
                "Ginnie Mae Login Failed - Missing Security Answer",
                """The Ginnie Mae login requires a security answer but it's not configured.

Please add the security answer to Secret Manager:
  gcloud secrets create ginnie-security-answer --data-file=- --project=gen-lang-client-0343560978
  (then type the answer and press Ctrl+D)

Or update existing:
  echo -n "YourAnswer" | gcloud secrets versions add ginnie-security-answer --data-file=-

Security Question: In what city did you meet your spouse or significant other?
"""
            )
            return False
        
        # Find the answer input field
        answer_field = self._page.query_selector(
            'input[name*="answer" i], '
            'input[id*="answer" i], '
            'input[type="text"]:near(:text("Answer"))'
        )
        
        if not answer_field:
            # Try to find by label
            answer_field = self._page.query_selector('input[type="text"]')
            
        if not answer_field:
            logger.error("Could not find security answer field")
            self._take_screenshot("no_answer_field")
            return False
        
        # Enter the answer
        answer_field.fill(security_answer.strip())
        logger.info("Entered security answer")
        
        # Find and click Verify button
        verify_btn = self._page.query_selector(
            'input[type="submit"][value*="Verify" i], '
            'button:has-text("Verify"), '
            'input[value="Verify"]'
        )
        
        if not verify_btn:
            logger.error("Could not find Verify button")
            self._take_screenshot("no_verify_button")
            return False
        
        verify_btn.click()
        logger.info("Clicked Verify button")
        
        # Wait for authentication to complete
        self._page.wait_for_load_state("networkidle", timeout=30000)
        time.sleep(2)
        
        # Check if we're authenticated (should redirect to download or bulk page)
        current_url = self._page.url.lower()
        if "profile.aspx" not in current_url and "login" not in current_url:
            logger.info("Security question authentication successful!")
            # Save cookies for future sessions
            cookies = self._context.cookies()
            self._save_cookies_to_secret(cookies)
            return True
        
        # Check if we need to handle a download that started
        page_content = self._page.content().lower()
        if "download" in page_content or "file" in page_content:
            logger.info("Authentication appears successful - download may have started")
            cookies = self._context.cookies()
            self._save_cookies_to_secret(cookies)
            return True
        
        logger.error("Authentication may have failed - still on profile page")
        self._take_screenshot("auth_failed")
        return False
    
    def _start_browser(self, headless: bool = True) -> None:
        """Start Playwright browser with retry logic."""
        from playwright.sync_api import sync_playwright
        
        self._playwright = sync_playwright().start()
        
        # Launch browser with specific args for Cloud Run compatibility
        self._browser = self._playwright.chromium.launch(
            headless=headless,
            args=[
                "--disable-dev-shm-usage",  # Overcome limited resource problems
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-gpu",
            ]
        )
        
        # Create context with standard viewport
        self._context = self._browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        
        # Load cookies if available
        cookies = self._get_cookies_from_secret()
        if cookies:
            try:
                self._context.add_cookies(cookies)
                self._cookies_loaded = True
                logger.info("Loaded session cookies")
            except Exception as e:
                logger.warning(f"Could not load cookies: {e}")
        
        self._page = self._context.new_page()
        self._page.set_default_timeout(self.PAGE_TIMEOUT)
        
        logger.info(f"Browser started (headless={headless})")
    
    def _stop_browser(self) -> None:
        """Stop browser and save cookies."""
        if self._context:
            # Save cookies for next run
            try:
                cookies = self._context.cookies()
                if cookies:
                    self._save_cookies_to_secret(cookies)
            except Exception as e:
                logger.warning(f"Could not save cookies: {e}")
        
        if self._browser:
            self._browser.close()
        if hasattr(self, '_playwright') and self._playwright:
            self._playwright.stop()
        
        self._browser = None
        self._context = None
        self._page = None
    
    def _take_screenshot(self, name: str) -> str | None:
        """Take screenshot for debugging."""
        if not self._page:
            return None
        
        try:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"ginnie_debug_{name}_{timestamp}.png"
            
            # Save to temp and upload to GCS
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
                self._page.screenshot(path=tmp.name, full_page=True)
                
                bucket = self.storage_client.bucket(self.gcs_config.raw_bucket)
                gcs_path = f"ginnie/debug/{filename}"
                blob = bucket.blob(gcs_path)
                blob.upload_from_filename(tmp.name)
                
                os.unlink(tmp.name)
                
            logger.info(f"Screenshot saved to gs://{self.gcs_config.raw_bucket}/{gcs_path}")
            return gcs_path
        except Exception as e:
            logger.warning(f"Could not take screenshot: {e}")
            return None
    
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=4, max=30),
    )
    def _navigate_to_bulk_page(self, auto_login: bool = True) -> bool:
        """
        Navigate to bulk download page and wait for it to load.
        
        Args:
            auto_login: If True, attempt automated login via magic link if needed
            
        Returns True if successful, raises exception otherwise.
        """
        logger.info(f"Navigating to {self.BULK_URL}")
        
        # Navigate with networkidle wait (most reliable for JS-heavy pages)
        self._page.goto(self.BULK_URL, wait_until="networkidle", timeout=self.PAGE_TIMEOUT)
        
        # Check for login wall
        page_content = self._page.content().lower()
        current_url = self._page.url.lower()
        
        is_login_page = (
            "login" in current_url or 
            "sign in" in page_content or
            "enter your e-mail" in page_content or
            "create a new account" in page_content
        )
        
        if is_login_page:
            logger.warning("Login page detected")
            self._take_screenshot("login_detected")
            
            if auto_login:
                # Attempt automated login
                if self._attempt_automated_login():
                    # Re-navigate after successful login
                    self._page.goto(self.BULK_URL, wait_until="networkidle", timeout=self.PAGE_TIMEOUT)
                else:
                    # Send alert and raise
                    self._send_alert_email(
                        "Ginnie Mae Login Required",
                        f"""The Ginnie Mae bulk download job requires authentication.

Automated login failed. Please manually refresh the session:
1. Go to https://bulk.ginniemae.gov/
2. Log in with anais@oasive.ai
3. Run: python -m src.ingestors.ginnie_ingestor --export-cookies

Or set up Gmail API for automated magic link capture.

Debug screenshot: gs://{self.gcs_config.raw_bucket}/ginnie/debug/

Job: ginnie-ingestor
Time: {datetime.now(timezone.utc).isoformat()}
"""
                    )
                    raise AuthenticationRequiredError(
                        "Login required and automated login failed. Alert sent."
                    )
        
        # Wait for the file table to appear
        try:
            self._page.wait_for_selector("table", timeout=30000)
            logger.info("Page loaded successfully - file table visible")
            return True
        except PlaywrightTimeout:
            self._take_screenshot("page_load_timeout")
            
            # Send alert with debug info
            self._send_alert_email(
                "Ginnie Mae Page Load Failed",
                f"""The Ginnie Mae bulk download page failed to load properly.

URL: {self._page.url}
Expected: File listing table

Debug screenshot: gs://{self.gcs_config.raw_bucket}/ginnie/debug/

This might be a temporary issue. The job will retry.

Job: ginnie-ingestor
Time: {datetime.now(timezone.utc).isoformat()}
"""
            )
            raise
    
    def _parse_file_table(self) -> list[dict[str, Any]]:
        """Parse the file listing table from the page."""
        files = []
        
        # Find all tables on the page
        tables = self._page.query_selector_all("table")
        
        for table in tables:
            rows = table.query_selector_all("tr")
            
            for row in rows:
                cells = row.query_selector_all("td")
                if len(cells) < 3:
                    continue
                
                # Look for download link
                link = row.query_selector("a[href]")
                if not link:
                    continue
                
                filename = link.inner_text().strip()
                if not filename or not (filename.endswith(".zip") or filename.endswith(".txt") or filename.endswith(".csv")):
                    continue
                
                href = link.get_attribute("href")
                
                # Parse file size and date from cells
                file_size = None
                last_posted = None
                
                for cell in cells:
                    text = cell.inner_text().strip()
                    
                    # Try to parse as file size (numeric with commas)
                    if re.match(r"^[\d,]+$", text):
                        try:
                            file_size = int(text.replace(",", ""))
                        except ValueError:
                            pass
                    
                    # Try to parse as date
                    if "/" in text and ("AM" in text or "PM" in text):
                        try:
                            last_posted = datetime.strptime(text, "%m/%d/%Y %I:%M %p")
                        except ValueError:
                            pass
                
                # Classify file type
                file_type = self._classify_file(filename)
                file_category = self._get_file_category(filename)
                file_date = self._extract_date_from_filename(filename)
                
                files.append({
                    "filename": filename,
                    "href": href,
                    "file_type": file_type,
                    "file_category": file_category,
                    "file_date": file_date,
                    "file_size_bytes": file_size,
                    "last_posted_at": last_posted,
                })
        
        logger.info(f"Found {len(files)} files on page")
        return files
    
    def _classify_file(self, filename: str) -> str:
        """Classify file based on filename pattern."""
        for file_type, pattern in self.FILE_PATTERNS.items():
            if pattern.match(filename):
                return file_type
        return "other"
    
    def _get_file_category(self, filename: str) -> str:
        """Determine file category (MBS_SF, HMBS, etc.)."""
        fn_lower = filename.lower()
        
        if fn_lower.startswith("h") and ("hmbs" in fn_lower or "hmonthly" in fn_lower or "hdaily" in fn_lower or "hllmon" in fn_lower or "hni" in fn_lower):
            return "HMBS"
        elif "mf" in fn_lower or "multifamily" in fn_lower:
            return "MULTIFAMILY"
        elif "plat" in fn_lower:
            return "PLATINUM"
        elif "factor" in fn_lower or "remic" in fn_lower:
            return "FACTOR"
        else:
            return "MBS_SF"
    
    def _extract_date_from_filename(self, filename: str) -> datetime | None:
        """Extract date from filename like llmon1_202512.zip."""
        match = re.search(r"(\d{6})", filename)
        if match:
            try:
                date_str = match.group(1)
                return datetime(int(date_str[:4]), int(date_str[4:6]), 1)
            except ValueError:
                pass
        return None
    
    def _download_file(self, file_info: dict[str, Any]) -> str:
        """
        Download a file and upload to GCS using Playwright with authentication handling.
        
        Strategy:
        1. Click on the download link
        2. If redirected to profile.aspx, complete authentication
        3. Download the file
        4. Upload to GCS
        
        Returns GCS path.
        """
        filename = file_info["filename"]
        expected_size = file_info.get('file_size_bytes', 0)
        file_size_mb = expected_size / 1024 / 1024
        logger.info(f"Downloading {filename} (expected: {file_size_mb:.1f} MB)")
        
        # Find the download link and get its href
        link_selector = f'a:has-text("{filename}")'
        link = self._page.query_selector(link_selector)
        
        if not link:
            raise ValueError(f"Could not find download link for {filename}")
        
        href = link.get_attribute("href")
        if not href:
            raise ValueError(f"Download link for {filename} has no href")
        
        # Make href absolute if needed
        if href.startswith("/"):
            href = f"https://bulk.ginniemae.gov{href}"
        elif not href.startswith("http"):
            href = f"https://bulk.ginniemae.gov/{href}"
        
        logger.info(f"Download URL: {href}")
        
        # Try to download - use expect_download to capture download that may start during navigation
        download_path = None
        
        try:
            # First attempt: Navigate to download URL with expect_download wrapping it
            with self._page.expect_download(timeout=60000) as download_info:
                try:
                    self._page.goto(href, wait_until="load", timeout=60000)
                except Exception as nav_error:
                    # "Download is starting" error is expected when download starts during navigation
                    if "Download is starting" not in str(nav_error):
                        raise
            
            download = download_info.value
            download_path = download.path()
            logger.info(f"Download captured during navigation")
            
        except PlaywrightTimeout:
            # Download didn't start immediately - likely redirected to auth page
            logger.info("Download didn't start immediately, checking for auth...")
            
            # Check if we're on auth page
            current_url = self._page.url.lower()
            page_content = self._page.content().lower()
            
            if "profile.aspx" in current_url or "enter your e-mail" in page_content or "secret question" in page_content:
                logger.info("Authentication required for download")
                
                # Handle full auth flow
                if not self._handle_download_auth():
                    raise AuthenticationRequiredError(f"Authentication failed for {filename}")
                
                # After auth, try download again
                logger.info("Auth completed, retrying download...")
                
                try:
                    with self._page.expect_download(timeout=self.DOWNLOAD_TIMEOUT) as download_info:
                        try:
                            self._page.goto(href, wait_until="load", timeout=60000)
                        except Exception as nav_error:
                            if "Download is starting" not in str(nav_error):
                                raise
                    
                    download = download_info.value
                    download_path = download.path()
                    logger.info(f"Download captured after authentication")
                    
                except Exception as e:
                    logger.error(f"Download failed after auth: {e}")
                    raise
            else:
                # We're on some other page - maybe the download page but it didn't auto-start
                raise ValueError(f"Unexpected page state for {filename}")
        
        if not download_path or not os.path.exists(download_path):
            raise ValueError(f"Download failed - no file path for {filename}")
        
        file_size = os.path.getsize(download_path)
        
        # Verify download is not an HTML error page
        with open(download_path, 'rb') as f:
            header = f.read(50)
            if b'<!DOCTYPE' in header or b'<html' in header:
                os.unlink(download_path)
                raise ValueError(f"Download returned HTML error page instead of actual file")
        
        logger.info(f"Downloaded {file_size / 1024 / 1024:.1f} MB")
        
        # Upload to GCS
        now = datetime.now(timezone.utc)
        gcs_path = f"ginnie/raw/{now.year}/{now.month:02d}/{filename}"
        
        bucket = self.storage_client.bucket(self.gcs_config.raw_bucket)
        blob = bucket.blob(gcs_path)
        blob.upload_from_filename(download_path, timeout=300)
        
        # Clean up
        try:
            os.unlink(download_path)
        except Exception:
            pass
        
        full_gcs_path = f"gs://{self.gcs_config.raw_bucket}/{gcs_path}"
        logger.info(f"Uploaded to {full_gcs_path}")
        
        return full_gcs_path
    
    def get_cataloged_files(self) -> dict[str, dict]:
        """Get cataloged files with their status."""
        with self.engine.connect() as conn:
            result = conn.execute(text("""
                SELECT filename, file_type, download_status, local_gcs_path
                FROM ginnie_file_catalog
            """))
            files = {}
            for row in result:
                # Don't compute href here - files from the page should be
                # downloaded by finding them on the page. Only historical
                # files generated programmatically should have hrefs.
                files[row.filename] = {
                    "status": row.download_status,
                    "gcs_path": row.local_gcs_path,
                    "file_type": row.file_type,
                    "href": None,  # Will be set by historical generation only
                }
            return files
    
    def add_to_catalog(self, file_info: dict[str, Any]) -> None:
        """Add file to catalog."""
        with self.engine.connect() as conn:
            conn.execute(
                text("""
                    INSERT INTO ginnie_file_catalog 
                    (filename, file_type, file_category, file_date, file_size_bytes, 
                     last_posted_at, download_status)
                    VALUES (:filename, :file_type, :file_category, :file_date, 
                            :file_size_bytes, :last_posted_at, 'pending')
                    ON CONFLICT (filename) DO UPDATE SET
                        file_size_bytes = EXCLUDED.file_size_bytes,
                        last_posted_at = EXCLUDED.last_posted_at,
                        updated_at = NOW()
                """),
                file_info
            )
            conn.commit()
    
    def update_catalog_status(
        self,
        filename: str,
        status: str,
        gcs_path: str | None = None,
        error_message: str | None = None,
    ) -> None:
        """Update catalog entry status."""
        with self.engine.connect() as conn:
            if status == "downloaded":
                conn.execute(
                    text("""
                        UPDATE ginnie_file_catalog 
                        SET download_status = :status,
                            local_gcs_path = :gcs_path,
                            downloaded_at = NOW(),
                            updated_at = NOW()
                        WHERE filename = :filename
                    """),
                    {"status": status, "gcs_path": gcs_path, "filename": filename}
                )
            else:
                conn.execute(
                    text("""
                        UPDATE ginnie_file_catalog 
                        SET download_status = :status,
                            error_message = :error_message,
                            updated_at = NOW()
                        WHERE filename = :filename
                    """),
                    {"status": status, "error_message": error_message, "filename": filename}
                )
            conn.commit()
    
    def log_ingest_run(
        self,
        status: str,
        run_mode: str,
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
                    INSERT INTO ginnie_ingest_log 
                    (run_started_at, run_completed_at, run_mode, status, files_discovered, 
                     files_downloaded, bytes_downloaded, error_message)
                    VALUES (:run_started_at, :run_completed_at, :run_mode, :status, 
                            :files_discovered, :files_downloaded, :bytes_downloaded, :error_message)
                """),
                {
                    "run_started_at": run_started_at or datetime.now(timezone.utc),
                    "run_completed_at": datetime.now(timezone.utc),
                    "run_mode": run_mode,
                    "status": status,
                    "files_discovered": files_discovered,
                    "files_downloaded": files_downloaded,
                    "bytes_downloaded": bytes_downloaded,
                    "error_message": error_message,
                }
            )
            conn.commit()
    
    def _generate_historical_file_list(
        self,
        start_year: int = 2013,
        start_month: int = 1,
        end_year: int | None = None,
        end_month: int | None = None,
    ) -> list[dict[str, Any]]:
        """
        Generate list of historical files to download.
        
        Historical files are available at predictable URLs but not listed on 
        the bulk download page. This generates the full list of monthly files
        from the start date to present.
        
        File types with monthly archives:
        - llmon1_YYYYMM.zip - Ginnie I loan-level portfolio
        - llmon2_YYYYMM.zip - Ginnie II loan-level portfolio  
        - monthlySFPS_YYYYMM.zip - Pool/Security data
        - monthlySFS_YYYYMM.zip - Pool Supplemental
        - nimonSFPS_YYYYMM.zip - Monthly new issues pool
        - nimonSFS_YYYYMM.zip - Monthly new issues supplemental
        - llmonliq_YYYYMM.zip - Liquidations
        - factorA1_YYYYMM.zip, factorA2_YYYYMM.zip - Factor data
        - factorB1_YYYYMM.zip, factorB2_YYYYMM.zip - Factor data
        """
        from datetime import date
        from dateutil.relativedelta import relativedelta
        
        if end_year is None:
            end_date = date.today()
        else:
            end_month = end_month or 12
            end_date = date(end_year, end_month, 1)
        
        start_date = date(start_year, start_month, 1)
        
        # File templates with their types and categories
        file_templates = [
            # Loan-level portfolio (most important for research)
            ("llmon1_{ym}.zip", "portfolio_loan_g1", "MBS_SF"),
            ("llmon2_{ym}.zip", "portfolio_loan_g2", "MBS_SF"),
            # Pool-level portfolio
            ("monthlySFPS_{ym}.zip", "portfolio_pool", "MBS_SF"),
            ("monthlySFS_{ym}.zip", "portfolio_pool_supp", "MBS_SF"),
            # Monthly new issues
            ("nimonSFPS_{ym}.zip", "monthly_new_pool", "MBS_SF"),
            ("nimonSFS_{ym}.zip", "monthly_new_pool_supp", "MBS_SF"),
            # Liquidations
            ("llmonliq_{ym}.zip", "liquidations", "MBS_SF"),
            # Factor data
            ("factorA1_{ym}.zip", "factor_a1", "FACTOR"),
            ("factorA2_{ym}.zip", "factor_a2", "FACTOR"),
            ("factorB1_{ym}.zip", "factor_b1", "FACTOR"),
            ("factorB2_{ym}.zip", "factor_b2", "FACTOR"),
            # HMBS loan-level
            ("hllmon1_{ym}.zip", "hmbs_monthly", "HMBS"),
            ("hllmon2_{ym}.zip", "hmbs_monthly", "HMBS"),
        ]
        
        files = []
        current = start_date
        
        while current <= end_date:
            ym = current.strftime("%Y%m")
            file_date = current
            
            for template, file_type, category in file_templates:
                filename = template.format(ym=ym)
                files.append({
                    "filename": filename,
                    "file_type": file_type,
                    "file_category": category,
                    "file_date": file_date,
                    "file_size_bytes": None,
                    "last_posted_at": None,
                    "href": f"https://bulk.ginniemae.gov/protectedfiledownload.aspx?dlfile=data_bulk/{filename}",
                })
            
            current += relativedelta(months=1)
        
        logger.info(f"Generated {len(files)} historical file URLs from {start_date} to {end_date}")
        return files
    
    def _download_file_direct(self, filename: str, url: str) -> str:
        """
        Download a file directly by URL using Playwright's download handling.
        This maintains the authenticated session properly.
        """
        logger.info(f"Downloading {filename} from {url}")
        
        try:
            # Use Playwright to download - this handles the session properly
            with self._page.expect_download(timeout=self.DOWNLOAD_TIMEOUT) as download_info:
                # Navigate to the download URL - this should trigger a download
                self._page.goto(url, wait_until="commit")
            
            download = download_info.value
            download_path = download.path()
            
            # Check if download succeeded
            if not download_path or not os.path.exists(download_path):
                raise ValueError(f"Download failed for {filename}")
            
            file_size = os.path.getsize(download_path)
            if file_size < 1000:
                os.unlink(download_path)
                raise ValueError(f"Downloaded file too small ({file_size} bytes)")
            
            logger.info(f"Downloaded {file_size / 1024 / 1024:.1f} MB")
            
        except PlaywrightTimeout:
            # If expect_download times out, try checking if it's a redirect to login
            current_url = self._page.url.lower()
            if 'profile.aspx' in current_url or 'login' in current_url:
                raise AuthenticationRequiredError(f"Authentication required for {filename}")
            raise
        
        # Upload to GCS
        # Extract year/month from filename for organization
        match = re.search(r"_(\d{4})(\d{2})\.", filename)
        if match:
            year, month = match.groups()
            gcs_path = f"ginnie/raw/{year}/{month}/{filename}"
        else:
            now = datetime.now(timezone.utc)
            gcs_path = f"ginnie/raw/{now.year}/{now.month:02d}/{filename}"
        
        bucket = self.storage_client.bucket(self.gcs_config.raw_bucket)
        blob = bucket.blob(gcs_path)
        blob.upload_from_filename(download_path, timeout=300)
        
        # Clean up
        try:
            os.unlink(download_path)
        except Exception:
            pass
        
        full_gcs_path = f"gs://{self.gcs_config.raw_bucket}/{gcs_path}"
        logger.info(f"Uploaded to {full_gcs_path}")
        
        return full_gcs_path
    
    def run(
        self,
        mode: str = "daily",
        file_types: list[str] | None = None,
        max_files: int | None = None,
        headless: bool = True,
        skip_catalog: bool = False,
    ) -> dict[str, Any]:
        """
        Run the Ginnie Mae bulk download sync.
        
        Args:
            mode: 'daily', 'monthly', 'factor', 'backfill', or 'catalog'
            file_types: Override file types to download
            max_files: Maximum files to download
            headless: Run browser in headless mode
            skip_catalog: Skip cataloging, download pending files directly
        
        Returns:
            Summary dictionary
        """
        run_started_at = datetime.now(timezone.utc)
        logger.info(f"Starting Ginnie Mae sync (mode={mode})")
        
        results = {
            "mode": mode,
            "files_discovered": 0,
            "files_cataloged": 0,
            "files_downloaded": 0,
            "bytes_downloaded": 0,
            "errors": [],
        }
        
        try:
            # Start browser
            self._start_browser(headless=headless)
            
            # Navigate to bulk download page
            self._navigate_to_bulk_page()
            
            # Parse file table or generate historical URLs
            if not skip_catalog:
                if mode == "historical":
                    # Generate historical file URLs programmatically
                    logger.info("Generating historical file list (2013-present)...")
                    remote_files = self._generate_historical_file_list(
                        start_year=2013,
                        start_month=1,
                    )
                    results["files_discovered"] = len(remote_files)
                else:
                    # Parse from current page
                    logger.info("Parsing file list...")
                    remote_files = self._parse_file_table()
                    results["files_discovered"] = len(remote_files)
                    
                    # Determine which file types to process
                    target_types = file_types or self.MODE_FILE_TYPES.get(mode)
                    
                    if target_types:
                        remote_files = [f for f in remote_files if f["file_type"] in target_types]
                        logger.info(f"Filtered to {len(remote_files)} files of types: {target_types}")
                
                # Catalog new files
                cataloged = self.get_cataloged_files()
                new_files = [f for f in remote_files if f["filename"] not in cataloged]
                
                for f in new_files:
                    self.add_to_catalog(f)
                results["files_cataloged"] = len(new_files)
                logger.info(f"Cataloged {len(new_files)} new files")
            
            # Download files based on mode
            if mode == "catalog":
                logger.info("Catalog-only mode, skipping downloads")
            else:
                # Get files to download
                cataloged = self.get_cataloged_files()
                target_types = file_types or self.MODE_FILE_TYPES.get(mode)
                
                to_download = [
                    {"filename": filename, **info}
                    for filename, info in cataloged.items()
                    if info["status"] in ("pending", "error")
                ]
                
                # Filter by type
                if target_types:
                    to_download = [
                        f for f in to_download 
                        if self._classify_file(f["filename"]) in target_types
                    ]
                
                # Limit
                if max_files:
                    to_download = to_download[:max_files]
                
                logger.info(f"Downloading {len(to_download)} files...")
                
                for file_info in to_download:
                    try:
                        file_info["file_size_bytes"] = file_info.get("file_size_bytes", 0)
                        
                        # Check if file has a direct URL (historical files)
                        # or needs to be found on the page (current files)
                        if "href" in file_info and file_info["href"]:
                            # Historical file with direct URL
                            gcs_path = self._download_file_direct(
                                file_info["filename"],
                                file_info["href"]
                            )
                        else:
                            # Current file - find on page
                            gcs_path = self._download_file(file_info)
                        
                        self.update_catalog_status(
                            file_info["filename"],
                            "downloaded",
                            gcs_path=gcs_path,
                        )
                        results["files_downloaded"] += 1
                        results["bytes_downloaded"] += file_info.get("file_size_bytes", 0)
                        
                    except Exception as e:
                        error_msg = f"Error downloading {file_info['filename']}: {e}"
                        logger.error(error_msg)
                        results["errors"].append(error_msg)
                        self.update_catalog_status(
                            file_info["filename"],
                            "error",
                            error_message=str(e)[:500],
                        )
                        self._take_screenshot(f"download_error_{file_info['filename']}")
            
            # Log successful run
            self.log_ingest_run(
                status="success" if not results["errors"] else "partial",
                run_mode=mode,
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
            
        except AuthenticationRequiredError as e:
            error_msg = str(e)
            logger.error(f"Authentication required: {error_msg}")
            results["errors"].append(error_msg)
            results["auth_required"] = True
            
            self.log_ingest_run(
                status="auth_required",
                run_mode=mode,
                error_message=error_msg[:500],
                run_started_at=run_started_at,
            )
            
            # Alert already sent in _navigate_to_bulk_page
            
        except Exception as e:
            error_msg = str(e)
            logger.error(f"Sync failed: {error_msg}")
            results["errors"].append(error_msg)
            
            screenshot_path = self._take_screenshot("sync_failed")
            
            # Send alert for unexpected errors
            self._send_alert_email(
                f"Ginnie Mae Sync Failed: {type(e).__name__}",
                f"""The Ginnie Mae bulk download job failed with an unexpected error.

Error: {error_msg}

Mode: {mode}
Files discovered: {results.get('files_discovered', 0)}
Files downloaded: {results.get('files_downloaded', 0)}

Debug screenshot: gs://{self.gcs_config.raw_bucket}/{screenshot_path if screenshot_path else 'N/A'}

Stack trace in Cloud Logging.

Job: ginnie-ingestor
Time: {datetime.now(timezone.utc).isoformat()}
"""
            )
            
            self.log_ingest_run(
                status="error",
                run_mode=mode,
                error_message=error_msg[:500],
                run_started_at=run_started_at,
            )
        
        finally:
            self._stop_browser()
        
        return results
    
    def export_cookies_interactive(self) -> None:
        """
        Interactive session to export cookies.
        Run this locally to get initial session cookies.
        """
        print("\n=== Ginnie Mae Cookie Export ===")
        print("This will open a browser for you to log in manually.")
        print("After logging in, cookies will be saved to Secret Manager.\n")
        
        self._start_browser(headless=False)
        
        try:
            self._page.goto(self.BULK_URL)
            
            print("Please log in if required, then press Enter when ready...")
            input()
            
            # Save cookies
            cookies = self._context.cookies()
            self._save_cookies_to_secret(cookies)
            
            print(f"\nSaved {len(cookies)} cookies to Secret Manager")
            print("You can now run the ingestor in headless mode.")
            
        finally:
            self._stop_browser()
    
    def reset_catalog(self) -> None:
        """
        Reset all downloaded/error files to pending status.
        Use this when files need to be re-downloaded (e.g., after auth fix).
        """
        logger.info("Resetting catalog - marking all downloaded/error files as pending")
        
        with self.engine.connect() as conn:
            # Reset all downloaded/error files to pending
            result = conn.execute(text("""
                UPDATE ginnie_file_catalog 
                SET download_status = 'pending', 
                    local_gcs_path = NULL, 
                    downloaded_at = NULL,
                    error_message = NULL
                WHERE download_status IN ('downloaded', 'error')
                RETURNING filename
            """))
            reset_count = result.rowcount
            conn.commit()
            logger.info(f"Reset {reset_count} files to pending status")
            
            # Show current status
            result = conn.execute(text("""
                SELECT download_status, COUNT(*) as cnt
                FROM ginnie_file_catalog 
                GROUP BY download_status
            """))
            logger.info("Catalog status after reset:")
            for row in result:
                logger.info(f"  {row.download_status}: {row.cnt}")
    
    def clean_historical(self) -> None:
        """
        Remove ALL files from catalog - start fresh.
        
        This clears all generated historical files and lets the ingestor
        re-catalog from the actual bulk download page.
        """
        logger.info("Clearing ALL files from catalog...")
        
        with self.engine.connect() as conn:
            # Delete ALL catalog entries
            result = conn.execute(text("""
                DELETE FROM ginnie_file_catalog 
                RETURNING filename
            """))
            deleted_count = result.rowcount
            conn.commit()
            logger.info(f"Deleted {deleted_count} files from catalog")
            
            # Confirm empty
            result = conn.execute(text("""
                SELECT COUNT(*) as cnt FROM ginnie_file_catalog
            """))
            remaining = result.fetchone()[0]
            logger.info(f"Remaining files in catalog: {remaining}")


class AuthenticationRequiredError(Exception):
    """Raised when login is required but no valid session exists."""
    pass


def main():
    """Entry point for Cloud Run job."""
    parser = argparse.ArgumentParser(description="Ginnie Mae Bulk Download Ingestor")
    parser.add_argument(
        "--mode",
        choices=["daily", "monthly", "factor", "backfill", "catalog", "historical"],
        default="daily",
        help="Run mode (historical = download all files from 2013 to present)"
    )
    parser.add_argument(
        "--file-types",
        nargs="+",
        help="Override file types to download"
    )
    parser.add_argument(
        "--max-files",
        type=int,
        help="Maximum number of files to download"
    )
    parser.add_argument(
        "--no-headless",
        action="store_true",
        help="Run with visible browser (for debugging)"
    )
    parser.add_argument(
        "--skip-catalog",
        action="store_true",
        help="Skip cataloging, just download pending files"
    )
    parser.add_argument(
        "--export-cookies",
        action="store_true",
        help="Interactive mode to export session cookies"
    )
    parser.add_argument(
        "--reset-catalog",
        action="store_true",
        help="Reset all downloaded/error files to pending status"
    )
    parser.add_argument(
        "--clean-historical",
        action="store_true",
        help="Remove historical files from catalog (keep only current month)"
    )
    
    args = parser.parse_args()
    
    ingestor = GinnieIngestor()
    
    if args.clean_historical:
        ingestor.clean_historical()
        return
    
    if args.reset_catalog:
        ingestor.reset_catalog()
        return
    
    if args.export_cookies:
        ingestor.export_cookies_interactive()
        return
    
    results = ingestor.run(
        mode=args.mode,
        file_types=args.file_types,
        max_files=args.max_files,
        headless=not args.no_headless,
        skip_catalog=args.skip_catalog,
    )
    
    if results["errors"]:
        # Check if auth required
        if any("auth" in e.lower() for e in results["errors"]):
            logger.error("Authentication required - run with --export-cookies to refresh session")
            exit(2)
        
        logger.warning(f"Completed with {len(results['errors'])} errors")
        exit(1)
    
    logger.info("Ginnie Mae sync completed successfully")


if __name__ == "__main__":
    main()
