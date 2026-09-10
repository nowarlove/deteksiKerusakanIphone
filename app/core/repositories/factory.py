import os

from ..supabase_store import SupabaseStore
from .mysql_repository import MySQLRepository
from .null_repository import NullRepository


def create_repository():
    backend = os.environ.get("DATABASE_BACKEND", "supabase").strip().lower()
    if backend == "mysql":
        repository = MySQLRepository()
        return repository if repository.configured else NullRepository("mysql_not_configured")
    if backend == "supabase":
        repository = SupabaseStore()
        return repository if repository.configured else NullRepository("supabase_not_configured")
    return NullRepository("database_backend_not_supported")
