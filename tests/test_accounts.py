# -*- coding: utf-8 -*-
"""按账号排队 + 按账号计数（HVTALD）。

这一家和别家不是一类：

    别家     一个 Key，并发上限由服务商限流决定 → 调大并发就更快
    这一家   **按账号计费**，一个账号同时只能生成一条 →
             想并发只能配多个账号，**并发上限 = 账号数**

挤在同一个账号上的后果不是报「并发超限」这种明白话，而是排队超时或者直接拒 ——
失败记录里只看得到「生成失败」，看不出是自己把自己撞了。所以这里钉紧。
"""
import json
import os
import shutil
import tempfile
import threading
import time
import unittest

from core import accounts as A


ONE = "deviceId=dev-aaaa1111;token=tok-1;webdav=http://x/dav;user=u;password=p"
TWO = "deviceId=dev-bbbb2222;token=tok-2;webdav=http://x/dav;user=u;password=p"


class ParseTests(unittest.TestCase):
    """一段粘进来的文本 → 几个账号。"""

    def test_one_account_per_line(self):
        """★ **最常见的粘法，也是最容易被吃掉的。**

        `parse_creds` 里 `[;\\n]+` 把换行和分号当成一回事 —— 三行三个账号
        会被合成一个（后面的覆盖前面的），只剩最后一个在跑。
        不报错，只是慢三倍。
        """
        got = A.parse_accounts(ONE + "\n" + TWO)
        self.assertEqual(len(got), 2)
        self.assertIn("tok-1", got[0].api_key)
        self.assertIn("tok-2", got[1].api_key)

    def test_blocks_separated_by_a_blank_line(self):
        text = "deviceId=dev-aaaa1111\ntoken=tok-1\n\ndeviceId=dev-bbbb2222\ntoken=tok-2"
        got = A.parse_accounts(text)
        self.assertEqual(len(got), 2)
        self.assertIn("tok-1", got[0].api_key)
        self.assertIn("tok-2", got[1].api_key)

    def test_a_json_array_is_one_account_per_element(self):
        got = A.parse_accounts(json.dumps(
            [{"deviceId": "dev-aaaa1111", "token": "t1"},
             {"deviceId": "dev-bbbb2222", "token": "t2"}]))
        self.assertEqual([a.label for a in got], ["dev-aaaa", "dev-bbbb"])

    def test_a_single_json_object_is_one_account(self):
        got = A.parse_accounts('{"deviceId":"dev-aaaa1111","token":"t"}')
        self.assertEqual(len(got), 1)

    def test_a_multi_line_single_account_is_not_split(self):
        """★ 别拆过头：一个账号写成多行时不许变成 4 个账号。"""
        got = A.parse_accounts("deviceId=dev-aaaa1111\nwebdav=http://x/dav\n"
                               "user=u\npassword=p")
        self.assertEqual(len(got), 1)

    def test_empty_means_no_accounts(self):
        self.assertEqual(A.parse_accounts(""), [])
        self.assertEqual(A.parse_accounts("   \n  "), [])

    def test_the_label_never_leaks_the_token(self):
        """★ label 会进日志和页面。绝不能拿 token 的片段当名字。"""
        got = A.parse_accounts("token=SUPERSECRET-abcdef;user=u")
        self.assertEqual(len(got), 1)
        self.assertNotIn("SUPERSECRET", got[0].label)
        self.assertNotIn("abcdef", got[0].label)

    def test_the_label_comes_from_the_device_id(self):
        self.assertEqual(A.parse_accounts(ONE)[0].label, "dev-aaaa")


