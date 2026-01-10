# AI Tagging Design for MBS Knowledge Base

## Overview

Oasive uses AI-generated tags to classify pools by behavioral characteristics that matter to traders. These tags power the "semantic translation" layer that converts natural language queries into database filters.

**Key Principle**: Tags capture *trader intuition* about pool behavior that isn't obvious from raw numbers. E.g., a pool with factor=0.85, WALA=36, and positive refi incentive isn't just "numbers" — it's a **burnout candidate**.

---

## Tag Architecture

### Four-Layer Framework

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                        LAYER 1: STATIC ATTRIBUTES                           │
│  (Stored in dim_pool, updated monthly with factor files)                    │
│  ─────────────────────────────────────────────────────────────────────────  │
│  • loan_balance_tier      • loan_program        • fico_bucket               │
│  • ltv_bucket             • occupancy_type      • loan_purpose              │
│  • state_prepay_friction  • servicer_prepay_risk • geo_concentration_tag    │
│  • has_rate_buydown       • property_type       • origination_channel       │
│  • risk_profile                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                      LAYER 2: DERIVED METRICS                               │
│  (Calculated from static + FRED data, updated daily/weekly)                 │
│  ─────────────────────────────────────────────────────────────────────────  │
│  • refi_incentive_bps     • burnout_score       • seasoning_stage           │
│  • premium_cpr_mult       • discount_cpr_mult   • convexity_score           │
│  • s_curve_position       • extension_risk_score• contraction_risk_score    │
└─────────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                    LAYER 3: BEHAVIORAL TAGS (JSONB)                         │
│  (Composite tags stored in behavior_tags column)                            │
│  ─────────────────────────────────────────────────────────────────────────  │
│  • prepay_protected       • prepay_exposed      • bull_market_rocket        │
│  • bear_market_stable     • burnout_candidate   • extension_risk            │
│  • positive_convexity     • negative_convexity  • all_weather               │
└─────────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                     LAYER 4: COMPOSITE SCORES                               │
│  (Single numeric scores for screening/ranking)                              │
│  ─────────────────────────────────────────────────────────────────────────  │
│  • composite_prepay_score (0-100)                                           │
│  • bull_scenario_score    • bear_scenario_score • neutral_scenario_score    │
│  • payup_efficiency_score • overall_attractiveness                          │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Section 1: Static Attribute Tags

### 1.1 Loan Balance Tier (`loan_balance_tier`)

**Critical Tag** - Market uses well-defined tiers. Protection level is inversely proportional to loan size because:
1. **Fixed costs** (appraisal, title, origination) are higher as % of loan
2. **Absolute savings** from rate reduction are smaller
3. **Borrower demographics** - starter homes, higher mobility

| Tier | Max Balance | Market Name | Premium Mult | Discount Mult | Baseline | Typical Payup |
|------|-------------|-------------|--------------|---------------|----------|---------------|
| `LLB1` | $85,000 | Super LLB | 0.65x | 1.20x | 0.68x | 24/32nds |
| `LLB2` | $110,000 | LLB 110 | 0.70x | 1.15x | 0.73x | 18/32nds |
| `LLB3` | $125,000 | LLB 125 | 0.75x | 1.12x | 0.78x | 14/32nds |
| `LLB4` | $150,000 | LLB 150 | 0.80x | 1.10x | 0.83x | 10/32nds |
| `LLB5` | $175,000 | LLB 175 | 0.84x | 1.08x | 0.86x | 8/32nds |
| `LLB6` | $200,000 | LLB 200 | 0.87x | 1.05x | 0.88x | 6/32nds |
| `LLB7` | $225,000 | LLB 225 | 0.90x | 1.03x | 0.91x | 4/32nds |
| `MLB` | $300,000 | Medium Balance | 0.95x | 1.00x | 0.95x | 2/32nds |
| `STD` | Conforming | Standard | 1.00x | 1.00x | 1.00x | 0 (baseline) |
| `JUMBO` | >Conforming | Jumbo/High Bal | 1.10x | 0.80x | 1.05x | -4/32nds |

**Rule Logic:**
```python
def classify_loan_balance_tier(avg_loan_size: float, conforming_limit: float = 766550) -> str:
    """Classify pool by loan balance tier."""
    if avg_loan_size <= 85000:
        return 'LLB1'
    elif avg_loan_size <= 110000:
        return 'LLB2'
    elif avg_loan_size <= 125000:
        return 'LLB3'
    elif avg_loan_size <= 150000:
        return 'LLB4'
    elif avg_loan_size <= 175000:
        return 'LLB5'
    elif avg_loan_size <= 200000:
        return 'LLB6'
    elif avg_loan_size <= 225000:
        return 'LLB7'
    elif avg_loan_size <= 300000:
        return 'MLB'
    elif avg_loan_size <= conforming_limit:
        return 'STD'
    else:
        return 'JUMBO'
```

**Convexity Note:** LLB pools exhibit **positive convexity** (discount mult > 1.0). They prepay FASTER when out-of-the-money due to housing mobility and credit improvement. This is investor-friendly in rising rate environments.

---

### 1.2 Loan Program (`loan_program`)

Government-backed loans have structural prepay protection due to program-specific frictions.

| Value | Description | Premium Mult | Discount Mult | Mechanism |
|-------|-------------|--------------|---------------|-----------|
| `VA` | VA Loans | 0.70x | 1.00x | VA IRRRL friction, funding fee recapture |
| `FHA` | FHA Loans | 0.82x | 0.95x | MIP calculation, streamline barriers |
| `USDA` | USDA/RD Loans | 0.65x | 0.90x | Geographic restrictions, program rules |
| `CONV` | Conventional | 1.00x | 1.00x | Baseline (no program friction) |

**Rule Logic:**
```python
def classify_loan_program(loan_type_code: str) -> str:
    """Classify by loan program from FRE_ILLD loan_type field."""
    VA_CODES = {'VA', '05', 'V'}
    FHA_CODES = {'FHA', '03', 'F'}
    USDA_CODES = {'USDA', 'RD', '07', 'R'}
    
    code = str(loan_type_code).upper().strip()
    if code in VA_CODES:
        return 'VA'
    elif code in FHA_CODES:
        return 'FHA'
    elif code in USDA_CODES:
        return 'USDA'
    else:
        return 'CONV'
```

**VA IRRRL Note:** VA Interest Rate Reduction Refinance Loans have restrictions that slow prepays. VA pools are considered "prepay protected" in premium environments.

---

### 1.3 FICO Bucket (`fico_bucket`)

Credit score affects both prepay speed AND direction of effect based on rate environment.

| Bucket | FICO Range | Premium Mult | Discount Mult | Typical Behavior |
|--------|------------|--------------|---------------|------------------|
| `FICO_LOW` | <660 | 0.75x | 1.15x | Credit-constrained, slow to refi, faster turnover |
| `FICO_SUBPRIME` | 660-679 | 0.80x | 1.10x | Limited refi options |
| `FICO_FAIR` | 680-719 | 0.90x | 1.05x | Moderate constraints |
| `FICO_GOOD` | 720-759 | 1.00x | 1.00x | Baseline (typical pool) |
| `FICO_EXCELLENT` | 760-779 | 1.08x | 0.90x | Fast refinancers, locked in when rates rise |
| `FICO_SUPER` | ≥780 | 1.12x | 0.85x | Most responsive to rates, worst extension |

**Key Insight:** High FICO borrowers exhibit **negative convexity** - they refinance quickly when in-the-money but become locked in when out-of-the-money. Low FICO borrowers show the opposite pattern.

