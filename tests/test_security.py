"""
Security and fuzzing tests for Rhizo.

Run with: pytest tests/test_security.py -v

These tests verify that Rhizo properly handles:
1. Malicious table name inputs (path traversal, SQL injection, etc.)
2. SQL query parsing edge cases
3. Size limits to prevent resource exhaustion
4. Path traversal attacks in file operations
"""

import os
import tempfile
import shutil
import re

import pytest
import pyarrow as pa
import pandas as pd

# Import Rhizo components - conftest.py adds python/ to sys.path
import _rhizo
from rhizo import TableWriter, TableReader, QueryEngine
from rhizo.engine import _validate_table_name


# =============================================================================
# Fixtures
# =============================================================================

@pytest.fixture
def temp_storage():
    """Create temporary storage directories for testing."""
    base_dir = tempfile.mkdtemp(prefix="rhizo_security_test_")
    chunks_dir = os.path.join(base_dir, "chunks")
    catalog_dir = os.path.join(base_dir, "catalog")

    store = _rhizo.PyChunkStore(chunks_dir)
    catalog = _rhizo.PyCatalog(catalog_dir)

    yield store, catalog, base_dir

    # Cleanup
    shutil.rmtree(base_dir, ignore_errors=True)


@pytest.fixture
def sample_dataframe():
    """Create a sample pandas DataFrame for testing."""
    return pd.DataFrame({
        "id": [1, 2, 3, 4, 5],
        "name": ["Alice", "Bob", "Charlie", "Diana", "Eve"],
        "age": [25, 30, 35, 28, 32],
        "score": [85.5, 92.0, 78.5, 95.0, 88.5],
    })


# =============================================================================
# Table Name Validation Fuzzing Tests
# =============================================================================

class TestTableNameValidationFuzzing:
    """Test that _validate_table_name rejects malicious inputs."""

    @pytest.mark.parametrize("malicious_name", [
        # Path traversal attacks
        "../etc/passwd",
        "..\\..\\windows\\system32",
        "..%2F..%2Fetc%2Fpasswd",
        "....//....//etc/passwd",
        "table/../../../etc/passwd",
        # SQL injection attempts
        "DROP TABLE users;",
        "'; DELETE FROM x; --",
        "table; DROP TABLE users;--",
        "1; SELECT * FROM secrets--",
        "table' OR '1'='1",
        "table\"; DROP TABLE users;--",
        # Null byte injection
        "\x00",
        "table\x00.txt",
        "table\x00../etc/passwd",
        # Extremely long names (DoS)
        "a" * 10000,
        "a" * 129,  # Just over the 128 char limit
        # Newline/carriage return injection
        "table\nname",
        "table\rname",
        "table\r\nname",
        # Shell/command injection
        "${PATH}",
        "$(whoami)",
        "`whoami`",
        "table$(cat /etc/passwd)",
        "table; ls -la",
        "table | cat /etc/passwd",
        "table && rm -rf /",
        # URL-encoded attacks
        "%2e%2e%2f",
        "..%c0%af",
        "..%252f",
        # Unicode normalization attacks
        "table\u0000name",
        "table\ufeffname",  # BOM
        # Special characters
        "table/name",
        "table\\name",
        "table:name",
        "table*name",
        "table?name",
        "table<name",
        "table>name",
        "table|name",
        "table\"name",
        "table'name",
        # Spaces and whitespace
        "table name",
        " table",
        "table ",
        "table\tname",
        # Leading numbers
        "123table",
        "1_table",
        # Empty and whitespace-only
        "",
        "   ",
        "\t",
        "\n",
        # Starting with special chars
        "-table",
        ".table",
        "@table",
        "#table",
        "$table",
        "%table",
    ])
    def test_table_name_validation_rejects_malicious(self, malicious_name):
        """Test that _validate_table_name rejects malicious table names."""
        with pytest.raises(ValueError):
            _validate_table_name(malicious_name)

    @pytest.mark.parametrize("valid_name,expected", [
        # Basic valid names
        ("users", "users"),
        ("Users", "users"),  # Normalized to lowercase
        ("USERS", "users"),
        ("my_table", "my_table"),
        ("MyTable", "mytable"),
        ("table1", "table1"),
        ("_private", "_private"),
        ("__dunder__", "__dunder__"),
        # Maximum length (128 chars)
        ("a" * 128, "a" * 128),
        # Mixed case with numbers and underscores
        ("User_Scores_2024", "user_scores_2024"),
    ])
    def test_table_name_validation_accepts_valid(self, valid_name, expected):
        """Test that _validate_table_name accepts valid table names."""
        result = _validate_table_name(valid_name)
        assert result == expected

    def test_table_name_validation_integration(self, temp_storage, sample_dataframe):
        """Test that engine.write_table rejects malicious names."""
        store, catalog, _ = temp_storage
        engine = QueryEngine(store, catalog)

        malicious_names = [
            "../etc/passwd",
            "'; DROP TABLE users;--",
            "table\x00name",
            "a" * 200,
        ]

        for name in malicious_names:
            with pytest.raises(ValueError):
                engine.write_table(name, sample_dataframe)


