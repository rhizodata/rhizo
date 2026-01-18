//! Parquet decoding to Arrow data.
//!
//! This module provides high-performance Parquet decoding using the Rust
//! parquet crate, with support for parallel batch decoding via Rayon.

use arrow::array::{Array, AsArray, BooleanArray};
use arrow::compute::kernels::cmp::{eq, gt, gt_eq, lt, lt_eq, neq};
use arrow::record_batch::RecordBatch;
use bytes::Bytes;
use parquet::arrow::arrow_reader::{
    ArrowPredicateFn, ParquetRecordBatchReaderBuilder, RowFilter,
};
use parquet::arrow::ProjectionMask;
use rayon::prelude::*;

use super::error::ParquetError;
use super::filter::{FilterOp, PredicateFilter, ScalarValue};

/// High-performance Parquet decoder.
///
/// Converts Parquet bytes to Arrow RecordBatches. Supports parallel batch
/// decoding via Rayon for multiple chunks.
#[derive(Debug, Clone, Default)]
pub struct ParquetDecoder {
    /// Batch size for reading (number of rows per batch)
    batch_size: usize,
}

impl ParquetDecoder {
    /// Create a new decoder with default settings.
    pub fn new() -> Self {
        Self {
            batch_size: 65536, // 64K rows per batch, good default
        }
    }

    /// Create a decoder with custom batch size.
    pub fn with_batch_size(batch_size: usize) -> Self {
        Self { batch_size }
    }

    /// Decode Parquet bytes to a single Arrow RecordBatch.
    ///
    /// If the Parquet file contains multiple row groups, they are combined
    /// into a single RecordBatch.
    ///
    /// # Arguments
    /// * `data` - Parquet file bytes
    ///
    /// # Returns
    /// * `Ok(RecordBatch)` - The decoded Arrow data
    /// * `Err(ParquetError)` - If decoding fails
    pub fn decode(&self, data: &[u8]) -> Result<RecordBatch, ParquetError> {
        let bytes = Bytes::copy_from_slice(data);
        let reader = ParquetRecordBatchReaderBuilder::try_new(bytes)?
            .with_batch_size(self.batch_size)
            .build()?;

        // Collect all batches
        let batches: Vec<RecordBatch> = reader.collect::<Result<Vec<_>, _>>()?;

        if batches.is_empty() {
            return Err(ParquetError::EmptyData);
        }

        // If only one batch, return it directly
        if batches.len() == 1 {
            return Ok(batches.into_iter().next().unwrap());
        }

        // Concatenate multiple batches
        let schema = batches[0].schema();
        arrow::compute::concat_batches(&schema, &batches).map_err(ParquetError::Arrow)
    }

    /// Decode multiple Parquet chunks in parallel using Rayon.
    ///
    /// This is significantly faster than decoding sequentially when you have
    /// multiple chunks (e.g., multiple chunks of a table).
    ///
    /// # Arguments
    /// * `chunks` - Slice of Parquet byte slices to decode
    ///
    /// # Returns
    /// * `Ok(Vec<RecordBatch>)` - Decoded batches for each chunk, in same order
    /// * `Err(ParquetError)` - If any decoding fails (returns first error)
    pub fn decode_batch(&self, chunks: &[&[u8]]) -> Result<Vec<RecordBatch>, ParquetError> {
        chunks.par_iter().map(|chunk| self.decode(chunk)).collect()
    }

    /// Decode from owned Vec<u8> slices (convenience for FFI).
    pub fn decode_batch_owned(
        &self,
        chunks: &[Vec<u8>],
    ) -> Result<Vec<RecordBatch>, ParquetError> {
        chunks
            .par_iter()
            .map(|chunk| self.decode(chunk.as_slice()))
            .collect()
    }