class PoolTests(unittest.TestCase):
    """排队：一个账号一个槽位。"""

    def setUp(self):
        A._POOLS.clear()

    def test_the_account_count_is_the_concurrency_limit(self):
        self.assertEqual(A.configure("hvtald", ONE + "\n" + TWO), 2)
        self.assertEqual(len(A.pool("hvtald")), 2)

    def test_one_account_runs_strictly_one_at_a_time(self):
        """★ **这一条是整件事的重点。**

        一个账号两条任务：第二条必须等第一条结束，不许同时在里面。
        """
        A.configure("hvtald", ONE)
        pool = A.pool("hvtald")
        inside, peak = [0], [0]
        lock = threading.Lock()

        def one():
            with pool.slot():
                with lock:
                    inside[0] += 1
                    peak[0] = max(peak[0], inside[0])
                time.sleep(0.1)
                with lock:
                    inside[0] -= 1

        ts = [threading.Thread(target=one) for _ in range(3)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        self.assertEqual(peak[0], 1, "同一个账号上挤进了多条 —— 这一家会拒或者超时")

    def test_two_accounts_give_two_lanes(self):
        """★ 反过来钉一次：多账号就该真的并行，不然配了也白配。"""
        A.configure("hvtald", ONE + "\n" + TWO)
        pool = A.pool("hvtald")
        inside, peak = [0], [0]
        lock = threading.Lock()

        def one():
            with pool.slot():
                with lock:
                    inside[0] += 1
                    peak[0] = max(peak[0], inside[0])
                time.sleep(0.15)
                with lock:
                    inside[0] -= 1

        ts = [threading.Thread(target=one) for _ in range(4)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        self.assertEqual(peak[0], 2, "两个账号没跑出两路并发")

    def test_each_task_gets_its_own_credentials(self):
        """★ 两条并发任务必须拿到**不同**账号的凭据。

        共享一个 provider 实例去改凭据是竞态：两条会互相把对方的账号改掉，
        于是双双打到同一个账号上 —— 而那正是要防的事。
        """
        A.configure("hvtald", ONE + "\n" + TWO)
        pool = A.pool("hvtald")
        seen, lock = [], threading.Lock()

        def one():
            with pool.slot() as acct:
                with lock:
                    seen.append(acct.api_key)
                time.sleep(0.1)

        ts = [threading.Thread(target=one) for _ in range(2)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        self.assertEqual(len(set(seen)), 2, "两条并发拿到了同一个账号")

    def test_the_account_comes_back_after_a_failure(self):
        """★ 抛异常也要还回去 —— 不还的话池子会一点点漏干，最后全卡住。"""
        A.configure("hvtald", ONE)
        pool = A.pool("hvtald")
        with self.assertRaises(RuntimeError):
            with pool.slot():
                raise RuntimeError("这条炸了")
        self.assertEqual(pool.busy(), 0)

    def test_reconfiguring_with_the_same_accounts_does_not_rebuild(self):
        """★ 重建会把在途任务占着的槽位凭空变回空闲 ——

        于是同一个账号上挤进两条。这正是这套东西要防的事。
        """
        A.configure("hvtald", ONE)
        before = A.pool("hvtald")
        A.configure("hvtald", ONE)
        self.assertIs(A.pool("hvtald"), before)

    def test_changing_the_accounts_does_rebuild(self):
        A.configure("hvtald", ONE)
        before = A.pool("hvtald")
        A.configure("hvtald", ONE + "\n" + TWO)
        self.assertIsNot(A.pool("hvtald"), before)
        self.assertEqual(len(A.pool("hvtald")), 2)

    def test_waiting_can_be_cancelled(self):
        """等空账号的时候点取消要停得下来，不然那一条永远显示「运行中」。"""
        A.configure("hvtald", ONE)
        pool = A.pool("hvtald")
        with pool.slot():
            with self.assertRaises(RuntimeError) as cm:
                with pool.slot(cancel=lambda: True):
                    pass
        self.assertIn("取消", str(cm.exception))

    def test_it_says_why_it_is_waiting(self):
        """★ 「卡住了」和「在排队」看着一样，所以必须说出来。"""
        A.configure("hvtald", ONE)
        pool = A.pool("hvtald")
        said = []
        with pool.slot():
            t = threading.Thread(target=lambda: _try(pool, said))
            t.start()
            time.sleep(5)
        t.join(timeout=5)
        self.assertTrue(any("都在忙" in s for s in said), said)
        self.assertTrue(any("多配账号" in s for s in said), said)


def _try(pool, said):
    try:
        with pool.slot(log=said.append):
            pass
    except Exception:                                       # noqa: BLE001
        pass


class CountTests(unittest.TestCase):
    """计数：账号维度，看一天能做多少。"""

    def setUp(self):
        from core import paths
        A._POOLS.clear()
        self.tmp = tempfile.mkdtemp()
        self._orig = paths.data_dir
        paths.data_dir = lambda: self.tmp                   # type: ignore[assignment]

    def tearDown(self):
        from core import paths
        paths.data_dir = self._orig                         # type: ignore[assignment]
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_it_counts_per_account_per_day(self):
        A.bump("hvtald", "dev-aaaa", day="2026-08-19")
        A.bump("hvtald", "dev-aaaa", day="2026-08-19")
        A.bump("hvtald", "dev-bbbb", day="2026-08-19")
        data = json.load(open(os.path.join(self.tmp, "account_usage.json"),
                              encoding="utf-8"))
        self.assertEqual(data["hvtald"]["dev-aaaa"]["2026-08-19"], 2)
        self.assertEqual(data["hvtald"]["dev-bbbb"]["2026-08-19"], 1)

    def test_the_count_is_global_not_per_project(self):
        """★ 账号是跨项目共用的 —— 记在项目里的话，同一天跑两部剧

        就得自己把两个数加起来，而用户问的是「这个账号一天能做多少」。
        """
        self.assertIn("account_usage.json", A._usage_path())
        self.assertTrue(A._usage_path().startswith(self.tmp))

    def test_the_report_lists_configured_accounts_even_with_zero(self):
        """★ 配了但今天还没跑的账号也要出现 —— 不然看着像没配。"""
        A.configure("hvtald", ONE + "\n" + TWO)
        rep = A.report("hvtald")
        self.assertEqual([r["label"] for r in rep["rows"]],
                         ["dev-aaaa", "dev-bbbb"])
        self.assertEqual(rep["accounts"], 2)
        self.assertEqual(rep["today_total"], 0)

    def test_the_report_also_shows_accounts_no_longer_configured(self):
        """「我记得昨天那个账号跑了很多」要查得到。"""
        A.bump("hvtald", "dev-zzzz")
        A.configure("hvtald", ONE)
        rep = A.report("hvtald")
        labels = [r["label"] for r in rep["rows"]]
        self.assertEqual(labels, ["dev-aaaa", "dev-zzzz"])
        self.assertFalse([r for r in rep["rows"]
                          if r["label"] == "dev-zzzz"][0]["configured"])

    def test_today_is_counted_as_today(self):
        A.bump("hvtald", "dev-aaaa")
        rep = A.report("hvtald")
        self.assertEqual(rep["today_total"], 1)

    def test_a_broken_usage_file_does_not_stop_production(self):
        """★ 计数是观测，不是生产。写不进去也不该拖垮出片。"""
        open(os.path.join(self.tmp, "account_usage.json"), "w").write("坏文件")
        A.bump("hvtald", "dev-aaaa")           # 不许抛
        self.assertEqual(A.report("hvtald")["today_total"], 1)


class WiringTests(unittest.TestCase):
    """接上了才算做完。"""

    def test_the_provider_declares_it(self):
        """★ 做成服务商自己声明 —— 写在调度那层就得维护一张名单，

        漏一家的后果是那家被并发打爆，而报错只说「生成失败」。
        """
        from core.providers.hvtald import HvtaldProvider
        self.assertTrue(HvtaldProvider.per_account_serial)

    def test_other_providers_are_untouched(self):
        from core.providers.chaomo import ChaomoProvider
        self.assertFalse(getattr(ChaomoProvider, "per_account_serial", False))

    def test_capabilities_expose_it_to_the_page(self):
        """页面靠这个字段决定渲染多账号粘贴框还是单个密码框。"""
        from core.providers import list_capabilities
        caps = {c["id"]: c for c in list_capabilities()}
        self.assertTrue(caps["hvtald"]["per_account_serial"])
        self.assertFalse(caps["chaomo"]["per_account_serial"])

    def test_the_video_worker_uses_the_pool(self):
        import inspect
        from core import produce as P
        src = inspect.getsource(P.make_video_worker)
        self.assertIn("accounts.configure(pid", src)
        self.assertIn("pool.slot(log=log, cancel=cancel)", src)
        self.assertIn("accounts.bump(pid", src)

    def test_each_task_builds_its_own_provider(self):
        """★ 共享实例改凭据是竞态 —— 必须每个账号一个实例。"""
        import inspect
        from core import produce as P
        src = inspect.getsource(P.make_video_worker)
        self.assertIn("mine = build_provider(pid, acct.api_key", src)

    def test_the_count_happens_only_on_success(self):
        """计数是「做出了多少条」，不是「试了多少次」。"""
        import inspect
        from core import produce as P
        src = inspect.getsource(P.make_video_worker)
        i = src.index("meta = soften.run_with_softening(\n                    lambda pr: _go(mine")
        self.assertLess(i, src.index("accounts.bump(pid"))

    def test_the_ledger_records_which_account_paid(self):
        import inspect
        from core import produce as P
        self.assertIn("account=acct_label",
                      inspect.getsource(P.make_video_worker))


if __name__ == "__main__":
    unittest.main()


class QueueForeverTests(unittest.TestCase):
    """★ 一个账号也要能一条接一条做完 —— 用户原话。

    原来 `WAIT_SECONDS = 3600` 是**绝对**上限，而合法的等待远超它：
    一个账号 × 50 条视频 × 每条 5 分钟 = 4 小时以上，第十几条之后就会报
    「等了 3600 秒也没等到空账号」—— 它其实在正常排队。

    换成按**进度**判断：只要还有账号在被归还，队伍就在往前走，接着等。
    """

    def setUp(self):
        A._POOLS.clear()

    def test_a_long_queue_on_one_account_does_not_time_out(self):
        """★ 这就是那个 bug。把判据调到 0.3 秒，让「一直有人归还」跑赢它。"""
        orig = A.STUCK_SECONDS
        A.STUCK_SECONDS = 0.3
        try:
            A.configure("hvtald", ONE)
            pool = A.pool("hvtald")
            done = []
            for _ in range(8):                 # 8 轮，总时长远超 0.3 秒
                with pool.slot():
                    time.sleep(0.1)
                    done.append(1)
            self.assertEqual(len(done), 8, "排队排到一半自己报错了")
        finally:
            A.STUCK_SECONDS = orig

    def test_the_stuck_check_is_progress_based(self):
        """判据必须是「多久没有账号被归还」，不是「自己等了多久」。"""
        import inspect
        src = inspect.getsource(A._Pool.slot)
        self.assertIn("_last_release", src)
        self.assertIn("没有任何账号被归还", src)

    def test_a_release_resets_the_clock(self):
        A.configure("hvtald", ONE)
        pool = A.pool("hvtald")
        before = pool._last_release
        time.sleep(0.05)
        with pool.slot():
            pass
        self.assertGreater(pool._last_release, before)

    def test_a_truly_stuck_queue_still_fails(self):
        """★ 别修过头：真卡住了还是要报，不能无限挂着。"""
        orig = A.STUCK_SECONDS
        A.STUCK_SECONDS = 0.2
        try:
            A.configure("hvtald", ONE)
            pool = A.pool("hvtald")
            with pool.slot():                  # 占住不放
                with self.assertRaises(RuntimeError) as cm:
                    with pool.slot():
                        pass
            self.assertIn("队伍不动了", str(cm.exception))
        finally:
            A.STUCK_SECONDS = orig

    def test_the_wait_message_explains_the_queue(self):
        """排队不是故障 —— 那句话要让人看懂是在排队。"""
        import inspect
        src = inspect.getsource(A._Pool.slot)
        self.assertIn("按顺序一条一条来", src)


class GateLimitTests(unittest.TestCase):
    """★ 等账号必须发生在闸门**外**，否则堵死别家。"""

    def test_the_gate_is_capped_at_the_account_count(self):
        """不压的话：一个账号 + 并发 10 = 9 条占着全局槽位干等，

        而全局默认只有 8 个槽 —— 别家的出图全被堵死。
        """
        import inspect

        from core import produce as P
        src = inspect.getsource(P.make_video_worker)
        self.assertIn("GATE.set_provider_limit(pid, n_acct)", src)

    def test_the_setter_only_touches_that_provider(self):
        from core.executor import Gate
        g = Gate(8, {"paisio": 6})
        g.set_provider_limit("hvtald", 3)
        snap = g.snapshot()["per_provider_limit"]
        self.assertEqual(snap["hvtald"], 3)
        self.assertEqual(snap["paisio"], 6, "动了别家的上限")

    def test_it_rebuilds_the_semaphore_on_change(self):
        """★ 不重建的话改了上限也不生效 —— 老信号量还在用旧的计数。"""
        from core.executor import Gate
        g = Gate(8, {})
        g.set_provider_limit("hvtald", 1)
        with g.slot("hvtald"):
            pass
        g.set_provider_limit("hvtald", 4)
        self.assertEqual(g.snapshot()["per_provider_limit"]["hvtald"], 4)
        self.assertNotIn("hvtald", g._sems, "旧信号量没被丢掉")

    def test_setting_the_same_limit_is_a_no_op(self):
        """每建一次 worker 都会调它 —— 同值时别把在途任务的信号量拆了。"""
        from core.executor import Gate
        g = Gate(8, {})
        g.set_provider_limit("hvtald", 2)
        with g.slot("hvtald"):
            sem = g._sems.get("hvtald")
            g.set_provider_limit("hvtald", 2)
            self.assertIs(g._sems.get("hvtald"), sem)
