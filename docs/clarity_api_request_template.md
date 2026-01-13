# Clarity API Access Request Template

Use this template to request programmatic API access to Freddie Mac's Clarity Data Intelligence platform for the SFLLD (Single-Family Loan-Level Dataset).

---

**To:** clarity@freddiemac.com

**Subject:** Request for Programmatic API Access to SFLLD Historical Data

---

Dear Clarity Data Intelligence Team,

I am writing to request programmatic API access to the Single-Family Loan-Level Dataset (SFLLD) through the CRT Disclosure Download API.

**Company/Organization:** [Your Company Name]  
**Clarity Account Email:** [Your registered email]  
**Use Case:** We are building an analytics platform for MBS research and require access to historical loan-level data (1999-2025) for prepayment modeling and risk analysis across economic cycles.

**Data Needs:**
- Standard Dataset (historical_data_YYYY.zip for years 1999-2025)
- Monthly performance data for prepay/default analysis
- Approximately 27 annual files (compressed)

**Technical Requirements:**
- Programmatic download capability for bulk data ingestion
- Integration into our cloud-based data pipeline (GCP)
- Ability to download updates as new data becomes available

We understand that the dataset is subject to Freddie Mac's licensing terms and will ensure compliance with all usage restrictions.

Please let us know:
1. Is API access available for our use case?
2. What are the requirements/process for obtaining API credentials?
3. Are there any additional licensing requirements for programmatic access?

Thank you for your assistance. We appreciate Freddie Mac's commitment to data transparency in the mortgage market.

Best regards,  
[Your Name]  
[Your Contact Information]

---

## Alternative Approaches (if API not available)

1. **Manual Download:** Download each year's file manually from Clarity
   - 27 files for Standard Dataset (1999-2025)
   - ~10 minutes of clicking

2. **Third-Party Data Vendors:** Licensed distributors mentioned on the SFLLD page:
   - 1010data
   - CoreLogic
   - dv01
   - ICE
   - MIAC
   - Milliman
   - Recursion

3. **Sample Files:** Each year has a sample_YYYY.zip with 50K loans for testing
