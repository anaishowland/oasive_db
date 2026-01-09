#!/usr/bin/env python3
"""
Empirical Prepay Factor Analysis

Analyzes Freddie Mac disclosure data to estimate prepay speed multipliers
for each pool characteristic (loan program, balance, LTV, FICO, etc.).

This research will calibrate the weights used in the composite spec pool score.

Usage:
    python scripts/analyze_prepay_factors.py --factor loan_program
    python scripts/analyze_prepay_factors.py --all
"""

import argparse
import logging
from dataclasses import dataclass
from typing import Dict, List, Optional
import os
import sys

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from sqlalchemy import create_engine, text
import pandas as pd
import numpy as np

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


@dataclass
class FactorResult:
    """Result of analyzing one factor value's prepay behavior."""
    value: str
    avg_cpr: float
    multiplier: float  # vs universe baseline
    pool_count: int
    month_count: int
    confidence: str  # 'high', 'medium', 'low'


@dataclass
class FactorAnalysis:
    """Complete analysis for one factor."""
    factor_name: str
    baseline_cpr: float
    results: Dict[str, FactorResult]
    suggested_weights: Dict[str, int]


class PrepayFactorAnalyzer:
    """
    Analyzes Freddie Mac data to estimate prepay multipliers by factor.
    
    The goal is to answer: "How much faster/slower does a VA pool prepay
    vs a conventional pool, all else equal?"
    """
    
    # Factor definitions with SQL column mappings
    FACTOR_CONFIGS = {
        'loan_program': {
            'table': 'dim_loan',
            'column': 'loan_purpose',  # Or use program type when available
            'values': ['VA', 'FHA', 'USDA', 'CONV'],
            'description': 'Government vs conventional loan programs'
        },
        'loan_balance_tier': {
            'table': 'dim_loan', 
            'column': 'orig_upb',
            'buckets': [85000, 110000, 150000, 250000, 350000, 726200],
            'labels': ['LLB_85', 'LLB_110', 'LLB_150', 'LLB_250', 'LLB_350', 'HLB', 'JUMBO'],
            'description': 'Loan balance tiers (low loan balance tends to prepay slower)'
        },
        'ltv': {
            'table': 'dim_loan',
            'column': 'ltv',
            'buckets': [60, 70, 80, 90, 95],
            'labels': ['<60', '60-70', '70-80', '80-90', '90-95', '95+'],
            'description': 'Loan-to-value ratio'
        },
        'fico': {
            'table': 'dim_loan',
            'column': 'fico',
            'buckets': [680, 720, 760, 780],
            'labels': ['<680', '680-720', '720-760', '760-780', '780+'],
            'description': 'Credit score at origination'
        },
        'occupancy': {
            'table': 'dim_loan',
            'column': 'occupancy',
            'values': ['OWNER_OCC', 'INVESTOR', 'SECOND_HOME'],
            'description': 'Property occupancy type'
        },
        'state': {
            'table': 'dim_loan',
            'column': 'state',
            'values': None,  # Will query for top states
            'description': 'Property state (proxy for refi friction)'
        },
    }
    
    # Control variables to isolate factor effects
    CONTROL_VARS = ['incentive_bucket', 'wala_bucket']
    
    def __init__(self, db_url: str):
        self.engine = create_engine(db_url)
        
    def get_baseline_cpr(self) -> float:
        """Calculate universe-wide average CPR."""
        query = """
        SELECT AVG(
            CASE WHEN factor > 0 AND LAG(factor) OVER (PARTITION BY pool_id ORDER BY as_of_month) > 0
                 THEN (1 - factor / LAG(factor) OVER (PARTITION BY pool_id ORDER BY as_of_month)) * 12 * 100
                 ELSE NULL
            END
        ) as avg_cpr
        FROM fact_pool_month
        WHERE as_of_month >= CURRENT_DATE - INTERVAL '24 months'
        """
        with self.engine.connect() as conn:
            result = conn.execute(text(query)).fetchone()
            return result[0] if result and result[0] else 0.0
    
    def analyze_categorical_factor(self, factor_name: str, config: dict) -> FactorAnalysis:
        """Analyze a categorical factor (like loan_program, occupancy)."""
        logger.info(f"Analyzing factor: {factor_name}")
        
        baseline_cpr = self.get_baseline_cpr()
        results = {}
        suggested_weights = {}
        
        values = config.get('values', [])
        if not values:
            # Query for unique values
            query = f"SELECT DISTINCT {config['column']} FROM {config['table']} LIMIT 20"
            with self.engine.connect() as conn:
                df = pd.read_sql(query, conn)
                values = df[config['column']].dropna().tolist()
        
        for value in values:
            result = self._calculate_cpr_for_value(
                table=config['table'],
                column=config['column'],
                value=value,
                baseline_cpr=baseline_cpr
            )
            if result:
                results[value] = result
                suggested_weights[value] = self._multiplier_to_weight(result.multiplier)
        
        return FactorAnalysis(
            factor_name=factor_name,
            baseline_cpr=baseline_cpr,
            results=results,
            suggested_weights=suggested_weights
        )
    
    def analyze_numeric_factor(self, factor_name: str, config: dict) -> FactorAnalysis:
        """Analyze a numeric factor with bucket ranges (like LTV, FICO)."""
        logger.info(f"Analyzing factor: {factor_name}")
        
        baseline_cpr = self.get_baseline_cpr()
        results = {}
        suggested_weights = {}
        
        buckets = config['buckets']
        labels = config['labels']
        
        for i, label in enumerate(labels):
            if i == 0:
                condition = f"{config['column']} < {buckets[0]}"
            elif i == len(labels) - 1:
                condition = f"{config['column']} >= {buckets[-1]}"
            else:
                condition = f"{config['column']} >= {buckets[i-1]} AND {config['column']} < {buckets[i]}"
            
            result = self._calculate_cpr_for_condition(
                table=config['table'],
                condition=condition,
                label=label,
                baseline_cpr=baseline_cpr
            )
            if result:
                results[label] = result
                suggested_weights[label] = self._multiplier_to_weight(result.multiplier)
        
        return FactorAnalysis(
            factor_name=factor_name,
            baseline_cpr=baseline_cpr,
            results=results,
            suggested_weights=suggested_weights
        )
    
    def _calculate_cpr_for_value(
        self, 
        table: str, 
        column: str, 
        value: str,
        baseline_cpr: float
    ) -> Optional[FactorResult]:
        """Calculate CPR for pools with a specific factor value."""
        
        # This is a simplified query - real implementation needs:
        # 1. Join loan-level to pool-level
        # 2. Calculate CPR from factor changes
        # 3. Control for confounders
        
        query = f"""
        WITH pool_loans AS (
            SELECT DISTINCT pool_id 
            FROM {table} 
            WHERE {column} = :value
        ),
        pool_cpr AS (
            SELECT 
                f.pool_id,
                AVG(
                    CASE WHEN f.factor > 0 AND LAG(f.factor) OVER (PARTITION BY f.pool_id ORDER BY f.as_of_month) > 0
                         THEN (1 - f.factor / LAG(f.factor) OVER (PARTITION BY f.pool_id ORDER BY f.as_of_month)) * 12 * 100
                         ELSE NULL
                    END
                ) as avg_cpr,
                COUNT(DISTINCT f.as_of_month) as months
            FROM fact_pool_month f
            JOIN pool_loans p ON f.pool_id = p.pool_id
            WHERE f.as_of_month >= CURRENT_DATE - INTERVAL '24 months'
            GROUP BY f.pool_id
        )
        SELECT 
            AVG(avg_cpr) as segment_cpr,
            COUNT(*) as pool_count,
            SUM(months) as total_months
        FROM pool_cpr
        WHERE avg_cpr IS NOT NULL
        """
        
        try:
            with self.engine.connect() as conn:
                result = conn.execute(text(query), {'value': value}).fetchone()
                
                if not result or not result[0]:
                    return None
                
                segment_cpr = float(result[0])
                pool_count = int(result[1])
                total_months = int(result[2])
                
                multiplier = segment_cpr / baseline_cpr if baseline_cpr > 0 else 1.0
                
                # Confidence based on sample size
                if pool_count >= 500 and total_months >= 6000:
                    confidence = 'high'
                elif pool_count >= 100 and total_months >= 1200:
                    confidence = 'medium'
                else:
                    confidence = 'low'
                
                return FactorResult(
                    value=value,
                    avg_cpr=segment_cpr,
                    multiplier=multiplier,
                    pool_count=pool_count,
                    month_count=total_months,
                    confidence=confidence
                )
        except Exception as e:
            logger.error(f"Error analyzing {column}={value}: {e}")
            return None
    
    def _calculate_cpr_for_condition(
        self,
        table: str,
        condition: str,
        label: str,
        baseline_cpr: float
    ) -> Optional[FactorResult]:
        """Calculate CPR for pools matching a condition."""
        # Similar to _calculate_cpr_for_value but with condition instead of value
        # Implementation would be similar
        return None  # Placeholder
    
    def _multiplier_to_weight(self, multiplier: float) -> int:
        """
        Convert prepay multiplier to composite score adjustment.
        
        - Multiplier 0.70 (30% slower) = +15 points
        - Multiplier 1.20 (20% faster) = -10 points
        """
        pct_diff = (1.0 - multiplier) * 100
        return int(pct_diff / 2)  # Each 2% = 1 point
    
    def run_all_analyses(self) -> Dict[str, FactorAnalysis]:
        """Run analysis for all configured factors."""
        results = {}
        
        for factor_name, config in self.FACTOR_CONFIGS.items():
            try:
                if 'values' in config:
                    analysis = self.analyze_categorical_factor(factor_name, config)
                elif 'buckets' in config:
                    analysis = self.analyze_numeric_factor(factor_name, config)
                else:
                    logger.warning(f"Unknown config type for {factor_name}")
                    continue
                    
                results[factor_name] = analysis
                self._print_analysis(analysis)
            except Exception as e:
                logger.error(f"Failed to analyze {factor_name}: {e}")
        
        return results
    
    def _print_analysis(self, analysis: FactorAnalysis):
        """Pretty print analysis results."""
        print(f"\n{'='*60}")
        print(f"Factor: {analysis.factor_name}")
        print(f"Baseline CPR: {analysis.baseline_cpr:.2f}%")
        print(f"{'='*60}")
        print(f"{'Value':<15} {'CPR':<8} {'Mult':<8} {'Pools':<8} {'Conf':<8} {'Weight':<8}")
        print(f"{'-'*60}")
        
        for value, result in sorted(analysis.results.items(), key=lambda x: x[1].multiplier):
            weight = analysis.suggested_weights.get(value, 0)
            weight_str = f"+{weight}" if weight > 0 else str(weight)
            print(f"{value:<15} {result.avg_cpr:<8.2f} {result.multiplier:<8.2f} {result.pool_count:<8} {result.confidence:<8} {weight_str:<8}")
    
    def export_weights(self, analyses: Dict[str, FactorAnalysis], output_file: str):
        """Export calibrated weights to a Python config file."""
        lines = [
            '"""',
            'Empirically calibrated prepay factor weights.',
            f'Generated from {sum(a.results[list(a.results.keys())[0]].pool_count for a in analyses.values() if a.results)} pools.',
            '"""',
            '',
            'CALIBRATED_WEIGHTS = {'
        ]
        
        for factor_name, analysis in analyses.items():
            lines.append(f"    '{factor_name}': {{")
            for value, weight in analysis.suggested_weights.items():
                lines.append(f"        '{value}': {weight},")
            lines.append("    },")
        
        lines.append("}")
        
        with open(output_file, 'w') as f:
            f.write('\n'.join(lines))
        
        logger.info(f"Exported weights to {output_file}")


