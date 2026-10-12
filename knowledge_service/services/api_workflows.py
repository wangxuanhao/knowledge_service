"""Application-facing workflows used by the HTTP layer.

This module is the public boundary between transport code and persistence.  It
deliberately exposes the repository's already-stable application operations
while keeping the concrete repository type, connection and private primitives
out of route modules.  Larger use cases can be moved behind explicit methods
without changing their HTTP contracts.
"""
from __future__ import annotations


class ApplicationData:
    """Public application data port.

    ``Repository`` remains an implementation detail of the service layer.  The
    explicit transaction helpers below are the only write orchestration seams;
    ordinary public repository operations are forwarded to preserve the
    existing API while route-owned use cases are incrementally deepened.
    """

    def __init__(self, repository):
        self.__repository = repository

    @property
    def backend_label(self):
        return self.__repository.backend_label

    @property
    def ontology_drafts_store(self):
        """Public store port required by the governed ontology workflow."""
        return self.__repository._ontology_drafts

    def unit_of_work(self, *, serializable=False):
        return self.__repository.unit_of_work(serializable=serializable)

    def insert_if_absent(self, project_id, record):
        return self.__repository.insert_if_absent(project_id, record)

    def __getattr__(self, name):
        if name.startswith('_'):
            raise AttributeError(name)
        return getattr(self.__repository, name)
