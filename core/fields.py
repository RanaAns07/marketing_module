import json
from django.db import models
from core.crypto import decrypt_value, encrypt_value


class EncryptedTextField(models.TextField):
    def get_prep_value(self, value):
        value = super().get_prep_value(value)
        if value is None or value == "":
            return value
        return encrypt_value(str(value))

    def from_db_value(self, value, expression, connection):
        if value is None or value == "":
            return value
        return decrypt_value(value)


class EncryptedJSONField(models.TextField):
    def get_prep_value(self, value):
        if value is None:
            return None
        if isinstance(value, str):
            raw = value
        else:
            raw = json.dumps(value)
        return encrypt_value(raw)

    def from_db_value(self, value, expression, connection):
        if value is None or value == "":
            return {}
        decrypted = decrypt_value(value)
        if not decrypted:
            return {}
        try:
            return json.loads(decrypted)
        except Exception:
            return {}
