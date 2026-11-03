"""Local sharing preferences. Missing/corrupt consent never enables automatic AI."""
from dataclasses import dataclass
from PyQt6.QtCore import QSettings
from settings import app_settings


@dataclass(frozen=True)
class PrivacyOptions:
    mask_identifiers: bool = True
    automatic_sharing: bool = False


class PrivacyPreferences:
    def __init__(self, storage=None):
        self.storage = storage if storage is not None else app_settings._create_qsettings()

    def load(self):
        try:
            self.storage.sync()
            if self.storage.status() != QSettings.Status.NoError:
                return PrivacyOptions()
            # Only an explicit false disables masking; unknown values keep it on.
            mask = self.storage.value("privacy/mask_identifiers", True)
            return PrivacyOptions(str(mask).lower() not in ("false", "0"),
                                  app_settings._enabled(self.storage.value("privacy/automatic_sharing", False)))
        except Exception:
            return PrivacyOptions()

    def save(self, options):
        if type(options.mask_identifiers) is not bool or type(options.automatic_sharing) is not bool:
            raise ValueError("Choose valid privacy options.")
        try:
            self.storage.setValue("privacy/mask_identifiers", options.mask_identifiers)
            self.storage.setValue("privacy/automatic_sharing", options.automatic_sharing)
            self.storage.sync()
            if self.storage.status() != QSettings.Status.NoError:
                raise RuntimeError()
        except Exception:
            raise RuntimeError("Could not save privacy preferences.") from None
