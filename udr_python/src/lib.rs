use pyo3::prelude::*;
use pyo3::exceptions::{PyIOError, PyValueError};
use udr_core::{
    ChunkStore, ChunkStoreError,
    FileCatalog, CatalogError, TableVersion,
    Branch, BranchDiff, BranchError, BranchManager,
};
use std::collections::HashMap;

/// Convert ChunkStoreError to appropriate Python exception
fn chunk_err_to_py(e: ChunkStoreError) -> PyErr {
    match e {
        ChunkStoreError::NotFound(h) => PyIOError::new_err(format!("Chunk not found: {}", h)),
        ChunkStoreError::InvalidHash(msg) => PyValueError::new_err(format!("Invalid hash: {}", msg)),
        ChunkStoreError::HashMismatch { expected, actual } => {
            PyValueError::new_err(format!("Hash mismatch: expected {}, got {}", expected, actual))
        }
        ChunkStoreError::Io(e) => PyIOError::new_err(e.to_string()),
    }
}

/// Convert CatalogError to appropriate Python exception
fn catalog_err_to_py(e: CatalogError) -> PyErr {
    match e {
        CatalogError::TableNotFound(t) => PyIOError::new_err(format!("Table not found: {}", t)),
        CatalogError::VersionNotFound(t, v) => {
            PyIOError::new_err(format!("Version not found: {} v{}", t, v))
        }
        CatalogError::InvalidVersion { expected, got } => {
            PyValueError::new_err(format!("Invalid version: expected {}, got {}", expected, got))
        }
        CatalogError::LatestPointerCorrupted(t) => {
            PyIOError::new_err(format!("Latest pointer corrupted for table: {}", t))
        }
        CatalogError::Io(e) => PyIOError::new_err(e.to_string()),
        CatalogError::Json(e) => PyValueError::new_err(format!("JSON error: {}", e)),
    }
}

/// Convert BranchError to appropriate Python exception
fn branch_err_to_py(e: BranchError) -> PyErr {
    match e {
        BranchError::BranchNotFound(name) => {
            PyIOError::new_err(format!("Branch not found: {}", name))
        }
        BranchError::BranchAlreadyExists(name) => {
            PyValueError::new_err(format!("Branch already exists: {}", name))
        }
        BranchError::CannotDeleteDefault(name) => {
            PyValueError::new_err(format!("Cannot delete default branch: {}", name))
        }
        BranchError::InvalidBranchName(msg) => {
            PyValueError::new_err(format!("Invalid branch name: {}", msg))
        }
        BranchError::MergeConflict(tables) => {
            PyValueError::new_err(format!("Merge conflict on tables: {:?}", tables))
        }
        BranchError::CannotFastForward { source_branch, target_branch } => {
            PyValueError::new_err(format!(
                "Cannot fast-forward: {} is not ahead of {}",
                source_branch, target_branch
            ))
        }
        BranchError::Io(e) => PyIOError::new_err(e.to_string()),
        BranchError::Json(e) => PyValueError::new_err(format!("JSON error: {}", e)),
    }
}

#[pyclass]
struct PyChunkStore {
    inner: ChunkStore,
}

#[pymethods]
impl PyChunkStore {
    #[new]
    fn new(path: &str) -> PyResult<Self> {
        let inner = ChunkStore::new(path).map_err(chunk_err_to_py)?;
        Ok(Self { inner })
    }

    fn put(&self, data: &[u8]) -> PyResult<String> {
        self.inner.put(data).map_err(chunk_err_to_py)
    }

    fn get(&self, hash: &str) -> PyResult<Vec<u8>> {
        self.inner.get(hash).map_err(chunk_err_to_py)
    }

    /// Get chunk data with integrity verification.
    /// Raises ValueError if the data doesn't match the expected hash.
    fn get_verified(&self, hash: &str) -> PyResult<Vec<u8>> {
        self.inner.get_verified(hash).map_err(chunk_err_to_py)
    }

