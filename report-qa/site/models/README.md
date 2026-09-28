# Local Chinese embedding model

- Original model: [BAAI/bge-small-zh-v1.5](https://huggingface.co/BAAI/bge-small-zh-v1.5).
- Original model card declares the **MIT** license.
- ONNX conversion: [Xenova/bge-small-zh-v1.5](https://huggingface.co/Xenova/bge-small-zh-v1.5).
- Pinned conversion revision: `75c43b069aac4d136ba6bc1122f995fedcfd2781`.
- Runtime variant: `onnx/model_quantized.onnx` (`q8`), 512-dimensional output.
- Downloaded-file sources, byte sizes and SHA-256 checksums: `bge-small-zh-v1.5/model-manifest.json`.

Run `node lib/download-model.mjs` from the report-qa directory. The program downloads public model files; no credentials or hosted inference service are used. Preserve this attribution when redistributing the assets.

Documents use normalized CLS embeddings. Queries receive the BGE Chinese retrieval instruction. Text longer than 480 tokens is fully split by sentence/line, encoded, combined using token-weighted averaging and normalized again; no text tail is silently discarded. Long multi-topic chunks may dilute retrieval quality, so passage-level source chunking remains preferable.
