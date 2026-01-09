# AI Tagging Design for MBS Knowledge Base

## Overview

Oasive uses AI-generated tags to classify pools by behavioral characteristics that matter to traders. These tags power the "semantic translation" layer that converts natural language queries into database filters.

**Key Principle**: Tags capture *trader intuition* about pool behavior that isn't obvious from raw numbers. E.g., a pool with factor=0.85, WALA=36, and positive refi incentive isn't just "numbers" — it's a **burnout candidate**.

## Tag Categories

### 1. Static Tags (Stored in Postgres, Recalibrated Monthly)

These tags are stored directly in `dim_pool` and updated after monthly factor files are processed.

#### `risk_profile` (TEXT)
Overall risk classification based on credit, prepay, and extension risk.

| Value | Description | Rule Logic |
|-------|-------------|------------|
| `conservative` | Low credit risk, predictable prepays, high FICO, low LTV | avg_fico >= 760 AND avg_ltv <= 70 AND serious_dlq_rate < 0.5% |
| `moderate` | Balanced risk profile, typical collateral | Default when not conservative or aggressive |
| `aggressive` | Higher credit risk or prepay uncertainty | avg_fico < 680 OR avg_ltv > 90 OR serious_dlq_rate > 2% |
| `high_extension` | Low prepay, risk of extension | wala > 48 AND factor > 0.9 AND refi_incentive < 0 |

#### `burnout_score` (NUMERIC 0-100)
Likelihood the pool has exhausted its prepay-prone borrowers.

**Formula:**
```sql
burnout_score = LEAST(100, 
    (wala / 60.0 * 30) +                      -- Seasoning factor (max 30 pts)
    ((1 - factor) * 50) +                      -- Paydown factor (max 50 pts)  
    CASE WHEN refi_incentive > 50 THEN 20 ELSE 0 END  -- In-the-money bonus
)
```

| Score | Interpretation |
|-------|---------------|
| 0-30 | Fresh pool, not burned out |
| 30-60 | Moderately seasoned |
| 60-80 | Significant burnout |
| 80-100 | Heavily burned out |

#### `geo_concentration_tag` (TEXT)
Geographic risk concentration.

| Value | Description | Rule Logic |
|-------|-------------|------------|
| `CA_heavy` | California concentration | top_state = 'CA' AND top_state_pct >= 30% |
| `TX_heavy` | Texas concentration | top_state = 'TX' AND top_state_pct >= 30% |
| `FL_heavy` | Florida concentration | top_state = 'FL' AND top_state_pct >= 30% |
| `NY_heavy` | New York concentration | top_state = 'NY' AND top_state_pct >= 25% |
| `coastal` | Concentrated in coastal states | top_state IN ('CA','FL','NY','WA','OR','MA') AND top_state_pct >= 25% |
| `sunbelt` | Sunbelt states | top_state IN ('TX','FL','AZ','NV','GA') AND top_state_pct >= 25% |
| `diversified` | No single state dominates | top_state_pct < 20% |
| `midwest` | Midwest concentration | top_state IN ('OH','MI','IL','IN','WI') AND top_state_pct >= 25% |

#### `state_prepay_friction` (TEXT) - NEW
States have different legal/regulatory environments that affect prepayment speed. Some states require attorney involvement, have higher closing costs, or judicial processes that slow refinancing.

**Slow Prepay States** (HIGH friction = good for investors):
- **New York (NY)**: Attorney-required closings, high taxes, complex title insurance, slow refi process
- **New Jersey (NJ)**: Attorney state, high closing costs
- **Florida (FL)**: Judicial foreclosure state, complex title
- **Illinois (IL)**: Attorney state, judicial foreclosure
- **Connecticut (CT)**: Attorney state, high costs
- **Massachusetts (MA)**: Attorney state, complex title  
- **Pennsylvania (PA)**: Judicial state
- **Ohio (OH)**: Judicial state

**Fast Prepay States** (LOW friction = worse for investors):
- **California (CA)**: Non-judicial, streamlined refi process
- **Texas (TX)**: Non-judicial, fast closings
- **Arizona (AZ)**: Non-judicial, fast process
- **Colorado (CO)**: Non-judicial, efficient
- **Washington (WA)**: Non-judicial
- **Georgia (GA)**: Non-judicial, fast