```python
def classify_fico_bucket(avg_fico: int) -> str:
    if avg_fico < 660:
        return 'FICO_LOW'
    elif avg_fico < 680:
        return 'FICO_SUBPRIME'
    elif avg_fico < 720:
        return 'FICO_FAIR'
    elif avg_fico < 760:
        return 'FICO_GOOD'
    elif avg_fico < 780:
        return 'FICO_EXCELLENT'
    else:
        return 'FICO_SUPER'
```

---

### 1.4 LTV Bucket (`ltv_bucket`)

Loan-to-value ratio affects prepay through equity availability and underwriting constraints.

| Bucket | LTV Range | Premium Mult | Discount Mult | Mechanism |
|--------|-----------|--------------|---------------|-----------|
| `LTV_LOW` | ≤60% | 1.05x | 0.95x | High equity = easy refi, less mobility pressure |
| `LTV_MOD` | 60-70% | 1.00x | 1.00x | Baseline |
| `LTV_STANDARD` | 70-80% | 0.98x | 1.02x | Typical conforming |
| `LTV_HIGH` | 80-90% | 0.90x | 1.05x | PMI constraints, tighter underwriting |
| `LTV_VERY_HIGH` | 90-95% | 0.85x | 1.08x | Limited refi options |
| `LTV_EXTREME` | >95% | 0.78x | 1.12x | Credit events needed to refinance |

```python
def classify_ltv_bucket(avg_ltv: float) -> str:
    if avg_ltv <= 60:
        return 'LTV_LOW'
    elif avg_ltv <= 70:
        return 'LTV_MOD'
    elif avg_ltv <= 80:
        return 'LTV_STANDARD'
    elif avg_ltv <= 90:
        return 'LTV_HIGH'
    elif avg_ltv <= 95:
        return 'LTV_VERY_HIGH'
    else:
        return 'LTV_EXTREME'
```

---

### 1.5 Occupancy Type (`occupancy_type`)

| Value | Description | Premium Mult | Discount Mult | Mechanism |
|-------|-------------|--------------|---------------|-----------|
| `OWNER` | Owner-occupied | 1.00x | 1.00x | Baseline |
| `INVESTOR` | Investment property | 0.78x | 0.92x | Tax considerations, less responsive to rates |
| `SECOND_HOME` | Second/vacation home | 0.88x | 0.95x | Lower rate sensitivity |

**Investor properties** are slower in both directions - investors are less responsive to rate changes and have different tax/financial motivations.

---

### 1.6 Loan Purpose (`loan_purpose`)

| Value | Description | Premium Mult | Discount Mult | Mechanism |
|-------|-------------|--------------|---------------|-----------|
| `PURCHASE` | Purchase money | 0.88x | 1.02x | Less likely to refi quickly, housing mobility |
| `RATE_TERM_REFI` | Rate/term refinance | 1.00x | 1.00x | Baseline - already refinanced once |
| `CASH_OUT_REFI` | Cash-out refinance | 1.05x | 1.15x | Extracted equity, credit-driven, faster turnover |

**Cash-out refi** pools have unusual behavior - they prepay quickly in ALL environments due to credit events and subsequent refinancing.

---

### 1.7 State Prepay Friction (`state_prepay_friction`)

States have different legal/regulatory environments affecting refi speed.

**High Friction States** (attorney closings, judicial process):
- **New York (NY)**: Attorney-required closings, high taxes, complex title insurance, slow refi process
- **New Jersey (NJ)**: Attorney state, high closing costs
- **Florida (FL)**: Judicial foreclosure state, complex title
- **Illinois (IL)**: Attorney state, judicial foreclosure
- **Connecticut (CT)**: Attorney state, high costs
- **Massachusetts (MA)**: Attorney state, complex title
- **Pennsylvania (PA)**: Judicial state
- **Ohio (OH)**: Judicial state

**Low Friction States** (non-judicial, streamlined):
- **California (CA)**: Non-judicial, streamlined refi process
- **Texas (TX)**: Non-judicial, fast closings
- **Arizona (AZ)**: Non-judicial, fast process
- **Colorado (CO)**: Non-judicial, efficient
- **Washington (WA)**: Non-judicial
- **Georgia (GA)**: Non-judicial, fast

| Value | Description | Premium Mult | Discount Mult | Example States |
|-------|-------------|--------------|---------------|----------------|
| `HIGH_FRICTION` | Slow prepay states | 0.85x | 0.95x | NY, NJ, FL, IL, CT, MA |
| `MODERATE_FRICTION` | Average | 1.00x | 1.00x | Most other states |
| `LOW_FRICTION` | Fast prepay states | 1.10x | 1.05x | CA, TX, AZ, CO, WA, GA |

```python
HIGH_FRICTION_STATES = {'NY', 'NJ', 'FL', 'IL', 'CT', 'MA', 'PA', 'OH'}
LOW_FRICTION_STATES = {'CA', 'TX', 'AZ', 'CO', 'WA', 'GA', 'NV', 'OR'}

def classify_state_friction(state_distribution: dict) -> str:
    """
    Classify based on UPB-weighted state distribution.
    state_distribution: dict of {state: pct_upb}
    """
    high_pct = sum(state_distribution.get(s, 0) for s in HIGH_FRICTION_STATES)
    low_pct = sum(state_distribution.get(s, 0) for s in LOW_FRICTION_STATES)
    
    if high_pct >= 40:
        return 'HIGH_FRICTION'
    elif low_pct >= 40:
        return 'LOW_FRICTION'
    else:
        return 'MODERATE_FRICTION'
```

---

### 1.8 Servicer Prepay Risk (`servicer_prepay_risk`)

**From an INVESTOR perspective**, servicer quality is about prepayment speed, NOT borrower experience.

⚠️ **Key Insight**: A "good" servicer for investors is one where borrowers have a HARDER time refinancing (slower, more bureaucratic). A "bad" servicer is one that makes refinancing easy and fast.

**Prepay-Friendly Servicers** (FAST prepays = BAD for investors):
- **Rocket/Quicken**: Highly automated, frictionless refi, aggressive marketing
- **Better.com**: Digital-first, fast closings
- **LoanDepot**: Aggressive refi solicitation
- **United Wholesale Mortgage**: Efficient broker channel
- **PennyMac** (retail originations): Strong refi machine
- **Freedom Mortgage**: Aggressive marketing

**Prepay-Protected Servicers** (SLOW prepays = GOOD for investors):
- **Large bank servicers**: Wells Fargo, Chase, BofA - bureaucratic, slow
- **Specialty servicers**: Ocwen, Carrington - focus on loss mitigation, not refi
- **Small/regional servicers**: Manual processes, limited capacity
- **Subservicers**: Additional layer of friction (Cenlar, etc.)

| Value | Description | Premium Mult | Discount Mult | Example Servicers |
|-------|-------------|--------------|---------------|-------------------|
| `PREPAY_PROTECTED` | Slow servicers | 0.85x | 0.90x | Wells Fargo, Chase, BofA, Ocwen |
| `NEUTRAL` | Average | 1.00x | 1.00x | Mid-tier servicers |
| `PREPAY_EXPOSED` | Fast servicers | 1.15x | 0.70x | Rocket, Better, LoanDepot, UWM |

**Convexity Note:** Digital servicers have EXTREME negative convexity (0.70x discount mult) - they lock borrowers in when rates rise.

