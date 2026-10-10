"""Behavioral safety checks with synthetic inputs, not accuracy evaluation."""
import sys
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
if (ROOT / ".deps").is_dir():
    sys.path.insert(0, str(ROOT / ".deps"))

from engine import JudgmentEngine, LAW, _near_duplicate


class DraftTests(unittest.TestCase):
    def draft(self, text):
        return JudgmentEngine._draft(text, "民间借贷纠纷", "支持")

    def test_amount_alone_does_not_override_reject_prediction(self):
        direction, verdict, _ = JudgmentEngine._draft("原告请求被告偿还借款本金10万元，双方争议较大。", "民间借贷纠纷", "驳回")
        self.assertEqual(direction, "驳回")
        self.assertNotIn("100,000", verdict)

    def test_partial_prediction_does_not_invent_award(self):
        direction, verdict, _ = JudgmentEngine._draft("原告请求被告偿还借款本金10万元，双方争议较大。", "民间借贷纠纷", "部分支持")
        self.assertEqual(direction, "部分支持")
        self.assertNotIn("100,000", verdict)
        self.assertIn("无法", verdict)

    def test_clear_evidence_can_override_reject_prediction(self):
        direction, verdict, _ = JudgmentEngine._draft("原告请求借款本金10万元。借条及转账记录证明本金已交付且到期未还。", "民间借贷纠纷", "驳回")
        self.assertEqual(direction, "支持")
        self.assertIn("100,000", verdict)

    def test_existing_evidence_cannot_prove_delivery(self):
        text = "原告诉请返还借款80000元。经审理查明，原告未提交借条、转账凭证或证人证言；现有证据不能证明实际交付。"
        for prediction in ("支持", "部分支持", "驳回"):
            direction, verdict, _ = JudgmentEngine._draft(text, "民间借贷纠纷", prediction)
            self.assertEqual(direction, "驳回")
            self.assertNotIn("80,000", verdict)
        # The same allegation, solely from an unproven defense, is not a fact.
        direction, _, _ = JudgmentEngine._draft("原告请求借款本金80000元。借条及转账记录证明本金已交付且到期未还。被告辩称现有证据不能证明实际交付，未提供证明。", "民间借贷纠纷", "支持")
        self.assertEqual(direction, "支持")

    def test_established_incomplete_delivery_with_payment_defense(self):
        text = "原告请求支付尾款40000元。经审理查明，合同总价60000元，已支付20000元；但原告始终未按合同交付剩余货物，被告有权在全部交货前拒付尾款。原告无证据证明交货完成。"
        for prediction in ("支持", "部分支持", "驳回"):
            direction, verdict, _ = JudgmentEngine._draft(text, "买卖合同纠纷", prediction)
            self.assertEqual(direction, "驳回")
            self.assertNotIn("40,000", verdict)
        direction, _, _ = JudgmentEngine._draft("原告请求支付货款40000元。原告已交货，被告签收，合同与签收记录齐全。被告辩称原告未交付剩余货物，被告有权拒付尾款，但未提供证据。", "买卖合同纠纷", "支持")
        self.assertEqual(direction, "支持")

    def test_returned_principal_is_deducted_without_double_deduction(self):
        text = "原告请求偿还本金50000元及利息10000元。双方借条载明借款50000元，未约定利息。被告已返还本金20000元，有转账凭证双方认可；剩余30000元未返还。原告利息请求为借款期间利息，未提供有息约定证据。"
        direction, verdict, _ = JudgmentEngine._draft(text, "民间借贷纠纷", "支持")
        self.assertEqual(direction, "部分支持")
        self.assertIn("30,000元", verdict)
        self.assertNotIn("50,000元", verdict)
        self.assertIn("驳回原告关于利息", verdict)
        direction, verdict, _ = JudgmentEngine._draft(text.replace("偿还本金50000元", "偿还剩余本金30000元"), "民间借贷纠纷", "支持")
        self.assertIn("30,000元", verdict)
        self.assertNotIn("10,000元", verdict)

    def test_termination_is_retained_with_money_claim(self):
        direction, verdict, _ = JudgmentEngine._draft("原告请求判令被告支付租金2万元并解除租赁合同、返还房屋。", "房屋租赁合同纠纷", "支持")
        self.assertEqual(direction, "支持")
        self.assertIn("20,000元", verdict)
        self.assertIn("解除原告与被告", verdict)
        self.assertIn("租赁房屋或租赁物返还原告", verdict)

    def test_principal_supported_unagreed_interest_rejected(self):
        direction, verdict, _ = self.draft("原告请求被告偿还借款本金10万元及利息2万元。借条及转账记录证明本金已经交付，双方未约定利息。")
        self.assertEqual(direction, "部分支持")
        self.assertIn("100,000元", verdict)
        self.assertNotIn("20,000元", verdict)
        self.assertIn("驳回原告关于利息", verdict)

    def test_interest_proof_failure_does_not_reject_principal(self):
        direction, verdict, _ = self.draft("原告请求借款本金10万元及利息5000元。借款有合同及转账凭证，原告未提供任何证据证明利息约定。")
        self.assertEqual(direction, "部分支持")
        self.assertIn("100,000元", verdict)

    def test_principal_not_delivered(self):
        direction, verdict, _ = self.draft("原告请求返还借款本金5万元。原告未提供任何证据证明借款已经交付。")
        self.assertEqual(direction, "驳回")
        self.assertNotIn("支付", verdict)

    def test_fully_paid_principal(self):
        direction, verdict, _ = self.draft("原告请求返还借款本金10万元。借款本金已全部清偿，原告对此确认。")
        self.assertEqual(direction, "驳回")
        self.assertNotIn("100,000元", verdict)

    def test_partial_payment_reduces_only_input_principal(self):
        direction, verdict, _ = self.draft("原告请求偿还借款本金10万元。被告已经归还3万元，原告认可该还款事实。")
        self.assertEqual(direction, "部分支持")
        self.assertIn("70,000元", verdict)
        self.assertNotIn("支付借款本金人民币100,000元", verdict)

    def test_claimed_balance_not_deducted_twice_or_old_amounts_added(self):
        direction, verdict, _ = self.draft("原告请求偿还尚欠借款本金7万元。原借款10万元，已归还3万元。")
        self.assertEqual(direction, "支持")
        self.assertIn("70,000元", verdict)
        self.assertNotIn("100,000元", verdict)
        self.assertNotIn("30,000元", verdict)
        self.assertNotIn("40,000元", verdict)

    def test_unproven_defense_not_treated_as_payment(self):
        for phrase in ["被告辩称已偿还8万元但未提供证据", "被告辩称已偿还8万元但无证据证明"]:
            with self.subTest(phrase=phrase):
                direction, verdict, _ = self.draft("原告请求偿还借款本金10万元。借款有转账记录。" + phrase + "。")
                self.assertEqual(direction, "支持")
                self.assertIn("100,000元", verdict)
                self.assertNotIn("20,000元", verdict)

    def test_no_invented_interest_rate_or_amount(self):
        direction, verdict, _ = self.draft("原告请求被告归还借款本金12345元并支付利息。借条和转账凭证能够证明借款已交付。")
        self.assertEqual(direction, "部分支持")
        self.assertIn("12,345元", verdict)
        self.assertNotRegex(verdict, r"(?:4|6|24|36)%")
        self.assertIn("未明确计算依据及金额", verdict)

    def test_monetary_fees_are_not_substantive_claims(self):
        direction, verdict, _ = self.draft("原告请求偿还本金50000元，并承担诉讼费1050元。双方确认借款已交付且尚未归还。")
        self.assertEqual(direction, "支持")
        self.assertIn("50,000元", verdict)
        self.assertNotIn("1,050元", verdict)

    def test_missing_amount_not_filled_from_precedent(self):
        direction, verdict, _ = self.draft("原告请求返还借款，被告尚未归还，但未记载具体金额。")
        self.assertEqual(direction, "支持")
        self.assertNotRegex(verdict, r"\d+(?:,\d+)*元")
        self.assertIn("不作补写", verdict)

    def test_citation_shape_and_duplicate_detection(self):
        self.assertIsNotNone(LAW.fullmatch("《中华人民共和国合同法》第一百零七条"))
        self.assertIsNone(LAW.fullmatch("合同法第107条"))
        self.assertTrue(_near_duplicate("原告请求被告返还本金五万元。", "原告请求被告返还本金五万元。"))
        self.assertFalse(_near_duplicate("原告要求支付物业费用。", "原告起诉要求离婚并抚养子女。"))


if __name__ == "__main__":
    unittest.main()