    /// Decode only specific columns by index (projection pushdown).
    ///
    /// This is significantly faster when you only need a subset of columns.
    /// Column indices are 0-based and refer to the schema order.
    ///
    /// # Mathematical Model
    ///
    /// Without projection: T = Σ(decode_time_i) for all columns
    /// With projection:    T' = Σ(decode_time_i) for requested columns
    ///
    /// Expected speedup ≈ n/k where n=total columns, k=requested columns
    /// Example: 10 columns, query 2 → ~5x speedup on decode phase
    ///
    /// # Arguments
    /// * `data` - Parquet file bytes
    /// * `column_indices` - 0-based indices of columns to decode
    ///
    /// # Returns
    /// * `Ok(RecordBatch)` - Decoded Arrow data with only requested columns
    /// * `Err(ParquetError)` - If decoding fails or indices are invalid
    pub fn decode_columns(
        &self,
        data: &[u8],
        column_indices: &[usize],
    ) -> Result<RecordBatch, ParquetError> {
        if column_indices.is_empty() {
            return Err(ParquetError::InvalidColumn(
                "No columns specified for projection".to_string(),
            ));
        }

        let bytes = Bytes::copy_from_slice(data);
        let builder = ParquetRecordBatchReaderBuilder::try_new(bytes)?;

        // Create projection mask for requested columns
        // ProjectionMask::leaves() selects specific leaf columns by index
        let parquet_schema = builder.parquet_schema();
        let mask = ProjectionMask::leaves(parquet_schema, column_indices.iter().copied());

        let reader = builder
            .with_projection(mask)
            .with_batch_size(self.batch_size)
            .build()?;

        // Collect all batches
        let batches: Vec<RecordBatch> = reader.collect::<Result<Vec<_>, _>>()?;

        if batches.is_empty() {
            return Err(ParquetError::EmptyData);
        }

        // If only one batch, return it directly
        if batches.len() == 1 {
            return Ok(batches.into_iter().next().unwrap());
        }

        // Concatenate multiple batches
        let schema = batches[0].schema();
        arrow::compute::concat_batches(&schema, &batches).map_err(ParquetError::Arrow)
    }

    /// Decode only specific columns by name (projection pushdown).
    ///
    /// This is a convenience method that resolves column names to indices
    /// and then applies projection pushdown.
    ///
    /// # Arguments
    /// * `data` - Parquet file bytes
    /// * `column_names` - Names of columns to decode
    ///
    /// # Returns
    /// * `Ok(RecordBatch)` - Decoded Arrow data with only requested columns
    /// * `Err(ParquetError)` - If decoding fails or names are invalid
    pub fn decode_columns_by_name(
        &self,
        data: &[u8],
        column_names: &[&str],
    ) -> Result<RecordBatch, ParquetError> {
        if column_names.is_empty() {
            return Err(ParquetError::InvalidColumn(
                "No columns specified for projection".to_string(),
            ));
        }

        // First, read schema to get column indices
        let bytes = Bytes::copy_from_slice(data);
        let builder = ParquetRecordBatchReaderBuilder::try_new(bytes)?;
        let arrow_schema = builder.schema();

        // Resolve names to indices
        let mut column_indices = Vec::with_capacity(column_names.len());
        for name in column_names {
            match arrow_schema.index_of(name) {
                Ok(idx) => column_indices.push(idx),
                Err(_) => {
                    return Err(ParquetError::InvalidColumn(format!(
                        "Column '{}' not found in schema. Available: {:?}",
                        name,
                        arrow_schema
                            .fields()
                            .iter()
                            .map(|f| f.name())
                            .collect::<Vec<_>>()
                    )));
                }
            }
        }

        // Use index-based projection
        self.decode_columns(data, &column_indices)
    }

