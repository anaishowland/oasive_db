# Prepay Research Framework

## Philosophy

Every prepay assumption in Oasive must be:
1. **Empirically validated** - backed by actual pool performance data
2. **Well-documented** - clear methodology and confidence levels
3. **Queryable** - users can ask "why do VA pools prepay slower?"
4. **Discoverable** - system surfaces unexpected patterns automatically

---

## Part 1: Assumption Registry

Every assumption we make about prepay behavior is logged here with its validation status.

### Single-Factor Assumptions

| ID | Assumption | Expected Effect | Status | Confidence | Data Source |
|----|------------|-----------------|--------|------------|-------------|
| A001 | VA loans prepay slower than conventional | 25-35% slower | `pending_validation` | High (market consensus) | FRE_ILLD |
| A002 | USDA loans prepay slower than conventional | 30-40% slower | `pending_validation` | Medium | FRE_ILLD |
| A003 | FHA loans prepay moderately slower | 10-20% slower | `pending_validation` | Medium | FRE_ILLD |
| A004 | Low loan balance (<$85k) prepay slower | 25-35% slower | `pending_validation` | High | FRE_ILLD |
| A005 | Low loan balance ($85-110k) prepay slower | 15-25% slower | `pending_validation` | High | FRE_ILLD |
| A006 | Jumbo conforming prepay faster | 10-20% faster | `pending_validation` | Medium | FRE_ILLD |
| A007 | High LTV (>90%) prepay slower | 10-20% slower | `pending_validation` | High | FRE_ILLD |
| A008 | Low FICO (<680) prepay slower | 15-25% slower | `pending_validation` | High | FRE_ILLD |
| A009 | High FICO (>780) prepay faster | 5-15% faster | `pending_validation` | Medium | FRE_ILLD |
| A010 | Investor properties prepay slower | 15-25% slower | `pending_validation` | High | FRE_ILLD |
| A011 | Second homes prepay slower | 5-15% slower | `pending_validation` | Medium | FRE_ILLD |
| A012 | NY/NJ (judicial foreclosure) prepay slower | 10-20% slower | `pending_validation` | High | FRE_ILLD |
| A013 | CA/TX/FL prepay faster | 5-15% faster | `pending_validation` | Medium | FRE_ILLD |
| A014 | Manual servicers (banks) prepay slower | 10-20% slower | `pending_validation` | High | FRE_ISS |
| A015 | Automated servicers (Rocket, UWM) prepay faster | 10-20% faster | `pending_validation` | High | FRE_ISS |
| A016 | Seasoned pools (WALA >36mo) prepay slower | Burnout effect | `pending_validation` | High | FRE_DPR_Fctr |
| A017 | New production (WALA <6mo) prepay slower | Lockout effect | `pending_validation` | High | FRE_DPR_Fctr |
| A018 | Higher coupon vs rate = faster prepay | Refi incentive | `pending_validation` | High | FRE_DPR_Fctr + FRED |
| A019 | Cash-out refi purposes prepay differently | TBD | `pending_validation` | Low | FRE_ILLD |
| A020 | Purchase loans prepay slower than refis | 5-15% slower | `pending_validation` | Medium | FRE_ILLD |

### Multi-Factor Interaction Hypotheses

These are combinations that may have non-linear effects:

| ID | Hypothesis | Factors | Expected Interaction | Status |
|----|------------|---------|----------------------|--------|
| I001 | VA + NY = ultra slow | loan_program × state | Additive or multiplicative? | `to_test` |
| I002 | LLB + Low FICO = compounding friction | balance × fico | May be multiplicative | `to_test` |
| I003 | High LTV + new vintage = locked in | ltv × wala | Near-zero prepays | `to_test` |
| I004 | Investor + high coupon = fast prepay | occupancy × incentive | Override investor slowness? | `to_test` |
| I005 | CA + Rocket serviced = fastest | state × servicer | Compound speed effect | `to_test` |
| I006 | NY + Bank serviced = slowest | state × servicer | Compound protection | `to_test` |
| I007 | VA + Low balance = spec pool gold | program × balance | Best of both? | `to_test` |
| I008 | High DTI + Low FICO = credit friction | dti × fico | Unable to refinance | `to_test` |
| I009 | Burnout + in-the-money = stable | wala × incentive | Exhausted refinancers | `to_test` |
| I010 | New prod + out-of-money = extension | wala × incentive | Rate volatility exposure | `to_test` |

---

## Part 2: Empirical Validation Protocol

### Step 1: Universe Definition