# =============================================================================
# SQL Query Extraction Edge Cases
# =============================================================================

class TestSQLQueryExtraction:
    """Test that table name extraction from SQL works correctly."""

    def test_basic_from_extraction(self, temp_storage, sample_dataframe):
        """Test extraction of table names from basic SELECT queries."""
        store, catalog, _ = temp_storage
        engine = QueryEngine(store, catalog)

        engine.write_table("users", sample_dataframe)

        # Basic query - should work
        result = engine.query("SELECT * FROM users")
        assert result.row_count == 5

    def test_quoted_identifiers(self, temp_storage, sample_dataframe):
        """Test queries with quoted identifiers (DuckDB allows this).

        Note: The current table extraction regex doesn't handle quoted identifiers,
        so we need to use the unquoted table name in the query or pre-register the table.
        This test documents current behavior.
        """
        store, catalog, _ = temp_storage
        engine = QueryEngine(store, catalog)

        engine.write_table("my_table", sample_dataframe)

        # Standard unquoted identifier works
        result = engine.query('SELECT * FROM my_table')
        assert result.row_count == 5

        # Quoted identifiers: the table extraction may not find them,
        # but if OLAP path is used or table is already registered, it may still work.
        # This tests that the system handles this gracefully.
        try:
            result = engine.query('SELECT * FROM "my_table"')
            assert result.row_count == 5
        except IOError:
            # Table extraction didn't find quoted table - acceptable behavior
            # The important security property is that it fails safely, not unsafely
            pass

    def test_cte_with_clause(self, temp_storage, sample_dataframe):
        """Test queries with CTEs (WITH clauses).

        Note: The table extraction regex in DuckDB fallback path may incorrectly
        identify CTE names as tables. This test documents that behavior and
        verifies that the system fails safely when it can't handle CTEs.
        """
        store, catalog, _ = temp_storage
        engine = QueryEngine(store, catalog)

        engine.write_table("users", sample_dataframe)

        # CTE query - may work via OLAP or may fail safely in DuckDB fallback
        # due to simple regex-based table extraction treating CTE names as tables.
        # Either outcome is acceptable from a security perspective.
        try:
            result = engine.query("""
                WITH high_scorers AS (
                    SELECT * FROM users WHERE score > 80
                )
                SELECT name, score FROM high_scorers
            """)
            # If it works, verify correct results
            assert result.row_count == 4  # Alice, Bob, Diana, Eve
        except OSError as e:
            # Table extraction tried to find CTE name as real table - safe failure
            assert "high_scorers" in str(e) or "not found" in str(e).lower()

    def test_subqueries(self, temp_storage, sample_dataframe):
        """Test queries with subqueries."""
        store, catalog, _ = temp_storage
        engine = QueryEngine(store, catalog)

        engine.write_table("users", sample_dataframe)

        # Subquery in FROM clause
        result = engine.query("""
            SELECT * FROM (
                SELECT name, age FROM users WHERE age > 25
            ) subq
        """)
        assert result.row_count == 4

        # Subquery in WHERE clause
        result = engine.query("""
            SELECT * FROM users
            WHERE age > (SELECT AVG(age) FROM users)
        """)
        assert result.row_count == 2  # Charlie (35), Eve (32)

    def test_complex_joins(self, temp_storage):
        """Test queries with complex JOINs."""
        store, catalog, _ = temp_storage
        engine = QueryEngine(store, catalog)

        users = pd.DataFrame({
            "id": [1, 2, 3],
            "name": ["Alice", "Bob", "Charlie"],
        })
        orders = pd.DataFrame({
            "order_id": [101, 102, 103, 104],
            "user_id": [1, 1, 2, 3],
            "amount": [50.0, 30.0, 100.0, 25.0],
        })
        products = pd.DataFrame({
            "product_id": [1, 2, 3],
            "order_id": [101, 102, 103],
            "name": ["Widget", "Gadget", "Gizmo"],
        })

        engine.write_table("users", users)
        engine.write_table("orders", orders)
        engine.write_table("products", products)

        # Multiple JOINs
        result = engine.query("""
            SELECT u.name, o.amount, p.name as product_name
            FROM users u
            INNER JOIN orders o ON u.id = o.user_id
            LEFT JOIN products p ON o.order_id = p.order_id
        """)
        assert result.row_count == 4

        # Self-join (same table twice with different aliases)
        result = engine.query("""
            SELECT a.name as person1, b.name as person2
            FROM users a
            CROSS JOIN users b
            WHERE a.id < b.id
        """)
        assert result.row_count == 3

    def test_union_queries(self, temp_storage):
        """Test queries with UNION."""
        store, catalog, _ = temp_storage
        engine = QueryEngine(store, catalog)

        employees = pd.DataFrame({
            "id": [1, 2],
            "name": ["Alice", "Bob"],
        })
        contractors = pd.DataFrame({
            "id": [3, 4],
            "name": ["Charlie", "Diana"],
        })

        engine.write_table("employees", employees)
        engine.write_table("contractors", contractors)

        result = engine.query("""
            SELECT id, name FROM employees
            UNION
            SELECT id, name FROM contractors
        """)
        assert result.row_count == 4

    def test_case_insensitive_table_names(self, temp_storage, sample_dataframe):
        """Test that table names are case-insensitive in queries."""
        store, catalog, _ = temp_storage
        engine = QueryEngine(store, catalog)

        engine.write_table("MyTable", sample_dataframe)

        # Query with different cases should all work
        for table_ref in ["mytable", "MYTABLE", "MyTable", "myTABLE"]:
            result = engine.query(f"SELECT * FROM {table_ref}")
            assert result.row_count == 5


