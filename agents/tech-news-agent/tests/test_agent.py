import argparse
import contextlib
from html import escape
import io
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch, Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import agent


def local_context(text='正文中记录了需要先核对资料来源，再开展分析。'):
    return {
        'schemaVersion': 1,
        'assets': [{'assetId': 'fixture', 'title': '测试学习资料',
                    'sha256': 'fixture-sha256', 'paragraphCount': 1}],
        'chunks': [{'chunkId': 'fixture:p1', 'assetId': 'fixture',
                    'title': '测试学习资料', 'anchor': '正文段落 1', 'text': text}],
        'limitations': ['仅使用测试清单中这一份资料。'],
    }


class ReportTests(unittest.TestCase):
    def report(self):
        return dict(title='科技<简报>', generatedAt='2026-09-26', overview='<script>x</script>',
                    runId='test', items=[dict(source='BBC', publishedAt='今天', freshness='recent',
                    title='A', summary='正文', whyItMatters='影响', url='https://example.com/a?q=x&b=y')],
                    localConnections=[dict(itemId='one', newsTitle='新闻标题',
                        sourceTitle='测试学习资料', anchor='正文段落 1',
                        quote='先核对资料来源，再开展分析。', relationship='可以用同样的方法核对本条新闻。',
                        nextStep='打开原文与资料，对照列出两条证据。', assetId='fixture', chunkId='fixture:p1')],
                    learningSuggestions=['练习'], localContext='仅使用测试清单中一份资料；其它资料未读取。',
                    sourceNote='来自RSS摘要')

    def test_escapes_text_and_preserves_valid_link(self):
        report, plain = agent.render_report(self.report(), 'me@example.com')
        self.assertNotIn('<script>', report)
        self.assertIn('&lt;script&gt;', report)
        self.assertIn('q=x&amp;b=y', report)
        self.assertIn('来自RSS摘要', plain)

    def test_rejects_non_https_or_credentials(self):
        for url in ('javascript:alert(1)', 'http://example.com', 'https://u:p@example.com'):
            data = self.report()
            data['items'][0]['url'] = url
            with self.assertRaises(ValueError):
                agent.render_report(data, 'me@example.com')

    def test_renders_local_quotes_connections_actions_and_escapes_every_field(self):
        data = self.report()
        connection = data['localConnections'][0]
        for field in connection:
            connection[field] = f'{field}<img src=x onerror="alert(1)">&\'引用'
        data['localContext'] = '只读一份资料；未读取<其它资料> & 附件。'
        report, plain = agent.render_report(data, 'me@example.com')
        self.assertIn('结合你的本地资料', report)
        self.assertIn('与这条新闻的联系：', report)
        self.assertIn('可以接着做：', report)
        self.assertIn('资料范围与限制：', report)
        self.assertNotIn('<img', report)
        for value in connection.values():
            self.assertIn(escape(value, quote=True), report)
        self.assertIn(escape(data['localContext']), report)
        for field in ('newsTitle', 'sourceTitle', 'anchor', 'quote', 'relationship', 'nextStep'):
            self.assertIn(connection[field], plain)
        self.assertIn(data['localContext'], plain)

    def test_rejects_old_summary_only_report_without_local_connections(self):
        for connections in (None, "旧的背景概括"):
            data = self.report()
            data['localConnections'] = connections
            with self.assertRaisesRegex(ValueError, '实际本地资料'):
                agent.render_report(data, 'me@example.com')

    def test_honest_no_match_is_visible_without_fabricated_references(self):
        data = self.report()
        data['localConnections'] = []
        data['localContext'] = '读取了平台研究，但本次硬件新闻缺乏直接联系。'
        report, plain = agent.render_report(data, 'me@example.com')
        self.assertIn('没有找到足够可靠', report)
        self.assertIn(data['localContext'], plain)

    def test_os_lock_blocks_concurrent_process_handle(self):
        with tempfile.TemporaryDirectory() as tmp:
            lock = Path(tmp) / 'agent.lock'
            with agent.exclusive_lock(lock):
                with self.assertRaises(RuntimeError):
                    with agent.exclusive_lock(lock):
                        pass
            with agent.exclusive_lock(lock):
                pass