    /// Decode with predicate pushdown (row-level filtering).
    ///
    /// This method applies filter predicates during decoding, potentially
    /// reducing the amount of data that needs to be processed.
    ///
    /// # Mathematical Model
    ///
    /// For selectivity `s` (fraction of rows matching):
    ///   - Row-level filtering reduces output by factor of `s`
    ///   - Combined with projection: Speedup ≈ (n/k) × (1/s)
    ///
    /// Example: 10 columns, query 2, 1% selectivity → up to 500x speedup
    ///
    /// # Arguments
    /// * `data` - Parquet file bytes
    /// * `filters` - Predicate filters to apply
    /// * `column_indices` - Optional column projection (None = all columns)
    ///
    /// # Returns
    /// * `Ok(RecordBatch)` - Decoded Arrow data with filters applied
    /// * `Err(ParquetError)` - If decoding fails
    pub fn decode_with_filter(
        &self,
        data: &[u8],
        filters: &[PredicateFilter],
        column_indices: Option<&[usize]>,
    ) -> Result<RecordBatch, ParquetError> {
        if filters.is_empty() {
            // No filters, use regular decode
            return match column_indices {
                Some(cols) => self.decode_columns(data, cols),
                None => self.decode(data),
            };
        }

        let bytes = Bytes::copy_from_slice(data);
        let builder = ParquetRecordBatchReaderBuilder::try_new(bytes)?;
        let arrow_schema = builder.schema();
        let parquet_schema = builder.parquet_schema();

        // Resolve filter column names to indices and create projection mask for filters
        let mut filter_column_indices = Vec::new();
        for filter in filters {
            match arrow_schema.index_of(&filter.column) {
                Ok(idx) => {
                    if !filter_column_indices.contains(&idx) {
                        filter_column_indices.push(idx);
                    }
                }
                Err(_) => {
                    return Err(ParquetError::InvalidColumn(format!(
                        "Filter column '{}' not found in schema",
                        filter.column
                    )));
                }
            }
        }

        // Create projection mask for filter columns
        let filter_mask = ProjectionMask::leaves(parquet_schema, filter_column_indices.iter().copied());

        // Clone filters for the closure
        let filters_owned: Vec<PredicateFilter> = filters.to_vec();
        let schema_for_closure = arrow_schema.clone();

        // Create the row filter predicate
        let predicate = ArrowPredicateFn::new(filter_mask, move |batch: RecordBatch| {
            apply_filters(&batch, &filters_owned, &schema_for_closure)
        });

        let row_filter = RowFilter::new(vec![Box::new(predicate)]);

        // Apply projection mask if specified
        let builder = if let Some(cols) = column_indices {
            let output_mask = ProjectionMask::leaves(parquet_schema, cols.iter().copied());
            builder.with_projection(output_mask)
        } else {
            builder
        };

        // Build reader with filter
        let reader = builder
            .with_row_filter(row_filter)
            .with_batch_size(self.batch_size)
            .build()?;

        // Collect all batches
        let batches: Vec<RecordBatch> = reader.collect::<Result<Vec<_>, _>>()?;

        if batches.is_empty() {
            // Return empty batch with correct schema
            return Err(ParquetError::EmptyData);
        }

        // If only one batch, return it directly
        if batches.len() == 1 {
            return Ok(batches.into_iter().next().unwrap());
        }

        // Concatenate multiple batches
        let schema = batches[0].schema();
        arrow::compute::concat_batches(&schema, &batches).map_err(ParquetError::Arrow)
    }
}

/// Apply multiple filters to a record batch, returning a boolean mask.
fn apply_filters(
    batch: &RecordBatch,
    filters: &[PredicateFilter],
    schema: &arrow::datatypes::SchemaRef,
) -> Result<BooleanArray, arrow::error::ArrowError> {
    let num_rows = batch.num_rows();

    // Start with all true
    let mut result = BooleanArray::from(vec![true; num_rows]);

    for filter in filters {
        // Verify column exists in schema (validates filter against original schema)
        if let Err(e) = schema.index_of(&filter.column) {
            return Err(e);
        }

        // Get the column from the batch - find by name since indices may differ
        let column = batch
            .column_by_name(&filter.column)
            .ok_or_else(|| {
                arrow::error::ArrowError::SchemaError(format!(
                    "Column '{}' not found in batch",
                    filter.column
                ))
            })?;

        // Apply the filter based on the scalar value type
        let filter_mask = apply_single_filter(column.as_ref(), &filter.op, &filter.value)?;

        // AND with existing result
        result = arrow::compute::and(&result, &filter_mask)?;
    }

    Ok(result)
}