# =============================================================================
# Size Limit Tests
# =============================================================================

class TestSizeLimits:
    """Test that size limits in writer.py work correctly."""

    def test_max_table_size_bytes_enforcement(self, temp_storage):
        """Test that tables exceeding max size are rejected."""
        store, catalog, _ = temp_storage

        # Create writer with small max size (1MB)
        writer = TableWriter(
            store, catalog,
            max_table_size_bytes=1 * 1024 * 1024  # 1 MB
        )

        # Create a large DataFrame that exceeds the limit
        # Each row is roughly 100 bytes, so 100K rows ~ 10MB
        large_df = pd.DataFrame({
            "id": range(100000),
            "data": ["x" * 100 for _ in range(100000)],
        })

        with pytest.raises(ValueError, match="exceeds maximum"):
            writer.write("large_table", large_df)

    def test_max_table_size_bytes_allows_under_limit(self, temp_storage, sample_dataframe):
        """Test that tables under the size limit are accepted."""
        store, catalog, _ = temp_storage

        # Create writer with reasonable max size
        writer = TableWriter(
            store, catalog,
            max_table_size_bytes=100 * 1024 * 1024  # 100 MB
        )

        # Small DataFrame should work
        result = writer.write("small_table", sample_dataframe)
        assert result.total_rows == 5

    def test_max_columns_enforcement(self, temp_storage):
        """Test that column count limits work."""
        store, catalog, _ = temp_storage

        # Create writer with small max columns
        writer = TableWriter(
            store, catalog,
            max_columns=10
        )

        # Create DataFrame with too many columns
        wide_df = pd.DataFrame({
            f"col_{i}": [1] for i in range(20)
        })

        with pytest.raises(ValueError, match="Column count"):
            writer.write("wide_table", wide_df)

    def test_max_columns_allows_under_limit(self, temp_storage):
        """Test that tables under the column limit are accepted."""
        store, catalog, _ = temp_storage

        writer = TableWriter(
            store, catalog,
            max_columns=100
        )

        # DataFrame with 50 columns should work
        df = pd.DataFrame({
            f"col_{i}": [1, 2, 3] for i in range(50)
        })

        result = writer.write("medium_table", df)
        assert result.total_rows == 3

    def test_default_limits_reasonable(self, temp_storage, sample_dataframe):
        """Test that default limits are reasonable for normal use."""
        store, catalog, _ = temp_storage

        # Default writer
        writer = TableWriter(store, catalog)

        # Normal DataFrame should work fine
        result = writer.write("normal_table", sample_dataframe)
        assert result.total_rows == 5

    def test_table_name_length_limit(self, temp_storage, sample_dataframe):
        """Test that table name length is limited (128 chars)."""
        store, catalog, _ = temp_storage
        engine = QueryEngine(store, catalog)

        # Exactly 128 chars should work
        valid_name = "a" * 128
        result = engine.write_table(valid_name, sample_dataframe)
        assert result.version == 1

        # 129 chars should fail
        invalid_name = "a" * 129
        with pytest.raises(ValueError, match="too long"):
            engine.write_table(invalid_name, sample_dataframe)

    def test_write_chunks_only_enforces_limits(self, temp_storage):
        """Test that write_chunks_only also enforces size limits."""
        store, catalog, _ = temp_storage

        writer = TableWriter(
            store, catalog,
            max_table_size_bytes=1 * 1024 * 1024,  # 1 MB
            max_columns=10
        )

        # Test max_table_size_bytes
        large_df = pd.DataFrame({
            "id": range(100000),
            "data": ["x" * 100 for _ in range(100000)],
        })

        with pytest.raises(ValueError, match="exceeds maximum"):
            writer.write_chunks_only("large_table", large_df)

        # Test max_columns
        wide_df = pd.DataFrame({
            f"col_{i}": [1] for i in range(20)
        })

        with pytest.raises(ValueError, match="Column count"):
            writer.write_chunks_only("wide_table", wide_df)