class DeliveryGuards(unittest.TestCase):
    def test_confirmed_receipt_repairs_both_ledger_and_run_status(self):
        mock_collector = types.SimpleNamespace(collect=lambda *a: self.fail('must not collect'))
        mock_analyzer = types.SimpleNamespace(analyze=lambda *a: self.fail('must not analyze'))
        mock_mailer = types.SimpleNamespace(GmailMailer=lambda *a: self.fail('must not send'),
                                            MailNotSent=RuntimeError, MailOutcomeUnknown=RuntimeError)
        with tempfile.TemporaryDirectory() as tmp, patch.object(agent, 'ROOT', Path(tmp)), patch.dict(
            sys.modules, collector=mock_collector, analyzer=mock_analyzer, gmail_delivery=mock_mailer):
            key='demo:fixed:me@example.com'
            state=agent.find_state(Path(tmp)/'data',key)
            run=Path(tmp)/'data/runs/stable'
            agent.write_json(state,dict(status='sending',runId='stable',messageId='<fixed@example.com>'))
            agent.write_json(run/'status.json',dict(status='preview_ready',sent=False))
            agent.write_json(run/'delivery-receipt.json',dict(status='sent',key=key,messageId='<fixed@example.com>',
                             recipient='me@example.com',acceptedAtUtc='2026-09-26T12:00:00Z'))
            args=argparse.Namespace(send=True,demo_id='fixed',trigger='manual')
            self.assertEqual(agent.run_pipeline(args,{'mail':{'recipient':'me@example.com'}}),0)
            self.assertEqual(agent.read_json(state)['status'],'sent')
            self.assertTrue(agent.read_json(run/'status.json')['sent'])

    def test_failed_model_attempt_can_retry_same_delivery_without_duplicate_mail(self):
        calls=[]
        profiles=[]
        def collect(run):
            news=dict(runId=run.name,generatedAtUtc='2026-09-26T12:00:00Z',items=[{'id':'one'}])
            agent.write_json(run/'news.json',news)
            return news
        def analyze(news,profile,attempt,codex):
            calls.append(attempt)
            profiles.append(profile)
            (attempt/'analysis-prompt.txt').write_text('attempt',encoding='utf-8')
            if len(calls)==1:
                raise RuntimeError('temporary model error')
            report=ReportTests().report()
            report['runId']=news['runId']
            return report
        mail=Mock()
        mail.send.return_value=dict(status='sent',acceptedAtUtc='2026-09-26T12:05:00Z',recipient='me@example.com')
        collector=types.SimpleNamespace(collect=Mock(side_effect=collect))
        analyzer=types.SimpleNamespace(analyze=analyze)
        mailer=types.SimpleNamespace(GmailMailer=lambda c, run_dir=None:mail,MailNotSent=type('NotSent',(Exception,),{}),
                                    MailOutcomeUnknown=type('Unknown',(Exception,),{}))
        library=types.SimpleNamespace(collect_local_context=Mock(side_effect=[
            local_context('本地资料第一版正文。'), local_context('重试前已更新的第二版正文。')]))
        with tempfile.TemporaryDirectory() as tmp,patch.object(agent,'ROOT',Path(tmp)),patch.dict(
                sys.modules,collector=collector,analyzer=analyzer,gmail_delivery=mailer,local_library=library):
            agent.write_json(Path(tmp)/'profile-context.json',{})
            args=argparse.Namespace(send=True,demo_id='retry',trigger='manual')
            config={'mail':{'recipient':'me@example.com'},'codexExecutable':'codex.exe'}
            self.assertEqual(agent.run_pipeline(args,config),1)
            self.assertEqual(agent.run_pipeline(args,config),0)
            self.assertNotEqual(calls[0],calls[1])
            self.assertEqual(calls[0].parent,calls[1].parent)
            self.assertEqual(collector.collect.call_count,1)
            self.assertEqual(library.collect_local_context.call_count,2)
            self.assertEqual(profiles[0]['localLibrary']['chunks'][0]['text'],'本地资料第一版正文。')
            self.assertEqual(profiles[1]['localLibrary']['chunks'][0]['text'],'重试前已更新的第二版正文。')
            mail.send.assert_called_once()

    def test_sent_and_uncertain_do_not_collect_or_send(self):
        mock_collector = types.SimpleNamespace(collect=lambda *a: self.fail('must not collect'))
        mock_analyzer = types.SimpleNamespace(analyze=lambda *a: self.fail('must not analyze'))
        mock_mailer = types.SimpleNamespace(GmailMailer=lambda *a: self.fail('must not send'),
                                            MailNotSent=RuntimeError, MailOutcomeUnknown=RuntimeError)
        with tempfile.TemporaryDirectory() as tmp, patch.object(agent, 'ROOT', Path(tmp)), patch.dict(
            sys.modules, collector=mock_collector, analyzer=mock_analyzer, gmail_delivery=mock_mailer):
            args = argparse.Namespace(send=True, demo_id='fixed', trigger='manual')
            config = {'mail': {'recipient': 'me@example.com'}}
            state_path = agent.find_state(Path(tmp) / 'data', 'demo:fixed:me@example.com')
            for status, code in [('sent', 0), ('sending', 3), ('uncertain', 3)]:
                agent.write_json(state_path, {'status': status, 'runId': 'stable'})
                self.assertEqual(agent.run_pipeline(args, config), code)


