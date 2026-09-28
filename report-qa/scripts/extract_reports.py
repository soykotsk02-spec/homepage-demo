"""Full-page text and row/column tables with company/section/physical-page provenance.

This pipeline is deliberately conservative: no OCR guesses, no silent cross-page
table merges, no conversion between financial units. Raw page records are kept.
"""
import argparse, concurrent.futures, hashlib, json, pathlib, re, time
from datetime import datetime, timezone
import pdfplumber
from pypdf import PdfReader
from table_extract import extract_tables
from section_map import section_for

ROOT = pathlib.Path(__file__).resolve().parents[1]
CHUNK_CHARS = 340

def clean(value):
    return re.sub(r'[ \t]+', ' ', str(value or '')).strip()

def outlines(reader):
    result=[]
    def visit(items):
        for item in items:
            if isinstance(item, list): visit(item)
            else:
                title=clean(item.get('/Title', ''))
                if re.match(r'第[一二三四五六七八九十百\d]+[章节部分]', title):
                    try: result.append((reader.get_destination_page_number(item)+1, title))
                    except Exception: pass
    try: visit(reader.outline)
    except Exception: pass
    return sorted(result)

def split_text(text, limit=CHUNK_CHARS):
    """Character spans (including tables' text fallback), with small overlap."""
    start=0
    while start<len(text):
        end=min(start+limit, len(text))
        if end<len(text):
            boundaries=[text.rfind(s,start+limit//2,end) for s in ['。','；','\n']]
            chosen=max(boundaries)
            if chosen>start: end=chosen+1
        segment=text[start:end].strip()
        if segment: yield segment, start, end
        if end>=len(text): break
        start=max(start+1,end-35)

def extract_one(args):
    report, workdir, force = args
    code=report['code']; path=workdir/report['file']; cache=workdir/'extracted'/f'{code}.json'
    pinned_maps=json.loads((ROOT/'reports'/'sections.json').read_text(encoding='utf-8'))['reports']
    pinned=next((item for item in pinned_maps if item['code']==code),None)
    if not pinned or pinned['pdfSha256']!=report['sha256']:
        raise ValueError(f'{code}: PDF changed or missing verified chapter map; recheck chapter start pages first')
    if cache.exists() and not force:
        result=json.loads(cache.read_text(encoding='utf-8'))
        if result['report']['sha256']==report['sha256']:
            for item in result['pages']+result['chunks']:
                item['section']=section_for(code,item['pdfPage'])
            result['report']['sectionMap']='reports/sections.json; verified physical-page ranges'
            cache.write_text(json.dumps(result,ensure_ascii=False,separators=(',',':')),encoding='utf-8')
            print(json.dumps({'cached':code,'chunks':len(result['chunks'])}),flush=True)
            return result
    reader=PdfReader(path); sections=outlines(reader); section='封面、重要提示与目录'
    chunks=[]; pages=[]; table_count=0; empty_pages=[]; methods={}
    with pdfplumber.open(path) as pdf:
        for n,page in enumerate(pdf.pages,1):
            text=page.extract_text(x_tolerance=2,y_tolerance=3) or ''
            text=clean(text)
            applicable=[name for p,name in sections if p<=n]
            if applicable: section=applicable[-1]
            else:
                for line in text.splitlines()[:8]:
                    match=re.search(r'第[一二三四五六七八九十百\d]+[章节]\s*[^\n]{2,35}',line)
                    if match and '...' not in line and '…' not in line: section=match.group(0).strip()
            section=section_for(code,n)
            base={k:report[k] for k in ['company','code','reportYear','sourceUrl']}
            base.update(section=section,pdfPage=n)
            page_tables=extract_tables(page)
            for table in page_tables:
                # A unit printed just above a table is outside the grid. Keep it
                # as explicit source context, never infer currency conversion.
                top=table['bbox'][1]
                nearby=page.crop((0,max(0,top-85),page.width,min(page.height,top+12))).extract_text() or ''
                notes=[line.strip() for line in nearby.splitlines() if re.search(r'(?:单位|人民币|币种).{0,20}(?:千元|百万元|亿元|元|%)',line)]
                table['unitContext']='；'.join(notes[-2:])
            if len(text)<20:empty_pages.append(n)
            record={**base,'text':text,'tables':page_tables,'width':page.width,'height':page.height}
            pages.append(record)
            for i,(segment,start,end) in enumerate(split_text(text),1):
                chunks.append({**base,'id':f'{code}-p{n:03}-x{i:02}','type':'text','text':segment,'charStart':start,'charEnd':end})
            for ti,table in enumerate(page_tables,1):
                table_count+=1
                method=table.get('method','unknown');methods[method]=methods.get(method,0)+1
                raw=table.get('rawRows') or [table['headers'],*table['rows']]
                headers=[clean(x) for x in table['headers']]
                rows=[[clean(x) for x in row] for row in table['rows']]
                table_id=f'{code}-p{n:03}-t{ti:02}'
                # Repeat full column headers, preserving column positions and blanks.
                unit_context=table.get('unitContext','')
                prefix=(('单位说明：'+unit_context+'\n') if unit_context else '')+'表头：'+' | '.join(headers)+'\n'
                batch=[]; batch_start=1; batch_length=len(prefix)
                def add_batch():
                    if not batch:return
                    body='\n'.join(' | '.join(row) for row in batch)
                    chunks.append({**base,'id':f'{table_id}-r{batch_start:03}','type':'table','text':prefix+body,'table':{'tableId':table_id,'headers':headers,'rows':list(batch),'unitContext':unit_context,'rowStart':batch_start,'rowEnd':batch_start+len(batch)-1,'bbox':table['bbox'],'method':method,'qualityFlags':table.get('qualityFlags',[])}})
                for ri,row in enumerate(rows,1):
                    rowlen=sum(map(len,row))+3*len(row)
                    if batch and batch_length+rowlen>CHUNK_CHARS:
                        add_batch();batch=[];batch_start=ri;batch_length=len(prefix)
                    if not batch: batch_start=ri
                    batch.append(row);batch_length+=rowlen
                add_batch()
            page.close()
            if n%50==0:print(json.dumps({'extracting':code,'page':n,'total':len(pdf.pages)},ensure_ascii=False),flush=True)
    result={'report':{**report,'pageCount':len(pages),'chunkCount':len(chunks),'tableCount':table_count,'lowTextPages':empty_pages,'tableMethods':methods},'pages':pages,'chunks':chunks}
    cache.parent.mkdir(parents=True,exist_ok=True)
    cache.write_text(json.dumps(result,ensure_ascii=False,separators=(',',':')),encoding='utf-8')
    print(json.dumps({'extracted':code,'pages':len(pages),'chunks':len(chunks),'tables':table_count},ensure_ascii=False),flush=True)
    return result

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--workdir',type=pathlib.Path,default=ROOT/'artifacts')
    parser.add_argument('--output',type=pathlib.Path,default=ROOT/'site'/'data')
    parser.add_argument('--workers',type=int,default=3)
    parser.add_argument('--force',action='store_true')
    parser.add_argument('--codes',default='')
    args=parser.parse_args()
    reports=json.loads((args.workdir/'downloads.json').read_text(encoding='utf-8'))
    if args.codes: reports=[r for r in reports if r['code'] in args.codes.split(',')]
    with concurrent.futures.ProcessPoolExecutor(max_workers=args.workers) as executor:
        results=list(executor.map(extract_one,[(r,args.workdir,args.force) for r in reports]))
    chunks=[chunk for result in results for chunk in result['chunks']]
    summaries=[result['report'] for result in results]
    args.output.mkdir(parents=True,exist_ok=True)
    raw=json.dumps(chunks,ensure_ascii=False,separators=(',',':'))
    (args.output/'chunks.json').write_text(raw,encoding='utf-8')
    manifest={'title':'银行年报问答研究台','reportYear':2025,'reports':summaries,'totalPages':sum(r['pageCount'] for r in summaries),'totalChunks':len(chunks),'totalTables':sum(r['tableCount'] for r in summaries),'generatedAt':datetime.now(timezone.utc).isoformat(),'chunksSha256':hashlib.sha256(raw.encode()).hexdigest(),'chunking':{'maxBodyCharacters':CHUNK_CHARS,'overlapCharacters':35,'physicalPageNumbering':True,'preserveTableRows':True},'limitations':['未自动合并跨页表；复杂合并表头保留空单元格和原表坐标。','图片页未进行OCR；低文本页在各报告lowTextPages中列出。','年报口径与单位不做自动统一，跨公司比较需核对原表。']}
    (args.output/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'complete':True,'reports':len(summaries),'pages':manifest['totalPages'],'chunks':len(chunks),'tables':manifest['totalTables']},ensure_ascii=False))

if __name__=='__main__': main()