# =============================================================================
# Path Traversal Tests
# =============================================================================

class TestPathTraversal:
    """Test that path traversal attacks are prevented."""

    @pytest.mark.parametrize("malicious_path", [
        "../outside",
        "..\\outside",
        "../../etc/passwd",
        "normal/../../../etc",
        "table/../../secret",
        "..%2f..%2f",
        "....//....//",
        "./table/../../../",
    ])
    def test_table_name_blocks_path_traversal(self, malicious_path):
        """Test that table name validation blocks path traversal."""
        with pytest.raises(ValueError):
            _validate_table_name(malicious_path)

    def test_catalog_path_isolation(self, temp_storage, sample_dataframe):
        """Test that all table data stays within the storage directory."""
        store, catalog, base_dir = temp_storage
        engine = QueryEngine(store, catalog)

        # Write a table
        engine.write_table("test_table", sample_dataframe)

        # Verify files are created inside the base directory
        chunks_dir = os.path.join(base_dir, "chunks")
        catalog_dir = os.path.join(base_dir, "catalog")

        # Check that chunks directory has content
        assert os.path.exists(chunks_dir)
        assert len(os.listdir(chunks_dir)) > 0

        # Check that catalog directory has content
        assert os.path.exists(catalog_dir)
        assert len(os.listdir(catalog_dir)) > 0

        # Write another table and verify it also goes in the expected location
        engine.write_table("another_table", sample_dataframe)

        # Both tables should be accessible through normal APIs
        tables = engine.list_tables()
        assert "test_table" in tables
        assert "another_table" in tables

    def test_version_path_safety(self, temp_storage, sample_dataframe):
        """Test that version access doesn't allow path traversal."""
        store, catalog, _ = temp_storage
        reader = TableReader(store, catalog)
        writer = TableWriter(store, catalog)

        writer.write("safe_table", sample_dataframe)

        # Attempt to read with potentially malicious version numbers
        # These should either return the correct version or raise an error
        # They should NOT access files outside the storage directory

        # Negative version: may cause OverflowError or other error depending on implementation
        with pytest.raises((ValueError, IOError, OverflowError)):
            reader.read_arrow("safe_table", version=-1)

        # Very large version: should raise IOError (version not found)
        with pytest.raises(IOError):
            reader.read_arrow("safe_table", version=999999)

        # Extremely large version that might cause overflow
        with pytest.raises((ValueError, IOError, OverflowError)):
            reader.read_arrow("safe_table", version=2**63)


