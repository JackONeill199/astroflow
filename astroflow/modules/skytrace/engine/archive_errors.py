"""Gemeinsame Ausnahme der Archiv- und Katalogabfragen (status = needs_archive, not_checkable, service_unavailable …)."""


class ArchiveAccessError(Exception):
    def __init__(self,status,detail):self.status=status;super().__init__(detail)
