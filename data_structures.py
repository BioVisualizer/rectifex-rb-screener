from dataclasses import dataclass, field
from typing import Dict, Any, Optional
import config
import pandas as pd

def safe_get(data: dict, key: str, default: Optional[Any] = config.SAFE_GET_DEFAULT) -> Optional[Any]:
    """
    Safely retrieves a value from a dictionary. Returns the default value if the
    key is missing or the retrieved value is None, not a number, or NaN.
    This prevents TypeErrors or ValueErrors during calculations.
    """
    if not isinstance(data, dict):
        return default

    value = data.get(key, default)

    if value is None:
        return default

    if isinstance(value, (int, float)):
        if pd.isna(value):
            return default
        return value

    return value


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