def main():
    parser = argparse.ArgumentParser(description='Analyze prepay factors from Freddie Mac data')
    parser.add_argument('--factor', type=str, help='Specific factor to analyze')
    parser.add_argument('--all', action='store_true', help='Analyze all factors')
    parser.add_argument('--export', type=str, help='Export weights to Python file')
    args = parser.parse_args()
    
    # Get database URL
    db_url = os.getenv('DATABASE_URL')
    if not db_url:
        logger.error("DATABASE_URL not set")
        sys.exit(1)
    
    analyzer = PrepayFactorAnalyzer(db_url)
    
    if args.all:
        analyses = analyzer.run_all_analyses()
        if args.export:
            analyzer.export_weights(analyses, args.export)
    elif args.factor:
        if args.factor not in analyzer.FACTOR_CONFIGS:
            logger.error(f"Unknown factor: {args.factor}")
            logger.info(f"Available factors: {list(analyzer.FACTOR_CONFIGS.keys())}")
            sys.exit(1)
        
        config = analyzer.FACTOR_CONFIGS[args.factor]
        if 'values' in config:
            analysis = analyzer.analyze_categorical_factor(args.factor, config)
        else:
            analysis = analyzer.analyze_numeric_factor(args.factor, config)
        
        analyzer._print_analysis(analysis)
    else:
        parser.print_help()


if __name__ == '__main__':
    main()