| Value | Description | States | Impact |
|-------|-------------|--------|--------|
| `high_friction` | Slow prepay states, investor-friendly | NY, NJ, FL, IL, CT, MA, PA, OH | Prepays 15-25% slower |
| `moderate_friction` | Average prepay speed | Most other states | Baseline |
| `low_friction` | Fast prepay states, borrower-friendly | CA, TX, AZ, CO, WA, GA | Prepays 10-20% faster |

**Rule Logic:**
```python
HIGH_FRICTION_STATES = {'NY', 'NJ', 'FL', 'IL', 'CT', 'MA', 'PA', 'OH'}
LOW_FRICTION_STATES = {'CA', 'TX', 'AZ', 'CO', 'WA', 'GA', 'NV', 'OR'}

# Calculate weighted friction based on state concentration
def calc_state_friction(loan_states: dict) -> str:
    high_pct = sum(loan_states.get(s, 0) for s in HIGH_FRICTION_STATES)
    low_pct = sum(loan_states.get(s, 0) for s in LOW_FRICTION_STATES)
    
    if high_pct >= 40:
        return 'high_friction'
    elif low_pct >= 40:
        return 'low_friction'
    else:
        return 'moderate_friction'
```

#### `servicer_prepay_risk` (TEXT) - RENAMED & CORRECTED
**From an INVESTOR perspective**, servicer quality is about prepayment protection, NOT borrower experience.

⚠️ **Key Insight**: A "good" servicer for investors is one where borrowers have a HARDER time refinancing (slower, more bureaucratic). A "bad" servicer is one that makes refinancing easy and fast.

**Prepay-Friendly Servicers** (FAST prepays = BAD for investors):
- **Rocket/Quicken**: Highly automated, frictionless refi, aggressive marketing
- **Better.com**: Digital-first, fast closings
- **LoanDepot**: Aggressive refi solicitation
- **United Wholesale Mortgage**: Efficient broker channel
- **PennyMac** (retail originations): Strong refi machine

**Prepay-Protected Servicers** (SLOW prepays = GOOD for investors):
- **Large bank servicers**: Wells Fargo, Chase, BofA - bureaucratic, slow
- **Specialty servicers**: Ocwen, Carrington - focus on loss mitigation, not refi
- **Small/regional servicers**: Manual processes, limited capacity
- **Subservicers**: Additional layer of friction

| Value | Description | Servicers | Impact |
|-------|-------------|-----------|--------|
| `prepay_protected` | Slow servicers (good for investors) | Wells Fargo, Chase, BofA, Ocwen, small regionals | 10-20% slower prepays |
| `neutral` | Average prepay speed | Mid-tier servicers | Baseline |
| `prepay_exposed` | Fast servicers (bad for investors) | Rocket, Better, LoanDepot, UWM | 15-30% faster prepays |

**Rule Logic:**
```python
FAST_SERVICERS = {
    'rocket', 'quicken', 'better', 'loandepot', 'uwm', 
    'united wholesale', 'pennymac retail', 'freedom mortgage'
}
SLOW_SERVICERS = {
    'wells fargo', 'chase', 'jpmorgan', 'bank of america', 'bofa',
    'ocwen', 'carrington', 'specialized loan servicing', 'cenlar'
}

def classify_servicer_prepay_risk(servicer_name: str) -> str:
    name_lower = servicer_name.lower()
    if any(s in name_lower for s in FAST_SERVICERS):
        return 'prepay_exposed'
    elif any(s in name_lower for s in SLOW_SERVICERS):
        return 'prepay_protected'
    else:
        return 'neutral'
```

### 2. Behavioral Tags (JSONB in `behavior_tags` column)

These are predictive tags that capture expected behavior under different market scenarios.

#### `burnout_candidate`
Pool likely to slow down prepays despite in-the-money.

```json
{
  "burnout_candidate": true,
  "confidence": 0.85,
  "factors": ["wala_36_plus", "factor_below_85", "positive_incentive"]
}
```

**Rule**: `burnout_score >= 60 AND refi_incentive > 25bps`

#### `bear_market_stable`
Pool expected to perform well (slow prepays) in rising rate environment.

```json
{
  "bear_market_stable": true,
  "confidence": 0.78,
  "factors": ["low_incentive", "seasoned", "high_fico"]
}
```

