# -*- coding: utf-8 -*-
"""账号填写方式：一个账号一张卡，每个框写清填什么。

用户原话（2026-08-21）：「我觉得还得更换他填写的方式，不要让用户填写一行一行
的东西，而是添加账号明确每个输入框填写什么」。

以前是一个大文本框，让人把客服发的那段话粘进去。三个问题都真实发生过：

  · 不知道要填哪几项 —— 跑起来报「凭据不全，缺 webdav_url」才知道
  · 不知道该怎么分隔 —— 换行还是分号
  · **共用字段只写一次会在拆分时丢掉** —— 三条任务同一秒全失败就是这个

用户定的两条：非密钥项回显、密钥项打码；webdav 那三项共用一份、单个账号可覆盖。
"""
import io
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "server"))

import app as A                                          # noqa: E402
from core import accounts as AC                          # noqa: E402
from core.providers.hvtald import HvtaldProvider, parse_creds  # noqa: E402

SAVED = ("webdav=https://dav.x/vid\n"
         "user=u1\n"
         "password=p1\n"
         "deviceId=dev-a;token=tok-a\n"
         "deviceId=dev-b;token=tok-b")


class DeclarationTests(unittest.TestCase):

    def test_the_provider_declares_the_form(self):
        af = HvtaldProvider.account_form
        self.assertEqual([f[0] for f in af["shared"]],
                         ["webdav", "user", "password"])
        self.assertEqual([f[0] for f in af["per"]][:2], ["deviceId", "token"])

    def test_every_declared_key_is_one_the_provider_understands(self):
        """★ 键名和服务商解凭据认的别名对不上 = 填了读不到，而且不报错。"""
        from core.providers.hvtald import _ALIAS
        for grp in ("shared", "per"):
            for f in HvtaldProvider.account_form[grp]:
                self.assertIn(f[0].lower(), _ALIAS, f"{f[0]} 不是它认的键名")

    def test_secret_flags_are_set_on_the_right_fields(self):
        """★ 标错的后果：密钥被明文回显到浏览器，或者非密钥项永远填不上。"""
        secret = {f[0] for grp in ("shared", "per")
                  for f in HvtaldProvider.account_form[grp] if f[2]}
        self.assertEqual(secret, {"token", "password"})

    def test_it_reaches_the_capability_payload(self):
        from core import providers as P
        cap = next(c for c in P.list_capabilities() if c["id"] == "hvtald")
        self.assertTrue(cap["account_form"]["per"])


class MaskTests(unittest.TestCase):
    """回显：非密钥项原样，密钥项只回「有没有」。"""

    def test_non_secret_values_come_back(self):
        got = A._mask_form("hvtald", SAVED)
        self.assertEqual(got["shared"]["webdav"], "https://dav.x/vid")
        self.assertEqual([r["deviceId"] for r in got["accounts"]],
                         ["dev-a", "dev-b"])

    def test_secrets_never_leave_the_server(self):
        """★ 密钥进了浏览器，截图、录屏、浏览器插件都看得到。"""
        got = A._mask_form("hvtald", SAVED)
        self.assertNotIn("tok-a", repr(got))
        self.assertNotIn("p1", repr(got))

    def test_but_it_says_which_secrets_are_already_set(self):
        """★ 不说的话每个密钥框都写着「粘贴」，人会以为没存上、重填一遍。"""
        got = A._mask_form("hvtald", SAVED)
        self.assertTrue(got["accounts"][0]["token__set"])
        self.assertTrue(got["shared"]["password__set"])

    def test_a_provider_without_a_form_gets_nothing(self):
        self.assertEqual(A._mask_form("paisio", "sk-xxx"), {})


