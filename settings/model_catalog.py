"""Dated, offline pricing guidance; model metadata does not contain API prices.

Only exact model IDs have verified rates. Never infer prices from family names,
snapshots, or fine-tuned base models. This is an illustration, not billing data.
Sources: https://developers.openai.com/api/docs/pricing and the model pages.
"""

from dataclasses import dataclass
from decimal import Decimal

PRICING_URL = "https://developers.openai.com/api/docs/pricing"
PRICING_CHECKED = "3 November 2026"
# ECB quotes USD per EUR; divide USD amounts by this rate to display euros.
# Bundled reference data, not a live exchange-rate feed or a billing quote.
USD_PER_EUR = Decimal("1.1179")
FX_DATE = "3 November 2026"
FX_SOURCE_URL = "https://www.ecb.europa.eu/stats/policy_and_exchange_rates/euro_reference_exchange_rates/html/index.en.html"
EUR_ESTIMATE_NOTE = (
    f"EUR estimates converted from USD using the ECB rate on {FX_DATE}: "
    f"€1 = {USD_PER_EUR} USD. Not a live rate; excludes taxes and conversion fees."
)
EXAMPLE_INPUT_TOKENS = 10_000
EXAMPLE_OUTPUT_TOKENS = 2_000
STARTER_MODELS = ("gpt-6-luna", "gpt-5.6-luna", "gpt-6.1-sol", "gpt-6-astra")
PRICE_FLAG_LEGEND = (
    "€ Budget · €€ Balanced · €€€ Higher cost · ? Price unknown. "
    "LogOpen compares the same example usage: up to €0.01, up to €0.05, or above €0.05 per analysis."
)


@dataclass(frozen=True)
class ModelGuide:
    tier: str
    description: str
    input_per_million: Decimal
    output_per_million: Decimal
    cached_input_per_million: Decimal | None = None
    source_url: str = PRICING_URL

    def example_cost(self, analyses=1):
        """Illustrate standard short-context USD cost for the same billed token use."""
        return analyses * (self.input_per_million * EXAMPLE_INPUT_TOKENS
                           + self.output_per_million * EXAMPLE_OUTPUT_TOKENS) / 1_000_000


# Standard text rates in USD / 1M tokens: input, output, cached input.
# Keep exact IDs: the models endpoint supplies availability, not prices. This
# catalog is deliberately independent of starter suggestions and compatibility.
_STANDARD_RATES = {
    "gpt-6-astra": ("10", "50", "1"),
    "gpt-6.1-sol": ("2", "10", ".10"),
    "gpt-6.1-terra": ("1", "5", ".05"),
    "gpt-6-luna": (".10", ".50", ".01"),
    "gpt-6-sol": ("2", "10", ".20"),
    "gpt-5.6-sol": ("4", "20", ".40"),
    "gpt-5.6-terra": ("2", "12", ".20"),
    "gpt-5.6-luna": (".20", "1.20", ".02"),
    "gpt-5.5": ("5", "30", ".50"),
    "gpt-5.5-pro": ("30", "180", None),
    "gpt-5.4": ("2.50", "15", ".25"),
    "gpt-5.4-pro": ("30", "180", None),
    "gpt-5.4-mini": (".75", "4.50", ".075"),
    "gpt-5.4-nano": (".20", "1.25", ".02"),
    "gpt-5.2": ("1.75", "14", ".175"),
    "gpt-5.2-pro": ("21", "168", None),
    "gpt-5.1": ("1.25", "10", ".125"),
    "gpt-5": ("1.25", "10", ".125"),
    "gpt-5-mini": (".25", "2", ".025"),
    "gpt-5-nano": (".05", ".40", ".005"),
    "gpt-5-pro": ("15", "120", None),
    "gpt-4.1": ("2", "8", ".50"),
    "gpt-4.1-mini": (".40", "1.60", ".10"),
    "gpt-4o": ("2.50", "10", "1.25"),
    "gpt-4o-mini": (".15", ".60", ".075"),
    "o3-pro": ("20", "80", None),
    "o3": ("2", "8", ".50"),
    "gpt-5.6-cyber": ("12.50", "75", "1.25"),
    "gpt-5.5-cyber": ("12.50", "75", "1.25"),
    "gpt-6-rosalind": ("15", "75", "1.50"),
    "gpt-rosalind-research": ("5", "25", ".50"),
    "gpt-rosalind-discovery": ("5", "25", ".50"),
    "gpt-6-astra-law": ("10", "50", "1"),
}

