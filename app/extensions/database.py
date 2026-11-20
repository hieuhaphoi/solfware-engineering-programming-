from typing import Optional
import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool
from flask_sqlalchemy import SQLAlchemy
from app.config import Config

# SQLAlchemy ORM instance for Flask MVC Models
db = SQLAlchemy()


class DatabaseConnectionSingleton:
    """
    <<Singleton>> Database Connection Pool Manager for PostgreSQL (Psycopg 3).
    Ensures only a single shared connection pool instance is instantiated
    throughout the application lifecycle (Section V.2 - Singleton Pattern).
    """

    _instance: Optional["DatabaseConnectionSingleton"] = None
    _connection_pool: Optional[ConnectionPool] = None

    def __new__(cls) -> "DatabaseConnectionSingleton":
        if cls._instance is None:
            cls._instance = super(DatabaseConnectionSingleton, cls).__new__(cls)
        return cls._instance

    def init_pool(self, min_size: int = 1, max_size: int = 20) -> None:
        """Initialize the PostgreSQL connection pool if not already created."""
        if self._connection_pool is None:
            conninfo = (
                f"host={Config.DB_HOST} "
                f"port={Config.DB_PORT} "
                f"dbname={Config.DB_NAME} "
                f"user={Config.DB_USER} "
                f"password={Config.DB_PASSWORD}"
            )
            self._connection_pool = ConnectionPool(
                conninfo=conninfo,
                min_size=min_size,
                max_size=max_size,
                kwargs={"row_factory": dict_row},
            )

    def get_connection(self):
        """Acquire a connection from the shared PostgreSQL pool."""
        if self._connection_pool is None:
            self.init_pool()
        return self._connection_pool.getconn()

    def release_connection(self, conn) -> None:
        """Return a connection back to the pool."""
        if self._connection_pool is not None and conn is not None:
            self._connection_pool.putconn(conn)

    def close_all(self) -> None:
        """Close all connections in the pool on application shutdown."""
        if self._connection_pool is not None:
            self._connection_pool.close()
            self._connection_pool = None


# Shared Singleton instance
pg_manager = DatabaseConnectionSingleton()