```python
# Control sample requirements
MIN_POOLS_PER_SEGMENT = 100
MIN_MONTHS_HISTORY = 12
LOOKBACK_PERIOD = "24 months"  # Rolling window

# Exclude outliers
EXCLUDE_FACTOR_ABOVE = 1.05  # Likely data error
EXCLUDE_FACTOR_BELOW = 0.50  # Likely liquidated
```

### Step 2: CPR Calculation

```python
def calculate_monthly_cpr(factor_t, factor_t_minus_1):
    """
    CPR = 1 - (1 - SMM)^12
    SMM = 1 - (factor_t / factor_t_minus_1)
    """
    if factor_t_minus_1 <= 0 or factor_t <= 0:
        return None
    smm = 1 - (factor_t / factor_t_minus_1)
    if smm < 0 or smm > 0.20:  # Sanity check
        return None
    cpr = 1 - (1 - smm) ** 12
    return cpr * 100  # As percentage
```

### Step 3: Control for Confounders

When testing one factor, we must control for others:

```python
CONFOUNDERS = {
    'loan_program': ['incentive', 'wala', 'fico', 'ltv', 'balance'],
    'loan_balance': ['incentive', 'wala', 'fico', 'ltv', 'program'],
    'fico': ['incentive', 'wala', 'ltv', 'balance', 'program'],
    'ltv': ['incentive', 'wala', 'fico', 'balance', 'program'],
    'state': ['incentive', 'wala', 'fico', 'ltv', 'balance'],
    'servicer': ['incentive', 'wala', 'fico', 'ltv', 'balance'],
    'occupancy': ['incentive', 'wala', 'fico', 'ltv', 'balance'],
}

def control_methodology(factor_under_test):
    """
    Option 1: Stratified Analysis
    - Bucket pools by confounder values
    - Calculate CPR within each bucket
    - Average across buckets (weighted by pool count)
    
    Option 2: Regression
    - CPR ~ factor + confounder1 + confounder2 + ...
    - Extract factor coefficient
    
    Option 3: Matched Pairs
    - For each VA pool, find similar CONV pool
    - Match on confounders
    - Compare CPR directly
    """
    pass
```

### Step 4: Statistical Significance

```python
def assess_significance(segment_cpr, baseline_cpr, n_pools, n_months):
    """
    Require:
    - p-value < 0.05 for difference
    - Effect size > 5% relative difference
    - n_pools >= 100
    - n_months >= 1200 (100 pools × 12 months)
    """
    from scipy import stats
    
    # T-test or bootstrap confidence interval
    # ...
    
    return {
        'significant': p_value < 0.05,
        'effect_size': (baseline_cpr - segment_cpr) / baseline_cpr,
        'confidence_interval': (lower, upper),
        'sample_size': n_pools,
    }
```

### Step 5: Document Results

```python
@dataclass
class ValidationResult:
    assumption_id: str
    validated: bool
    observed_effect: float  # e.g., 0.72 = 28% slower
    expected_effect: float  # From assumption registry
    difference: float  # observed - expected
    confidence_interval: Tuple[float, float]
    p_value: float
    sample_size: int
    methodology: str
    run_date: datetime
    notes: str
```

---

## Part 3: Discovery Engine

Beyond validating assumptions, we want to discover unexpected patterns.

### Automated Anomaly Detection

```python
def discover_anomalies(all_segment_results: Dict[str, float]):
    """
    Find segments that deviate significantly from expectations.
    """
    anomalies = []
    
    for segment, observed_cpr in all_segment_results.items():
        expected = get_expected_cpr(segment)  # Based on assumptions
        
        # Flag if observed differs by >20% from expected
        if abs(observed_cpr - expected) / expected > 0.20:
            anomalies.append({
                'segment': segment,
                'observed': observed_cpr,
                'expected': expected,
                'deviation': (observed_cpr - expected) / expected,
                'direction': 'slower' if observed_cpr < expected else 'faster',
            })
    
    return anomalies
```

### Interaction Discovery

```python
def discover_interactions():
    """
    Test all pairwise factor combinations for non-additive effects.
    
    If VA is 30% slower and NY is 15% slower:
    - Additive: VA+NY = 45% slower
    - Multiplicative: VA+NY = 40% slower (0.7 × 0.85 = 0.595)
    - Super-additive: VA+NY = 55% slower (synergy)
    - Sub-additive: VA+NY = 35% slower (overlap)
    
    Flag super-additive and sub-additive as discoveries.
    """
    factors = ['loan_program', 'balance_tier', 'fico_bucket', 'ltv_bucket', 
               'state', 'servicer', 'occupancy']
    
    discoveries = []
    
    for f1, f2 in itertools.combinations(factors, 2):
        for v1 in factor_values[f1]:
            for v2 in factor_values[f2]:
                # Get individual effects
                effect_f1 = single_factor_effect[f1][v1]
                effect_f2 = single_factor_effect[f2][v2]
                
                # Get combined effect
                combined = get_cpr_for_combo(f1, v1, f2, v2)
                
                # Expected if multiplicative
                expected_mult = effect_f1 * effect_f2
                
                # Check for interaction
                interaction = combined / expected_mult
                
                if abs(1 - interaction) > 0.10:  # >10% deviation
                    discoveries.append({
                        'factors': (f1, f2),
                        'values': (v1, v2),
                        'combined_effect': combined,
                        'expected_multiplicative': expected_mult,
                        'interaction_type': 'synergy' if interaction < 0.9 else 'overlap',
                        'interaction_strength': interaction,
                    })
    
    return discoveries
```

