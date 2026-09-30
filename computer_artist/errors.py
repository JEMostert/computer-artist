"""Shared interruption signals with portable, read-only recovery evidence."""

import json


class Interrupted(RuntimeError):
    def __init__(self, reason, *, code='interrupted', details=None):
        super().__init__(reason)
        if not isinstance(code, str) or not 1 <= len(code) <= 64:
            raise ValueError('Interruption code must contain 1–64 characters')
        if details is not None and not isinstance(details, dict):
            raise ValueError('Interruption details must be a JSON object')
        self.code = code
        # Evidence must survive exception handling and JSON result publication.
        self.details = json.loads(json.dumps(details or {}, allow_nan=False))

    def as_dict(self):
        return {'code': self.code, 'reason': str(self), 'details': self.details}


class YieldToAgent(Interrupted):
    pass
