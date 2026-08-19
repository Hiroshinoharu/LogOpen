"""Classification helpers for providers, events, and incidents."""

from config import PROVIDER_CLASSIFICATIONS


def classify_provider(provider):
    """Return a readable label for a provider name."""

    if provider in PROVIDER_CLASSIFICATIONS:
        return PROVIDER_CLASSIFICATIONS[provider]

    cleaned_provider = provider
    for prefix in ("Microsoft-Windows-", "Microsoft-", "Windows-"):
        if cleaned_provider.startswith(prefix):
            cleaned_provider = cleaned_provider[len(prefix):]
            break

    return cleaned_provider.replace("-", " ")


# cspell:ignore dcom
def classify_dcom_event(event_id, message):
    """Classify a DCOM event."""

    if event_id == 10005 and "gamingservices" in message:
        return "Gaming Services DCOM Startup Failure"
    if event_id == 10010:
        return "DCOM Server Start Timeout"
    if event_id != 10016:
        return "DCOM Issue"

    permission_labels = {
        "windows.securitycenter": "Security Center DCOM Permission Warning",
        "openai.chatgpt-desktop": "ChatGPT Desktop DCOM Permission Warning",
        "openai.codex": "Codex Permission Warning",
    }
    return next(
        (label for token, label in permission_labels.items() if token in message),
        "DCOM Permission Warning",
    )


def classify_service_event(message):
    """Classify a Service Control Manager event."""

    if "microsoft defender antivirus service" in message:
        return "Defender Service Crash"
    return "Service Terminated Unexpectedly"


def classify_event(event):
    """Return an incident-style classification for a single event."""

    provider = event["provider"]
    event_id = event["event_id"]
    message = event["message"].lower()

    provider_classifications = {
        ("Microsoft-Windows-DNS-Client", 1014): "DNS Resolution Timeout",
        ("Microsoft-Windows-WLAN-AutoConfig", 10002): "WLAN Module Stopped",
        ("Tcpip", 4266): "UDP Ephemeral Port Exhaustion",
    }
    classification = provider_classifications.get((provider, event_id))
    if classification:
        return classification
    if provider == "DCOM":
        return classify_dcom_event(event_id, message)
    if provider == "Service Control Manager" and event_id == 7031:
        return classify_service_event(message)
    if provider == "winsrvext" and "delaying system shutdown" in message:
        return "Application Delayed System Shutdown"
    return classify_provider(provider)


def classify_incident(incident):
    """
    Classify every provider in an incident to something more readable.

    Args:
        incident (dict): An incident summary dictionary.
    """

    return {
        provider: classify_provider(provider)
        for provider in incident["providers"]
    }
