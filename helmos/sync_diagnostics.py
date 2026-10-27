"""Allowlisted sync evidence; never retain exception text or raw remote payloads."""

import json
import ssl
from dataclasses import dataclass, field

from .social_connectors import ConnectorError
from .social_import import SocialImportError


@dataclass
class SyncDiagnostics:
    context: dict = field(default_factory=dict)

    def stage(self, name):
        self.context["stage"] = name

    def failure(self, error):
        if isinstance(error, ConnectorError):
            code = error.code
        else:
            code = {
                "state_directory": "scheduled_state_path_failed",
                "execution_lock": "scheduled_lock_failed",
                "execution_unlock": "scheduled_unlock_failed",
                "journal_recovery": "scheduled_record_recovery_failed",
                "journal_start": "scheduled_record_start_failed",
                "journal_finish": "scheduled_record_finish_failed",
                "credentials_lock": "credential_lock_unexpected",
                "credentials_load": "credential_load_unexpected",
                "credentials_identity": "credential_identity_unexpected",
                "credentials_refresh": "credential_refresh_unexpected",
                "credentials_save": "credential_save_unexpected",
                "credentials_unlock": "credential_unlock_unexpected",
                "data_lock": "sync_lock_failed",
                "data_unlock": "sync_unlock_failed",
                "history_read": "sync_history_read_failed",
                "collection": "sync_collection_failed",
                "observations_write": "sync_observations_write_failed",
                "result": "sync_result_failed",
            }.get(self.context.get("stage"), "scheduled_record_failed")
        category = "unexpected"
        for kind, label in ((ConnectorError, "connector"), (SocialImportError, "social_import"),
                            (json.JSONDecodeError, "invalid_json"), (ssl.SSLError, "tls"),
                            (TimeoutError, "timeout"), (PermissionError, "permission"),
                            (OSError, "os_error"), (KeyError, "missing_key"),
                            (TypeError, "type_error"), (ValueError, "value_error")):
            if isinstance(error, kind):
                category = label
                break
        result = dict(self.context, code=code, category=category)
        if isinstance(error, OSError):
            for key in ("errno", "winerror"):
                value = getattr(error, key, None)
                if type(value) is int:
                    result[key] = value
        return result
