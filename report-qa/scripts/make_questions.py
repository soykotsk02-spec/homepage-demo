"""Fixed evaluation set. Gold numbers transcribed from official PDF pages.

Not imported by retrieval or answer generation. Evaluation-only evidence.
Physical PDF pages start at 1 (printed page labels can differ).
"""
import json,pathlib
ROOT=pathlib.Path(__file__).resolve().parents[1]
sources=json.loads((ROOT/'sources.json').read_text(encoding='utf-8-sig'))
bycode={r['code']:r for r in sources}
def evidence(code,page,term,values,unit='',note=''):
    r=bycode[code]
    return {'company':r['company'],'code':code,'pdfPage':page,'sourceUrl':r['sourceUrl'],'term':term,'values':values,'unit':unit,'note':note}
def q(i,question,type,expected,items,**kwargs):
    return {'id':f'Q{i:02}','question':question,'type':type,'expectedAnswer':expected,'goldEvidence':items,**kwargs}
questions=[
 q(1,'宁波银行2025年营业收入是多少？请注明单位。','single','71,969 百万元人民币。',[evidence('002142',9,'营业收入',['71,969'],'百万元')],company='宁波银行'),
 q(2,'招商银行2025年归属于本行股东的净利润是多少？','single','150,181 百万元人民币；不能替换为净利润151,126百万元。',[evidence('600036',14,'归属于本行股东的净利润',['150,181'],'百万元')],company='招商银行'),
 q(3,'平安银行2025年末不良贷款率是多少，较2024年末变化多少？','comparison','2025年末1.05%，2024年末1.06%，下降0.01个百分点。',[evidence('000001',17,'不良贷款率',['1.05','1.06','0.01'],'% / 个百分点')],company='平安银行'),
 q(4,'宁波银行2025年末公司贷款及垫款本金是多少，同比增长多少？','single','1,073,136百万元，同比增长30.45%。',[evidence('002142',9,'公司贷款及垫款本金',['1,073,136','30.45'],'百万元 / %')],company='宁波银行'),
 q(5,'招商银行在2025年数智化转型中落地了多少个领域专精模型和场景应用？','qualitative','183个领域专精模型，856个场景应用。',[evidence('600036',8,'模型',['183','856'],'个')],company='招商银行'),
 q(6,'江苏银行2025年净息差是多少，采用什么利息净收入口径计算？','footnote','集团净息差1.73%；采用还原口径利息净收入计算，需结合表下注4。',[evidence('600919',23,'净息差',['1.73'],'%'),evidence('600919',23,'还原口径',['还原口径','利息净收'],'', '表下注4跨行文本')],company='江苏银行'),
 q(7,'苏州银行2025年的净利润与归属于母公司股东的净利润分别是多少？','comparison','净利润5,578,439千元；归母净利润5,348,303千元。',[evidence('002966',12,'净利润',['5,578,439','5,348,303'],'千元')],company='苏州银行'),
 q(8,'上海银行2025年净息差为什么下降？生息资产收益率和计息负债付息率各下降多少个百分点？','reasoning','LPR下行和存量资产重定价，资产端收益率下降快于负债成本；生息资产平均收益率下降0.46个百分点，计息负债平均付息率下降0.40个百分点。',[evidence('601229',24,'净息差',['LPR','重定价']),evidence('601229',24,'平均收益率',['0.46','0.40'],'个百分点')],company='上海银行'),
]
rev=[('600036',14,'337,532','百万元'),('000001',17,'131,442','百万元'),('002142',9,'71,969','百万元'),('600919',17,'87,942,367','千元'),('601009',16,'55,541,916','千元'),('600926',13,'38,798,611','千元'),('601838',15,'23,602,625','千元'),('002966',12,'12,355,561','千元'),('601577',12,'25,470,844','千元'),('002948',8,'14,572,778','千元'),('601187',12,'5,859,815','千元'),('601229',16,'54,761,019','千元')]
npl=[('600036',16,'0.94'),('000001',17,'1.05'),('002142',18,'0.76'),('600919',19,'0.84'),('601009',18,'0.83'),('600926',15,'0.76'),('601838',16,'0.68'),('002966',15,'0.82'),('601577',13,'1.15'),('002948',9,'0.97'),('601187',13,'0.77'),('601229',18,'1.18')]
questions.append(q(9,'请逐家列出知识库内12家银行2025年的营业收入，保留各报告原始单位并给出处。','panoramic','；'.join(f"{bycode[c]['company']} {v} {u}" for c,p,v,u in rev),[evidence(c,p,'营业收入',[v],u) for c,p,v,u in rev],crossCompany=True))
questions.append(q(10,'请逐家列出知识库内12家银行2025年末的不良贷款率，并给出各自出处。','panoramic','；'.join(f"{bycode[c]['company']} {v}%" for c,p,v in npl),[evidence(c,p,'不良贷款率',[v],'%') for c,p,v in npl],crossCompany=True))
out={'schemaVersion':1,'frozenBeforeRetrieval':True,'goldMethod':'人工从官方年报指定物理页转录；不向检索器和答案模块传入标准答案。','questions':questions}
(ROOT/'questions.json').write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print('Wrote 10 fixed questions, including 2 twelve-company panoramic questions.')
