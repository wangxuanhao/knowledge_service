"""Stable user-domain values shared by HTTP and persistence."""

ROLES = ('superadmin', 'admin', 'viewer')


class UserAlreadyExists(ValueError):
    pass
