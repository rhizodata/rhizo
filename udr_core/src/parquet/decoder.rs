//! Parquet decoding to Arrow data.
//!
//! This module provides high-performance Parquet decoding using the Rust
//! parquet crate, with support for parallel batch decoding via Rayon.

use arrow::record_batch::RecordBatch;
use bytes::Bytes;
use parquet::arrow::arrow_reader::ParquetRecordBatchReaderBuilder;
use rayon::prelude::*;

use super::error::ParquetError;

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
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::parquet::{ParquetCompression, ParquetEncoder};
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
}
