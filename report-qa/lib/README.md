# Reproducible local retrieval

Run from the `report-qa` directory with Node.js 20 or later:

```sh
pnpm install --ignore-scripts --lockfile-dir lib
node lib/download-model.mjs
node scripts/embed.mjs
node scripts/build-browser.mjs
node --test lib/*.test.mjs
```

The checked-in lockfile is `lib/pnpm-lock.yaml`. Package versions are pinned. The model downloader verifies fixed SHA-256 values in `lib/model-manifest.json`, including the 24 MB quantized model. In a network environment requiring a proxy, configure that proxy outside the repository; no network credentials are part of this project. The model, WASM and bundle can instead be provisioned at deployment using their checked-in manifests.

## Build input and output

`node scripts/embed.mjs [chunksPath] [outputDirectory]` defaults to `site/data/chunks.json`. Input is a nonempty array of chunks with unique `id`, `company`, `code`, `reportYear`, `section`, `pdfPage`, `sourceUrl`, `text`, and optional `type`/`table`.

Output is `vectors.bin` (little-endian Float32, normalized 512-dimensional rows in input order) and `index-meta.json`. The metadata records model revision, library version, source/vector hashes, chunk IDs, long-text span counts, retrieval parameters and duration. Long chunks are fully encoded as tokenizer-bounded sentence/line spans, combined by token-weighted mean and normalized. Source text is never silently cut off at the model limit. Interrupted runs resume every 25 completed chunks only when the source hash and model settings match. Temporary `.embed-*` files should not be published.

No fixed answer list is included. Dense scores come from model vectors; BM25 scores come from corpus term/document frequencies; hybrid results use reciprocal-rank fusion with `k=60` over each method's top 50. Scores are ranking signals, not confidence probabilities. Float32/quantized inference can differ slightly across hardware, so compare metrics and ranked evidence with an appropriate numerical tolerance rather than expecting browser/Node bitwise identity.

## Shared Node interface

```js
import { buildIndex, attachVectors, search } from './lib/search.mjs';
import { createEncoder, encodeQuery } from './lib/embedding.mjs';
const index = buildIndex(chunks);
attachVectors(index, vectors, 512); // Float32Array in chunk order
const encoder = await createEncoder({ modelPath: '/absolute/path/site/models/' });
const vector = await encodeQuery(encoder, question);
const result = search(index, question, vector, { mode: 'hybrid', topK: 10 });
await encoder.dispose();
```

BM25 mode accepts a null query vector. `company` or `companies` filters match exact company names or codes. Explicit filters take priority over names in a question. Otherwise recognized company names or common bank aliases constrain ordinary search automatically. `crossCompany:true` uses every company unless an explicit filter is provided; `perCompany:2` returns each company's own top two. `coverage` counts companies with retrieved candidates, not companies with proven answers.

## Browser worker protocol

Create `new Worker('./worker.js', { type: 'module' })`.

- Send `{type:'init',requestId}`. Receive an early `{type:'ready',requestId,partial:true,capabilities:{bm25:true,dense:false},stats}`. Dense initialization finishes with a second ready event containing `partial:false` and `dense:true`.
- `stats` has `chunkCount`, `dimension`, `model`, and `companies:[{company,code,count}]`.
- Progress is `{type:'progress',requestId,stage,message,progress?}`. `stage:'dense-unavailable'` leaves BM25 available and explains the failure.
- Send `{type:'search',requestId,query,mode:'hybrid',topK:10,company?,companies?,crossCompany?,perCompany?}`.
- Results are `{type:'results',requestId,query,mode,hits,groups?,coverage?,timings,notes}`. Each hit preserves its original chunk metadata and adds `score`, `denseScore`, `bm25Score`, `denseRank`, `bm25Rank`, `hybridRank`. Uncomputed dense values are null. Cross-company `hits` flattens all groups rather than dropping companies at a global top-ten boundary.
- Errors are `{type:'error',requestId,code,message}`. Requesting semantic retrieval before it is ready returns `DENSE_NOT_READY`; it never silently pretends BM25 is semantic retrieval.

All query encoding runs in the browser with locally served resources. Data vectors and source text are hash-checked before dense search. The embedding model is not a text-generation model: answers must be assembled from retrieved evidence, or use a separately disclosed generator.

## Licenses and primary sources

- BGE model: [BAAI model card](https://huggingface.co/BAAI/bge-small-zh-v1.5), MIT declaration; [Xenova ONNX conversion](https://huggingface.co/Xenova/bge-small-zh-v1.5).
- [Transformers.js](https://huggingface.co/docs/transformers.js/v3.8.1/en/index), Apache-2.0. License included in `site/vendor/`.
- [ONNX Runtime](https://github.com/microsoft/onnxruntime), MIT. License included in this directory and `site/vendor/`.
- Runtime download URLs and hashes: `site/vendor/runtime-manifest.json`.
