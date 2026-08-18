# -*- coding: utf-8 -*-
"""打码后的回显值，绝不能被当成真密钥存回去。

用户实跑撞到：

    上传到对象存储失败：Credential access key has length 5, should be 32

「5」不是巧合 —— 后端把 access key 打码成「前4位 + …」，正好 5 个字符：

    up["access_key"] = up["access_key"][:4] + "…"

而前端渲染时**只清空了 secret_key，漏了 access_key**：

    $('#' + el).value = (key === 'secret_key') ? '' : (u[key] ?? '');

于是掩码被塞回输入框，用户在那一页点一下「保存」（哪怕只是改了 bucket），
这 5 个字符就被当成真 key 存了进去。**而他完全看不出是自己点保存造成的** ——
上一秒还好好的，下一秒上传全挂。

两道一起补：前端不再回填任何密钥；后端看到像掩码的值一律不存 ——
前端哪天又漏一个字段，配置也不会被写坏。
"""
import io
import os
import shutil
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

REAL_AK = "0123456789abcdef0123456789abcdef"      # R2 的 access key 是 32 位
REAL_SK = "s" * 64


class BackendTests(unittest.TestCase):

    def setUp(self):
        from core import paths
        # **数据目录是进程级的全局状态。** 只 set 不还原的话，
        # 同一次 pytest 里后面所有用例都会看到一个空配置 ——
        # 表现成一堆莫名其妙的失败（「还没有可用的服务商」之类），
        # 而真正的原因在这儿。第一版就是这么把别的测试搞挂的。
        self.prev = paths._forced["data"]
        self.dir = tempfile.mkdtemp()
        paths.set_data_dir(self.dir)

    def tearDown(self):
        from core import paths
        paths.set_data_dir(self.prev)
        shutil.rmtree(self.dir, ignore_errors=True)

    def _save(self, upload):
        from server.app import api_post
        return api_post("/api/config", {"upload": upload})

    def _get(self):
        from server.app import load_config
        return (load_config().get("upload") or {})

    def test_a_masked_value_never_overwrites_the_real_key(self):
        """★ 这就是那个 bug。"""
        self._save({"bucket": "b", "access_key": REAL_AK, "secret_key": REAL_SK})
        self._save({"bucket": "b2", "access_key": REAL_AK[:4] + "…"})
        self.assertEqual(self._get()["access_key"], REAL_AK)
        self.assertEqual(self._get()["bucket"], "b2", "非密钥字段照常更新")

    def test_a_dot_mask_is_also_refused(self):
        """有些前端用圆点打码，一样不能存。"""
        self._save({"bucket": "b", "access_key": REAL_AK})
        self._save({"bucket": "b", "access_key": "•••••"})
        self.assertEqual(self._get()["access_key"], REAL_AK)

    def test_an_empty_value_still_means_do_not_change(self):
        self._save({"bucket": "b", "access_key": REAL_AK})
        self._save({"bucket": "b", "access_key": ""})
        self.assertEqual(self._get()["access_key"], REAL_AK)

    def test_a_real_new_key_does_replace_it(self):
        """★ 别拦过头 —— 真换 key 必须换得掉。"""
        self._save({"bucket": "b", "access_key": REAL_AK})
        self._save({"bucket": "b", "access_key": "f" * 32})
        self.assertEqual(self._get()["access_key"], "f" * 32)

    def test_provider_keys_are_protected_too(self):
        """服务商的 key 走的是另一条分支，一样要拦。"""
        self._save({})
        from server.app import api_post, load_config
        api_post("/api/config", {"providers": {"paisio": {"api_key": "sk-real-key"}}})
        api_post("/api/config", {"providers": {"paisio": {"api_key": "sk-r…"}}})
        self.assertEqual(
            load_config()["providers"]["paisio"]["api_key"], "sk-real-key")


class PageTests(unittest.TestCase):

    def test_neither_key_is_echoed_back_into_the_box(self):
        """★ 漏一个就够了 —— 这次漏的就是 access_key。"""
        html = io.open(os.path.join(ROOT, "web", "index.html"),
                       encoding="utf-8").read()
        i = html.index("function renderUpload()")
        blk = html[i:i + 700]
        self.assertIn("access_key", blk)
        self.assertIn("secret_key", blk)
        self.assertNotIn("(key === 'secret_key') ? ''", blk,
                         "还是只清了 secret_key")


if __name__ == "__main__":
    unittest.main()