    fn exists(&self, hash: &str) -> PyResult<bool> {
        self.inner.exists(hash).map_err(chunk_err_to_py)
    }

    fn delete(&self, hash: &str) -> PyResult<()> {
        self.inner.delete(hash).map_err(chunk_err_to_py)
    }
}

#[pyclass]
#[derive(Clone)]
struct PyTableVersion {
    #[pyo3(get)]
    table_name: String,
    #[pyo3(get)]
    version: u64,
    #[pyo3(get)]
    chunk_hashes: Vec<String>,
    #[pyo3(get)]
    schema_hash: Option<String>,
    #[pyo3(get)]
    created_at: i64,
    #[pyo3(get)]
    parent_version: Option<u64>,
    #[pyo3(get)]
    metadata: HashMap<String, String>,
}

#[pymethods]
impl PyTableVersion {
    #[new]
    fn new(table_name: String, version: u64, chunk_hashes: Vec<String>) -> Self {
        Self {
            table_name,
            version,
            chunk_hashes,
            schema_hash: None,
            created_at: std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_secs() as i64,
            parent_version: if version > 1 { Some(version - 1) } else { None },
            metadata: HashMap::new(),
        }
    }
}

impl From<TableVersion> for PyTableVersion {
    fn from(tv: TableVersion) -> Self {
        Self {
            table_name: tv.table_name,
            version: tv.version,
            chunk_hashes: tv.chunk_hashes,
            schema_hash: tv.schema_hash,
            created_at: tv.created_at,
            parent_version: tv.parent_version,
            metadata: tv.metadata,
        }
    }
}

impl From<PyTableVersion> for TableVersion {
    fn from(ptv: PyTableVersion) -> Self {
        Self {
            table_name: ptv.table_name,
            version: ptv.version,
            chunk_hashes: ptv.chunk_hashes,
            schema_hash: ptv.schema_hash,
            created_at: ptv.created_at,
            parent_version: ptv.parent_version,
            metadata: ptv.metadata,
        }
    }
}

#[pyclass]
struct PyCatalog {
    inner: FileCatalog,
}

#[pymethods]
impl PyCatalog {
    #[new]
    fn new(path: &str) -> PyResult<Self> {
        let inner = FileCatalog::new(path).map_err(catalog_err_to_py)?;
        Ok(Self { inner })
    }

    fn commit(&self, version: PyTableVersion) -> PyResult<u64> {
        self.inner.commit(version.into()).map_err(catalog_err_to_py)
    }

    #[pyo3(signature = (table_name, version=None))]
    fn get_version(&self, table_name: &str, version: Option<u64>) -> PyResult<PyTableVersion> {
        self.inner
            .get_version(table_name, version)
            .map(|tv| tv.into())
            .map_err(catalog_err_to_py)
    }

    fn list_versions(&self, table_name: &str) -> PyResult<Vec<u64>> {
        self.inner.list_versions(table_name).map_err(catalog_err_to_py)
    }

    fn list_tables(&self) -> PyResult<Vec<String>> {
        self.inner.list_tables().map_err(catalog_err_to_py)
    }
}

// ============================================================================
// Branch Classes
// ============================================================================

#[pyclass]
#[derive(Clone)]
struct PyBranch {
    #[pyo3(get)]
    name: String,
    #[pyo3(get)]
    head: HashMap<String, u64>,
    #[pyo3(get)]
    created_at: i64,
    #[pyo3(get)]
    parent_branch: Option<String>,
    #[pyo3(get)]
    description: Option<String>,
}

impl From<Branch> for PyBranch {
    fn from(b: Branch) -> Self {
        Self {
            name: b.name,
            head: b.head,
            created_at: b.created_at,
            parent_branch: b.parent_branch,
            description: b.description,
        }
    }
}