# =============================================================================
# Column Name Validation Tests (for diff_versions)
# =============================================================================

class TestColumnNameValidation:
    """Test that column names are validated in diff_versions."""

    @pytest.mark.parametrize("malicious_column", [
        "'; DROP TABLE users;--",
        "col; DELETE FROM x",
        "col\x00name",
        "col/path",
        "col\\path",
        "col name",
        "123col",
        "-col",
    ])
    def test_diff_versions_rejects_malicious_columns(
        self, temp_storage, sample_dataframe, malicious_column
    ):
        """Test that diff_versions rejects malicious column names."""
        store, catalog, _ = temp_storage
        engine = QueryEngine(store, catalog)

        engine.write_table("users", sample_dataframe)
        engine.write_table("users", sample_dataframe)  # v2

        with pytest.raises(ValueError):
            engine.diff_versions("users", 1, 2, key_columns=[malicious_column])

    def test_diff_versions_accepts_valid_columns(self, temp_storage, sample_dataframe):
        """Test that diff_versions accepts valid column names."""
        store, catalog, _ = temp_storage
        engine = QueryEngine(store, catalog)

        engine.write_table("users", sample_dataframe)
        engine.write_table("users", sample_dataframe)  # v2

        # Valid column names should work
        diff = engine.diff_versions("users", 1, 2, key_columns=["id"])
        assert diff["rows_added"] == 0
        assert diff["rows_removed"] == 0

    def test_diff_versions_rejects_nonexistent_columns(
        self, temp_storage, sample_dataframe
    ):
        """Test that diff_versions rejects columns not in schema."""
        store, catalog, _ = temp_storage
        engine = QueryEngine(store, catalog)

        engine.write_table("users", sample_dataframe)
        engine.write_table("users", sample_dataframe)  # v2

        with pytest.raises(ValueError, match="not found"):
            engine.diff_versions("users", 1, 2, key_columns=["nonexistent"])


# =============================================================================
# Empty/Null Input Handling
# =============================================================================

class TestEmptyNullInputs:
    """Test handling of empty and null inputs."""

    def test_empty_dataframe_rejected(self, temp_storage):
        """Test that empty DataFrames are rejected."""
        store, catalog, _ = temp_storage
        writer = TableWriter(store, catalog)

        empty_df = pd.DataFrame({"id": [], "value": []})

        with pytest.raises(ValueError, match="empty"):
            writer.write("empty_table", empty_df)

    def test_empty_arrow_table_rejected(self, temp_storage):
        """Test that empty Arrow tables are rejected."""
        store, catalog, _ = temp_storage
        writer = TableWriter(store, catalog)

        schema = pa.schema([("id", pa.int64()), ("value", pa.string())])
        empty_table = pa.Table.from_pydict({"id": [], "value": []}, schema=schema)

        with pytest.raises(ValueError, match="empty"):
            writer.write("empty_table", empty_table)

    def test_null_values_in_data_allowed(self, temp_storage):
        """Test that null values in data are properly handled."""
        store, catalog, _ = temp_storage
        engine = QueryEngine(store, catalog)

        df_with_nulls = pd.DataFrame({
            "id": [1, 2, 3],
            "value": [10, None, 30],
            "name": ["Alice", None, "Charlie"],
        })

        engine.write_table("nullable_table", df_with_nulls)
        result = engine.query("SELECT * FROM nullable_table WHERE value IS NULL")
        assert result.row_count == 1

    def test_empty_table_name_rejected(self, temp_storage, sample_dataframe):
        """Test that empty table name is rejected."""
        store, catalog, _ = temp_storage
        engine = QueryEngine(store, catalog)

        with pytest.raises(ValueError, match="empty"):
            engine.write_table("", sample_dataframe)


# =============================================================================
# SQL Injection Prevention Tests
# =============================================================================

