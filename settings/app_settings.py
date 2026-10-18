"""Store only non-secret AI preferences in the current user's QSettings."""

from dataclasses import dataclass
import re

from PyQt6.QtCore import QSettings

import config

MIN_ANALYSES = 1
MAX_ANALYSES = 20


class SettingsError(RuntimeError):
    """A settings failure with a fixed, safe user-facing message."""


@dataclass(frozen=True)
class AISettings:
    """Immutable preference snapshot: enabled flag, model name, and scan request limit.

    AppSettings validates loaded/saved values. Constructing this dataclass alone
    does not enforce the annotations or validate the model identifier.
    """
    enabled: bool
    model: str
    max_analyses: int


def validate_model(value):
    """Return a trimmed model identifier after local syntax validation.

    Accepts 1-200 characters using letters, digits, dots, underscores, colons,
    and hyphens, starting with a letter/digit. Rejects apparent sk- credentials.
    Raises ValueError for invalid input; does not verify API model availability.
    """
    if not isinstance(value, str):
        raise ValueError("Enter a valid OpenAI model name.")
    value = value.strip()
    if value.startswith("sk-") or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,199}", value):
        raise ValueError("Enter a valid OpenAI model name.")
    return value


def _limit(value, default=3):
    """Parse an integer-like preference and clamp it to 1-20, or return default.

    Booleans, fractional text, and failed conversions use the supplied default.
    Callers provide a valid default; it is not independently clamped here.
    """
    try:
        if isinstance(value, bool) or not re.fullmatch(r"-?\d+", str(value)):
            return default
        return max(MIN_ANALYSES, min(MAX_ANALYSES, int(value)))
    except (ValueError, TypeError, OverflowError):
        return default


def _enabled(value):
    # Unknown/corrupt values must not accidentally enable paid API requests.
    """Accept only True, integer 1, or case-insensitive text true/1 as enabled.

    Every other value disables AI so corrupted preferences cannot enable it.
    """
    return value is True or (type(value) is int and value == 1) or (
        isinstance(value, str) and value.strip().lower() in ("true", "1")
    )


def _create_qsettings():
    """Create current-user native storage for organization/application LogOpen.

    On Windows this uses the registry. Disabling QSettings fallbacks prevents
    organization-wide/system defaults from overriding missing user preferences.
    """
    settings = QSettings(QSettings.Format.NativeFormat, QSettings.Scope.UserScope,
                         "LogOpen", "LogOpen")
    settings.setFallbacksEnabled(False)
    return settings


class AppSettings:
    """Load and persist non-secret AI preferences through a QSettings-like backend.

    API keys are managed separately by credential_store. load recovers invalid
    saved values, while save rejects invalid new preferences and reports errors.
    """
    def __init__(self, storage=None):
        """Use an injected storage backend, or create native current-user QSettings.

        Injection allows tests to use temporary INI files or simulate storage errors.
        """
        self._storage = storage if storage is not None else _create_qsettings()

    def load(self):
        """Return validated AISettings, using config defaults for missing preferences.

        Synchronizes storage first. Invalid models/limits recover to valid defaults;
        unrecognized enabled values and unreadable storage disable AI. The method
        does not rewrite repaired values or read credentials.
        """
        default_limit = _limit(config.MAX_LLM_ANALYSES_PER_RUN)
        try:
            default_model = validate_model(config.DEFAULT_ANALYSIS_MODEL)
        except ValueError:
            default_model = "gpt-5.6-luna"
        try:
            self._storage.sync()
            if self._storage.status() != QSettings.Status.NoError:
                return AISettings(False, default_model, default_limit)
            enabled = _enabled(self._storage.value("ai/enabled", config.ENABLE_LLM_ANALYSIS))
            try:
                model = validate_model(self._storage.value("ai/model", default_model))
            except ValueError:
                model = default_model
            limit = _limit(self._storage.value("ai/max_analyses", default_limit), default_limit)
            return AISettings(enabled, model, limit)
        except Exception:
            return AISettings(False, default_model, default_limit)

    def save(self, preferences):
        """Validate preferences, write the three AI values, and synchronize storage.

        Args:
            preferences: AISettings with an actual bool enabled value, valid model
                identifier, and actual int max_analyses between 1 and 20.

        Raises:
            ValueError: A preference is invalid; validation precedes all writes.
            SettingsError: Writing or synchronizing preferences fails.

        Returns None. Model whitespace is trimmed; credentials are never stored here.
        """
        model = validate_model(preferences.model)
        if type(preferences.enabled) is not bool:
            raise ValueError("Choose whether AI analysis is enabled.")
        if type(preferences.max_analyses) is not int or not MIN_ANALYSES <= preferences.max_analyses <= MAX_ANALYSES:
            raise ValueError(f"Choose between {MIN_ANALYSES} and {MAX_ANALYSES} analyses per scan.")
        try:
            self._storage.setValue("ai/enabled", preferences.enabled)
            self._storage.setValue("ai/model", model)
            self._storage.setValue("ai/max_analyses", preferences.max_analyses)
            self._storage.sync()
            if self._storage.status() != QSettings.Status.NoError:
                raise SettingsError("Could not save AI settings.")
        except Exception:
            raise SettingsError("Could not save AI settings.") from None