```python
FAST_SERVICERS = {
    'rocket', 'quicken', 'better', 'loandepot', 'uwm', 
    'united wholesale', 'pennymac', 'freedom mortgage'
}
SLOW_SERVICERS = {
    'wells fargo', 'chase', 'jpmorgan', 'bank of america', 'bofa',
    'ocwen', 'carrington', 'specialized loan servicing', 'cenlar', 'us bank'
}

def classify_servicer_prepay_risk(servicer_name: str) -> str:
    name_lower = servicer_name.lower() if servicer_name else ''
    if any(s in name_lower for s in FAST_SERVICERS):
        return 'PREPAY_EXPOSED'
    elif any(s in name_lower for s in SLOW_SERVICERS):
        return 'PREPAY_PROTECTED'
    else:
        return 'NEUTRAL'
```

---

### 1.9 Geographic Concentration (`geo_concentration_tag`)

| Value | Description | Rule |
|-------|-------------|------|
| `CA_HEAVY` | California concentration | top_state = 'CA' AND top_state_pct >= 30% |
| `TX_HEAVY` | Texas concentration | top_state = 'TX' AND top_state_pct >= 30% |
| `FL_HEAVY` | Florida concentration | top_state = 'FL' AND top_state_pct >= 30% |
| `NY_HEAVY` | New York concentration | top_state = 'NY' AND top_state_pct >= 25% |
| `COASTAL` | Coastal states | top_state IN (CA,FL,NY,WA,OR,MA) AND >= 25% |
| `SUNBELT` | Sunbelt states | top_state IN (TX,FL,AZ,NV,GA) AND >= 25% |
| `MIDWEST` | Midwest concentration | top_state IN (OH,MI,IL,IN,WI) AND >= 25% |
| `DIVERSIFIED` | No concentration | top_state_pct < 20% |

---

### 1.10 Risk Profile (`risk_profile`)

Overall risk classification based on credit, prepay, and extension risk.

| Value | Description | Rule Logic |
|-------|-------------|------------|
| `CONSERVATIVE` | Low credit risk, predictable prepays | avg_fico >= 760 AND avg_ltv <= 70 AND serious_dlq_rate < 0.5% |
| `MODERATE` | Balanced risk profile, typical collateral | Default when not conservative or aggressive |
| `AGGRESSIVE` | Higher credit risk or prepay uncertainty | avg_fico < 680 OR avg_ltv > 90 OR serious_dlq_rate > 2% |
| `HIGH_EXTENSION` | Low prepay, risk of extension | wala > 48 AND factor > 0.9 AND refi_incentive < 0 |

---

### 1.11 Rate Buydown Flag (`has_rate_buydown`)

Temporary buydowns have become prevalent (2024-2026) and significantly affect prepayment behavior.

| Value | Description | Impact |
|-------|-------------|--------|
| `NO_BUYDOWN` | No rate buydown | Baseline |
| `BUYDOWN_2_1` | 2-1 buydown | First 2 years: lower prepays due to rate step-up |
| `BUYDOWN_3_2_1` | 3-2-1 buydown | First 3 years: lower prepays, then accelerate |
| `PERMANENT_BUYDOWN` | Permanent buydown | Higher prepays (lower effective rate) |

**Buydown pools** require special handling - they may show artificially low prepays during the buydown period, then accelerate.

---

### 1.12 Seasoning Stage (`seasoning_stage`)

| Value | WALA Range | Premium Mult | Discount Mult | Behavior |
|-------|------------|--------------|---------------|----------|
| `NEW_PRODUCTION` | 0-6 months | 0.72x | 0.85x | Ramp-up period, very slow |
| `RAMPING` | 6-12 months | 0.85x | 0.92x | Accelerating |
| `SEASONED` | 12-24 months | 0.95x | 0.98x | Near baseline |
| `FULLY_SEASONED` | 24-36 months | 1.00x | 1.00x | Baseline behavior |
| `WELL_SEASONED` | 36-60 months | 0.85x | 1.05x | Burnout beginning |
| `BURNED_OUT` | >60 months | 0.75x | 1.10x | Significant burnout |

```python
def classify_seasoning_stage(wala: int) -> str:
    if wala <= 6:
        return 'NEW_PRODUCTION'
    elif wala <= 12:
        return 'RAMPING'
    elif wala <= 24:
        return 'SEASONED'
    elif wala <= 36:
        return 'FULLY_SEASONED'
    elif wala <= 60:
        return 'WELL_SEASONED'
    else:
        return 'BURNED_OUT'
```

---

### 1.13 Property Type (`property_type`)

| Value | Description | Premium Mult | Discount Mult |
|-------|-------------|--------------|---------------|
| `SINGLE_FAMILY` | Single-family detached | 1.00x | 1.00x |
| `CONDO` | Condominium | 0.95x | 1.02x |
| `PUD` | Planned unit development | 0.98x | 1.01x |
| `MULTI_FAMILY` | 2-4 unit | 0.85x | 0.95x |
| `MANUFACTURED` | Manufactured housing | 0.75x | 1.10x |

---

### 1.14 Origination Channel (`origination_channel`)

| Value | Description | Premium Mult | Discount Mult |
|-------|-------------|--------------|---------------|
| `RETAIL` | Bank retail | 0.95x | 0.98x |
| `CORRESPONDENT` | Correspondent | 1.00x | 1.00x |
| `BROKER` | Broker | 1.05x | 1.02x |
| `DIRECT` | Direct/online | 1.10x | 0.95x |

---

## Section 2: Derived Metrics

These are calculated from static attributes combined with market data (FRED rates).

### 2.1 Refi Incentive (`refi_incentive_bps`)

```python
def calc_refi_incentive(pool_wac: float, current_30yr_rate: float) -> float:
    """
    Calculate refinancing incentive in basis points.
    Positive = in-the-money (borrower wants to refi)
    Negative = out-of-the-money (borrower locked in)
    """
    return (pool_wac - current_30yr_rate) * 100  # bps
```

| Range | Classification | Behavior |
|-------|----------------|----------|
| < -100 bps | `DEEP_OTM` | Locked in, extension risk |
| -100 to -50 bps | `OTM` | Moderately locked in |
| -50 to +50 bps | `ATM` | At-the-money, baseline |
| +50 to +100 bps | `ITM` | In-the-money, prepay risk |
| > +100 bps | `DEEP_ITM` | Highly in-the-money, fast prepays |

---

### 2.2 Burnout Score (`burnout_score`)

Likelihood the pool has exhausted its prepay-prone borrowers (0-100).

```python
def calc_burnout_score(wala: int, factor: float, refi_incentive_bps: float) -> float:
    """
    Calculate burnout score (0-100).
    Higher = more burned out = slower prepays in premium environments.
    """
    # Seasoning component (max 35 pts)
    seasoning_pts = min(35, wala / 60.0 * 35)
    
    # Paydown component (max 45 pts) - larger paydown = more burnout
    paydown_pts = min(45, (1 - factor) * 60)
    
    # In-the-money persistence bonus (max 20 pts)
    # Pool that stayed in-the-money without prepaying = burned out
    if refi_incentive_bps > 50:
        itm_bonus = min(20, (wala / 24) * 20) if wala > 12 else 0
    else:
        itm_bonus = 0
    
    return min(100, seasoning_pts + paydown_pts + itm_bonus)
```

| Score | Interpretation |
|-------|---------------|
| 0-25 | Fresh pool, not burned out |
| 25-50 | Moderately seasoned |
| 50-70 | Significant burnout |
| 70-85 | Heavily burned out |
| 85-100 | Extremely burned out |

---

### 2.3 S-Curve Position (`s_curve_position`)

Where on the prepayment S-curve this pool sits, based on incentive.

