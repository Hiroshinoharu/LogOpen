"""Documented capabilities are distinct from paid end-to-end testing/account access."""
# Official model pages checked 3 November 2026: /api/docs/models/{exact_id}.
# No prefix matching for snapshots or fine tunes.
DOCUMENTED_MODELS = frozenset({"gpt-5.6-luna", "gpt-4.1-mini", "gpt-4.1", "gpt-4o-mini"})
SUGGESTED_MODEL = "gpt-5.6-luna"


def compatibility_label(model):
    return "Documented support" if model.strip() in DOCUMENTED_MODELS else "Compatibility unverified"


def compatibility_hint(model):
    if model.strip() in DOCUMENTED_MODELS:
        return ("OpenAI documents Responses and structured outputs for this exact model. "
                "Not live-tested by LogOpen on your account; permissions and quota still apply. "
                f"Source: https://developers.openai.com/api/docs/models/{model.strip()}")
    return ("Compatibility unverified for this exact ID. Appearing in your account's model list "
            "does not confirm support for LogOpen's structured analysis. Try the suggested model if analysis fails.")
