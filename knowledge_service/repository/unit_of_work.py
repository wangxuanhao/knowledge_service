"""Public transaction boundary used by application services.

The repository keeps savepoints, isolation and rollback mechanics private.  A
service can own a multi-step use case by entering this seam without reaching
for ``Repository._transaction`` or the database connection.
"""


class UnitOfWork:
    def __init__(self, repository, *, serializable=False):
        self._repository = repository
        self._serializable = bool(serializable)
        self._transaction = None

    def __enter__(self):
        self._transaction = self._repository._transaction(
            serializable=self._serializable)
        self._transaction.__enter__()
        return self

    def __exit__(self, exc_type, exc, traceback):
        return self._transaction.__exit__(exc_type, exc, traceback)
