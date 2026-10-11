"""Regression checks against the separately authored, real source/answer audit.

Run from any directory: python -m unittest discover -s scripts -p test_review_deliverables.py
The tests never modify evaluation, audit, manifest, or deliverable files.
"""
from __future__ import annotations

from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / 'scripts/review_deliverables.py'
spec = importlib.util.spec_from_file_location('review_deliverables_under_test', MODULE_PATH)
module = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(module)


class ValidateReviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        review_path = ROOT / 'reports/source-answer-review.json'
        if not review_path.is_file():
            raise unittest.SkipTest('The real source-answer-review.json has not yet been authored')
        cls.evaluation_bytes = (ROOT / 'site/data/evaluation.json').read_bytes()
        cls.answer_bytes = (ROOT / 'site/answer.mjs').read_bytes()
        cls.evaluation = json.loads(cls.evaluation_bytes)
        cls.review = json.loads(review_path.read_text(encoding='utf-8'))
        cls.manifest = json.loads((ROOT / 'site/data/manifest.json').read_text(encoding='utf-8'))
        cls.originals = {q['id']: q for q in cls.evaluation['questions']}
        cls.report_map = {r['code']: r for r in cls.manifest['reports']}

    def setUp(self):
        self.candidate = deepcopy(self.review)

    def validate(self, *, evaluation_bytes=None, answer_bytes=None):
        return module.validate(
            self.evaluation_bytes if evaluation_bytes is None else evaluation_bytes,
            self.candidate, self.manifest,
            self.answer_bytes if answer_bytes is None else answer_bytes,
        )

    def panoramic(self):
        return next(q for q in self.candidate['questions']
                    if self.originals[q['id']].get('crossCompany'))

    def single(self):
        return next(q for q in self.candidate['questions']
                    if not self.originals[q['id']].get('crossCompany'))

    def test_real_review_validates_without_mutation(self):
        original = deepcopy(self.candidate)
        result = self.validate()
        self.assertEqual(result['summary']['questionCount'], 10)
        self.assertEqual(result['summary']['retrievalRuns'], 30)
        self.assertEqual(result['summary']['reviewedUnits'], 96)
        self.assertEqual(self.candidate, original)

    def test_rejects_stale_evaluation_hash(self):
        # Extra JSON whitespace preserves semantics but changes frozen bytes.
        with self.assertRaises(ValueError):
            self.validate(evaluation_bytes=self.evaluation_bytes + b'\n')

    def test_rejects_stale_answer_module_hash(self):
        with self.assertRaises(ValueError):
            self.validate(answer_bytes=self.answer_bytes + b'\n')

    def test_rejects_missing_method(self):
        del self.single()['methods']['dense']
        with self.assertRaises(ValueError):
            self.validate()

    def test_rejects_missing_panoramic_company(self):
        self.panoramic()['methods']['bm25']['companies'].pop()
        with self.assertRaises(ValueError):
            self.validate()

    def test_rejects_duplicate_company_replacing_another(self):
        companies = self.panoramic()['methods']['dense']['companies']
        companies[-1] = deepcopy(companies[0])
        with self.assertRaises(ValueError):
            self.validate()

    def test_rejects_another_company_retrieval_citation(self):
        question = self.panoramic()
        record = question['methods']['bm25']['companies'][0]
        original = self.originals[question['id']]['results']['bm25']
        foreign = next(h for h in original['hits'] if h['code'] != record['code'])
        record['retrieval']['supportingIds'] = [foreign['id']]
        with self.assertRaises(ValueError):
            self.validate()

    def test_rejects_another_company_answer_citation(self):
        question = self.panoramic()
        record = question['methods']['hybrid']['companies'][0]
        original = self.originals[question['id']]['results']['hybrid']['answer']
        foreign = next(h for g in original['groups'] if g['code'] != record['code'] for h in g['hits'])
        record['answer']['supportingIds'] = [foreign['id']]
        with self.assertRaises(ValueError):
            self.validate()

    def test_rejects_source_code_disagreeing_with_record(self):
        record = self.panoramic()['methods']['bm25']['companies'][0]
        # The URL is unchanged: code itself must also be checked.
        record['sourceChecks'][0]['code'] = next(code for code in self.report_map if code != record['code'])
        with self.assertRaises(ValueError):
            self.validate()

    def test_rejects_wrong_displayed_company_name(self):
        record = self.panoramic()['methods']['bm25']['companies'][0]
        record['company'] = next(r['company'] for c, r in self.report_map.items() if c != record['code'])
        with self.assertRaises(ValueError):
            self.validate()

    def test_rejects_single_question_reassigned_to_another_company(self):
        question = self.single()
        record = question['methods']['bm25']
        expected = self.originals[question['id']]['goldEvidence'][0]['code']
        foreign = next(r for c, r in self.report_map.items() if c != expected)
        record['code'], record['company'] = foreign['code'], foreign['company']
        for dimension in ['retrieval', 'answer']:
            record[dimension]['status'] = 'partial'
            record[dimension]['supportingIds'] = []
        for source in record['sourceChecks']:
            source['code'], source['sourceUrl'], source['pdfPage'] = foreign['code'], foreign['sourceUrl'], 1
        with self.assertRaises(ValueError):
            self.validate()

    def test_rejects_invalid_source_url(self):
        self.single()['methods']['bm25']['sourceChecks'][0]['sourceUrl'] = 'https://example.com/unrelated.pdf'
        with self.assertRaises(ValueError):
            self.validate()

    def test_rejects_missing_publishing_protocol(self):
        self.candidate.pop('protocol', None)
        with self.assertRaises(ValueError):
            self.validate()


if __name__ == '__main__':
    unittest.main(verbosity=2)