| Position | Incentive | Description |
|----------|-----------|-------------|
| `LEFT_TAIL` | < -100 bps | Flat/slow region - extension risk |
| `LEFT_SHOULDER` | -100 to -25 bps | Beginning of acceleration |
| `INFLECTION` | -25 to +75 bps | Steepest part of curve |
| `RIGHT_SHOULDER` | +75 to +150 bps | Decelerating |
| `RIGHT_TAIL` | > +150 bps | Saturation - max prepay speed |

---

### 2.4 Convexity Score (`convexity_score`)

**Key Metric** - Measures how the pool behaves across rate environments.

```python
def calc_convexity_score(premium_mult: float, discount_mult: float) -> float:
    """
    Convexity = Premium Multiplier / Discount Multiplier
    
    Lower is BETTER for investors:
    - < 0.6 = Positive convexity (best)
    - 0.6-0.8 = Good convexity
    - 0.8-1.0 = Neutral
    - 1.0-1.2 = Negative convexity
    - > 1.2 = High negative convexity (worst)
    """
    return premium_mult / max(discount_mult, 0.01)
```

| Score Range | Classification | Investor Implication |
|-------------|----------------|---------------------|
| < 0.55 | `POSITIVE_CONVEXITY_STRONG` | Excellent - protected both ways |
| 0.55-0.70 | `POSITIVE_CONVEXITY` | Good - slight asymmetry in favor |
| 0.70-0.85 | `NEUTRAL_CONVEXITY` | Balanced risk |
| 0.85-1.00 | `SLIGHT_NEGATIVE_CONVEXITY` | Mild negative |
| 1.00-1.25 | `NEGATIVE_CONVEXITY` | Bad - prepays fast, extends slow |
| > 1.25 | `HIGH_NEGATIVE_CONVEXITY` | Worst - avoid |

---

### 2.5 Contraction Risk Score (`contraction_risk_score`)

Risk of fast prepays if rates fall (0-100). Higher = MORE contraction risk.

```python
def calc_contraction_risk(
    premium_mult: float,
    burnout_score: float,
    servicer_risk: str,
    fico_bucket: str
) -> float:
    # Base from premium multiplier (inverted - higher mult = more risk)
    base_risk = (premium_mult - 0.5) / 0.7 * 50  # Scale 0.5-1.2 to 0-50
    
    # Burnout reduces risk
    burnout_reduction = (burnout_score / 100) * 25
    
    # Servicer adjustment
    servicer_adj = {'PREPAY_PROTECTED': -10, 'NEUTRAL': 0, 'PREPAY_EXPOSED': +15}
    
    # FICO adjustment (high FICO = more risk)
    fico_adj = {
        'FICO_LOW': -10, 'FICO_SUBPRIME': -5, 'FICO_FAIR': 0,
        'FICO_GOOD': +5, 'FICO_EXCELLENT': +10, 'FICO_SUPER': +15
    }
    
    risk = base_risk - burnout_reduction + servicer_adj.get(servicer_risk, 0) + fico_adj.get(fico_bucket, 0)
    return max(0, min(100, risk))
```

---

### 2.6 Extension Risk Score (`extension_risk_score`)

Risk of slow prepays (duration extension) if rates rise (0-100). Higher = MORE extension risk.

```python
def calc_extension_risk(
    discount_mult: float,
    refi_incentive_bps: float,
    wala: int,
    factor: float,
    loan_balance_tier: str
) -> float:
    # Base from discount multiplier (inverted - lower mult = more extension risk)
    base_risk = (1.2 - discount_mult) / 0.5 * 40  # Scale 0.7-1.2 to 0-40
    
    # Deep OTM increases risk
    otm_adj = 20 if refi_incentive_bps < -100 else (10 if refi_incentive_bps < -50 else 0)
    
    # High factor (not paid down) increases risk
    factor_adj = (factor - 0.5) * 20 if factor > 0.5 else 0
    
    # LLB tiers REDUCE extension risk (they turnover faster)
    llb_adj = {
        'LLB1': -15, 'LLB2': -12, 'LLB3': -10, 'LLB4': -8,
        'LLB5': -6, 'LLB6': -4, 'LLB7': -2, 'MLB': 0,
        'STD': 0, 'JUMBO': +10
    }
    
    risk = base_risk + otm_adj + factor_adj + llb_adj.get(loan_balance_tier, 0)
    return max(0, min(100, risk))
```

---

## Section 3: Behavioral Tags (JSONB)

Stored in `behavior_tags` column as JSONB for flexible querying.

### 3.1 `prepay_protected`

Pool has structural characteristics that slow prepayments (investor-friendly in premium environments).

```json
{
  "prepay_protected": {
    "value": true,
    "confidence": 0.85,
    "strength": "strong",
    "factors": ["LLB1", "VA", "bank_servicer", "NY_heavy"],
    "premium_mult": 0.48,
    "protection_score": 72
  }
}
```

**Rules** (score-based):
```python
def is_prepay_protected(pool: PoolData) -> dict:
    protection_score = 0
    factors = []
    
    # LLB bonus
    llb_bonus = {'LLB1': 25, 'LLB2': 20, 'LLB3': 15, 'LLB4': 12, 
                 'LLB5': 10, 'LLB6': 8, 'LLB7': 5, 'MLB': 2}
    if pool.loan_balance_tier in llb_bonus:
        protection_score += llb_bonus[pool.loan_balance_tier]
        factors.append(pool.loan_balance_tier)
    
    # Loan program bonus
    program_bonus = {'VA': 20, 'USDA': 22, 'FHA': 8}
    if pool.loan_program in program_bonus:
        protection_score += program_bonus[pool.loan_program]
        factors.append(pool.loan_program)
    
    # Servicer bonus
    if pool.servicer_prepay_risk == 'PREPAY_PROTECTED':
        protection_score += 12
        factors.append('bank_servicer')
    
    # State friction bonus
    if pool.state_prepay_friction == 'HIGH_FRICTION':
        protection_score += 10
        factors.append('high_friction_state')
    
    # FICO bonus (low FICO = protection)
    fico_bonus = {'FICO_LOW': 15, 'FICO_SUBPRIME': 10, 'FICO_FAIR': 5}
    if pool.fico_bucket in fico_bonus:
        protection_score += fico_bonus[pool.fico_bucket]
        factors.append(f'fico_{pool.fico_bucket}')
    
    # Burnout bonus
    if pool.burnout_score >= 60:
        protection_score += 15
        factors.append('burnout')
    
    # Threshold
    if protection_score >= 35:
        strength = 'strong' if protection_score >= 50 else 'moderate'
        return {
            'value': True,
            'confidence': min(0.95, 0.6 + protection_score / 100),
            'strength': strength,
            'factors': factors,
            'protection_score': protection_score
        }
    return {'value': False}
```

---

### 3.2 `prepay_exposed`

Pool at high risk of fast prepayments (investor-unfriendly).

```json
{
  "prepay_exposed": {
    "value": true,
    "confidence": 0.82,
    "severity": "high",
    "factors": ["digital_servicer", "high_fico", "jumbo", "CA_heavy"],
    "premium_mult": 1.35
  }
}
```

**Rules:**
- `servicer_prepay_risk = 'PREPAY_EXPOSED'` AND
- `fico_bucket IN ('FICO_EXCELLENT', 'FICO_SUPER')` AND
- `loan_balance_tier IN ('STD', 'JUMBO')` AND
- `burnout_score < 30`

---

### 3.3 `bull_market_rocket`

Pool at extreme contraction risk if rates drop significantly.

