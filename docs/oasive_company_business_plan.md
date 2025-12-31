### **Vision & Executive Summary**

**Oasive** is an AI copilot for fixed-income portfolio managers and traders that accelerates security selection with explainable analytics. It functions as a "Search Engine for Collateral," unlocking the "tacit knowledge" typically locked in the heads of expert traders.

**Taglines:**

* “Trader’s copilot for securitized markets”  
* “Explainable collateral intelligence for faster, better trades”  
* “Building the domain intelligence for the securitized market”

**Value Proposition:** Oasive does not try to reinvent the math (Yieldbook/Intex). It acts as the **top-of-funnel filter**. It saves traders hours of time by ensuring they only run the heavy math on the top 1% of pools that fit their mandate.

### **Problem Statement**

Security selection and collateral due diligence in securitized products (MBS, ABS, CMBS) and other fixed income products (Munis or Corporate Bonds) is slow, manual, and locked in experts’ heads. PMs and traders waste hours scanning bonds, collateral and reviewing messy disclosures bonds, skim pool tapes, and eventually rely on tacit rules of thumb. **Multi-factor collateral attributes make mispricing or value hard to spot quickly at scale.** The result is slower rebalancing, inconsistent decisions, and missed relative-value opportunities.

### **Our Solution**

We are building a domain-intelligent copilot that ingests loan- and pool-level data, understands collateral structure, and surfaces investable ideas with clear, auditable reasoning.