**Rules**:
- `refi_incentive < -50bps` (out of the money)
- `avg_fico >= 720` (won't need to refi for distress)
- `avg_ltv <= 80` (equity cushion)

#### `bull_market_rocket`
Pool at high risk of fast prepays if rates drop.

```json
{
  "bull_market_rocket": true,
  "confidence": 0.72,
  "factors": ["high_fico", "low_burnout", "large_loans"]
}
```

**Rules**:
- `burnout_score < 30`
- `avg_fico >= 760`
- `avg_loan_size >= 350000` (jumbo/high balance)
- `wala <= 12`

#### `extension_risk`
Pool at risk of extending longer than expected.

```json
{
  "extension_risk": true,
  "confidence": 0.81,
  "factors": ["discount", "seasoned", "locked_in"]
}
```

**Rules**:
- `refi_incentive < -100bps` (deeply out of the money)
- `wala >= 24`
- `factor > 0.85` (hasn't paid down much)

#### `low_balance_prepay`
Low loan balance pools that prepay slower.

```json
{
  "low_balance_prepay": true,
  "confidence": 0.88,
  "factors": ["loan_size_below_85k"]
}
```

**Rule**: `avg_loan_size < 85000`

#### `prepay_protected`
Pool has structural characteristics that slow prepayments (investor-friendly).

```json
{
  "prepay_protected": true,
  "confidence": 0.82,
  "factors": ["slow_servicer", "high_friction_state", "low_balance"]
}
```

**Rules** (any combination):
- `servicer_prepay_risk = 'prepay_protected'`
- `state_prepay_friction = 'high_friction'`
- `avg_loan_size < 100000`
- `burnout_score >= 60`

#### `prepay_exposed`
Pool at high risk of fast prepayments (investor-unfriendly).

```json
{
  "prepay_exposed": true,
  "confidence": 0.79,
  "factors": ["fast_servicer", "low_friction_state", "high_fico", "fresh"]
}
```

**Rules**:
- `servicer_prepay_risk = 'prepay_exposed'` AND
- `state_prepay_friction IN ('low_friction', 'moderate_friction')` AND
- `burnout_score < 40` AND
- `avg_fico >= 720`

---

## Implementation

### Tag Generation Pipeline

```
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│  Monthly Factor │────▶│  Tag Generator  │────▶│   dim_pool      │
│  Files Loaded   │     │  (Python)       │     │   UPDATE        │
└─────────────────┘     └─────────────────┘     └─────────────────┘
                               │
                               │ Needs:
                               │ - Pool static attrs
                               │ - Latest factor/month data
                               │ - Servicer lookup table
                               │ - Current rate for incentive calc
                               ▼
                        ┌─────────────────┐
                        │  FRED Data      │
                        │  (MORTGAGE30US) │
                        └─────────────────┘
```

### Refi Incentive Calculation

```python
def calc_refi_incentive(pool_wac: float, current_rate: float) -> float:
    """
    Calculate refi incentive in basis points.
    Positive = in-the-money (borrower wants to refi)
    Negative = out-of-the-money (borrower locked in)
    """
    return (pool_wac - current_rate) * 100  # bps
```

### Tag Generator Code Structure

```python
# src/tagging/pool_tagger.py

class PoolTagger:
    def __init__(self, db_engine, fred_rate_series='MORTGAGE30US'):
        self.engine = db_engine
        self.current_rate = self._get_current_mortgage_rate(fred_rate_series)
    
    def generate_tags(self, pool: PoolData) -> dict:
        """Generate all AI tags for a pool."""
        tags = {
            'risk_profile': self._classify_risk_profile(pool),
            'burnout_score': self._calc_burnout_score(pool),
            'geo_concentration_tag': self._classify_geo(pool),
            'state_prepay_friction': self._classify_state_friction(pool),
            'servicer_prepay_risk': self._classify_servicer_prepay_risk(pool),
            'behavior_tags': self._generate_behavior_tags(pool)
        }
        return tags
    
    def _classify_state_friction(self, pool: PoolData) -> str:
        """Classify prepay friction based on state concentration."""
        HIGH_FRICTION = {'NY', 'NJ', 'FL', 'IL', 'CT', 'MA', 'PA', 'OH'}
        LOW_FRICTION = {'CA', 'TX', 'AZ', 'CO', 'WA', 'GA', 'NV', 'OR'}
        
        # Use top_state as proxy (would need full state breakdown for precision)
        if pool.top_state in HIGH_FRICTION and pool.top_state_pct >= 30:
            return 'high_friction'
        elif pool.top_state in LOW_FRICTION and pool.top_state_pct >= 30:
            return 'low_friction'
        return 'moderate_friction'
    
    def _classify_servicer_prepay_risk(self, pool: PoolData) -> str:
        """Classify servicer by prepayment speed (investor perspective)."""
        name = pool.servicer_name.lower() if pool.servicer_name else ''
        
        # Fast servicers = BAD for investors (easy refi)
        FAST = ['rocket', 'quicken', 'better', 'loandepot', 'uwm', 'freedom']
        if any(s in name for s in FAST):
            return 'prepay_exposed'
        
        # Slow servicers = GOOD for investors (bureaucratic)
        SLOW = ['wells fargo', 'chase', 'jpmorgan', 'bofa', 'ocwen', 'carrington']
        if any(s in name for s in SLOW):
            return 'prepay_protected'
        
        return 'neutral'
    
    def _calc_burnout_score(self, pool: PoolData) -> float:
        wala_factor = min(30, pool.wala / 60.0 * 30)
        paydown_factor = min(50, (1 - pool.factor) * 50)
        incentive_bonus = 20 if self._calc_refi_incentive(pool) > 50 else 0
        return min(100, wala_factor + paydown_factor + incentive_bonus)
    
    def _generate_behavior_tags(self, pool: PoolData) -> dict:
        tags = {}
        
        # Burnout candidate
        if self._calc_burnout_score(pool) >= 60:
            incentive = self._calc_refi_incentive(pool)
            if incentive > 25:
                tags['burnout_candidate'] = {
                    'value': True,
                    'confidence': min(0.95, 0.6 + incentive/100),
                    'factors': self._get_burnout_factors(pool)
                }
        
        # Bear market stable
        incentive = self._calc_refi_incentive(pool)
        if incentive < -50 and pool.avg_fico >= 720 and pool.avg_ltv <= 80:
            tags['bear_market_stable'] = {
                'value': True,
                'confidence': 0.75 + (abs(incentive) / 200) * 0.2,
                'factors': ['low_incentive', 'high_fico', 'equity_cushion']
            }
        
        # Bull market rocket
        if (self._calc_burnout_score(pool) < 30 and 
            pool.avg_fico >= 760 and 
            pool.avg_loan_size >= 350000 and
            pool.wala <= 12):
            tags['bull_market_rocket'] = {
                'value': True,
                'confidence': 0.72,
                'factors': ['fresh', 'high_fico', 'large_loans']
            }
        
        return tags
```

---

## Query Translation Examples

### "Show me high burnout pools"
```sql
SELECT * FROM pool_summary 
WHERE burnout_score >= 70
ORDER BY burnout_score DESC;
```

### "Find pools stable in bear markets"
```sql
SELECT * FROM pool_summary
WHERE behavior_tags->>'bear_market_stable' = 'true'
ORDER BY (behavior_tags->'bear_market_stable'->>'confidence')::float DESC;
```

### "Build me a monitor for NY-based, high-FICO, low-loan-balance pools"
```sql
SELECT * FROM pool_summary
WHERE geo_concentration_tag = 'NY_heavy'
  AND avg_fico >= 750
  AND avg_loan_size < 150000;
```

### "Find pools with extension risk"
```sql
SELECT * FROM pool_summary
WHERE behavior_tags->>'extension_risk' = 'true';
```

---

## Semantic Translation Layer

The semantic layer translates trader jargon into SQL filters:

| Trader Query | Semantic Interpretation |
|--------------|------------------------|
| "high burnout" | `burnout_score >= 70` |
| "low burnout" | `burnout_score <= 30` |
| "seasoned" | `wala >= 24` |
| "fresh" | `wala <= 12` |
| "in the money" | `wac - current_rate > 50bps` |
| "out of the money" | `wac - current_rate < -50bps` |
| "high FICO" | `avg_fico >= 750` |
| "low LTV" | `avg_ltv <= 70` |
| "jumbo" | `is_jumbo = true OR avg_loan_size >= 726200` |
| "low balance" | `is_low_balance = true OR avg_loan_size < 85000` |
| "CA heavy" | `geo_concentration_tag = 'CA_heavy'` |
| "diversified" | `geo_concentration_tag = 'diversified'` |
| "short duration" | Extension risk pools |
| "long duration" | Low factor, high burnout |
| **"slow prepay states"** | `state_prepay_friction = 'high_friction'` |
| **"NY pools"** | `top_state = 'NY'` (slow prepay, high friction) |
| **"fast prepay states"** | `state_prepay_friction = 'low_friction'` |
| **"prepay protected"** | `behavior_tags->>'prepay_protected' = 'true'` |
| **"prepay exposed"** | `behavior_tags->>'prepay_exposed' = 'true'` |
| **"slow servicer"** | `servicer_prepay_risk = 'prepay_protected'` |
| **"fast servicer"** | `servicer_prepay_risk = 'prepay_exposed'` |
| **"Rocket pools"** | `servicer_name ILIKE '%rocket%' OR servicer_name ILIKE '%quicken%'` (fast!) |
| **"bank servicer"** | `servicer_name ILIKE '%wells%' OR servicer_name ILIKE '%chase%'` (slow) |

---

## Phase 1 Implementation Plan

1. **Create servicer lookup table** with prepay risk classification
   - Map servicer names to `prepay_protected`, `neutral`, `prepay_exposed`
   - Include historical prepay speed multipliers if available
   
2. **Create state friction lookup table**
   - Map states to `high_friction`, `moderate_friction`, `low_friction`
   - Include friction score (0-100) for weighted calculations
   
3. **Add new columns to dim_pool** (migration 005):
   ```sql
   ALTER TABLE dim_pool ADD COLUMN state_prepay_friction TEXT;
   ALTER TABLE dim_pool ADD COLUMN servicer_prepay_risk TEXT;
   ```
   
4. **Implement `PoolTagger` class** with all tag rules
5. **Add tagging step** to monthly factor file processing
6. **Build semantic translation agent** for query parsing
7. **Create pool screener API** that accepts natural language

---

## Future Enhancements (Phase 2+)

- **ML-based burnout prediction** using historical prepay data
- **Servicer quality scoring** from actual loss/modification data
- **Dynamic behavior tags** that update based on rate moves
- **Vector embeddings** for semantic pool similarity search
- **Knowledge graph** linking pools to servicers, originators, and MSAs

---

## Dynamic Servicer Prepay Scoring (Data-Driven)

### Concept

Instead of static servicer classifications, we calculate **actual prepay speed metrics** from historical pool performance, filtering for single-servicer pools to isolate the servicer effect.

**Key Benefits:**
- Captures servicers that change behavior (e.g., automate workflows → faster prepays)
- Uses rolling windows to detect recent trends vs. historical baseline
- Produces quantitative scores, not just categories
- Updates automatically with each factor file

### Database Schema (Migration 005)

```sql
-- Table: servicer_prepay_metrics
-- Monthly prepay metrics aggregated by servicer
CREATE TABLE IF NOT EXISTS servicer_prepay_metrics (
    servicer_id TEXT NOT NULL,
    servicer_name TEXT NOT NULL,
    as_of_month DATE NOT NULL,
    
    -- Pool universe (single-servicer pools only)
    pool_count INTEGER,
    total_upb NUMERIC(18,2),
    
    -- Prepay metrics (weighted by UPB)
    avg_cpr NUMERIC(6,3),
    median_cpr NUMERIC(6,3),
    
    -- Benchmarking
    universe_avg_cpr NUMERIC(6,3),         -- Market CPR this month
    cpr_vs_universe NUMERIC(6,3),          -- Spread to market (bps)
    cpr_ratio NUMERIC(6,4),                -- Servicer / Universe
    
    -- Controls (apples-to-apples comparison)
    avg_wac NUMERIC(5,3),
    avg_wala INTEGER,
    avg_fico INTEGER,
    avg_incentive NUMERIC(6,2),            -- Avg refi incentive (bps)
    
    PRIMARY KEY (servicer_id, as_of_month)
);

-- Table: servicer_prepay_scores
-- Rolling scores updated with each factor file
CREATE TABLE IF NOT EXISTS servicer_prepay_scores (
    servicer_id TEXT PRIMARY KEY,
    servicer_name TEXT NOT NULL,
    
    -- Scores (0-100: higher = faster prepays = worse for investors)
    prepay_speed_score NUMERIC(5,2),
    prepay_speed_percentile INTEGER,
    
    -- Rolling windows
    cpr_3mo_avg NUMERIC(6,3),
    cpr_6mo_avg NUMERIC(6,3),
    cpr_12mo_avg NUMERIC(6,3),
    
    -- Trend detection (key for catching behavior changes!)
    trend_3mo_vs_12mo NUMERIC(6,3),        -- (3mo - 12mo) / 12mo
    trend_direction TEXT,                   -- 'accelerating', 'stable', 'decelerating'
    
    -- Derived classification
    prepay_risk_category TEXT,             -- 'prepay_protected', 'neutral', 'prepay_exposed'
    
    last_updated TIMESTAMPTZ,
    months_of_data INTEGER
);

-- Table: servicer_prepay_alerts
-- Alerts when servicer behavior changes significantly
CREATE TABLE IF NOT EXISTS servicer_prepay_alerts (
    id SERIAL PRIMARY KEY,
    servicer_id TEXT NOT NULL,
    alert_date DATE NOT NULL,
    alert_type TEXT,                       -- 'speed_increase', 'speed_decrease', 'category_change'
    old_score NUMERIC(5,2),
    new_score NUMERIC(5,2),
    old_category TEXT,
    new_category TEXT,
    description TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
```

### Scoring Methodology

**Step 1: Filter to Single-Servicer Pools**
```python
# Only use pools where servicer hasn't changed to isolate servicer effect
pools = get_pools_where(
    servicer_id IS NOT NULL,
    wala >= 3,  # Exclude brand new pools
)
```

**Step 2: Calculate Incentive-Adjusted CPR**
```python
# A servicer with high CPR might just have in-the-money pools
# Adjust for refi incentive for apples-to-apples comparison
df['incentive_bucket'] = bucket_by_incentive(df['refi_incentive'])
df['cpr_vs_bucket'] = df['cpr'] - bucket_avg_cpr  # Residual after incentive
```

**Step 3: Aggregate by Servicer (UPB-weighted)**
```python
servicer_metrics = df.groupby('servicer_id').agg({
    'cpr': weighted_mean(weights='curr_upb'),
    'pool_count': count,
    'universe_cpr': universe_avg,  # Benchmark
})
servicer_metrics['cpr_vs_universe'] = servicer_cpr - universe_cpr
```

**Step 4: Calculate Rolling Scores & Detect Trends**
```python
cpr_3mo = last_3_months_avg(servicer_id)
cpr_12mo = last_12_months_avg(servicer_id)

# Trend: positive = speeding up (⚠️ for investors)
trend = (cpr_3mo - cpr_12mo) / cpr_12mo * 100

if trend > 15:
    trend_direction = 'accelerating'  # ⚠️ Getting faster!
elif trend < -15:
    trend_direction = 'decelerating'  # Slowing down (good for investors)
else:
    trend_direction = 'stable'

# Score: 50 = average, higher = faster prepays = worse for investors
score = 50 + (avg_spread_to_universe * 10)
```

**Step 5: Generate Alerts on Significant Changes**
```python
if abs(new_score - old_score) >= 10:
    create_alert('speed_change', f"Score changed by {change:.1f} points")
    
if new_category != old_category:
    create_alert('category_change', f"Moved from {old} to {new}")
```

### Example: Catching a Servicer Automation

**Scenario**: Mr. Cooper implements new automated refi system in Q3 2025

| Month | cpr_3mo | cpr_12mo | trend | score | category |
|-------|---------|----------|-------|-------|----------|
| Jun 2025 | 8.2 | 8.5 | -3.5% | 42 | neutral |
| Jul 2025 | 9.1 | 8.4 | +8.3% | 48 | neutral |
| Aug 2025 | 11.2 | 8.6 | +30.2% | 58 | neutral |
| Sep 2025 | 13.5 | 9.1 | +48.4% | **67** | **prepay_exposed** ⚠️ |

**Alert Generated:**
> ⚠️ **Mr. Cooper**: Prepay speed increased by 25 points over 3 months.
> Category changed from 'neutral' to 'prepay_exposed'.
> Possible cause: System automation or policy change.

### Integration with Factor File Processing

Every time new factor files arrive:
1. Update `fact_pool_month` with new CPR data
2. Recalculate `servicer_prepay_metrics` for the month
3. Recalculate `servicer_prepay_scores` with rolling windows
4. Check for alerts and notify if thresholds exceeded
5. Update `dim_pool.servicer_prepay_risk` with latest category