class BuildTests(unittest.TestCase):
    """存回去：每一项各自「留空 = 不改」。"""

    def _save(self, payload, saved=SAVED):
        return A._build_account_key("hvtald", payload, saved)

    def test_editing_one_field_keeps_all_the_others(self):
        """★ 密钥项回显是空的 —— 不按项合并的话，改一个 deviceId 就会

        把所有 token 清掉，而清掉之后报的是「凭据不全」，
        看不出是自己点了一下保存造成的。
        """
        out = self._save({"shared": {"webdav": "", "user": "", "password": ""},
                          "accounts": [{"deviceId": "dev-AAA", "token": ""},
                                       {"deviceId": "dev-b", "token": ""}]})
        acs = AC.parse_accounts(out)
        self.assertEqual(len(acs), 2)
        creds = [parse_creds(a.api_key) for a in acs]
        self.assertEqual(creds[0]["device_id"], "dev-AAA")
        self.assertEqual(creds[0]["token"], "tok-a", "token 被清掉了")
        self.assertEqual(creds[1]["token"], "tok-b")
        for c in creds:
            self.assertEqual([k for k, v in c.items() if not v], [])

    def test_the_shared_block_is_inherited_by_every_account(self):
        out = self._save({"shared": {}, "accounts": [{"deviceId": "d1",
                                                      "token": "t1"},
                                                     {"deviceId": "d2",
                                                      "token": "t2"}]})
        for a in AC.parse_accounts(out):
            self.assertEqual(parse_creds(a.api_key)["webdav_url"],
                             "https://dav.x/vid")

    def test_an_account_can_override_the_shared_block(self):
        """★ 顺序决定谁说话算数：服务商解凭据是后面的覆盖前面的。

        共用写在后面的话，它会盖掉某个账号自己填的 webdav。
        """
        out = self._save({"shared": {},
                          "accounts": [{"deviceId": "d1", "token": "t1"},
                                       {"deviceId": "d2", "token": "t2",
                                        "webdav": "https://own/x"}]})
        got = [parse_creds(a.api_key)["webdav_url"]
               for a in AC.parse_accounts(out)]
        self.assertEqual(got, ["https://dav.x/vid", "https://own/x"])

    def test_a_brand_new_row_with_no_id_is_dropped(self):
        """★ 一张刚点「添加账号」还没填的空卡，不许变成一个账号。

        留着的话它只有共用项、没有 deviceId —— 发出去必然「凭据不全」，
        而人以为自己配了 N 个账号。
        """
        out = A._build_account_key("hvtald", {
            "shared": {"webdav": "w", "user": "u", "password": "p"},
            "accounts": [{"deviceId": "d1", "token": "t1"},
                         {"deviceId": "", "token": ""}]}, "")
        self.assertEqual(len(AC.parse_accounts(out)), 1)

    def test_blanking_an_existing_row_does_not_delete_it(self):
        """★ 清空输入框是「不改这一项」，**不是**删除手势 —— 删除有按钮。

        混在一起的话，一次误删（点进去又退出来）会静默少一个账号，
        而少一个账号只表现为「慢了三分之一」。
        """
        out = self._save({"shared": {},
                          "accounts": [{"deviceId": "d1", "token": "t1"},
                                       {"deviceId": "", "token": ""}]})
        acs = AC.parse_accounts(out)
        self.assertEqual(len(acs), 2)
        self.assertEqual(parse_creds(acs[1].api_key)["device_id"], "dev-b")

    def test_a_masked_echo_is_not_stored_as_a_value(self):
        out = self._save({"shared": {"webdav": "https://dav.x…"},
                          "accounts": [{"deviceId": "d1", "token": "t1"}]})
        self.assertIn("https://dav.x/vid", out)

    def test_no_accounts_means_do_not_touch_the_key(self):
        """★ 返回空串时保存路径不会覆盖 api_key —— 别把已配的清掉。"""
        self.assertEqual(self._save({"shared": {}, "accounts": []}), "")

    def test_three_accounts_from_scratch_are_all_complete(self):
        """从零填：共用块一次 + 三张卡各两项 —— 三个账号都该是齐的。"""
        out = A._build_account_key("hvtald", {
            "shared": {"webdav": "https://d/v", "user": "u", "password": "p"},
            "accounts": [{"deviceId": f"d{i}", "token": f"t{i}"}
                         for i in range(1, 4)]}, "")
        acs = AC.parse_accounts(out)
        self.assertEqual(len(acs), 3)
        for a in acs:
            self.assertEqual(
                [k for k, v in parse_creds(a.api_key).items() if not v], [])


class PageTests(unittest.TestCase):

    HTML = io.open(os.path.join(ROOT, "web", "index.html"),
                   encoding="utf-8").read()

    def test_the_form_is_rendered_from_the_declaration(self):
        """★ 页面不维护第二份字段表 —— 两份对不上就是「填了没生效」。"""
        self.assertIn("c.account_form", self.HTML)
        self.assertIn("function accountForm(", self.HTML)
        self.assertIn("(af.shared || []).map", self.HTML)
        self.assertIn("af.per", self.HTML)

    def test_there_is_an_add_button(self):
        self.assertIn("+ 添加账号", self.HTML)
        self.assertIn("data-acct-add", self.HTML)

    def test_deleting_renumbers_the_rest(self):
        """★ `data-af-name` 里带序号 —— 不重编号会在中间留个空洞，

        而空洞被后端当成没有 deviceId 的账号丢掉：
        表现是「删了第二个，第三个也没了」。
        """
        self.assertIn("rows.splice(+ev.target.dataset.acctDel, 1)", self.HTML)
        self.assertIn(".map((r, i) => acctCard(c, i, r))", self.HTML)

    def test_every_box_says_what_to_type(self):
        """★ 这就是用户要的：「明确每个输入框填写什么」。

        说明写在**服务商的声明**里（只有它知道自己那几项是什么），
        页面只负责把它画出来 —— 所以两边各查一半。
        """
        # **两组分开查。** `webdav` 在共用组和账号组里都有（一个是共用地址、
        # 一个是单账号覆盖），合成一个 dict 会被后来的盖掉。
        af = HvtaldProvider.account_form
        shared_why = {f[0]: f[3] for f in af["shared"]}
        self.assertIn("https", shared_why["webdav"])
        self.assertIn("留空", af["per"][2][3],
                      "「单独用的 WebDAV」没写清能留空，人会以为必填")
        # 页面把说明渲染出来
        self.assertIn("mdBold(why)", self.HTML)

    def test_the_nested_payload_goes_in_its_own_field(self):
        self.assertIn("obj.account_form = collectAccounts(id)", self.HTML)

    def test_empty_boxes_are_still_submitted(self):
        """★ 不送的话「这一项被清掉了」和「这一项没动」分不出来。"""
        i = self.HTML.index("function collectAccounts(")
        blk = self.HTML[i:i + 700]
        self.assertNotIn("if (!i.value) return", blk)

    def test_the_old_textarea_still_exists_for_others(self):
        """没声明表单的家照旧走大文本框 —— 别把它们弄坏。"""
        self.assertIn("c.per_account_serial", self.HTML)
        self.assertIn("账号（一行一个，或用空行分段）", self.HTML)


if __name__ == "__main__":
    unittest.main()