```json
{
  "bull_market_rocket": {
    "value": true,
    "confidence": 0.78,
    "expected_cpr_100bps_rally": 45.0,
    "factors": ["jumbo", "high_fico", "digital_servicer", "fresh"]
  }
}
```

**Rules:**
- `burnout_score < 25`
- `fico_bucket IN ('FICO_EXCELLENT', 'FICO_SUPER')`
- `loan_balance_tier IN ('STD', 'JUMBO')`
- `wala <= 12`
- `servicer_prepay_risk = 'PREPAY_EXPOSED'`

---

### 3.4 `bear_market_stable`

Pool expected to perform well (stable/faster turnover) in rising rate environment.

```json
{
  "bear_market_stable": {
    "value": true,
    "confidence": 0.80,
    "expected_cpr_100bps_selloff": 8.0,
    "factors": ["LLB2", "high_ltv", "low_fico", "seasoned"]
  }
}
```

**Rules:**
- `discount_cpr_mult >= 1.05`
- `loan_balance_tier IN ('LLB1', 'LLB2', 'LLB3', 'LLB4')`
- OR `fico_bucket IN ('FICO_LOW', 'FICO_SUBPRIME')`
- OR `ltv_bucket IN ('LTV_HIGH', 'LTV_VERY_HIGH', 'LTV_EXTREME')`

---

### 3.5 `extension_risk`

Pool at risk of extending duration longer than expected.

```json
{
  "extension_risk": {
    "value": true,
    "confidence": 0.85,
    "severity": "high",
    "factors": ["deep_otm", "high_fico", "jumbo", "digital_servicer", "high_factor"]
  }
}
```

**Rules:**
- `refi_incentive < -100 bps`
- `discount_cpr_mult < 0.85`
- `factor > 0.85`
- `servicer_prepay_risk = 'PREPAY_EXPOSED'` (digital servicers lock in when OTM)

---

### 3.6 `burnout_candidate`

Pool likely to slow prepays despite being in-the-money.

```json
{
  "burnout_candidate": {
    "value": true,
    "confidence": 0.88,
    "burnout_score": 72,
    "factors": ["wala_48", "factor_below_75", "persistent_itm"]
  }
}
```

**Rules:**
- `burnout_score >= 60`
- `refi_incentive > 25 bps`

---

### 3.7 `positive_convexity`

Pool exhibits favorable convexity (protection in both directions).

```json
{
  "positive_convexity": {
    "value": true,
    "convexity_score": 0.54,
    "premium_mult": 0.65,
    "discount_mult": 1.20,
    "factors": ["LLB1", "positive_convexity"]
  }
}
```

**Rules:** `convexity_score < 0.70`

---

### 3.8 `negative_convexity`

Pool exhibits unfavorable convexity (bad in both directions).

```json
{
  "negative_convexity": {
    "value": true,
    "severity": "high",
    "convexity_score": 1.38,
    "premium_mult": 1.10,
    "discount_mult": 0.80,
    "factors": ["jumbo", "high_fico", "digital_servicer"]
  }
}
```

**Rules:** `convexity_score > 1.10`

---

### 3.9 `all_weather`

Pool suitable for all rate environments (balanced convexity, moderate protection).

```json
{
  "all_weather": {
    "value": true,
    "bull_score": 65,
    "bear_score": 70,
    "neutral_score": 68,
    "variance": 5
  }
}
```

**Rules:**
- `0.70 <= convexity_score <= 0.90`
- `bull_score >= 50 AND bear_score >= 50 AND neutral_score >= 50`
- `ABS(bull_score - bear_score) < 15`

---

## Section 4: Composite Scores

### 4.1 Composite Prepay Protection Score (`composite_prepay_score`)

Single 0-100 score combining all protection factors.

```python
def calc_composite_prepay_score(pool: PoolData) -> float:
    """
    Calculate overall prepay protection score.
    Higher = more protection (slower prepays in premium environments).
    """
    # Weight factors by importance
    weights = {
        'loan_balance': 0.25,      # LLB is strongest factor
        'loan_program': 0.20,      # VA/FHA/USDA
        'servicer': 0.15,          # Servicer effect
        'fico_ltv': 0.15,          # Credit constraints
        'state': 0.08,             # Geographic friction
        'burnout': 0.10,           # Seasoning/burnout
        'occupancy': 0.05,         # Occupancy type
        'purpose': 0.02,           # Loan purpose
    }
    
    # Score each component (0-100)
    scores = {
        'loan_balance': LLB_SCORES.get(pool.loan_balance_tier, 50),
        'loan_program': PROGRAM_SCORES.get(pool.loan_program, 50),
        'servicer': SERVICER_SCORES.get(pool.servicer_prepay_risk, 50),
        'fico_ltv': (FICO_SCORES.get(pool.fico_bucket, 50) + 
                    LTV_SCORES.get(pool.ltv_bucket, 50)) / 2,
        'state': STATE_SCORES.get(pool.state_prepay_friction, 50),
        'burnout': pool.burnout_score,
        'occupancy': OCCUPANCY_SCORES.get(pool.occupancy_type, 50),
        'purpose': PURPOSE_SCORES.get(pool.loan_purpose, 50),
    }
    
    composite = sum(scores[k] * weights[k] for k in weights)
    return round(composite, 1)

# Score lookup tables (higher = more protection)
LLB_SCORES = {
    'LLB1': 95, 'LLB2': 88, 'LLB3': 80, 'LLB4': 72,
    'LLB5': 65, 'LLB6': 58, 'LLB7': 52, 'MLB': 45,
    'STD': 40, 'JUMBO': 25
}
PROGRAM_SCORES = {'VA': 90, 'USDA': 95, 'FHA': 70, 'CONV': 50}
SERVICER_SCORES = {'PREPAY_PROTECTED': 80, 'NEUTRAL': 50, 'PREPAY_EXPOSED': 20}
```

---

### 4.2 Scenario Scores

Scores optimized for specific rate environments.

```python
def calc_bull_scenario_score(pool: PoolData) -> float:
    """
    Score for BULL market (rates falling) - maximize contraction protection.
    Higher = better for holding premium pools when rates fall.
    """
    return (
        0.60 * (100 - pool.contraction_risk_score) +  # Low contraction risk
        0.25 * pool.composite_prepay_score +           # Overall protection
        0.15 * (pool.burnout_score)                    # Burnout helps
    )

def calc_bear_scenario_score(pool: PoolData) -> float:
    """
    Score for BEAR market (rates rising) - minimize extension risk.
    Higher = better for holding discount pools when rates rise.
    """
    return (
        0.50 * (100 - pool.extension_risk_score) +     # Low extension risk
        0.30 * pool.discount_cpr_mult * 50 +           # Higher discount mult = better
        0.20 * (100 if pool.convexity_score < 0.7 else 50)  # Positive convexity
    )

def calc_neutral_scenario_score(pool: PoolData) -> float:
    """
    Score for NEUTRAL market (rates stable) - balanced profile.
    Higher = good all-around pool.
    """
    return (
        0.35 * pool.composite_prepay_score +
        0.35 * (100 - abs(50 - pool.convexity_score * 50)) +  # Neutral convexity
        0.30 * 50  # Baseline
    )
```

---

### 4.3 Payup Efficiency Score

Value analysis: protection relative to payup cost.

```python
def calc_payup_efficiency(protection_score: float, expected_payup_32nds: int) -> float:
    """
    Calculate payup efficiency (protection per unit cost).
    Higher = better value.
    """
    if expected_payup_32nds <= 0:
        return protection_score  # Free protection is best
    
    return protection_score / expected_payup_32nds
```

---

## Section 5: Factor Interactions

