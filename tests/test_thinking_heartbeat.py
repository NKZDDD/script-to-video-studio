# -*- coding: utf-8 -*-
"""模型思考期也要有心跳 —— 那段静默恰恰是最该看见的。

实跑（电影级，第9环节）：

    [10:27:27] 提示词 54420 字，调 gpt-5.6-sol
    [10:27:27] 网络：直连（系统与环境都没有代理）
    ……然后整整十分钟，一行都没有

看上去就是死了。实际它一直在等模型吐第一个字节。

原因：心跳那句写在 `for raw in stream_lines(r):` 的**循环体里** ——
没有数据到达，循环体就不执行，于是一条日志都不出。

而这段静默正是最要紧的信息：
  · 模型的思考期不产生任何数据
  · 中转站看不到数据就会在 125 秒左右切断（那些 HTTP 524 就是这么来的）
  · 分不清「还在想」和「已经挂了」，人只能干等或者瞎重启

所以心跳必须**独立于数据到达**：拿一个定时线程打，收没收到字都打。
"""
import inspect
import time
import unittest

from core.llm import LLM


class HeartbeatShapeTests(unittest.TestCase):
    """结构上钉住 —— 这一条在重构里最容易被静静挪回循环体。"""

    def test_the_beat_is_on_a_timer_not_on_incoming_data(self):
        """★ 这就是那十分钟空白。"""
        src = inspect.getsource(LLM._stream_once)
        self.assertIn("threading.Thread(target=beat", src)
        self.assertIn("stop_beat.wait(15)", src)

    def test_it_is_stopped_when_the_call_ends(self):
        """★ 不停的话，一个跑完的调用会一直往日志里刷。"""
        src = inspect.getsource(LLM._stream_once)
        self.assertIn("finally:", src)
        self.assertIn("stop_beat.set()", src)

    def test_the_receiving_loop_no_longer_logs_ticks(self):
        """两处都打就会双份刷屏，而且两个计时起点还不一样。"""
        src = inspect.getsource(LLM._stream_body)
        self.assertNotIn("正在生成…", src)

    def test_the_two_cases_read_differently(self):
        """★ 「还没开口」和「正在吐字」必须一眼分得开 —— 修法完全不同。

        没开口 → 减小输入 / 换线路（流式救不了思考期）
        在吐字 → 什么都不用做，等着就行
        """
        src = inspect.getsource(LLM._stream_once)
        self.assertIn("还没收到第一个字", src)
        self.assertIn("正在生成…", src)

    def test_it_names_the_524_connection(self):
        """思考期太长正是 524 的成因 —— 在这儿说，人才对得上号。"""
        self.assertIn("524", inspect.getsource(LLM._stream_once))

    def test_it_does_not_reference_a_name_it_does_not_have(self):
        """★ 心跳跑在后台线程里：那里抛异常主流程看不见，

        表现只是「从此不再有心跳」—— 比没写还难查。
        写这段时我就先引用了一个不存在的 `user`。
        """
        src = inspect.getsource(LLM._stream_once)
        self.assertNotIn("len(user)", src)
        self.assertIn('body.get("messages")', src)


class HeartbeatBehaviourTests(unittest.TestCase):
    """真跑一遍那个心跳循环，别只看源码。"""

    def _beat(self, parts, seconds=0.7, interval=0.05):
        """照 _stream_once 里那段的形状复刻一个，验行为。"""
        import threading
        said = []
        stop = threading.Event()
        started = time.time()

        def beat():
            while not stop.wait(interval):
                waited = int(time.time() - started)
                got = sum(len(p) for p in parts)
                said.append(f"收到 {got} 字" if got else f"还没收到第一个字（{waited}s）")

        t = threading.Thread(target=beat, daemon=True)
        t.start()
        time.sleep(seconds)
        stop.set()
        t.join(1)
        return said

    def test_it_speaks_up_even_with_zero_bytes(self):
        """★ 一个字都没收到时也要出声 —— 那正是最需要出声的时候。"""
        said = self._beat([])
        self.assertTrue(said, "十分钟一行都不打，就是这么来的")
        self.assertTrue(all("还没收到第一个字" in s for s in said))

    def test_it_switches_wording_once_data_arrives(self):
        parts = []
        import threading
        done = threading.Event()

        def feed():
            time.sleep(0.2)
            parts.append("已经在吐字了")
            done.set()

        threading.Thread(target=feed, daemon=True).start()
        said = self._beat(parts, seconds=0.6)
        done.wait(1)
        self.assertTrue(any("还没收到" in s for s in said))
        self.assertTrue(any("收到 6 字" in s for s in said))

    def test_it_stops_after_the_call_ends(self):
        """★ 停不掉的话，几十个跑完的调用会一起往日志里刷。"""
        said = self._beat([], seconds=0.3)
        n = len(said)
        time.sleep(0.3)
        self.assertEqual(len(said), n, "叫停之后还在打")


if __name__ == "__main__":
    unittest.main()