# Additional text rates checked on each official /api/docs/models/{id} page.
_MODEL_PAGE_RATES = {
    "gpt-4.1-nano": (".10", ".40", ".025"),
    "o4-mini": ("1.10", "4.40", ".275"),
    "o3-mini": ("1.10", "4.40", ".55"),
    "o1": ("15", "60", "7.50"),
    "o1-mini": ("1.10", "4.40", ".55"),
    "o1-pro": ("150", "600", None),
    "gpt-4-turbo": ("10", "30", None),
    "gpt-4": ("30", "60", None),
    "gpt-3.5-turbo": (".50", "1.50", None),
}


def _guide(rates, source_url=PRICING_URL):
    input_rate, output_rate, cached_rate = (Decimal(rate) if rate is not None else None for rate in rates)
    example = (input_rate * EXAMPLE_INPUT_TOKENS + output_rate * EXAMPLE_OUTPUT_TOKENS) / 1_000_000 / USD_PER_EUR
    tier = "Budget" if example <= Decimal(".01") else "Balanced" if example <= Decimal(".05") else "Higher cost"
    description = {
        "Budget": "Lower cost for the example usage below.",
        "Balanced": "Mid-range cost for the example usage below.",
        "Higher cost": "Higher cost for the same example usage. Review the estimate before analysing.",
    }[tier]
    return ModelGuide(tier, description, input_rate, output_rate, cached_rate, source_url)


MODEL_GUIDES = {model: _guide(rates) for model, rates in _STANDARD_RATES.items()}
MODEL_GUIDES.update({
    model: _guide(rates, f"https://developers.openai.com/api/docs/models/{model}")
    for model, rates in _MODEL_PAGE_RATES.items()
})


def model_guide(model):
    return MODEL_GUIDES.get(model.strip())


def cost_label(model):
    guide = model_guide(model)
    return guide.tier if guide else "Price unknown"


def price_flag(model):
    tier = cost_label(model)
    symbol = {"Budget": "€", "Balanced": "€€", "Higher cost": "€€€", "Price unknown": "?"}[tier]
    return f"{symbol} {tier}"


def model_rates(model):
    guide = model_guide(model)
    if guide is None:
        return "Rates not verified for this exact model ID."
    cached = (f" · {eur_from_usd(guide.cached_input_per_million)} cached input"
              if guide.cached_input_per_million is not None else "")
    return (f"Estimated EUR per 1M tokens: {eur_from_usd(guide.input_per_million)} input / "
            f"{eur_from_usd(guide.output_per_million)} output{cached}. Standard, short context.")


def eur_from_usd(value):
    """Convert once at display time, preserving sub-cent estimates to four places."""
    whole, fraction = format(value / USD_PER_EUR, ".4f").split(".")
    return f"€{whole}.{fraction.rstrip('0').ljust(2, '0')}"


def model_tooltip(model):
    guide = model_guide(model)
    if guide is None:
        return "Price unknown. Check OpenAI pricing before using this model; unknown does not mean free."
    return (f"{price_flag(model)}. {model_rates(model)} "
            f"Example analysis: approximately {eur_from_usd(guide.example_cost())}. "
            f"Rates checked {PRICING_CHECKED}; longer context and other service tiers may cost more. "
            f"{EUR_ESTIMATE_NOTE} "
            f"Source: {guide.source_url}")