When multiple characteristics combine, effects can be super-additive or sub-additive.

### 5.1 Super-Additive Combinations (Stack Well)

| Combination | Individual Effects | Combined Effect | Reason |
|-------------|-------------------|-----------------|--------|
| VA + LLB1 | 0.70 × 0.65 = 0.46 | ~0.42 (better) | Different friction sources |
| VA + NY | 0.70 × 0.85 = 0.60 | ~0.55 (better) | Program + legal friction |
| LLB + Bank Servicer | 0.70 × 0.85 = 0.60 | ~0.55 (better) | Balance + process friction |
| High LTV + Low FICO | 0.85 × 0.78 = 0.66 | ~0.60 (better) | Dual credit constraints |

### 5.2 Sub-Additive Combinations (Overlap)

| Combination | Individual Effects | Combined Effect | Reason |
|-------------|-------------------|-----------------|--------|
| VA + Low FICO | 0.70 × 0.78 = 0.55 | ~0.60 (worse) | VA already captures credit constraint |
| LLB + Investor | 0.70 × 0.78 = 0.55 | ~0.58 (similar) | Some overlap in borrower type |

### 5.3 Combination Multiplier Table

```python
# Interaction adjustments (multiply the naive product by this)
INTERACTION_ADJUSTMENTS = {
    ('VA', 'LLB1'): 0.92,       # Super-additive
    ('VA', 'LLB2'): 0.94,
    ('VA', 'NY'): 0.93,
    ('LLB1', 'PREPAY_PROTECTED'): 0.93,
    ('VA', 'FICO_LOW'): 1.08,   # Sub-additive (overlap)
    ('JUMBO', 'FICO_SUPER'): 1.00,  # Already multiplicative
    ('JUMBO', 'PREPAY_EXPOSED'): 1.05,  # Extra bad
}
```

---

## Section 6: Implementation

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

### Tag Generator Class

```python
# src/tagging/pool_tagger.py

class PoolTagger:
    def __init__(self, db_engine, fred_rate_series='MORTGAGE30US'):
        self.engine = db_engine
        self.current_rate = self._get_current_mortgage_rate(fred_rate_series)
    
    def generate_tags(self, pool: PoolData) -> dict:
        """Generate all AI tags for a pool."""
        # Static tags
        tags = {
            'loan_balance_tier': self._classify_loan_balance_tier(pool),
            'loan_program': self._classify_loan_program(pool),
            'fico_bucket': self._classify_fico_bucket(pool),
            'ltv_bucket': self._classify_ltv_bucket(pool),
            'occupancy_type': pool.occupancy_type,
            'loan_purpose': pool.loan_purpose,
            'risk_profile': self._classify_risk_profile(pool),
            'geo_concentration_tag': self._classify_geo(pool),
            'state_prepay_friction': self._classify_state_friction(pool),
            'servicer_prepay_risk': self._classify_servicer_prepay_risk(pool),
            'seasoning_stage': self._classify_seasoning_stage(pool),
        }
        
        # Derived metrics
        tags['refi_incentive_bps'] = self._calc_refi_incentive(pool)
        tags['burnout_score'] = self._calc_burnout_score(pool)
        tags['premium_cpr_mult'] = self._calc_premium_mult(pool)
        tags['discount_cpr_mult'] = self._calc_discount_mult(pool)
        tags['convexity_score'] = self._calc_convexity_score(pool)
        tags['contraction_risk_score'] = self._calc_contraction_risk(pool)
        tags['extension_risk_score'] = self._calc_extension_risk(pool)
        
        # Composite scores
        tags['composite_prepay_score'] = self._calc_composite_score(pool)
        tags['bull_scenario_score'] = self._calc_bull_score(pool)
        tags['bear_scenario_score'] = self._calc_bear_score(pool)
        tags['neutral_scenario_score'] = self._calc_neutral_score(pool)
        
        # Behavioral tags (JSONB)
        tags['behavior_tags'] = self._generate_behavior_tags(pool)
        
        return tags
```

---

## Section 7: Database Schema

### New Columns for `dim_pool`

```sql
-- Migration: Add enhanced tagging columns

-- Static attributes
ALTER TABLE dim_pool ADD COLUMN loan_balance_tier TEXT;
ALTER TABLE dim_pool ADD COLUMN loan_program TEXT;
ALTER TABLE dim_pool ADD COLUMN fico_bucket TEXT;
ALTER TABLE dim_pool ADD COLUMN ltv_bucket TEXT;
ALTER TABLE dim_pool ADD COLUMN occupancy_type TEXT;
ALTER TABLE dim_pool ADD COLUMN loan_purpose TEXT;
ALTER TABLE dim_pool ADD COLUMN seasoning_stage TEXT;
ALTER TABLE dim_pool ADD COLUMN property_type TEXT;
ALTER TABLE dim_pool ADD COLUMN origination_channel TEXT;
ALTER TABLE dim_pool ADD COLUMN has_rate_buydown TEXT;

-- Derived metrics
ALTER TABLE dim_pool ADD COLUMN refi_incentive_bps NUMERIC(8,2);
ALTER TABLE dim_pool ADD COLUMN premium_cpr_mult NUMERIC(5,3);
ALTER TABLE dim_pool ADD COLUMN discount_cpr_mult NUMERIC(5,3);
ALTER TABLE dim_pool ADD COLUMN convexity_score NUMERIC(5,3);
ALTER TABLE dim_pool ADD COLUMN contraction_risk_score NUMERIC(5,2);
ALTER TABLE dim_pool ADD COLUMN extension_risk_score NUMERIC(5,2);
ALTER TABLE dim_pool ADD COLUMN s_curve_position TEXT;

-- Composite scores
ALTER TABLE dim_pool ADD COLUMN composite_prepay_score NUMERIC(5,2);
ALTER TABLE dim_pool ADD COLUMN bull_scenario_score NUMERIC(5,2);
ALTER TABLE dim_pool ADD COLUMN bear_scenario_score NUMERIC(5,2);
ALTER TABLE dim_pool ADD COLUMN neutral_scenario_score NUMERIC(5,2);
ALTER TABLE dim_pool ADD COLUMN payup_efficiency_score NUMERIC(5,2);

-- Update timestamp
ALTER TABLE dim_pool ADD COLUMN tags_updated_at TIMESTAMPTZ;

-- Indexes for common queries
CREATE INDEX idx_pool_balance_tier ON dim_pool(loan_balance_tier);
CREATE INDEX idx_pool_loan_program ON dim_pool(loan_program);
CREATE INDEX idx_pool_composite_score ON dim_pool(composite_prepay_score);
CREATE INDEX idx_pool_convexity ON dim_pool(convexity_score);
CREATE INDEX idx_pool_scenario_scores ON dim_pool(bull_scenario_score, bear_scenario_score);
```

### Lookup Tables

```sql
-- Factor multiplier lookup table
CREATE TABLE IF NOT EXISTS factor_multipliers (
    factor_type TEXT NOT NULL,      -- 'loan_balance', 'loan_program', etc.
    factor_value TEXT NOT NULL,     -- 'LLB1', 'VA', etc.
    premium_mult NUMERIC(5,3),      -- Multiplier when in-the-money
    baseline_mult NUMERIC(5,3),     -- Baseline multiplier
    discount_mult NUMERIC(5,3),     -- Multiplier when out-of-the-money
    typical_payup_32nds INTEGER,    -- Expected payup in 32nds
    confidence TEXT,                -- 'high', 'medium', 'low'
    data_source TEXT,               -- 'empirical', 'market_consensus'
    last_updated DATE,
    PRIMARY KEY (factor_type, factor_value)
);
```