#[pyclass]
#[derive(Clone)]
struct PyBranchDiff {
    #[pyo3(get)]
    source_branch: String,
    #[pyo3(get)]
    target_branch: String,
    #[pyo3(get)]
    unchanged: Vec<String>,
    #[pyo3(get)]
    modified: Vec<(String, u64, u64)>,
    #[pyo3(get)]
    added_in_source: Vec<(String, u64)>,
    #[pyo3(get)]
    added_in_target: Vec<(String, u64)>,
    #[pyo3(get)]
    has_conflicts: bool,
}

impl From<BranchDiff> for PyBranchDiff {
    fn from(d: BranchDiff) -> Self {
        Self {
            source_branch: d.source_branch,
            target_branch: d.target_branch,
            unchanged: d.unchanged,
            modified: d.modified,
            added_in_source: d.added_in_source,
            added_in_target: d.added_in_target,
            has_conflicts: d.has_conflicts,
        }
    }
}

#[pyclass]
struct PyBranchManager {
    inner: BranchManager,
}

#[pymethods]
impl PyBranchManager {
    #[new]
    fn new(path: &str) -> PyResult<Self> {
        let inner = BranchManager::new(path).map_err(branch_err_to_py)?;
        Ok(Self { inner })
    }

    /// Create a new branch from an existing branch.
    /// If from_branch is None, creates from the default branch (main).
    #[pyo3(signature = (name, from_branch=None, description=None))]
    fn create(
        &self,
        name: &str,
        from_branch: Option<&str>,
        description: Option<&str>,
    ) -> PyResult<PyBranch> {
        self.inner
            .create(name, from_branch, description)
            .map(|b| b.into())
            .map_err(branch_err_to_py)
    }

    /// Get a branch by name.
    fn get(&self, name: &str) -> PyResult<PyBranch> {
        self.inner.get(name).map(|b| b.into()).map_err(branch_err_to_py)
    }

    /// List all branch names.
    fn list(&self) -> PyResult<Vec<String>> {
        self.inner.list().map_err(branch_err_to_py)
    }

    /// Delete a branch. Cannot delete the default branch.
    fn delete(&self, name: &str) -> PyResult<()> {
        self.inner.delete(name).map_err(branch_err_to_py)
    }

    /// Update the head pointer for a table on a branch.
    fn update_head(&self, branch_name: &str, table_name: &str, version: u64) -> PyResult<()> {
        self.inner
            .update_head(branch_name, table_name, version)
            .map_err(branch_err_to_py)
    }

    /// Get the version of a table on a branch.
    fn get_table_version(&self, branch_name: &str, table_name: &str) -> PyResult<Option<u64>> {
        self.inner
            .get_table_version(branch_name, table_name)
            .map_err(branch_err_to_py)
    }

    /// Compare two branches.
    fn diff(&self, source: &str, target: &str) -> PyResult<PyBranchDiff> {
        self.inner
            .diff(source, target)
            .map(|d| d.into())
            .map_err(branch_err_to_py)
    }

    /// Check if a fast-forward merge is possible.
    fn can_fast_forward(&self, source: &str, target: &str) -> PyResult<bool> {
        self.inner
            .can_fast_forward(source, target)
            .map_err(branch_err_to_py)
    }

    /// Merge source branch into target branch (fast-forward only).
    fn merge(&self, source: &str, into: &str) -> PyResult<()> {
        self.inner
            .merge_fast_forward(source, into)
            .map_err(branch_err_to_py)
    }

    /// Get the default branch name.
    fn get_default(&self) -> PyResult<Option<String>> {
        self.inner.get_default().map_err(branch_err_to_py)
    }

    /// Set the default branch.
    fn set_default(&self, name: &str) -> PyResult<()> {
        self.inner.set_default(name).map_err(branch_err_to_py)
    }
}

#[pymodule]
fn udr(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<PyChunkStore>()?;
    m.add_class::<PyTableVersion>()?;
    m.add_class::<PyCatalog>()?;
    m.add_class::<PyBranch>()?;
    m.add_class::<PyBranchDiff>()?;
    m.add_class::<PyBranchManager>()?;
    Ok(())
}
