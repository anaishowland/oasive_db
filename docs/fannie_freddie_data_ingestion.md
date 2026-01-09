**Data Details**

**Fannie Feeds**

* Helpful resource, column data definitions: [https://capitalmarkets.fanniemae.com/sites/g/files/koqyhd216/files/2025-02/mbsglossary.pdf](https://capitalmarkets.fanniemae.com/sites/g/files/koqyhd216/files/2025-02/mbsglossary.pdf)  
* Use SFTP feed to pull data. Request SFTP credentials from PoolTalk/Capital Markets support and point job at the SFTP directory structure noted in the guide.

**File names & time of data release:**

**Fannie Mae (FNM):**
* **Intraday loan-level issuance**: FNM\_ILLD\_YYYYMMDD\_{1..4} at \~6:30, 10:30, 13:30, 15:30 ET.  
* **Intraday security issuance**: FNM\_IS\_YYYYMMDD\_{1..4} at the same cadence.  
* **Month-end issuance**: FNM\_ILLD\_YYYYMM and FNM\_IS\_YYYYMM on BD1 6:30am ET.  
* **Monthly**: FNM\_MLLD\_YYYYMM and FNM\_MF\_YYYYMM on BD4 4:30pm ET.  
* **Corrections**: FNM\_RIS\_YYYYMM and FNM\_RISS\_YYYYMM on BD1–BD4 6:30am ET when present.

**Freddie Mac (FRE):**
* **Intraday security issuance**: FRE\_FISS\_YYYYMMDD.zip (assumed similar cadence to Fannie ~6:30, 10:30, 13:30, 15:30 ET - **VERIFY with CSS support**)
* **Monthly security issuance**: FRE\_IS\_YYYYMM.zip (on BD1)

⚠️ **TODO**: Contact CSS support (Investor_Inquiry@freddiemac.com) to confirm exact Freddie Mac intraday release times.

**GCP-first, minimal devops**

* **Storage**: GCS buckets with versioned raw zips in gcs://securi/raw/fannie/{type}/{YYYY}/{MM}/...zip  
* **Compute**: Cloud Run jobs for ingestion and parsing, triggered by Cloud Scheduler  
* **Orchestration**: Cloud Scheduler \+ Pub/Sub fan-out, or Prefect if you want retries and state UI  
* **Warehouse**: Postgres in Cloud SQL for the app, BigQuery for heavy joins, or DuckDB+Parquet for the very first pass  
* **API**: FastAPI on Cloud Run  
* **UI**: NextJS \+ Tailwind, serverless on Cloud Run

**Data quality & change management**

* Track intraday vs month-end vs monthly file provenance and a loan\_correction\_ind if present.   
* Keep raw, staged, and canonical layers.   
* Build a small dashboard for file arrivals, row deltas, and schema drifts

**Data schema & Metrics**

* **dim\_pool** (pool\_id, cusip, prefix, product, coupon, wam\_iss, wala\_iss, issue\_dt, issuer, servicer\_id, arm\_flag, ... )  
* **dim\_loan** (loan\_id, pool\_id, first\_pay\_dt, note\_rate, orig\_upb, fico, ltv, dti, state, msa, purpose, occ, prop\_type, loan\_term, ... )  
* **fact\_pool\_month** (pool\_id, as\_of\_month, loan\_count, factor, upb, paydown\_prin, delinq\_30\_60\_90, invol\_removals, ... )  
* **fact\_loan\_month** (loan\_id, pool\_id, as\_of\_month, curr\_upb, status, dlq\_status, mod\_flag, forbear\_flag, ... )  
* **dim\_calendar** (as\_of\_month, bd1\_dt, bd4\_dt, holiday\_flag, ...)

Note: loan-level monthly files exclude paid-off loans for subsequent months, while factor files reflect decreased loan count and factors when loans pay off. Your snapshot model should respect that.

Query examples

* rank pools by incentive and seasoning

—----------------