---

## Section 8: Dynamic Servicer Prepay Scoring

Instead of static servicer classifications, we calculate **actual prepay speed metrics** from historical pool performance.

### Database Schema

```sql
-- Table: servicer_prepay_metrics
CREATE TABLE IF NOT EXISTS servicer_prepay_metrics (
    servicer_id TEXT NOT NULL,
    servicer_name TEXT NOT NULL,
    as_of_month DATE NOT NULL,
    pool_count INTEGER,
    total_upb NUMERIC(18,2),
    avg_cpr NUMERIC(6,3),
    median_cpr NUMERIC(6,3),
    universe_avg_cpr NUMERIC(6,3),
    cpr_vs_universe NUMERIC(6,3),
    cpr_ratio NUMERIC(6,4),
    avg_wac NUMERIC(5,3),
    avg_wala INTEGER,
    avg_fico INTEGER,
    avg_ltv NUMERIC(5,2),
    avg_incentive NUMERIC(6,2),
    PRIMARY KEY (servicer_id, as_of_month),
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Table: servicer_prepay_scores
CREATE TABLE IF NOT EXISTS servicer_prepay_scores (
    servicer_id TEXT PRIMARY KEY,
    servicer_name TEXT NOT NULL,
    prepay_speed_score NUMERIC(5,2),
    prepay_speed_percentile INTEGER,
    cpr_3mo_avg NUMERIC(6,3),
    cpr_6mo_avg NUMERIC(6,3),
    cpr_12mo_avg NUMERIC(6,3),
    trend_3mo_vs_12mo NUMERIC(6,3),
    trend_direction TEXT,
    lifetime_avg_cpr NUMERIC(6,3),
    prepay_risk_category TEXT,
    pool_count INTEGER,
    last_updated TIMESTAMPTZ,
    months_of_data INTEGER
);

-- Table: servicer_prepay_alerts
CREATE TABLE IF NOT EXISTS servicer_prepay_alerts (
    id SERIAL PRIMARY KEY,
    servicer_id TEXT NOT NULL,
    alert_date DATE NOT NULL,
    alert_type TEXT NOT NULL,
    old_score NUMERIC(5,2),
    new_score NUMERIC(5,2),
    score_change NUMERIC(5,2),
    old_category TEXT,
    new_category TEXT,
    description TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
```

---

## Section 9: Query Examples

### Find prepay-protected pools for bull market

```sql
SELECT pool_id, cusip, composite_prepay_score, convexity_score,
       loan_balance_tier, loan_program, servicer_prepay_risk
FROM dim_pool
WHERE composite_prepay_score >= 70
  AND convexity_score < 0.75
  AND bull_scenario_score >= 65
ORDER BY bull_scenario_score DESC;
```

### Find pools with positive convexity (all-weather)

```sql
SELECT pool_id, cusip, convexity_score, 
       premium_cpr_mult, discount_cpr_mult,
       bull_scenario_score, bear_scenario_score
FROM dim_pool
WHERE behavior_tags->>'positive_convexity' = 'true'
  AND ABS(bull_scenario_score - bear_scenario_score) < 15
ORDER BY convexity_score ASC;
```

### Find VA + LLB combinations

```sql
SELECT pool_id, cusip, 
       loan_balance_tier, loan_program,
       premium_cpr_mult, composite_prepay_score
FROM dim_pool
WHERE loan_program = 'VA'
  AND loan_balance_tier IN ('LLB1', 'LLB2', 'LLB3')
ORDER BY premium_cpr_mult ASC;
```

### Avoid extension risk pools

```sql
SELECT pool_id, cusip, extension_risk_score,
       discount_cpr_mult, refi_incentive_bps
FROM dim_pool
WHERE extension_risk_score < 30
  AND discount_cpr_mult >= 1.0
ORDER BY extension_risk_score ASC;
```

---

## Section 10: Semantic Translation Layer

The semantic layer translates trader jargon into SQL filters:

| Trader Query | SQL Translation |
|--------------|-----------------|
| "high burnout" | `burnout_score >= 70` |
| "low burnout" | `burnout_score <= 30` |
| "fresh pools" | `seasoning_stage = 'NEW_PRODUCTION'` |
| "seasoned" | `seasoning_stage IN ('WELL_SEASONED', 'BURNED_OUT')` |
| "in the money" | `refi_incentive_bps > 50` |
| "out of the money" | `refi_incentive_bps < -50` |
| "deep OTM" | `refi_incentive_bps < -100` |
| "high FICO" | `fico_bucket IN ('FICO_EXCELLENT', 'FICO_SUPER')` |
| "low FICO" | `fico_bucket IN ('FICO_LOW', 'FICO_SUBPRIME')` |
| "low LTV" | `ltv_bucket IN ('LTV_LOW', 'LTV_MOD')` |
| "high LTV" | `ltv_bucket IN ('LTV_HIGH', 'LTV_VERY_HIGH', 'LTV_EXTREME')` |
| "super LLB" / "LLB1" | `loan_balance_tier = 'LLB1'` |
| "low balance" | `loan_balance_tier IN ('LLB1', 'LLB2', 'LLB3', 'LLB4')` |
| "jumbo" | `loan_balance_tier = 'JUMBO'` |
| "VA pools" | `loan_program = 'VA'` |
| "government pools" | `loan_program IN ('VA', 'FHA', 'USDA')` |
| "conventional" | `loan_program = 'CONV'` |
| "NY pools" / "slow states" | `state_prepay_friction = 'HIGH_FRICTION'` |
| "CA pools" / "fast states" | `state_prepay_friction = 'LOW_FRICTION'` |
| "bank servicer" / "slow servicer" | `servicer_prepay_risk = 'PREPAY_PROTECTED'` |
| "rocket pools" / "fast servicer" | `servicer_prepay_risk = 'PREPAY_EXPOSED'` |
| "prepay protected" | `composite_prepay_score >= 70` |
| "prepay exposed" | `composite_prepay_score <= 35` |
| "extension risk" | `extension_risk_score >= 60` |
| "contraction risk" | `contraction_risk_score >= 60` |
| "positive convexity" | `convexity_score < 0.70` |
| "negative convexity" | `convexity_score > 1.10` |
| "bull market stable" | `bull_scenario_score >= 70` |
| "bear market stable" | `bear_scenario_score >= 70` |
| "all weather" | `behavior_tags->>'all_weather' = 'true'` |
| "investor properties" | `occupancy_type = 'INVESTOR'` |
| "purchase loans" | `loan_purpose = 'PURCHASE'` |
| "cash out refi" | `loan_purpose = 'CASH_OUT_REFI'` |

---

## Section 11: Implementation Phases

### Phase 1: Core Infrastructure (Week 1-2)
- [ ] Add new columns to dim_pool (migration)
- [ ] Create factor_multipliers lookup table
- [ ] Implement `PoolTagger` class with all classification functions
- [ ] Add tagging step to factor file processing pipeline

### Phase 2: Loan-Level Integration (Week 2-3)
- [ ] Parse FRE_ILLD files for loan-level attributes
- [ ] Aggregate loan attributes to pool level
- [ ] Populate FICO, LTV, occupancy, purpose tags

### Phase 3: Derived Metrics (Week 3-4)
- [ ] Integrate FRED MORTGAGE30US for incentive calculation
- [ ] Calculate convexity scores
- [ ] Implement scenario scoring
- [ ] Generate behavioral tags

### Phase 4: Validation & Calibration (Week 4-5)
- [ ] Validate multipliers against historical CPR data
- [ ] Calibrate composite score weights
- [ ] Build servicer prepay scoring (data-driven)

