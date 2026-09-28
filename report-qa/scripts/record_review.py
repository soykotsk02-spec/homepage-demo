"""Human evidence review of the saved hybrid outputs, separate from metrics.

This script records the review for this exact frozen input/index; it is not an
automatic judge and is never used by the query UI's answer builder.
"""
import json,pathlib
ROOT=pathlib.Path(__file__).resolve().parents[1]
path=ROOT/'site'/'data'/'evaluation.json'
data=json.loads(path.read_text(encoding='utf-8'))
reviews=[
 ('correct','Top-10原文包含完整的2025年、营业收入71,969及百万元单位。p94母行57,947是干扰片段，不能替代集团指标。',['002142-p009-t01-r001']),
 ('correct','p19替代证据明确区分集团净利润151,126与归属于本行股东净利润150,181百万元，并有2025列；首条p249缺单位，不能只读首条。规范页p14未召回不等于没有答案。',['600036-p019-t01-r011']),
 ('correct','p23完整表头与行显示2025末1.05%、2024末1.06%、下降0.01个百分点，年份与变化单位一致。',['000001-p023-t03-r001']),
 ('correct','p9第10条证据完整给出公司贷款及垫款本金1,073,136百万元、增长30.45%。前排日均规模及母行分类贷款不能替代。',['002142-p009-t02-r004','002142-p009-x04']),
 ('correct','p45第1条直接给出183个领域专精模型和856个场景应用；p8再次支持，没有拿科技项目数替代。',['600036-p045-x02','600036-p008-x07']),
 ('correct','p23正文的集团净息差1.73%与脚注4的还原口径利息净收入联合回答完整，脚注没有丢失。',['600919-p023-x01','600919-p023-x04']),
 ('partial','p19第9条给出全年净利润5,578,439千元、归母5,348,303千元；但首条p13混入季度数据，且季度表头和真实单位在切块时丢失，又接上下一节的单位说明。已修复把正文任意单位提升为全块标签的展示缺陷，仍须人工排除这条残缺季度证据。',['002966-p019-t01-r026','002966-p013-x03']),
 ('correct','p24多个片段联合支持LPR下行、存量资产重定价，资产收益率下降0.46个百分点、负债付息率下降0.40个百分点。二者之差0.06对应净利差，不能误当净息差降幅；净息差实际下降0.01个百分点。',['601229-p024-x01','601229-p024-x02','601229-p024-x03']),
 ('partial','12家公司均返回候选，但只3家同时具备完整年份、金额、单位和主体；5家上下文不足，4家未答出目标收入。Top-2经常优先选中分部收入、2024年度或报表尾部，不能把返回12家公司当成答全。',[]),
 ('partial','按集团/整体可比口径检查：5家完整、3家部分、4家缺失。常被地区、行业、产品不良率替代；南京0.80%是真实母公司全行值，集团为0.83%，应说明口径差别。若接受母公司口径，南京可另计完整。',[]),
]
panorama=[
 [('招商银行','partial','337,532与2025合并收入明确，但两块均缺单位。',['600036-p251-t01-r007','600036-p252-x01']),('平安银行','correct','131,442、2025全年、总收入和百万元齐全。',['000001-p026-t01-r016','000001-p229-x01']),('宁波银行','incorrect','仅分部收入，另一块1,733,314是贷款总额；缺总收入71,969。',['002142-p033-x01','002142-p204-t01-r022']),('江苏银行','incorrect','仅营业利润、支出及2024分部；缺87,942,367。',['600919-p192-t01-r020','600919-p114-t01-r019']),('南京银行','incorrect','两块均2024年度50,273,070，不能回答2025。',['601009-p207-t01-r006','601009-p207-x02']),('杭州银行','partial','38,798,611存在，但年份、单位、合计列头缺失且抵消与合计混格；另一块为2024。',['600926-p231-t01-r001','600926-p232-t01-r001']),('成都银行','incorrect','只有利润和附注尾句，没有营业收入。',['601838-p137-t01-r001','601838-p227-x06']),('苏州银行','partial','12,355,561,062元是真实原值，但集团/本行和年份上层列头缺失。',['002966-p109-t01-r017','002966-p109-t01-r024']),('长沙银行','correct','第一块明确2025总额25,470,844千元，第二块2024分部为干扰。',['601577-p028-t01-r007','601577-p218-x04']),('青岛银行','partial','14,572,778千元和年份存在，两组2025列缺集团/本行上层表头。',['002948-p019-x05','002948-p148-t01-r016']),('厦门银行','partial','5,859,815千元存在，两组年度列缺集团/本行区分；另块仅厦门地区。',['601187-p121-t01-r001','601187-p032-x02']),('上海银行','correct','集团、2025、54,761,019千元与547.61亿元相互印证。',['601229-p047-x01','601229-p047-t01-r017'])],
 [('招商银行','correct','本集团、报告期末、不良率0.94%明确。',['600036-p031-x03','600036-p035-x01']),('平安银行','correct','日期及整体1.05%清楚，企业/个人分项明确区分。',['000001-p023-t03-r001','000001-p053-x04']),('宁波银行','incorrect','只有地区和行业比率，没有整体0.76%。',['002142-p030-x01','002142-p030-t01-r001']),('江苏银行','partial','0.84%正确但句首指标名、主体、期末限定切掉；另一块为行业比率。',['600919-p042-t01-r001','600919-p041-x03']),('南京银行','partial','正确母公司全行0.80%及对公0.63%；未说明与集团0.83%的口径差异。接受母公司口径时可以另计完整。',['601009-p038-x01','601009-p039-x01']),('杭州银行','incorrect','只有公司、个人及产品比率，缺整体0.76%。',['600926-p023-t01-r004','600926-p023-t01-r001']),('成都银行','correct','第二块明确报告期末本行不良贷款比例0.68%。',['601838-p036-t03-r001','601838-p048-x02']),('苏州银行','incorrect','一块仅表头、另一块仅行业比率，缺整体0.82%。',['002966-p037-x06','002966-p036-x04']),('长沙银行','incorrect','仅公司0.61%、个人2.43%及迁徙率，缺整体1.15%。',['601577-p035-x02','601577-p035-x01']),('青岛银行','correct','第二块明确本公司期末0.97%；第一块1.50%是行业数据干扰。',['002948-p015-x05','002948-p035-x01']),('厦门银行','partial','0.77%和指标名保留，但期末日期与公司限定在句首被切断。',['601187-p044-x03','601187-p044-x05']),('上海银行','correct','第一块期末整体1.18%可识别，其他分项具有限定，原页支持集团口径。',['601229-p023-x03','601229-p062-x03'])]
]
labels={'correct':'正确（证据足以作答）','partial':'部分正确 / 未完整回答','incorrect':'未答出目标事实'}
for i,(question,(status,reason,ids)) in enumerate(zip(data['questions'],reviews)):
    available={hit['id'] for hit in question['results']['hybrid']['hits']}
    assert set(ids)<=available, (question['id'],set(ids)-available)
    review={'status':status,'reason':reason,'checkedEvidence':ids,'scope':'默认hybrid实际展示的完整Top-10；全景题每家公司Top-2','method':'人工核对实际摘录与官方原页；自动数字覆盖仅作辅助'}
    if i>=8:
        review['companies']=[{'company':company,'status':s,'reason':r,'checkedEvidence':cids} for company,s,r,cids in panorama[i-8]]
        for company in review['companies']:assert set(company['checkedEvidence'])<=available
        review['companyCounts']={s:sum(item['status']==s for item in review['companies']) for s in labels}
    question['results']['hybrid']['manualReview']=review
    question['verdict']=labels[status]
    question['errorAnalysis']=reason
data['summary']['manualReviewMode']='hybrid'
data['summary']['manualCounts']={'correct':7,'partial':3,'incorrect':0}
data['summary']['manualReviewCriteria']='正确表示当前完整摘录中证据足以回答，并非Top-1正确率或生成式答案准确率。部分表示口径、表头或覆盖仍有缺口。'
data['summary']['note']+=' 人工逐题核查默认混合检索：7题证据充分，3题部分回答；两路基线未另做人工正确率。'
path.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
reviewfile={'provenance':data['provenance'],'criteria':data['summary']['manualReviewCriteria'],'questions':[{'id':q['id'],'question':q['question'],**q['results']['hybrid']['manualReview']} for q in data['questions']]}
(ROOT/'reports'/'manual-review.json').write_text(json.dumps(reviewfile,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print('Recorded 10 manually reviewed hybrid questions; no baseline accuracy claims.')
