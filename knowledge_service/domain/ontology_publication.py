"""Shared contract for rejected atomic ontology publications."""


class OntologyPublicationConflict(ValueError):
    """Stable conflict raised by the atomic ontology publication boundary."""

    def __init__(self, code, message, *, details=None, retryable=False):
        self.code = code
        self.details = dict(details or {})
        self.retryable = retryable
        super().__init__(message)