### Phase 5: Semantic Layer (Week 5-6)
- [ ] Build query translation agent
- [ ] Create pool screener API
- [ ] Test natural language queries

---

## Appendix A: Complete Factor Multiplier Reference

| Factor | Type | Value | Premium | Baseline | Discount | Convexity |
|--------|------|-------|---------|----------|----------|-----------|
| loan_balance | LLB1 | ≤$85k | 0.65 | 0.68 | 1.20 | 0.54 |
| loan_balance | LLB2 | ≤$110k | 0.70 | 0.73 | 1.15 | 0.61 |
| loan_balance | LLB3 | ≤$125k | 0.75 | 0.78 | 1.12 | 0.67 |
| loan_balance | LLB4 | ≤$150k | 0.80 | 0.83 | 1.10 | 0.73 |
| loan_balance | LLB5 | ≤$175k | 0.84 | 0.86 | 1.08 | 0.78 |
| loan_balance | LLB6 | ≤$200k | 0.87 | 0.88 | 1.05 | 0.83 |
| loan_balance | LLB7 | ≤$225k | 0.90 | 0.91 | 1.03 | 0.87 |
| loan_balance | MLB | ≤$300k | 0.95 | 0.95 | 1.00 | 0.95 |
| loan_balance | STD | Conforming | 1.00 | 1.00 | 1.00 | 1.00 |
| loan_balance | JUMBO | >Conforming | 1.10 | 1.05 | 0.80 | 1.38 |
| loan_program | VA | VA Loans | 0.70 | 0.72 | 1.00 | 0.70 |
| loan_program | FHA | FHA Loans | 0.82 | 0.85 | 0.95 | 0.86 |
| loan_program | USDA | USDA/RD | 0.65 | 0.68 | 0.90 | 0.72 |
| loan_program | CONV | Conventional | 1.00 | 1.00 | 1.00 | 1.00 |
| fico | FICO_LOW | <660 | 0.75 | 0.78 | 1.15 | 0.65 |
| fico | FICO_SUBPRIME | 660-679 | 0.80 | 0.82 | 1.10 | 0.73 |
| fico | FICO_FAIR | 680-719 | 0.90 | 0.92 | 1.05 | 0.86 |
| fico | FICO_GOOD | 720-759 | 1.00 | 1.00 | 1.00 | 1.00 |
| fico | FICO_EXCELLENT | 760-779 | 1.08 | 1.05 | 0.90 | 1.20 |
| fico | FICO_SUPER | ≥780 | 1.12 | 1.08 | 0.85 | 1.32 |
| ltv | LTV_LOW | ≤60% | 1.05 | 1.02 | 0.95 | 1.11 |
| ltv | LTV_MOD | 60-70% | 1.00 | 1.00 | 1.00 | 1.00 |
| ltv | LTV_STANDARD | 70-80% | 0.98 | 0.98 | 1.02 | 0.96 |
| ltv | LTV_HIGH | 80-90% | 0.90 | 0.92 | 1.05 | 0.86 |
| ltv | LTV_VERY_HIGH | 90-95% | 0.85 | 0.88 | 1.08 | 0.79 |
| ltv | LTV_EXTREME | >95% | 0.78 | 0.82 | 1.12 | 0.70 |
| occupancy | OWNER | Owner-occ | 1.00 | 1.00 | 1.00 | 1.00 |
| occupancy | INVESTOR | Investment | 0.78 | 0.80 | 0.92 | 0.85 |
| occupancy | SECOND_HOME | 2nd Home | 0.88 | 0.90 | 0.95 | 0.93 |
| purpose | PURCHASE | Purchase | 0.88 | 0.90 | 1.02 | 0.86 |
| purpose | RATE_TERM_REFI | Rate/Term | 1.00 | 1.00 | 1.00 | 1.00 |
| purpose | CASH_OUT_REFI | Cash-Out | 1.05 | 1.02 | 1.15 | 0.91 |
| state_friction | HIGH_FRICTION | NY,NJ,etc | 0.85 | 0.87 | 0.95 | 0.89 |
| state_friction | MODERATE | Other | 1.00 | 1.00 | 1.00 | 1.00 |
| state_friction | LOW_FRICTION | CA,TX,etc | 1.10 | 1.05 | 1.05 | 1.05 |
| servicer | PREPAY_PROTECTED | Banks | 0.85 | 0.87 | 0.90 | 0.94 |
| servicer | NEUTRAL | Mid-tier | 1.00 | 1.00 | 1.00 | 1.00 |
| servicer | PREPAY_EXPOSED | Digital | 1.15 | 1.12 | 0.70 | 1.64 |
| seasoning | NEW_PRODUCTION | 0-6mo | 0.72 | 0.75 | 0.85 | 0.85 |
| seasoning | RAMPING | 6-12mo | 0.85 | 0.88 | 0.92 | 0.92 |
| seasoning | SEASONED | 12-24mo | 0.95 | 0.96 | 0.98 | 0.97 |
| seasoning | FULLY_SEASONED | 24-36mo | 1.00 | 1.00 | 1.00 | 1.00 |
| seasoning | WELL_SEASONED | 36-60mo | 0.85 | 0.87 | 1.05 | 0.81 |
| seasoning | BURNED_OUT | >60mo | 0.75 | 0.78 | 1.10 | 0.68 |

---

## Appendix B: Changelog

### Version 2.0 (January 2026)

**Major additions based on prepay empirical research analysis:**

| Category | What Changed |
|----------|--------------|
| **Loan Balance Tiers** | Expanded from single `low_balance_prepay` ($85k threshold) to full 10-tier taxonomy (LLB1-LLB7, MLB, STD, JUMBO) with premium/discount multipliers |
| **Convexity Framework** | Added entirely new framework measuring contraction vs extension risk with `convexity_score` metric |
| **Loan Programs** | Added `loan_program` tag (VA, FHA, USDA, CONV) with specific multipliers |
| **FICO Buckets** | Added 6-bucket FICO classification with convexity implications |
| **LTV Buckets** | Added 6-bucket LTV classification |
| **Occupancy/Purpose** | Added `occupancy_type` and `loan_purpose` tags |
| **Seasoning Stages** | Added `seasoning_stage` with 6 stages and multipliers |
| **Scenario Scores** | Added `bull_scenario_score`, `bear_scenario_score`, `neutral_scenario_score` |
| **Composite Score** | Added `composite_prepay_score` (0-100) combining all factors |
| **Risk Scores** | Added `contraction_risk_score` and `extension_risk_score` |
| **Behavioral Tags** | Added `positive_convexity`, `negative_convexity`, `all_weather` |
| **Factor Interactions** | Added super-additive/sub-additive combination analysis |
| **Rate Buydowns** | Added `has_rate_buydown` for 2-1/3-2-1 buydowns |
| **Semantic Mappings** | Expanded from ~15 to 40+ trader query translations |

**Retained from v1:**
- `risk_profile` tag (conservative/moderate/aggressive/high_extension)
- `burnout_score` calculation (enhanced formula)
- `geo_concentration_tag` values
- `state_prepay_friction` classification
- `servicer_prepay_risk` classification
- Dynamic servicer prepay scoring system
- Core behavioral tags: `burnout_candidate`, `bear_market_stable`, `bull_market_rocket`, `extension_risk`, `prepay_protected`, `prepay_exposed`

---

*Document Version: 2.0*  
*Last Updated: January 10, 2026*  
*Based on: Oasive Prepay Empirical Analysis v1.0*
