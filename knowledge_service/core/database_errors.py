"""Driver error types shared by persistence and HTTP error handling."""
from psycopg import InterfaceError, OperationalError

__all__ = ['InterfaceError', 'OperationalError']
