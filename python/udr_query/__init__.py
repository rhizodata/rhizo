"""
UDR Query Layer - SQL queries over versioned, content-addressable data.

This module provides:
- TableWriter: Write DataFrames as chunked Parquet files
- TableReader: Read and assemble tables from chunks
- QueryEngine: SQL interface with time travel support
"""

from .writer import TableWriter
from .reader import TableReader
from .engine import QueryEngine

__version__ = "0.1.0"
__all__ = ["TableWriter", "TableReader", "QueryEngine"]
