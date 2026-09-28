"""Download the full official reports listed in sources.json; no third-party copies."""
import argparse, concurrent.futures, hashlib, json, pathlib, time, urllib.request
from urllib.parse import urlparse

ROOT = pathlib.Path(__file__).resolve().parents[1]

def download(report, directory):
    url = report['sourceUrl']
    if urlparse(url).hostname not in {'static.cninfo.com.cn', 'www.sse.com.cn', 'www.szse.cn', 'disc.static.szse.cn'}:
        raise ValueError('Unexpected report host: ' + url)
    path = directory / (report['code'] + '-2025.pdf')
    if not path.exists():
        for attempt in range(3):
            try:
                request = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (AnnualReportResearch/1.0)'})
                with urllib.request.urlopen(request, timeout=120) as response:
                    data = response.read()
                if not data.startswith(b'%PDF'):
                    raise ValueError('Response is not a PDF')
                temporary = path.with_suffix('.partial')
                temporary.write_bytes(data)
                temporary.replace(path)
                break
            except Exception:
                if attempt == 2: raise
                time.sleep(2 * (attempt + 1))
    data = path.read_bytes()
    if not data.startswith(b'%PDF'): raise ValueError(f'Invalid cached PDF: {path}')
    result = {**report, 'file': path.name, 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
    print(json.dumps({'downloaded': report['company'], 'bytes': len(data)}, ensure_ascii=False), flush=True)
    return result

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--sources', type=pathlib.Path, default=ROOT / 'sources.json')
    parser.add_argument('--workdir', type=pathlib.Path, default=ROOT / 'artifacts')
    args = parser.parse_args()
    args.workdir.mkdir(parents=True, exist_ok=True)
    sources = json.loads(args.sources.read_text(encoding='utf-8-sig'))
    if isinstance(sources, dict): sources = sources.get('reports', sources.get('sources'))
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
        results = list(executor.map(lambda report: download(report, args.workdir), sources))
    (args.workdir / 'downloads.json').write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')

if __name__ == '__main__': main()
