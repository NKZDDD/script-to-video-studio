# -*- coding: utf-8 -*-
"""「这会儿排不上」不等于「账户没钱了」。

实跑撞到：

    HTTP 429 {"message":"No available image quota. Please try again later."}

「quota」命中了余额关键词表，于是被判成 batch_fatal —— **整批图立刻熔断**，
卡片写着「这家服务商的账户没钱了」，让人去充值。而账上是有钱的，
那句话的后半段写着 try again later。

两处都要分开：分级（要不要熔断整批）和卡片（该跟人说什么）。
真欠费的家不会让你稍后再试。
"""
import unittest

from core import diagnose
from core.apiutil import BATCH_FATAL, RETRYABLE, classify

BUSY = ('{"error":{"message":"No available image quota. '
        'Please try again later.","code":429}}')
BROKE = '{"error":{"message":"Insufficient balance, please recharge","code":429}}'


class ClassifyTests(unittest.TestCase):

    def test_a_busy_pool_does_not_burn_the_batch(self):
        """★ 这就是那个 bug —— 一次临时排队停掉整批图。"""
        self.assertEqual(classify(429, BUSY), RETRYABLE)

    def test_really_being_out_of_money_still_burns_it(self):
        """★ 别放过头：真欠费时继续发只是把同一个错重复几百遍。"""
        self.assertEqual(classify(429, BROKE), BATCH_FATAL)

    def test_402_is_still_fatal_whatever_it_says(self):
        self.assertEqual(classify(402, "try again later"), BATCH_FATAL)

    def test_other_ways_of_saying_wait_a_bit(self):
        for msg in ("额度暂时不可用，请稍后再试",
                    "Quota temporarily unavailable",
                    "rate limit exceeded, retry later"):
            self.assertEqual(classify(429, msg), RETRYABLE, msg)

    def test_a_plain_429_is_unchanged(self):
        self.assertEqual(classify(429, "too many requests"), RETRYABLE)


class CardTests(unittest.TestCase):

    def test_it_gets_its_own_card(self):
        self.assertEqual(diagnose.code_of(BUSY, 429), "QUOTA_BUSY")

    def test_the_card_says_it_is_not_about_money(self):
        """★ 卡片说错话的代价：人跑去充值，而问题在别处。"""
        c = diagnose.CATALOG["QUOTA_BUSY"]
        self.assertIn("不是你欠费", c["title"])
        self.assertNotEqual(c["scope"], "batch", "别让它显示成整批停了")

    def test_the_money_one_is_untouched(self):
        self.assertEqual(diagnose.code_of(BROKE, 429), "QUOTA_EXHAUSTED")
        self.assertIn("没钱", diagnose.CATALOG["QUOTA_EXHAUSTED"]["title"])

    def test_both_can_switch_providers(self):
        """别家的池子是另一个，换过去多半就出得来。"""
        for code in ("QUOTA_BUSY", "QUOTA_EXHAUSTED"):
            self.assertTrue(diagnose.should_failover({"code": code}), code)

    def test_the_busy_one_is_ranked_before_the_money_one(self):
        """★ 顺序反了这条永远命中不到 —— 两边都带 quota 这个词。"""
        codes = [c for c, _ in diagnose._PATTERNS]
        self.assertLess(codes.index("QUOTA_BUSY"), codes.index("QUOTA_EXHAUSTED"))


if __name__ == "__main__":
    unittest.main()
