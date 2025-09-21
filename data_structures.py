from dataclasses import dataclass, field
from typing import Dict, Any

@dataclass
class ReboundCandidate:
    ticker: str
    scenario: str
    rebound_score: int
    technical_score: int = 0
    fundamental_score: int = 0
    market_context_score: int = 0
    technicals: Dict[str, Any] = field(default_factory=dict)
    fundamentals: Dict[str, Any] = field(default_factory=dict)
    score_breakdown: Dict[str, Any] = field(default_factory=dict)
    # NOTE: Storing the entire DataFrame is memory-intensive, but it's a deliberate
    # trade-off to guarantee that the data plotted in the chart is the *exact*
    # same data used to generate the scan results, preventing inconsistencies.
    history_df: Any = None
    # Example for technicals: {'price': 150.5, 'rsi': 45, 'support_level': 148.0}
    # Example for fundamentals: {'eps_growth': 0.15, 'revenue_growth': 0.08, 'debt_to_equity': 0.4}
