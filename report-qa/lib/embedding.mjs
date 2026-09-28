import { pipeline, env } from '@huggingface/transformers';
import { MODEL_FOLDER, MODEL_OPTIONS, MODEL_DIMENSION, MAX_TOKENS, QUERY_PREFIX } from './model-config.mjs';

export async function createEncoder({ modelPath, wasmPath, progress, browser = false }) {
  env.allowRemoteModels = false;
  env.allowLocalModels = true;
  env.localModelPath = modelPath;
  env.backends.onnx.wasm.numThreads = 1;
  if (wasmPath) env.backends.onnx.wasm.wasmPaths = wasmPath;
  const extractor = await pipeline('feature-extraction', MODEL_FOLDER, {
    ...MODEL_OPTIONS, device: browser ? 'wasm' : 'cpu', progress_callback: progress,
  });
  return {
    tokenCount: text => extractor.tokenizer(text, { truncation: false, padding: false }).input_ids.data.length,
    async encode(text) {
      const result = await extractor(text, { pooling: 'cls', normalize: true, truncation: false });
      if (result.data.length !== MODEL_DIMENSION) throw new Error('模型输出维度不符合约定。');
      return Float32Array.from(result.data);
    },
    async dispose() { await extractor.dispose(); },
  };
}

// Split all source text by actual tokenizer length. Long chunks are fully covered,
// never silently truncated at 512 tokens. Each span gets its own CLS embedding.
export function splitForEmbedding(text, tokenCount, maxTokens = MAX_TOKENS) {
  const original = String(text ?? '').trim();
  if (!original) throw new Error('不能编码空正文。');
  if (tokenCount(original) <= maxTokens) return [original];
  const units = original.match(/[^。！？!?\n]+[。！？!?\n]*|[。！？!?\n]+/gu) ?? [original];
  const fragments = [];
  for (const unit of units) {
    let remaining = unit;
    while (remaining && tokenCount(remaining) > maxTokens) {
      const chars = Array.from(remaining);
      let lo = 1, hi = chars.length;
      while (lo < hi) {
        const mid = Math.ceil((lo + hi) / 2);
        if (tokenCount(chars.slice(0, mid).join('')) <= maxTokens) lo = mid; else hi = mid - 1;
      }
      if (tokenCount(chars.slice(0, lo).join('')) > maxTokens) throw new Error('单字符 token 长度超过上限。');
      fragments.push(chars.slice(0, lo).join(''));
      remaining = chars.slice(lo).join('');
    }
    if (remaining) fragments.push(remaining);
  }
  const spans = [];
  let pending = '';
  for (const fragment of fragments) {
    if (pending && tokenCount(pending + fragment) > maxTokens) { spans.push(pending); pending = ''; }
    pending += fragment;
  }
  if (pending) spans.push(pending);
  if (spans.join('') !== original) throw new Error('编码切分未完整覆盖原文。');
  return spans;
}

export async function encodeDocument(encoder, text) {
  const spans = splitForEmbedding(text, encoder.tokenCount);
  const vector = new Float32Array(MODEL_DIMENSION);
  let totalWeight = 0;
  for (const span of spans) {
    const weight = Math.max(1, encoder.tokenCount(span) - 2);
    const part = await encoder.encode(span);
    for (let i = 0; i < vector.length; i++) vector[i] += part[i] * weight;
    totalWeight += weight;
  }
  let norm = 0;
  for (let i = 0; i < vector.length; i++) { vector[i] /= totalWeight; norm += vector[i] ** 2; }
  norm = Math.sqrt(norm);
  if (!norm || !Number.isFinite(norm)) throw new Error('模型生成无效向量。');
  for (let i = 0; i < vector.length; i++) vector[i] /= norm;
  return { vector, spanCount: spans.length, tokenCount: totalWeight };
}

export async function encodeQuery(encoder, query) {
  const text = QUERY_PREFIX + String(query).trim();
  if (encoder.tokenCount(text) > MAX_TOKENS) throw new Error('问题过长；请缩短问题以保留完整查询内容。');
  return encoder.encode(text);
}
