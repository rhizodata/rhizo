pub mod error;
pub mod branch;
pub mod manager;

pub use error::BranchError;
pub use branch::{Branch, BranchDiff};
pub use manager::BranchManager;
