"""
Pool Tagger - AI-Generated Tags for MBS Pools

Implements the tagging methodology from docs/ai_tagging_design.md v2.0.
Applies classification rules and calculates composite scores for pools.

Usage:
    from src.tagging import PoolTagger
    tagger = PoolTagger(engine)
    tagger.tag_all_pools(batch_size=1000)
"""

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import text
from sqlalchemy.engine import Engine

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# =============================================================================
# CONSTANTS - From ai_tagging_design.md
# =============================================================================

# States by prepay friction level
HIGH_FRICTION_STATES = {'NY', 'NJ', 'FL', 'IL', 'CT', 'MA', 'PA', 'OH'}
LOW_FRICTION_STATES = {'CA', 'TX', 'AZ', 'CO', 'WA', 'GA', 'NV', 'OR'}

# Servicers by prepay speed
FAST_SERVICERS = {
    'rocket', 'quicken', 'better', 'loandepot', 'uwm',
    'united wholesale', 'pennymac', 'freedom mortgage'
}
SLOW_SERVICERS = {
    'wells fargo', 'chase', 'jpmorgan', 'bank of america', 'bofa',
    'ocwen', 'carrington', 'specialized loan servicing', 'cenlar', 'us bank'
}

# Score lookup tables (higher = more prepay protection)
LLB_SCORES = {
    'LLB1': 95, 'LLB2': 88, 'LLB3': 80, 'LLB4': 72,
    'LLB5': 65, 'LLB6': 58, 'LLB7': 52, 'MLB': 45,
    'STD': 40, 'JUMBO': 25
}
PROGRAM_SCORES = {'VA': 90, 'USDA': 95, 'FHA': 70, 'CONV': 50}
SERVICER_SCORES = {'PREPAY_PROTECTED': 80, 'NEUTRAL': 50, 'PREPAY_EXPOSED': 20}
STATE_SCORES = {'HIGH_FRICTION': 75, 'MODERATE_FRICTION': 50, 'LOW_FRICTION': 30}
FICO_SCORES = {
    'FICO_LOW': 80, 'FICO_SUBPRIME': 70, 'FICO_FAIR': 55,
    'FICO_GOOD': 50, 'FICO_EXCELLENT': 35, 'FICO_SUPER': 25
}
LTV_SCORES = {
    'LTV_LOW': 40, 'LTV_MOD': 50, 'LTV_STANDARD': 55,
    'LTV_HIGH': 65, 'LTV_VERY_HIGH': 75, 'LTV_EXTREME': 85
}


@dataclass
class PoolData:
    """Pool data for tagging."""
    pool_id: str
    avg_loan_size: Optional[float] = None
    avg_fico: Optional[int] = None
    avg_ltv: Optional[float] = None
    wala: Optional[int] = None
    wac: Optional[float] = None
    top_state: Optional[str] = None
    top_state_pct: Optional[float] = None
    servicer_name: Optional[str] = None
    product_type: Optional[str] = None
    factor: Optional[float] = None
    current_rate: Optional[float] = None  # From FRED


