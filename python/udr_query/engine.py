"""
QueryEngine - SQL queries over versioned data with time travel.

The engine provides:
1. SQL interface via DuckDB
2. Time travel - query any historical version
3. Table registration caching for performance
4. Multiple result formats (Arrow, pandas, dict)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional, Dict, List, Any, Union

import duckdb
import pyarrow as pa

from .reader import TableReader
from .writer import TableWriter, WriteResult

if TYPE_CHECKING:
    import pandas as pd


@dataclass
class RegisteredTable:
    """Tracks a table registered with DuckDB."""
    table_name: str
    version: int
    arrow_table: pa.Table


@dataclass
class QueryResult:
    """Result of a SQL query."""
    arrow_table: pa.Table
    row_count: int
    column_names: List[str]

    def to_pandas(self) -> "pd.DataFrame":
        """Convert result to pandas DataFrame."""
        return self.arrow_table.to_pandas()

    def to_dict(self) -> List[Dict[str, Any]]:
        """Convert result to list of dictionaries."""
        return self.arrow_table.to_pylist()

    def to_arrow(self) -> pa.Table:
        """Return as Arrow Table."""
        return self.arrow_table


class QueryEngine:
    """
    SQL query engine with time travel support.

    Provides a DuckDB-based SQL interface over versioned UDR tables.
    Tables are loaded on-demand and cached for performance.

    Example:
        >>> from udr import PyChunkStore, PyCatalog
        >>> from udr_query import QueryEngine
        >>>
        >>> store = PyChunkStore("./data/chunks")
        >>> catalog = PyCatalog("./data/catalog")
        >>> engine = QueryEngine(store, catalog)
        >>>
        >>> # Query latest version
        >>> result = engine.query("SELECT * FROM users WHERE age > 21")
        >>> df = result.to_pandas()
        >>>
        >>> # Time travel - query version 5 of users table
        >>> result = engine.query(
        ...     "SELECT * FROM users WHERE age > 21",
        ...     versions={"users": 5}
        ... )
        >>>
        >>> # Compare two versions
        >>> v1 = engine.query("SELECT COUNT(*) as cnt FROM users", versions={"users": 1})
        >>> v2 = engine.query("SELECT COUNT(*) as cnt FROM users", versions={"users": 2})

    Note:
        Table names in queries are case-insensitive but stored lowercase.
    """

    def __init__(
        self,
        store,  # PyChunkStore
        catalog,  # PyCatalog
        verify_integrity: bool = False,
    ):
        """
        Initialize the QueryEngine.

        Args:
            store: PyChunkStore instance for content-addressable storage
            catalog: PyCatalog instance for version metadata
            verify_integrity: If True, verify chunk hashes on read
        """
        self.store = store
        self.catalog = catalog
        self.reader = TableReader(store, catalog, verify_integrity)
        self.writer = TableWriter(store, catalog)

        # DuckDB connection (in-memory)
        self._conn = duckdb.connect(":memory:")

        # Cache of registered tables: (table_name, version) -> RegisteredTable
        self._registered: Dict[tuple, RegisteredTable] = {}

    def query(
        self,
        sql: str,
        versions: Optional[Dict[str, int]] = None,
        params: Optional[List[Any]] = None,
    ) -> QueryResult:
        """
        Execute a SQL query with optional time travel.

        Args:
            sql: SQL query string
            versions: Dict mapping table names to specific versions.
                      If a table is not in this dict, latest version is used.
            params: Optional query parameters for prepared statements

        Returns:
            QueryResult with Arrow table and metadata

        Raises:
            IOError: If a referenced table doesn't exist
            duckdb.Error: If SQL is invalid

        Example:
            >>> # Query latest
            >>> engine.query("SELECT * FROM users")
            >>>
            >>> # Time travel
            >>> engine.query("SELECT * FROM users", versions={"users": 5})
            >>>
            >>> # Multiple tables at different versions
            >>> engine.query(
            ...     "SELECT u.name, o.total FROM users u JOIN orders o ON u.id = o.user_id",
            ...     versions={"users": 5, "orders": 10}
            ... )
        """
        versions = versions or {}

        # Extract table names from query
        table_names = self._extract_table_names(sql)

        # Register each table with the appropriate version
        for table_name in table_names:
            version = versions.get(table_name)  # None means latest
            self._ensure_registered(table_name, version)

        # Execute query
        if params:
            result = self._conn.execute(sql, params)
        else:
            result = self._conn.execute(sql)

        arrow_table = result.fetch_arrow_table()

        return QueryResult(
            arrow_table=arrow_table,
            row_count=arrow_table.num_rows,
            column_names=arrow_table.column_names,
        )

    def query_pandas(
        self,
        sql: str,
        versions: Optional[Dict[str, int]] = None,
        params: Optional[List[Any]] = None,
    ) -> "pd.DataFrame":
        """Execute query and return pandas DataFrame."""
        return self.query(sql, versions, params).to_pandas()

    def query_arrow(
        self,
        sql: str,
        versions: Optional[Dict[str, int]] = None,
        params: Optional[List[Any]] = None,
    ) -> pa.Table:
        """Execute query and return Arrow Table."""
        return self.query(sql, versions, params).to_arrow()

    def write_table(
        self,
        table_name: str,
        data: Union["pd.DataFrame", pa.Table],
        metadata: Optional[Dict[str, str]] = None,
    ) -> WriteResult:
        """
        Write data as a new version of a table.

        Convenience method that wraps TableWriter.

        Args:
            table_name: Name of the table
            data: DataFrame or Arrow Table to write
            metadata: Optional metadata for this version

        Returns:
            WriteResult with version info
        """
        result = self.writer.write(table_name, data, metadata)

        # Invalidate cache for this table (force reload on next query)
        self._invalidate_cache(table_name)

        return result

    def list_tables(self) -> List[str]:
        """List all tables in the catalog."""
        return self.reader.list_tables()

    def list_versions(self, table_name: str) -> List[int]:
        """List all versions of a table."""
        return self.reader.list_versions(table_name)

    def get_table_info(self, table_name: str, version: Optional[int] = None) -> Dict[str, Any]:
        """
        Get information about a table version.

        Args:
            table_name: Name of the table
            version: Specific version (None for latest)

        Returns:
            Dict with table metadata and schema info
        """
        metadata = self.reader.get_metadata(table_name, version)

        # Load schema from first chunk
        arrow_table = self.reader.read_arrow(table_name, version)

        return {
            "table_name": metadata.table_name,
            "version": metadata.version,
            "chunk_count": metadata.chunk_count,
            "row_count": arrow_table.num_rows,
            "created_at": metadata.created_at,
            "parent_version": metadata.parent_version,
            "schema": {
                field.name: str(field.type)
                for field in arrow_table.schema
            },
        }

    def diff_versions(
        self,
        table_name: str,
        version_a: int,
        version_b: int,
        key_columns: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        Compare two versions of a table.

        Args:
            table_name: Name of the table
            version_a: First version to compare
            version_b: Second version to compare
            key_columns: Columns to use as primary key for row matching.
                        If None, compares aggregate statistics only.

        Returns:
            Dict with comparison results
        """
        table_a = self.reader.read_arrow(table_name, version_a)
        table_b = self.reader.read_arrow(table_name, version_b)

        result = {
            "table_name": table_name,
            "version_a": version_a,
            "version_b": version_b,
            "rows_a": table_a.num_rows,
            "rows_b": table_b.num_rows,
            "row_diff": table_b.num_rows - table_a.num_rows,
            "schema_changed": table_a.schema != table_b.schema,
        }

        if key_columns:
            # Detailed row-level diff using DuckDB
            # Register both versions temporarily
            self._conn.register("__diff_a", table_a)
            self._conn.register("__diff_b", table_b)

            key_cols = ", ".join(key_columns)

            # Find added rows (in B but not in A)
            added = self._conn.execute(f"""
                SELECT * FROM __diff_b b
                WHERE NOT EXISTS (
                    SELECT 1 FROM __diff_a a
                    WHERE {" AND ".join(f"a.{c} = b.{c}" for c in key_columns)}
                )
            """).fetch_arrow_table()

            # Find removed rows (in A but not in B)
            removed = self._conn.execute(f"""
                SELECT * FROM __diff_a a
                WHERE NOT EXISTS (
                    SELECT 1 FROM __diff_b b
                    WHERE {" AND ".join(f"a.{c} = b.{c}" for c in key_columns)}
                )
            """).fetch_arrow_table()

            result["rows_added"] = added.num_rows
            result["rows_removed"] = removed.num_rows

            # Unregister temp tables
            self._conn.unregister("__diff_a")
            self._conn.unregister("__diff_b")

        return result

    def _ensure_registered(self, table_name: str, version: Optional[int] = None) -> None:
        """Ensure a table is registered with DuckDB at the specified version."""
        # Resolve version
        if version is None:
            metadata = self.reader.get_metadata(table_name)
            version = metadata.version

        cache_key = (table_name.lower(), version)

        # Check if already registered at this version
        if cache_key in self._registered:
            return

        # Load the table
        arrow_table = self.reader.read_arrow(table_name, version)

        # Unregister any previous version of this table
        self._unregister_table(table_name)

        # Register with DuckDB
        self._conn.register(table_name.lower(), arrow_table)

        # Cache the registration
        self._registered[cache_key] = RegisteredTable(
            table_name=table_name.lower(),
            version=version,
            arrow_table=arrow_table,
        )

    def _unregister_table(self, table_name: str) -> None:
        """Unregister all versions of a table from DuckDB."""
        table_lower = table_name.lower()

        # Find and remove all cached versions
        to_remove = [k for k in self._registered if k[0] == table_lower]

        for key in to_remove:
            del self._registered[key]

        # Try to unregister from DuckDB (may not exist)
        try:
            self._conn.unregister(table_lower)
        except Exception:
            pass  # Table wasn't registered

    def _invalidate_cache(self, table_name: str) -> None:
        """Invalidate cache for a table (called after writes)."""
        self._unregister_table(table_name)

    def _extract_table_names(self, sql: str) -> List[str]:
        """
        Extract table names from a SQL query.

        This is a simple regex-based approach. For production,
        consider using sqlparse or DuckDB's query plan.
        """
        # Normalize whitespace
        sql_normalized = " ".join(sql.split())

        # Patterns to match table names:
        # - FROM table_name
        # - JOIN table_name
        # - FROM table_name alias
        # - JOIN table_name alias
        patterns = [
            r'\bFROM\s+(\w+)',
            r'\bJOIN\s+(\w+)',
        ]

        table_names = set()
        for pattern in patterns:
            matches = re.findall(pattern, sql_normalized, re.IGNORECASE)
            table_names.update(m.lower() for m in matches)

        # Filter out DuckDB built-in functions/keywords that might match
        keywords = {'select', 'where', 'group', 'order', 'limit', 'offset', 'union', 'except', 'intersect'}
        table_names -= keywords

        return list(table_names)

    def close(self) -> None:
        """Close the DuckDB connection."""
        self._conn.close()

    def __enter__(self) -> "QueryEngine":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()