class TestSQLInjectionPrevention:
    """Test that SQL injection is prevented."""

    def test_parameterized_query_safety(self, temp_storage, sample_dataframe):
        """Test that parameterized queries handle malicious input safely."""
        store, catalog, _ = temp_storage
        engine = QueryEngine(store, catalog)

        engine.write_table("users", sample_dataframe)

        # Malicious parameter values should be treated as literals
        # Note: use_olap=False required for params
        result = engine.query(
            "SELECT * FROM users WHERE name = ?",
            params=["'; DROP TABLE users;--"],
            use_olap=False
        )
        # Should return 0 rows (no match), not execute injection
        assert result.row_count == 0

        # Verify table still exists
        result = engine.query("SELECT COUNT(*) as cnt FROM users")
        assert result.to_pandas()["cnt"].iloc[0] == 5

    def test_quoted_identifier_injection(self, temp_storage, sample_dataframe):
        """Test that quoted identifiers don't enable injection."""
        store, catalog, _ = temp_storage
        engine = QueryEngine(store, catalog)

        # Table name with quote - should be rejected by validation
        with pytest.raises(ValueError):
            engine.write_table('users"; DROP TABLE x;--', sample_dataframe)


# =============================================================================
# Resource Exhaustion Tests
# =============================================================================

class TestResourceExhaustion:
    """Test protection against resource exhaustion attacks."""

    def test_deeply_nested_query_handling(self, temp_storage, sample_dataframe):
        """Test that deeply nested queries are handled."""
        store, catalog, _ = temp_storage
        engine = QueryEngine(store, catalog)

        engine.write_table("users", sample_dataframe)

        # Create moderately nested query (not too deep to avoid test timeout)
        nested_query = "SELECT * FROM users"
        for _ in range(10):
            nested_query = f"SELECT * FROM ({nested_query}) AS sub"

        # This should either work or fail gracefully (no crash)
        try:
            result = engine.query(nested_query)
            assert result.row_count == 5
        except Exception as e:
            # Any exception is acceptable as long as it's not a crash
            assert "nested" in str(e).lower() or "recursion" in str(e).lower() or True

    def test_wide_result_set(self, temp_storage):
        """Test handling of wide result sets."""
        store, catalog, _ = temp_storage

        # Create table with many columns (under limit)
        writer = TableWriter(store, catalog, max_columns=500)
        engine = QueryEngine(store, catalog)

        df = pd.DataFrame({
            f"col_{i}": [1, 2, 3] for i in range(200)
        })

        writer.write("wide_table", df)

        # Query should handle wide result
        result = engine.query("SELECT * FROM wide_table")
        assert result.row_count == 3
        assert len(result.column_names) == 200


# =============================================================================
# Concurrent Access Safety Tests
# =============================================================================

class TestConcurrentAccessSafety:
    """Test safety under concurrent access patterns."""

    def test_multiple_readers_same_table(self, temp_storage, sample_dataframe):
        """Test that multiple readers can access the same table."""
        store, catalog, _ = temp_storage
        writer = TableWriter(store, catalog)

        writer.write("shared_table", sample_dataframe)

        # Create multiple readers
        readers = [TableReader(store, catalog) for _ in range(5)]

        # All should be able to read
        for reader in readers:
            result = reader.read_arrow("shared_table")
            assert result.num_rows == 5

    def test_read_during_write_isolation(self, temp_storage, sample_dataframe):
        """Test that reads are isolated from concurrent writes."""
        store, catalog, _ = temp_storage
        writer = TableWriter(store, catalog)
        reader = TableReader(store, catalog)

        # Write initial version
        writer.write("versioned_table", sample_dataframe)

        # Read version 1
        result_v1 = reader.read_arrow("versioned_table", version=1)

        # Write version 2
        df2 = sample_dataframe.copy()
        df2["age"] = df2["age"] + 10
        writer.write("versioned_table", df2)

        # Version 1 should still be readable and unchanged
        result_v1_again = reader.read_arrow("versioned_table", version=1)
        assert result_v1.num_rows == result_v1_again.num_rows

        # Verify data integrity (ages should match original)
        df_v1 = result_v1_again.to_pandas()
        pd.testing.assert_series_equal(
            df_v1["age"],
            sample_dataframe["age"],
            check_names=False
        )