class LocalContextPipelineTests(unittest.TestCase):
    def test_current_local_evidence_reaches_model_and_read_status_is_saved(self):
        context = local_context('这是本次实际读取的独特正文，不是背景概括。')
        library = types.SimpleNamespace(collect_local_context=Mock(return_value=context))
        news = {'runId': 'fixture', 'generatedAtUtc': '2026-09-26T12:00:00Z', 'items': [{'id': 'one'}]}
        collector = types.SimpleNamespace(collect=Mock(return_value=news))
        analyzer = types.SimpleNamespace(analyze=Mock(return_value=ReportTests().report()))
        mailer = types.SimpleNamespace(GmailMailer=Mock(), MailNotSent=RuntimeError, MailOutcomeUnknown=RuntimeError)
        with tempfile.TemporaryDirectory() as tmp, patch.object(agent, 'ROOT', Path(tmp)), patch.dict(
                sys.modules, collector=collector, analyzer=analyzer, local_library=library, gmail_delivery=mailer):
            agent.write_json(Path(tmp) / 'profile-context.json', {'background': '现有概括'})
            args = argparse.Namespace(send=False, demo_id='', trigger='manual')
            self.assertEqual(agent.run_pipeline(args, {'mail': {'recipient': 'me@example.com'},
                                                      'codexExecutable': 'codex.exe'}), 0)
            run_dir = next((Path(tmp) / 'data/runs').iterdir())
            library.collect_local_context.assert_called_once_with(Path(tmp) / 'local-sources.json', run_dir, news)
            passed_profile = analyzer.analyze.call_args.args[1]
            self.assertEqual(passed_profile['background'], '现有概括')
            self.assertIs(passed_profile['localLibrary'], context)
            status = agent.read_json(run_dir / 'status.json')
            self.assertEqual(status['localReadStatus'], 'completed')
            self.assertEqual((status['localSourceCount'], status['localChunkCount']), (1, 1))
            self.assertIn('localReadAtUtc', status)
            self.assertIn('实际读取 1 份本地资料、1 个正文段落',
                          (run_dir / 'events.jsonl').read_text(encoding='utf-8'))
            mailer.GmailMailer.assert_not_called()

    def test_missing_or_empty_local_sources_stop_before_analysis_and_send(self):
        for outcome in (FileNotFoundError('fixture missing'), {'assets': [], 'chunks': []}):
            with self.subTest(outcome=type(outcome).__name__):
                collect_local = Mock(side_effect=outcome) if isinstance(outcome, Exception) else Mock(return_value=outcome)
                library = types.SimpleNamespace(collect_local_context=collect_local)
                collector = types.SimpleNamespace(collect=Mock(return_value={
                    'generatedAtUtc': '2026-09-26T12:00:00Z', 'items': [{'id': 'one'}]}))
                analyzer = types.SimpleNamespace(analyze=Mock())
                mail = Mock()
                mailer = types.SimpleNamespace(GmailMailer=Mock(return_value=mail),
                                               MailNotSent=RuntimeError, MailOutcomeUnknown=RuntimeError)
                with tempfile.TemporaryDirectory() as tmp, patch.object(agent, 'ROOT', Path(tmp)), patch.dict(
                        sys.modules, collector=collector, analyzer=analyzer, local_library=library, gmail_delivery=mailer):
                    agent.write_json(Path(tmp) / 'profile-context.json', {'background': '不可用此概括兜底'})
                    args = argparse.Namespace(send=True, demo_id='missing', trigger='manual')
                    self.assertEqual(agent.run_pipeline(args, {'mail': {'recipient': 'me@example.com'},
                                                              'codexExecutable': 'codex.exe'}), 1)
                    analyzer.analyze.assert_not_called()
                    mail.send.assert_not_called()
                    run_dir = next((Path(tmp) / 'data/runs').iterdir())
                    status = agent.read_json(run_dir / 'status.json')
                    self.assertEqual(status['localReadStatus'], 'failed')
                    self.assertEqual(status['status'], 'failed')
                    self.assertFalse(status['sent'])
                    self.assertFalse((run_dir / 'report.html').exists())

    def test_check_requires_local_manifest_without_reading_personal_sources(self):
        mailer = types.SimpleNamespace(GmailMailer=Mock())
        with tempfile.TemporaryDirectory() as tmp, patch.object(agent, 'ROOT', Path(tmp)), patch.dict(
                sys.modules, gmail_delivery=mailer):
            root = Path(tmp)
            executable = root / 'fixture-codex.exe'
            executable.write_bytes(b'')
            agent.write_json(root / 'config.json', {'codexExecutable': str(executable), 'mail': {}})
            agent.write_json(root / 'profile-context.json', {})
            missing_output = io.StringIO()
            with contextlib.redirect_stdout(missing_output):
                self.assertEqual(agent.main(['check']), 2)
            self.assertFalse(json.loads(missing_output.getvalue())['localSourcesManifestReady'])
            agent.write_json(root / 'local-sources.json', {'sources': []})
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(agent.main(['check']), 0)


if __name__ == '__main__':
    unittest.main()