Users describe mandates, views, and constraints in natural language (i.e. for MBS "Build me a monitor for NY-based, high-FICO, low-loan-balance pools". The system screens interesting pools, explains the drivers that matter, and filters for bonds that fit the portfolio. The output is not a static chat response but a live, dynamic data grid that allows for deep analysis.

Our moat will be our proprietary ontology and the generated metadata (LLM generated tags) that no other provider has.

### **Product Scope**

**Core Features**

* **Screener and story engine:** A search interface (the “Screener”) where users filter the market using natural language and preset investment goals. The backend will have a “story” engine that automatically generates behavioral profiles for each bond.  
* **Live monitors**: once a screen is built, it persists as a live dashboard updating with market movements

*Consideration: To avoid being considered as a financial advisor (too much regulatory scrutiny), we will frame this as a recommendation of specific CUSIPs/Pools that match the user's "View," functioning as a search engine rather than a financial advisor.*

**User Experience:** There will be a hybrid and dynamic interface that uses chat to build the view but the view itself is a live data grid. Everything will need to ensure seamless export (Excel add-in, Excel export, CSV, API) to meet portfolio managers where they already are.

### **Technical Implementation & Architecture**

The backend will use a **hybrid architecture** that separates intrinsic traits (static) from market states (dynamic) to make sure the intelligence is time-aware and scalable.

**3 DB layer:**

1. **Postgres (structured DB):** stores core pool data \+ AI-generated static tags (i.e. risk profile, servicer score, etc.)  
2. **Vector index (semantic DB):** stores behavioral embeddings of pools for pattern matching  
3. **Mini knowledge graph (relational DB):** stores rigid entity relationships (corporate hierarchy, legal, etc.)

**Mutli-agent router system**  
The router activates specific agents based in the user’s query type:

* **Semantic translation agent:** logic engine that translates trader speak into database queries helped by a proprietary predictive tagging logic   
* **Graph retrieval agent:** answers relationship-based queries  
* **Pattern matching agent:** uses vector search on pool attributes and tags to derive state and relationship

**Data workflow**

* **Ingestion (daily or cyclical):** process new bonds and trigger collateral analysis to assign  
  * intrinsic traits or properties of bond.collateral which change slowly like aggressive servicer, geo concentration. Those will be stored as static tags in Postgres and recalibrated monthly once factor files are posted  
  * Predictive behavior tagging to anticipate different market scenarios (i.e. bear market stability, burnout candidate, bull market refi, etc.)  
* Live market feed: system updates market rates and macro data in real-time (i.e. via time series, Redis)  
* Live query execution: derived state and interaction between the traits (“behavior”) and the market data computed on each query. For efficiency, the system will use vector filtering (based on the predictive traits).

### **Data Strategy**

Core feeds to ingest (for MBS):

* agency pool disclosures from FNMA, FHLMC, and GNMA  
* secondary TRACE prints where available  
* vendor and public loan-level attributes  
* macro and rates time series; house price indice and other economic indicators 

Additional data considerations: expensive commercial licenses needed to access different feeds. We will not have a prepayment model in-house at the start, instead we will allow users to connect to Intex, Yieldbook, Bloomberg or Polypaths to access pricing and prepayment modeling to get their OAS.

### **Target Market & Asset Classes**

* Target market: Buy-side portfolio managers, traders, and research analysts across MBS, ABS, CMBS, and munis.   
* Asset classes covered: Start with MBS where prepayment and collateral heterogeneity drive most of the edge. Expand to ABS and CMBS, then munis. Add international later.

### **Roadmap**

* Phase 1 \[now\]: the search engine (explainable collateral) including natural language screening, custom tagging and live dashboard for MBS only then expand to other asset classes (ABS, CMBS, Munis)  
* Phase 2 \[medium term\]: introduce Oasive quantitative model for OAS, training ML model on historicals to provide cheap/fast pricing analysis  
* Phase 3 \[long term\]: introduce a valuation engine with full cashflow modeling, creating a proprietary engine to compete/displace Yieldbook, Intex and other providers  
* Phase 4 \[aspirational\]: B2C asset explanation and filtering for retail investors

**Phase 1 next steps:**

1. Create multiple databases that ingest the loan level and pool disclosures data, the economic indicators, the trace prints, and all the different core feeds  
2. Create the vector embedding and AI-tagging logic as well as the mini knowledge graph of the MBS space linking relationships between the different indicators (like correlations between MBS, rates, economic indicators, refi ramps, etc.)  
3. Create multi-agent architecture  
4. Built the UI for the platform (start simple and move into dynamic modelling)  
5. Expand to other asset classes: CMBS, ABS, Munis

Long-term roadmap:

* Step 1 \[now\]: The Search Engine. You own the "Selection" workflow.  
* Step 2 \[Medium\]: The Proxy Model. You introduce "Oasive Estimated Prepay" alongside the collateral data. You train this on historicals using ML (which is cheaper/faster than traditional OAS engines). Traders start using it as a "sanity check."  
* Step 3 \[Long\]: The Valuation Engine. Once traders trust your selection and your proxies, you introduce full cashflow modeling (perhaps partnering with an open-source library wrapper or building your own engine) to become the "All-in-One" desk.  
* Step 4: B2C assets explanation for retail investors & filtering/recommendations

### **Business model**

SaaS subscription per desk with seat licenses and rate limits per month with additional rate limits available on purchase for a premium.

### **Considerations**

* Regulatory risk: must decouple user portfolio and screener and not ingest a portfolio and output a trade list otherwise we become a financial advisor. Instead we provide attributes/a screener  
* Cost management: expensive commercial licenses are needed for data redistribution (GSEs, Bloomberg API, Yieldbook, etc.). For now, we have the user bring their own licenses for OAS/pricing and we focus on collateral attributes  
* Hallucinations: strictly constrain LLM output to schemas and require citation for every claim

### **Example Queries**

Example query: "Show me high burnout pools"  
Semantic layer: translates "burnout" → "look for pools where factor \> 0.8 AND WALA \> 24 AND Refi Incentive \> 50bps"  
Semantic layer needs to have a predictive behavior tagging as well to deepen moat and make sure the tagging can dynamically update to different market scenarios (i.e. bull or bear market scenario like bear\_market\_stable, bull\_market\_rocket, burnout\_candidate)

Query examples:

* Find pools with short effective duration, low refi risk  
* Build me a monitor for NY-based, high-FICO, low-loan-balance pools  
* Show me high burnout pools