/// Apply a single filter to a column.
fn apply_single_filter(
    column: &dyn Array,
    op: &FilterOp,
    value: &ScalarValue,
) -> Result<BooleanArray, arrow::error::ArrowError> {
    use arrow::array::{Float64Array, Int32Array, Int64Array, StringArray};
    use arrow::datatypes::DataType;

    match (column.data_type(), value) {
        (DataType::Int64, ScalarValue::Int64(v)) => {
            let col = column.as_primitive::<arrow::datatypes::Int64Type>();
            let scalar = Int64Array::new_scalar(*v);
            match op {
                FilterOp::Eq => eq(col, &scalar),
                FilterOp::Ne => neq(col, &scalar),
                FilterOp::Lt => lt(col, &scalar),
                FilterOp::Le => lt_eq(col, &scalar),
                FilterOp::Gt => gt(col, &scalar),
                FilterOp::Ge => gt_eq(col, &scalar),
            }
        }
        (DataType::Int32, ScalarValue::Int32(v)) => {
            let col = column.as_primitive::<arrow::datatypes::Int32Type>();
            let scalar = Int32Array::new_scalar(*v);
            match op {
                FilterOp::Eq => eq(col, &scalar),
                FilterOp::Ne => neq(col, &scalar),
                FilterOp::Lt => lt(col, &scalar),
                FilterOp::Le => lt_eq(col, &scalar),
                FilterOp::Gt => gt(col, &scalar),
                FilterOp::Ge => gt_eq(col, &scalar),
            }
        }
        (DataType::Float64, ScalarValue::Float64(v)) => {
            let col = column.as_primitive::<arrow::datatypes::Float64Type>();
            let scalar = Float64Array::new_scalar(*v);
            match op {
                FilterOp::Eq => eq(col, &scalar),
                FilterOp::Ne => neq(col, &scalar),
                FilterOp::Lt => lt(col, &scalar),
                FilterOp::Le => lt_eq(col, &scalar),
                FilterOp::Gt => gt(col, &scalar),
                FilterOp::Ge => gt_eq(col, &scalar),
            }
        }
        (DataType::Utf8, ScalarValue::Utf8(v)) => {
            let col = column.as_string::<i32>();
            let scalar = StringArray::new_scalar(v);
            match op {
                FilterOp::Eq => eq(col, &scalar),
                FilterOp::Ne => neq(col, &scalar),
                FilterOp::Lt => lt(col, &scalar),
                FilterOp::Le => lt_eq(col, &scalar),
                FilterOp::Gt => gt(col, &scalar),
                FilterOp::Ge => gt_eq(col, &scalar),
            }
        }
        _ => Err(arrow::error::ArrowError::SchemaError(format!(
            "Unsupported filter: column type {:?} with value {:?}",
            column.data_type(),
            value
        ))),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::parquet::{FilterOp, ParquetCompression, ParquetEncoder, PredicateFilter, ScalarValue};
    use arrow::array::{Float64Array, Int64Array, StringArray};
    use arrow::datatypes::{DataType, Field, Schema};
    use std::sync::Arc;

    fn create_test_batch(num_rows: usize) -> RecordBatch {
        let schema = Schema::new(vec![
            Field::new("id", DataType::Int64, false),
            Field::new("value", DataType::Float64, false),
            Field::new("name", DataType::Utf8, true),
        ]);

        let ids: Vec<i64> = (0..num_rows as i64).collect();
        let values: Vec<f64> = (0..num_rows).map(|i| i as f64 * 1.5).collect();
        let names: Vec<Option<&str>> = (0..num_rows)
            .map(|i| if i % 2 == 0 { Some("test") } else { None })
            .collect();

        RecordBatch::try_new(
            Arc::new(schema),
            vec![
                Arc::new(Int64Array::from(ids)),
                Arc::new(Float64Array::from(values)),
                Arc::new(StringArray::from(names)),
            ],
        )
        .unwrap()
    }

    fn encode_batch(batch: &RecordBatch) -> Vec<u8> {
        ParquetEncoder::new().encode(batch).unwrap()
    }

    #[test]
    fn test_decode_simple() {
        let original = create_test_batch(100);
        let encoded = encode_batch(&original);

        let decoder = ParquetDecoder::new();
        let decoded = decoder.decode(&encoded).unwrap();

        assert_eq!(decoded.num_rows(), original.num_rows());
        assert_eq!(decoded.num_columns(), original.num_columns());
        assert_eq!(decoded.schema(), original.schema());
    }

    #[test]
    fn test_roundtrip_data_integrity() {
        let original = create_test_batch(1000);
        let encoded = encode_batch(&original);

        let decoder = ParquetDecoder::new();
        let decoded = decoder.decode(&encoded).unwrap();

        // Verify data integrity
        let orig_ids = original
            .column(0)
            .as_any()
            .downcast_ref::<Int64Array>()
            .unwrap();
        let dec_ids = decoded
            .column(0)
            .as_any()
            .downcast_ref::<Int64Array>()
            .unwrap();

        assert_eq!(orig_ids.len(), dec_ids.len());
        for i in 0..orig_ids.len() {
            assert_eq!(orig_ids.value(i), dec_ids.value(i));
        }
    }

    #[test]
    fn test_decode_batch_parallel() {
        let batches: Vec<RecordBatch> = (0..10).map(|_| create_test_batch(1000)).collect();

        let encoded: Vec<Vec<u8>> = batches.iter().map(|b| encode_batch(b)).collect();

        let decoder = ParquetDecoder::new();
        let decoded = decoder.decode_batch_owned(&encoded).unwrap();

        assert_eq!(decoded.len(), 10);
        for (orig, dec) in batches.iter().zip(decoded.iter()) {
            assert_eq!(orig.num_rows(), dec.num_rows());
            assert_eq!(orig.schema(), dec.schema());
        }
    }

    #[test]
    fn test_decode_large_batch() {
        let original = create_test_batch(100_000);
        let encoded = encode_batch(&original);

        let decoder = ParquetDecoder::new();
        let decoded = decoder.decode(&encoded).unwrap();

        assert_eq!(decoded.num_rows(), 100_000);
    }

    #[test]
    fn test_roundtrip_with_different_compressions() {
        let original = create_test_batch(1000);

        let compressions = [
            ParquetCompression::Uncompressed,
            ParquetCompression::Snappy,
            ParquetCompression::Zstd,
        ];

        let decoder = ParquetDecoder::new();

        for compression in compressions {
            let encoder = ParquetEncoder::with_compression(compression);
            let encoded = encoder.encode(&original).unwrap();
            let decoded = decoder.decode(&encoded).unwrap();

            assert_eq!(decoded.num_rows(), original.num_rows());
            assert_eq!(decoded.schema(), original.schema());
        }
    }

    #[test]
    fn test_custom_batch_size() {
        let original = create_test_batch(10_000);
        let encoded = encode_batch(&original);

        // Small batch size
        let decoder = ParquetDecoder::with_batch_size(100);
        let decoded = decoder.decode(&encoded).unwrap();

        // Should still get all rows
        assert_eq!(decoded.num_rows(), 10_000);
    }

    // ========== Projection Pushdown Tests ==========

    #[test]
    fn test_projection_single_column() {
        let original = create_test_batch(1000);
        let encoded = encode_batch(&original);

        let decoder = ParquetDecoder::new();

        // Decode only the first column (id)
        let projected = decoder.decode_columns(&encoded, &[0]).unwrap();

        assert_eq!(projected.num_rows(), 1000);
        assert_eq!(projected.num_columns(), 1);
        assert_eq!(projected.schema().field(0).name(), "id");

        // Verify data integrity
        let orig_ids = original
            .column(0)
            .as_any()
            .downcast_ref::<Int64Array>()
            .unwrap();
        let proj_ids = projected
            .column(0)
            .as_any()
            .downcast_ref::<Int64Array>()
            .unwrap();

        for i in 0..orig_ids.len() {
            assert_eq!(orig_ids.value(i), proj_ids.value(i));
        }
    }

    #[test]
    fn test_projection_multiple_columns() {
        let original = create_test_batch(1000);
        let encoded = encode_batch(&original);

        let decoder = ParquetDecoder::new();

        // Decode id and value columns (indices 0 and 1)
        let projected = decoder.decode_columns(&encoded, &[0, 1]).unwrap();

        assert_eq!(projected.num_rows(), 1000);
        assert_eq!(projected.num_columns(), 2);
        assert_eq!(projected.schema().field(0).name(), "id");
        assert_eq!(projected.schema().field(1).name(), "value");
    }

    #[test]
    fn test_projection_by_name_single() {
        let original = create_test_batch(1000);
        let encoded = encode_batch(&original);

        let decoder = ParquetDecoder::new();

        // Decode only the value column by name
        let projected = decoder.decode_columns_by_name(&encoded, &["value"]).unwrap();

        assert_eq!(projected.num_rows(), 1000);
        assert_eq!(projected.num_columns(), 1);
        assert_eq!(projected.schema().field(0).name(), "value");

        // Verify data integrity
        let orig_values = original
            .column(1)
            .as_any()
            .downcast_ref::<Float64Array>()
            .unwrap();
        let proj_values = projected
            .column(0)
            .as_any()
            .downcast_ref::<Float64Array>()
            .unwrap();

        for i in 0..orig_values.len() {
            assert!((orig_values.value(i) - proj_values.value(i)).abs() < 1e-10);
        }
    }

    #[test]
    fn test_projection_by_name_multiple() {
        let original = create_test_batch(1000);
        let encoded = encode_batch(&original);

        let decoder = ParquetDecoder::new();

        // Decode id and name columns by name
        let projected = decoder
            .decode_columns_by_name(&encoded, &["id", "name"])
            .unwrap();

        assert_eq!(projected.num_rows(), 1000);
        assert_eq!(projected.num_columns(), 2);
        assert_eq!(projected.schema().field(0).name(), "id");
        assert_eq!(projected.schema().field(1).name(), "name");
    }

    #[test]
    fn test_projection_empty_columns_error() {
        let original = create_test_batch(100);
        let encoded = encode_batch(&original);

        let decoder = ParquetDecoder::new();

        // Empty column list should error
        let result = decoder.decode_columns(&encoded, &[]);
        assert!(matches!(result, Err(ParquetError::InvalidColumn(_))));
    }

    #[test]
    fn test_projection_invalid_name_error() {
        let original = create_test_batch(100);
        let encoded = encode_batch(&original);

        let decoder = ParquetDecoder::new();

        // Invalid column name should error
        let result = decoder.decode_columns_by_name(&encoded, &["nonexistent"]);
        assert!(matches!(result, Err(ParquetError::InvalidColumn(_))));

        // Error message should list available columns
        if let Err(ParquetError::InvalidColumn(msg)) = result {
            assert!(msg.contains("nonexistent"));
            assert!(msg.contains("id") || msg.contains("value") || msg.contains("name"));
        }
    }

    #[test]
    fn test_projection_all_columns_equals_full_decode() {
        let original = create_test_batch(1000);
        let encoded = encode_batch(&original);

        let decoder = ParquetDecoder::new();

        // Full decode
        let full = decoder.decode(&encoded).unwrap();

        // Projection with all columns
        let projected = decoder.decode_columns(&encoded, &[0, 1, 2]).unwrap();

        // Should be equivalent
        assert_eq!(full.num_rows(), projected.num_rows());
        assert_eq!(full.num_columns(), projected.num_columns());
        assert_eq!(full.schema(), projected.schema());
    }

    #[test]
    fn test_projection_preserves_data_types() {
        let original = create_test_batch(100);
        let encoded = encode_batch(&original);

        let decoder = ParquetDecoder::new();

        // Original schema types
        let orig_schema = original.schema();

        // Projection by name
        let projected = decoder
            .decode_columns_by_name(&encoded, &["id", "value", "name"])
            .unwrap();

        // Types should match
        for (i, field) in orig_schema.fields().iter().enumerate() {
            assert_eq!(field.data_type(), projected.schema().field(i).data_type());
            assert_eq!(field.is_nullable(), projected.schema().field(i).is_nullable());
        }
    }

    // ========== Predicate Pushdown Tests ==========

    #[test]
    fn test_filter_int64_eq() {
        // 1000 rows with id 0-999
        let original = create_test_batch(1000);
        let encoded = encode_batch(&original);

        let decoder = ParquetDecoder::new();
        let filter = PredicateFilter::new("id", FilterOp::Eq, ScalarValue::Int64(500));

        let filtered = decoder.decode_with_filter(&encoded, &[filter], None).unwrap();

        // Should only have 1 row where id = 500
        assert_eq!(filtered.num_rows(), 1);
        let ids = filtered.column(0).as_any().downcast_ref::<Int64Array>().unwrap();
        assert_eq!(ids.value(0), 500);
    }

    #[test]
    fn test_filter_int64_gt() {
        // 1000 rows with id 0-999
        let original = create_test_batch(1000);
        let encoded = encode_batch(&original);

        let decoder = ParquetDecoder::new();
        let filter = PredicateFilter::new("id", FilterOp::Gt, ScalarValue::Int64(995));

        let filtered = decoder.decode_with_filter(&encoded, &[filter], None).unwrap();

        // Should have rows 996, 997, 998, 999 (4 rows)
        assert_eq!(filtered.num_rows(), 4);
        let ids = filtered.column(0).as_any().downcast_ref::<Int64Array>().unwrap();
        for i in 0..4 {
            assert!(ids.value(i) > 995);
        }
    }

    #[test]
    fn test_filter_float64_lt() {
        // 100 rows with value = i * 1.5 (so 0.0, 1.5, 3.0, ...)
        let original = create_test_batch(100);
        let encoded = encode_batch(&original);

        let decoder = ParquetDecoder::new();
        // value < 15.0 means i * 1.5 < 15.0, i.e., i < 10
        let filter = PredicateFilter::new("value", FilterOp::Lt, ScalarValue::Float64(15.0));

        let filtered = decoder.decode_with_filter(&encoded, &[filter], None).unwrap();

        // Should have rows where id < 10 (10 rows: 0-9)
        assert_eq!(filtered.num_rows(), 10);
        let values = filtered.column(1).as_any().downcast_ref::<Float64Array>().unwrap();
        for i in 0..10 {
            assert!(values.value(i) < 15.0);
        }
    }

    #[test]
    fn test_filter_multiple_and() {
        // 1000 rows with id 0-999, value = i * 1.5
        let original = create_test_batch(1000);
        let encoded = encode_batch(&original);

        let decoder = ParquetDecoder::new();
        // id > 100 AND id < 110 → 9 rows (101-109)
        let filters = vec![
            PredicateFilter::new("id", FilterOp::Gt, ScalarValue::Int64(100)),
            PredicateFilter::new("id", FilterOp::Lt, ScalarValue::Int64(110)),
        ];

        let filtered = decoder.decode_with_filter(&encoded, &filters, None).unwrap();

        assert_eq!(filtered.num_rows(), 9);
        let ids = filtered.column(0).as_any().downcast_ref::<Int64Array>().unwrap();
        for i in 0..9 {
            let id_val = ids.value(i);
            assert!(id_val > 100 && id_val < 110);
        }
    }

    #[test]
    fn test_filter_with_projection() {
        // 1000 rows, filter + projection
        let original = create_test_batch(1000);
        let encoded = encode_batch(&original);

        let decoder = ParquetDecoder::new();
        let filter = PredicateFilter::new("id", FilterOp::Ge, ScalarValue::Int64(990));

        // Filter by id, but only project value column
        let filtered = decoder.decode_with_filter(&encoded, &[filter], Some(&[1])).unwrap();

        // Should have 10 rows (990-999), but only value column
        assert_eq!(filtered.num_rows(), 10);
        assert_eq!(filtered.num_columns(), 1);
        assert_eq!(filtered.schema().field(0).name(), "value");

        // Verify values match expected (990*1.5, 991*1.5, ...)
        let values = filtered.column(0).as_any().downcast_ref::<Float64Array>().unwrap();
        for i in 0..10 {
            let expected = (990 + i) as f64 * 1.5;
            assert!((values.value(i) - expected).abs() < 1e-10);
        }
    }

    #[test]
    fn test_filter_no_matches_error() {
        let original = create_test_batch(100);
        let encoded = encode_batch(&original);

        let decoder = ParquetDecoder::new();
        // No id is 9999
        let filter = PredicateFilter::new("id", FilterOp::Eq, ScalarValue::Int64(9999));

        let result = decoder.decode_with_filter(&encoded, &[filter], None);
        // Should return EmptyData error when no rows match
        assert!(matches!(result, Err(ParquetError::EmptyData)));
    }

    #[test]
    fn test_filter_all_match() {
        let original = create_test_batch(100);
        let encoded = encode_batch(&original);

        let decoder = ParquetDecoder::new();
        // All ids are >= 0
        let filter = PredicateFilter::new("id", FilterOp::Ge, ScalarValue::Int64(0));

        let filtered = decoder.decode_with_filter(&encoded, &[filter], None).unwrap();

        // Should return all 100 rows
        assert_eq!(filtered.num_rows(), 100);
    }

    #[test]
    fn test_filter_empty_filters_falls_back() {
        let original = create_test_batch(100);
        let encoded = encode_batch(&original);

        let decoder = ParquetDecoder::new();

        // No filters should act like regular decode
        let filtered = decoder.decode_with_filter(&encoded, &[], None).unwrap();

        assert_eq!(filtered.num_rows(), 100);
        assert_eq!(filtered.num_columns(), 3);
    }

    #[test]
    fn test_filter_invalid_column_error() {
        let original = create_test_batch(100);
        let encoded = encode_batch(&original);

        let decoder = ParquetDecoder::new();
        let filter = PredicateFilter::new("nonexistent", FilterOp::Eq, ScalarValue::Int64(1));

        let result = decoder.decode_with_filter(&encoded, &[filter], None);
        assert!(matches!(result, Err(ParquetError::InvalidColumn(_))));
    }
}