### Time-Varying Effects

```python
def detect_regime_changes():
    """
    Check if factor effects change over time.
    
    Examples:
    - Servicer effect stronger after 2020 (more digital refis)
    - State friction reduced after COVID forbearance
    - VA effect stable across all rate environments
    """
    for factor in factors:
        for time_window in ['2018-2019', '2020-2021', '2022-2023', '2024-2025']:
            effect = calculate_factor_effect(factor, time_window)
            # Compare to baseline period
            # Flag significant changes
    pass
```

---

## Part 4: Queryable Research Interface

Users should be able to explore this research through natural language.

### Example Queries and Responses

**Query:** "Why do VA pools prepay slower?"

**Response:**
```
VA Loan Prepay Analysis (Assumption A001)
=========================================

Hypothesis: VA loans prepay 25-35% slower than conventional
Status: VALIDATED ✓

Empirical Results:
- VA pool avg CPR: 8.2%
- Conventional avg CPR: 11.4%
- Observed effect: 28% slower (multiplier: 0.72)
- Confidence interval: 25-31% slower
- Sample size: 2,847 pools, 34,164 pool-months
- Statistical significance: p < 0.001

Why This Happens:
1. VA streamline refi requires staying with VA program
2. VA funding fee adds friction to refinancing
3. VA borrowers tend to have longer tenure intentions
4. Lower average income limits refinance options

Interaction Effects:
- VA + NY: 42% slower (stronger than additive)
- VA + Low Balance: 48% slower (compounding)
- VA + Rocket Serviced: Only 18% slower (servicer partially offsets)

Data Source: FRE_ILLD, validated 2025-01-09
Methodology: Stratified analysis controlling for incentive, WALA, FICO
```

**Query:** "Compare servicer prepay speeds"

**Response:**
```
Servicer Prepay Comparison
==========================

Ranking (Slowest to Fastest):
                                          
| Servicer         | CPR   | vs Avg | Confidence |
|------------------|-------|--------|------------|
| Wells Fargo      | 7.1%  | -32%   | High       |
| JPMorgan Chase   | 7.8%  | -25%   | High       |
| US Bank          | 8.2%  | -21%   | High       |
| Mr. Cooper       | 9.4%  | -10%   | High       |
| Pennymac         | 10.2% | -2%    | Medium     |
| Nationstar       | 10.8% | +4%    | High       |
| Freedom Mortgage | 11.9% | +14%   | High       |
| Rocket/Quicken   | 13.1% | +26%   | High       |
| United Wholesale | 14.2% | +36%   | High       |

Key Finding: Bank servicers are 25-35% slower than digital-first servicers.
This effect has INCREASED since 2020 (was 15-20% in 2018-2019).

Data Source: FRE_ISS + FRE_DPR_Fctr, validated 2025-01-09
```

**Query:** "What unexpected patterns have you found?"

**Response:**
```
Discovered Anomalies (Not In Original Assumptions)
===================================================

1. SUPER-ADDITIVE INTERACTION
   Investor + High Coupon (>1% in-the-money)
   - Expected: 15% slower (investor) + faster (incentive) = ~0%
   - Observed: 12% FASTER than baseline
   - Insight: Investor properties refi aggressively for cash flow

2. UNEXPECTED PROTECTION
   New Production + Low FICO
   - Expected: Similar to average (offsetting factors)
   - Observed: 35% slower
   - Insight: Credit-impaired borrowers locked out of refi market

3. STATE SURPRISE
   Texas prepay speeds declined 40% since 2023
   - Previous: Matched CA as "fast" state
   - Current: Now middle of pack
   - Possible cause: Insurance costs, rate environment

4. BURNOUT REVERSAL
   Highly seasoned pools (WALA >60mo) showed prepay increase in 2024
   - Expected: Continued burnout
   - Observed: 15% uptick vs 2023
   - Possible cause: Cash-out refi surge for home equity extraction
```

---

## Part 5: Research Database Schema

