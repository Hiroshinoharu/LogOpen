"""Dated, offline pricing guidance; model metadata does not contain API prices.

Only exact model IDs have verified rates. Never infer prices from family names,
snapshots, or fine-tuned base models. This is an illustration, not billing data.
Sources: https://developers.openai.com/api/docs/pricing and the model pages.
"""

from dataclasses import dataclass
from decimal import Decimal

PRICING_URL = "https://developers.openai.com/api/docs/pricing"
PRICING_CHECKED = "21 October 2026"
EXAMPLE_INPUT_TOKENS = 10_000
EXAMPLE_OUTPUT_TOKENS = 2_000


@dataclass(frozen=True)
class ModelGuide:
    tier: str
    description: str
    input_per_million: Decimal
    output_per_million: Decimal

    def example_cost(self, analyses=1):
        """Illustrate standard short-context USD cost for the same billed token use."""
        return analyses * (self.input_per_million * EXAMPLE_INPUT_TOKENS
                           + self.output_per_million * EXAMPLE_OUTPUT_TOKENS) / 1_000_000


MODEL_GUIDES = {
    "gpt-6-luna": ModelGuide("Budget", "The lowest-cost starter choice for routine explanations.",
                             Decimal("0.10"), Decimal("0.50")),
    "gpt-5.6-luna": ModelGuide("Budget", "A cost-focused choice. GPT-6 Luna has lower published text rates.",
                               Decimal("0.20"), Decimal("1.20")),
    "gpt-6.1-sol": ModelGuide("Balanced", "For more complex questions, with a higher cost than the budget choices.",
                              Decimal("2.00"), Decimal("10.00")),
    "gpt-6-astra": ModelGuide("Higher cost", "For demanding analysis. The same amount of text costs more.",
                              Decimal("10.00"), Decimal("50.00")),
}


def model_guide(model):
    return MODEL_GUIDES.get(model.strip())


def cost_label(model):
    guide = model_guide(model)
    return guide.tier if guide else "Price unknown"


def usd(value):
    """Keep small illustrative amounts visible without rounding them to zero."""
    whole, fraction = format(value, ".4f").split(".")
    return f"${whole}.{fraction.rstrip('0').ljust(2, '0')}"


def model_tooltip(model):
    guide = model_guide(model)
    if guide is None:
        return "Price unknown. Check OpenAI pricing before using this model; unknown does not mean free."
    return (f"{guide.tier}. {guide.description} Standard USD per 1M tokens: "
            f"${guide.input_per_million:.2f} input / ${guide.output_per_million:.2f} output. "
            f"Rates checked {PRICING_CHECKED}; actual charges vary.")
