class DomainError(Exception):
    def __init__(self, message, code="validation", status=400, details=None):
        super().__init__(message)
        self.message = message
        self.code = code
        self.status = status
        self.details = details or {}


def reject(condition, message, code="validation", status=400, details=None):
    if condition:
        raise DomainError(message, code, status, details)