```sql
-- Store all validation results
CREATE TABLE research_validation_results (
    id SERIAL PRIMARY KEY,
    assumption_id TEXT NOT NULL,
    run_date DATE NOT NULL,
    
    -- Results
    validated BOOLEAN,
    observed_effect NUMERIC(5,3),  -- e.g., 0.72 = 28% slower
    confidence_interval_low NUMERIC(5,3),
    confidence_interval_high NUMERIC(5,3),
    p_value NUMERIC(8,6),
    
    -- Sample info
    sample_pools INTEGER,
    sample_months INTEGER,
    time_period_start DATE,
    time_period_end DATE,
    
    -- Methodology
    methodology TEXT,  -- 'stratified', 'regression', 'matched_pairs'
    confounders_controlled TEXT[],
    
    -- Metadata
    notes TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Store discovered anomalies
CREATE TABLE research_discoveries (
    id SERIAL PRIMARY KEY,
    discovery_date DATE NOT NULL,
    discovery_type TEXT,  -- 'interaction', 'anomaly', 'regime_change'
    
    -- Details
    factors TEXT[],
    factor_values TEXT[],
    description TEXT,
    
    -- Effect
    observed_effect NUMERIC(5,3),
    expected_effect NUMERIC(5,3),
    deviation NUMERIC(5,3),
    
    -- Significance
    sample_size INTEGER,
    confidence TEXT,  -- 'high', 'medium', 'low'
    
    -- Action
    incorporated_into_model BOOLEAN DEFAULT FALSE,
    notes TEXT,
    
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Store factor weights over time (for audit trail)
CREATE TABLE research_weight_history (
    id SERIAL PRIMARY KEY,
    effective_date DATE NOT NULL,
    factor TEXT NOT NULL,
    factor_value TEXT NOT NULL,
    weight INTEGER NOT NULL,
    
    -- Derivation
    based_on_validation_id INTEGER REFERENCES research_validation_results(id),
    methodology TEXT,
    
    -- Supersedes
    previous_weight INTEGER,
    change_reason TEXT,
    
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Indexes for query performance
CREATE INDEX idx_validation_assumption ON research_validation_results(assumption_id);
CREATE INDEX idx_validation_date ON research_validation_results(run_date);
CREATE INDEX idx_discoveries_type ON research_discoveries(discovery_type);
CREATE INDEX idx_weight_factor ON research_weight_history(factor, factor_value);
```

---

## Part 6: Research Roadmap

### Phase 1: Single-Factor Validation (Q1 2025)
Once historical data is loaded:
- [ ] Validate all A001-A020 assumptions
- [ ] Document results in `research_validation_results`
- [ ] Update composite score weights with empirical values
- [ ] Create user-facing research summaries

### Phase 2: Interaction Analysis (Q2 2025)
- [ ] Test all I001-I010 hypotheses
- [ ] Run discovery engine for unexpected interactions
- [ ] Incorporate significant interactions into scoring
- [ ] Build interaction lookup table for queries

### Phase 3: Time-Series Analysis (Q3 2025)
- [ ] Detect regime changes in factor effects
- [ ] Build rolling factor effect dashboard
- [ ] Alert on significant effect changes
- [ ] Seasonal adjustment if needed

### Phase 4: Continuous Learning (Ongoing)
- [ ] Monthly re-validation of key assumptions
- [ ] Quarterly discovery runs
- [ ] Annual comprehensive re-calibration
- [ ] Incorporate new factors as data becomes available

---

## Part 7: Query API Design

```python
class PrepayResearchAPI:
    """
    Natural language interface to prepay research.
    """
    
    def explain_factor(self, factor: str, value: str) -> str:
        """
        "Why do VA pools prepay slower?"
        Returns formatted research summary.
        """
        pass
    
    def compare_segments(self, factor: str) -> str:
        """
        "Compare servicer prepay speeds"
        Returns ranked comparison table.
        """
        pass
    
    def get_pool_factors(self, pool_id: str) -> dict:
        """
        "What affects this pool's prepay risk?"
        Returns all factor values and their effects for a pool.
        """
        pass
    
    def find_similar_pools(self, pool_id: str, criteria: list) -> list:
        """
        "Find pools similar to X but with different servicer"
        Natural comparison shopping.
        """
        pass
    
    def get_discoveries(self, since: date = None) -> list:
        """
        "What unexpected patterns have you found?"
        Returns anomalies and discoveries.
        """
        pass
    
    def get_assumption_status(self) -> pd.DataFrame:
        """
        "How validated is your model?"
        Returns assumption registry with validation status.
        """
        pass
```

---

*This framework ensures every prepay assumption is transparent, validated, and explorable.*
