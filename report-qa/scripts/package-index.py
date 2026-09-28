"""Package public index/model/runtime into deterministic, checksum-verified Git blobs.

The original three data outputs retain their names, compression and hashes.
Optional output.root is relative to site/; omitted roots continue to mean data/.
Small model configuration and runtime .mjs files remain ordinary repository files.
"""
import gzip
import hashlib
import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
PART_BYTES = 500_000
SPECS = [
    ("data", "chunks.json", "gzip"),
    ("data", "vectors.bin", "none"),
    ("data", "evaluation.json", "gzip"),
    ("models", "bge-small-zh-v1.5/onnx/model_quantized.onnx", "gzip"),
    ("vendor", "ort-wasm-simd-threaded.jsep.wasm", "gzip"),
    ("vendor", "ort-wasm-simd-threaded.wasm", "gzip"),
]


def digest(data):
    return hashlib.sha256(data).hexdigest()


def write_if_changed(path, data):
    # Identical parts are not deleted/replaced while another process uploads them.
    # Only the manifest's referenced parts define the package.
    if path.exists() and path.read_bytes() == data:
        return
    temporary = path.with_name(path.name + ".partial")
    temporary.write_bytes(data)
    temporary.replace(path)


def main():
    target = ROOT / "datasets"
    target.mkdir(exist_ok=True)
    outputs = []
    names = set()
    for root, relative, compression in SPECS:
        raw = (ROOT / "site" / root / relative).read_bytes()
        packed = gzip.compress(raw, compresslevel=9, mtime=0) if compression == "gzip" else raw
        parts = []
        prefix = pathlib.PurePosixPath(relative).name
        for index, start in enumerate(range(0, len(packed), PART_BYTES)):
            filename = f"{prefix}.part{index:03}.bin"
            if filename in names:
                raise ValueError(f"Package part name collision: {filename}")
            names.add(filename)
            part = packed[start:start + PART_BYTES]
            write_if_changed(target / filename, part)
            parts.append({"path": filename, "bytes": len(part), "sha256": digest(part)})
        output = {"path": relative, "bytes": len(raw), "sha256": digest(raw),
                  "compression": compression, "parts": parts}
        if root != "data":
            output["root"] = root
        outputs.append(output)
    encoded = (json.dumps({"schemaVersion": 1, "outputs": outputs}, indent=2) + "\n").encode("utf-8")
    write_if_changed(target / "package.json", encoded)
    print(json.dumps({"packaged": [{"root": o.get("root", "data"), "path": o["path"],
                                    "parts": len(o["parts"])} for o in outputs]}))


if __name__ == "__main__":
    main()