class PoolTagger:
    """
    Generates AI tags for MBS pools based on their characteristics.
    
    Implements the four-layer framework:
    1. Static Attributes (loan_balance_tier, loan_program, etc.)
    2. Derived Metrics (refi_incentive, burnout_score, etc.)
    3. Behavioral Tags (prepay_protected, positive_convexity, etc.)
    4. Composite Scores (0-100 scores for screening)
    """
    
    def __init__(self, engine: Engine, current_mortgage_rate: float = 6.5):
        """
        Initialize tagger.
        
        Args:
            engine: SQLAlchemy database engine
            current_mortgage_rate: Current 30yr mortgage rate (from FRED)
        """
        self.engine = engine
        self.current_rate = current_mortgage_rate
        
    def _get_current_mortgage_rate(self) -> float:
        """Fetch latest 30yr mortgage rate from FRED data in database."""
        query = """
            SELECT value FROM fred_observations 
            WHERE series_id = 'MORTGAGE30US' 
            ORDER BY observation_date DESC LIMIT 1
        """
        try:
            with self.engine.connect() as conn:
                result = conn.execute(text(query)).fetchone()
                if result:
                    return float(result[0])
        except Exception as e:
            logger.warning(f"Could not fetch mortgage rate: {e}")
        return 6.5  # Default fallback
    
    # =========================================================================
    # LAYER 1: Static Attribute Classifications
    # =========================================================================
    
    def classify_loan_balance_tier(self, avg_loan_size: float, conforming_limit: float = 766550) -> str:
        """Classify pool by loan balance tier (LLB1-LLB7, MLB, STD, JUMBO)."""
        if avg_loan_size is None:
            return 'STD'
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
    
    def classify_fico_bucket(self, avg_fico: int) -> str:
        """Classify pool by FICO bucket."""
        if avg_fico is None:
            return 'FICO_GOOD'
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
    
    def classify_ltv_bucket(self, avg_ltv: float) -> str:
        """Classify pool by LTV bucket."""
        if avg_ltv is None:
            return 'LTV_STANDARD'
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
    
    def classify_seasoning_stage(self, wala: int) -> str:
        """Classify pool by seasoning stage."""
        if wala is None:
            return 'FULLY_SEASONED'
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
    
    def classify_state_friction(self, top_state: str, top_state_pct: float) -> str:
        """Classify pool by state prepay friction."""
        if not top_state or top_state_pct is None or top_state_pct < 25:
            return 'MODERATE_FRICTION'
        
        state = top_state.upper()
        if state in HIGH_FRICTION_STATES and top_state_pct >= 30:
            return 'HIGH_FRICTION'
        elif state in LOW_FRICTION_STATES and top_state_pct >= 30:
            return 'LOW_FRICTION'
        else:
            return 'MODERATE_FRICTION'
    
    def classify_servicer_prepay_risk(self, servicer_name: str) -> str:
        """Classify servicer by prepay risk (investor perspective)."""
        if not servicer_name:
            return 'NEUTRAL'
        
        name_lower = servicer_name.lower()
        
        # Check for fast (prepay-exposed) servicers
        if any(s in name_lower for s in FAST_SERVICERS):
            return 'PREPAY_EXPOSED'
        # Check for slow (prepay-protected) servicers
        elif any(s in name_lower for s in SLOW_SERVICERS):
            return 'PREPAY_PROTECTED'
        else:
            return 'NEUTRAL'
    
    def classify_geo_concentration(self, top_state: str, top_state_pct: float) -> str:
        """Classify pool by geographic concentration."""
        if not top_state or top_state_pct is None:
            return 'DIVERSIFIED'
        
        state = top_state.upper()
        pct = top_state_pct
        
        if state == 'CA' and pct >= 30:
            return 'CA_HEAVY'
        elif state == 'TX' and pct >= 30:
            return 'TX_HEAVY'
        elif state == 'FL' and pct >= 30:
            return 'FL_HEAVY'
        elif state == 'NY' and pct >= 25:
            return 'NY_HEAVY'
        elif state in ('CA', 'FL', 'NY', 'WA', 'OR', 'MA') and pct >= 25:
            return 'COASTAL'
        elif state in ('TX', 'FL', 'AZ', 'NV', 'GA') and pct >= 25:
            return 'SUNBELT'
        elif state in ('OH', 'MI', 'IL', 'IN', 'WI') and pct >= 25:
            return 'MIDWEST'
        elif pct < 20:
            return 'DIVERSIFIED'
        else:
            return f'{state}_CONCENTRATED'
    
    # =========================================================================
    # LAYER 2: Derived Metrics
    # =========================================================================
    
    def calc_refi_incentive(self, wac: float) -> float:
        """Calculate refi incentive in basis points."""
        if wac is None:
            return 0.0
        return (wac - self.current_rate) * 100
    
    def calc_burnout_score(self, wala: int, factor: float, refi_incentive_bps: float) -> float:
        """
        Calculate burnout score (0-100).
        Higher = more burned out = slower prepays in premium environments.
        """
        if wala is None:
            wala = 36
        if factor is None:
            factor = 0.85
        
        # Seasoning component (max 35 pts)
        seasoning_pts = min(35, wala / 60.0 * 35)
        
        # Paydown component (max 45 pts)
        paydown_pts = min(45, (1 - factor) * 60)
        
        # In-the-money persistence bonus (max 20 pts)
        if refi_incentive_bps > 50 and wala > 12:
            itm_bonus = min(20, (wala / 24) * 20)
        else:
            itm_bonus = 0
        
        return min(100, seasoning_pts + paydown_pts + itm_bonus)
    
    def calc_premium_mult(self, pool: PoolData) -> float:
        """Calculate aggregate premium CPR multiplier."""
        mult = 1.0
        
        # Loan balance tier
        tier_mults = {
            'LLB1': 0.65, 'LLB2': 0.70, 'LLB3': 0.75, 'LLB4': 0.80,
            'LLB5': 0.84, 'LLB6': 0.87, 'LLB7': 0.90, 'MLB': 0.95,
            'STD': 1.00, 'JUMBO': 1.10
        }
        tier = self.classify_loan_balance_tier(pool.avg_loan_size)
        mult *= tier_mults.get(tier, 1.0)
        
        # Servicer effect
        servicer_risk = self.classify_servicer_prepay_risk(pool.servicer_name)
        servicer_mults = {'PREPAY_PROTECTED': 0.85, 'NEUTRAL': 1.0, 'PREPAY_EXPOSED': 1.15}
        mult *= servicer_mults.get(servicer_risk, 1.0)
        
        # FICO effect
        fico_bucket = self.classify_fico_bucket(pool.avg_fico)
        fico_mults = {
            'FICO_LOW': 0.75, 'FICO_SUBPRIME': 0.80, 'FICO_FAIR': 0.90,
            'FICO_GOOD': 1.0, 'FICO_EXCELLENT': 1.08, 'FICO_SUPER': 1.12
        }
        mult *= fico_mults.get(fico_bucket, 1.0)
        
        return round(mult, 3)
    
    def calc_discount_mult(self, pool: PoolData) -> float:
        """Calculate aggregate discount CPR multiplier."""
        mult = 1.0
        
        # Loan balance tier (LLB has HIGHER discount mult - positive convexity)
        tier_mults = {
            'LLB1': 1.20, 'LLB2': 1.15, 'LLB3': 1.12, 'LLB4': 1.10,
            'LLB5': 1.08, 'LLB6': 1.05, 'LLB7': 1.03, 'MLB': 1.00,
            'STD': 1.00, 'JUMBO': 0.80
        }
        tier = self.classify_loan_balance_tier(pool.avg_loan_size)
        mult *= tier_mults.get(tier, 1.0)
        
        # Servicer effect
        servicer_risk = self.classify_servicer_prepay_risk(pool.servicer_name)
        servicer_mults = {'PREPAY_PROTECTED': 0.90, 'NEUTRAL': 1.0, 'PREPAY_EXPOSED': 0.70}
        mult *= servicer_mults.get(servicer_risk, 1.0)
        
        # FICO effect (low FICO = HIGHER discount mult - positive convexity)
        fico_bucket = self.classify_fico_bucket(pool.avg_fico)
        fico_mults = {
            'FICO_LOW': 1.15, 'FICO_SUBPRIME': 1.10, 'FICO_FAIR': 1.05,
            'FICO_GOOD': 1.0, 'FICO_EXCELLENT': 0.90, 'FICO_SUPER': 0.85
        }
        mult *= fico_mults.get(fico_bucket, 1.0)
        
        return round(mult, 3)
    
    def calc_convexity_score(self, premium_mult: float, discount_mult: float) -> float:
        """
        Calculate convexity score.
        Lower is BETTER for investors (positive convexity).
        """
        if discount_mult <= 0:
            discount_mult = 0.01
        return round(premium_mult / discount_mult, 3)
    
    def classify_s_curve_position(self, refi_incentive_bps: float) -> str:
        """Classify position on prepayment S-curve."""
        if refi_incentive_bps < -100:
            return 'LEFT_TAIL'
        elif refi_incentive_bps < -25:
            return 'LEFT_SHOULDER'
        elif refi_incentive_bps < 75:
            return 'INFLECTION'
        elif refi_incentive_bps < 150:
            return 'RIGHT_SHOULDER'
        else:
            return 'RIGHT_TAIL'
    
    # =========================================================================
    # LAYER 3: Behavioral Tags
    # =========================================================================
    
    def generate_behavior_tags(self, pool: PoolData, tags: Dict[str, Any]) -> Dict[str, Any]:
        """Generate behavioral tags as JSONB."""
        behavior = {}
        
        # prepay_protected tag
        protection_score = self._calc_protection_score(pool, tags)
        if protection_score >= 35:
            behavior['prepay_protected'] = {
                'value': True,
                'strength': 'strong' if protection_score >= 50 else 'moderate',
                'protection_score': protection_score
            }
        
        # prepay_exposed tag
        if (tags.get('servicer_prepay_risk') == 'PREPAY_EXPOSED' and
            tags.get('fico_bucket') in ('FICO_EXCELLENT', 'FICO_SUPER') and
            tags.get('loan_balance_tier') in ('STD', 'JUMBO') and
            tags.get('burnout_score', 100) < 30):
            behavior['prepay_exposed'] = {
                'value': True,
                'severity': 'high',
                'premium_mult': tags.get('premium_cpr_mult', 1.0)
            }
        
        # positive_convexity tag
        convexity = tags.get('convexity_score', 1.0)
        if convexity < 0.70:
            behavior['positive_convexity'] = {
                'value': True,
                'convexity_score': convexity,
                'premium_mult': tags.get('premium_cpr_mult'),
                'discount_mult': tags.get('discount_cpr_mult')
            }
        
        # negative_convexity tag
        if convexity > 1.10:
            behavior['negative_convexity'] = {
                'value': True,
                'severity': 'high' if convexity > 1.25 else 'moderate',
                'convexity_score': convexity
            }
        
        # burnout_candidate tag
        burnout = tags.get('burnout_score', 0)
        incentive = tags.get('refi_incentive_bps', 0)
        if burnout >= 60 and incentive > 25:
            behavior['burnout_candidate'] = {
                'value': True,
                'burnout_score': burnout
            }
        
        # extension_risk tag
        if (incentive < -100 and
            tags.get('discount_cpr_mult', 1.0) < 0.85 and
            (pool.factor or 1.0) > 0.85):
            behavior['extension_risk'] = {
                'value': True,
                'severity': 'high'
            }
        
        return behavior
    
    def _calc_protection_score(self, pool: PoolData, tags: Dict[str, Any]) -> float:
        """Calculate prepay protection score for behavioral tagging."""
        score = 0
        
        # LLB bonus
        llb_bonus = {'LLB1': 25, 'LLB2': 20, 'LLB3': 15, 'LLB4': 12,
                     'LLB5': 10, 'LLB6': 8, 'LLB7': 5, 'MLB': 2}
        tier = tags.get('loan_balance_tier', 'STD')
        score += llb_bonus.get(tier, 0)
        
        # Servicer bonus
        if tags.get('servicer_prepay_risk') == 'PREPAY_PROTECTED':
            score += 12
        
        # State friction bonus
        if tags.get('state_prepay_friction') == 'HIGH_FRICTION':
            score += 10
        
        # FICO bonus (low FICO = protection)
        fico_bonus = {'FICO_LOW': 15, 'FICO_SUBPRIME': 10, 'FICO_FAIR': 5}
        fico = tags.get('fico_bucket', 'FICO_GOOD')
        score += fico_bonus.get(fico, 0)
        
        # Burnout bonus
        if tags.get('burnout_score', 0) >= 60:
            score += 15
        
        return score
    
    # =========================================================================
    # LAYER 4: Composite Scores
    # =========================================================================
    
    def calc_composite_prepay_score(self, tags: Dict[str, Any]) -> float:
        """
        Calculate overall prepay protection score (0-100).
        Higher = more protection (slower prepays in premium environments).
        """
        weights = {
            'loan_balance': 0.25,
            'servicer': 0.15,
            'fico': 0.15,
            'state': 0.08,
            'burnout': 0.10,
            'ltv': 0.07,
            'seasoning': 0.10,
            'other': 0.10,
        }
        
        scores = {
            'loan_balance': LLB_SCORES.get(tags.get('loan_balance_tier', 'STD'), 40),
            'servicer': SERVICER_SCORES.get(tags.get('servicer_prepay_risk', 'NEUTRAL'), 50),
            'fico': FICO_SCORES.get(tags.get('fico_bucket', 'FICO_GOOD'), 50),
            'state': STATE_SCORES.get(tags.get('state_prepay_friction', 'MODERATE_FRICTION'), 50),
            'burnout': tags.get('burnout_score', 50),
            'ltv': LTV_SCORES.get(tags.get('ltv_bucket', 'LTV_STANDARD'), 55),
            'seasoning': 50,  # Base value
            'other': 50,
        }
        
        # Adjust seasoning score
        seasoning = tags.get('seasoning_stage', 'FULLY_SEASONED')
        seasoning_map = {
            'NEW_PRODUCTION': 60, 'RAMPING': 55, 'SEASONED': 50,
            'FULLY_SEASONED': 50, 'WELL_SEASONED': 65, 'BURNED_OUT': 75
        }
        scores['seasoning'] = seasoning_map.get(seasoning, 50)
        
        composite = sum(scores[k] * weights[k] for k in weights)
        return round(composite, 1)
    
    def calc_bull_scenario_score(self, tags: Dict[str, Any]) -> float:
        """Score for BULL market (rates falling) - maximize contraction protection."""
        contraction_risk = tags.get('contraction_risk_score', 50)
        composite = tags.get('composite_prepay_score', 50)
        burnout = tags.get('burnout_score', 50)
        
        score = (
            0.60 * (100 - contraction_risk) +
            0.25 * composite +
            0.15 * burnout
        )
        return round(score, 1)
    
    def calc_bear_scenario_score(self, tags: Dict[str, Any]) -> float:
        """Score for BEAR market (rates rising) - minimize extension risk."""
        extension_risk = tags.get('extension_risk_score', 50)
        discount_mult = tags.get('discount_cpr_mult', 1.0)
        convexity = tags.get('convexity_score', 1.0)
        
        score = (
            0.50 * (100 - extension_risk) +
            0.30 * min(100, discount_mult * 50) +
            0.20 * (100 if convexity < 0.7 else 50)
        )
        return round(score, 1)
    
    def calc_neutral_scenario_score(self, tags: Dict[str, Any]) -> float:
        """Score for NEUTRAL market (rates stable) - balanced profile."""
        composite = tags.get('composite_prepay_score', 50)
        convexity = tags.get('convexity_score', 1.0)
        
        # Penalize extreme convexity
        convexity_score = 100 - abs(50 - convexity * 50) * 2
        
        score = (
            0.40 * composite +
            0.35 * convexity_score +
            0.25 * 50  # Baseline
        )
        return round(score, 1)
    
    def calc_contraction_risk(self, tags: Dict[str, Any]) -> float:
        """Calculate contraction risk score (0-100)."""
        premium_mult = tags.get('premium_cpr_mult', 1.0)
        burnout = tags.get('burnout_score', 50)
        servicer = tags.get('servicer_prepay_risk', 'NEUTRAL')
        fico = tags.get('fico_bucket', 'FICO_GOOD')
        
        # Base from premium multiplier
        base_risk = (premium_mult - 0.5) / 0.7 * 50
        
        # Burnout reduces risk
        burnout_reduction = (burnout / 100) * 25
        
        # Servicer adjustment
        servicer_adj = {'PREPAY_PROTECTED': -10, 'NEUTRAL': 0, 'PREPAY_EXPOSED': 15}
        
        # FICO adjustment
        fico_adj = {
            'FICO_LOW': -10, 'FICO_SUBPRIME': -5, 'FICO_FAIR': 0,
            'FICO_GOOD': 5, 'FICO_EXCELLENT': 10, 'FICO_SUPER': 15
        }
        
        risk = (base_risk - burnout_reduction + 
                servicer_adj.get(servicer, 0) + 
                fico_adj.get(fico, 0))
        return max(0, min(100, round(risk, 1)))
    
    def calc_extension_risk(self, tags: Dict[str, Any], pool: PoolData) -> float:
        """Calculate extension risk score (0-100)."""
        discount_mult = tags.get('discount_cpr_mult', 1.0)
        incentive = tags.get('refi_incentive_bps', 0)
        factor = pool.factor or 0.85
        tier = tags.get('loan_balance_tier', 'STD')
        
        # Base from discount multiplier
        base_risk = (1.2 - discount_mult) / 0.5 * 40
        
        # Deep OTM increases risk
        if incentive < -100:
            otm_adj = 20
        elif incentive < -50:
            otm_adj = 10
        else:
            otm_adj = 0
        
        # High factor increases risk
        factor_adj = (factor - 0.5) * 20 if factor > 0.5 else 0
        
        # LLB reduces extension risk
        llb_adj = {
            'LLB1': -15, 'LLB2': -12, 'LLB3': -10, 'LLB4': -8,
            'LLB5': -6, 'LLB6': -4, 'LLB7': -2, 'MLB': 0,
            'STD': 0, 'JUMBO': 10
        }
        
        risk = base_risk + otm_adj + factor_adj + llb_adj.get(tier, 0)
        return max(0, min(100, round(risk, 1)))
    
    # =========================================================================
    # MAIN TAGGING METHOD
    # =========================================================================
    
    def generate_all_tags(self, pool: PoolData) -> Dict[str, Any]:
        """Generate all tags for a pool."""
        tags = {}
        
        # Layer 1: Static attributes
        tags['loan_balance_tier'] = self.classify_loan_balance_tier(pool.avg_loan_size)
        tags['fico_bucket'] = self.classify_fico_bucket(pool.avg_fico)
        tags['ltv_bucket'] = self.classify_ltv_bucket(pool.avg_ltv)
        tags['seasoning_stage'] = self.classify_seasoning_stage(pool.wala)
        tags['state_prepay_friction'] = self.classify_state_friction(pool.top_state, pool.top_state_pct)
        tags['servicer_prepay_risk'] = self.classify_servicer_prepay_risk(pool.servicer_name)
        tags['geo_concentration_tag'] = self.classify_geo_concentration(pool.top_state, pool.top_state_pct)
        
        # Layer 2: Derived metrics
        tags['refi_incentive_bps'] = self.calc_refi_incentive(pool.wac)
        tags['burnout_score'] = self.calc_burnout_score(pool.wala, pool.factor, tags['refi_incentive_bps'])
        tags['premium_cpr_mult'] = self.calc_premium_mult(pool)
        tags['discount_cpr_mult'] = self.calc_discount_mult(pool)
        tags['convexity_score'] = self.calc_convexity_score(tags['premium_cpr_mult'], tags['discount_cpr_mult'])
        tags['s_curve_position'] = self.classify_s_curve_position(tags['refi_incentive_bps'])
        
        # Risk scores
        tags['contraction_risk_score'] = self.calc_contraction_risk(tags)
        tags['extension_risk_score'] = self.calc_extension_risk(tags, pool)
        
        # Layer 3: Behavioral tags
        tags['behavior_tags'] = self.generate_behavior_tags(pool, tags)
        
        # Layer 4: Composite scores
        tags['composite_prepay_score'] = self.calc_composite_prepay_score(tags)
        tags['bull_scenario_score'] = self.calc_bull_scenario_score(tags)
        tags['bear_scenario_score'] = self.calc_bear_scenario_score(tags)
        tags['neutral_scenario_score'] = self.calc_neutral_scenario_score(tags)
        
        return tags
    
    # =========================================================================
    # BATCH PROCESSING
    # =========================================================================
    
    def tag_all_pools(self, batch_size: int = 1000, limit: Optional[int] = None) -> int:
        """
        Tag all pools in the database.
        
        Args:
            batch_size: Number of pools to process per batch
            limit: Optional limit on total pools to process
            
        Returns:
            Number of pools tagged
        """
        # Refresh current rate
        self.current_rate = self._get_current_mortgage_rate()
        logger.info(f"Using mortgage rate: {self.current_rate}%")
        
        # Count pools to process
        with self.engine.connect() as conn:
            count_query = "SELECT COUNT(*) FROM dim_pool WHERE tags_updated_at IS NULL"
            if limit:
                count_query = f"SELECT LEAST({limit}, ({count_query}))"
            total = conn.execute(text(count_query)).scalar()
        
        logger.info(f"Tagging {total:,} pools in batches of {batch_size}")
        
        processed = 0
        offset = 0
        
        while processed < total:
            # Fetch batch of pools
            query = f"""
                SELECT pool_id, avg_loan_size, avg_fico, avg_ltv, wala, wac,
                       top_state, top_state_pct, servicer_name, product_type
                FROM dim_pool
                WHERE tags_updated_at IS NULL
                ORDER BY pool_id
                LIMIT {batch_size} OFFSET {offset}
            """
            
            with self.engine.connect() as conn:
                rows = conn.execute(text(query)).fetchall()
            
            if not rows:
                break
            
            # Process batch
            updates = []
            for row in rows:
                pool = PoolData(
                    pool_id=row[0],
                    avg_loan_size=row[1],
                    avg_fico=row[2],
                    avg_ltv=row[3],
                    wala=row[4],
                    wac=row[5],
                    top_state=row[6],
                    top_state_pct=row[7],
                    servicer_name=row[8],
                    product_type=row[9],
                )
                
                tags = self.generate_all_tags(pool)
                tags['pool_id'] = pool.pool_id
                updates.append(tags)
            
            # Batch update
            self._batch_update_tags(updates)
            
            processed += len(rows)
            offset += batch_size
            
            if processed % 5000 == 0:
                logger.info(f"Progress: {processed:,} / {total:,} ({processed/total*100:.1f}%)")
        
        logger.info(f"Tagging complete: {processed:,} pools tagged")
        return processed
    
    def _batch_update_tags(self, updates: List[Dict[str, Any]]) -> None:
        """Batch update pool tags."""
        import json
        
        update_sql = """
            UPDATE dim_pool SET
                loan_balance_tier = :loan_balance_tier,
                fico_bucket = :fico_bucket,
                ltv_bucket = :ltv_bucket,
                seasoning_stage = :seasoning_stage,
                state_prepay_friction = :state_prepay_friction,
                servicer_prepay_risk = :servicer_prepay_risk,
                geo_concentration_tag = :geo_concentration_tag,
                refi_incentive_bps = :refi_incentive_bps,
                burnout_score = :burnout_score,
                premium_cpr_mult = :premium_cpr_mult,
                discount_cpr_mult = :discount_cpr_mult,
                convexity_score = :convexity_score,
                s_curve_position = :s_curve_position,
                contraction_risk_score = :contraction_risk_score,
                extension_risk_score = :extension_risk_score,
                composite_prepay_score = :composite_prepay_score,
                bull_scenario_score = :bull_scenario_score,
                bear_scenario_score = :bear_scenario_score,
                neutral_scenario_score = :neutral_scenario_score,
                behavior_tags = :behavior_tags,
                tags_updated_at = NOW()
            WHERE pool_id = :pool_id
        """
        
        with self.engine.connect() as conn:
            for tags in updates:
                # Convert behavior_tags dict to JSON string
                tags['behavior_tags'] = json.dumps(tags.get('behavior_tags', {}))
                conn.execute(text(update_sql), tags)
            conn.commit()


# =============================================================================
# CLI INTERFACE
# =============================================================================

def main():
    """Run tagging from command line."""
    import argparse
    from src.db.connection import get_engine
    
    parser = argparse.ArgumentParser(description="Tag MBS pools with AI-generated attributes")
    parser.add_argument('--batch-size', type=int, default=1000, help='Batch size for processing')
    parser.add_argument('--limit', type=int, help='Limit number of pools to process')
    parser.add_argument('--rate', type=float, default=6.5, help='Current 30yr mortgage rate')
    
    args = parser.parse_args()
    
    engine = get_engine()
    tagger = PoolTagger(engine, current_mortgage_rate=args.rate)
    
    count = tagger.tag_all_pools(batch_size=args.batch_size, limit=args.limit)
    print(f"Tagged {count:,} pools")


if __name__ == '__main__':
    main()
