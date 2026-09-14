# -*- coding: utf-8 -*-
"""实拉模型清单要用**这一家所有的 Key**，不只 `api_key`。

超模按能力分四把：`llm_api_key` / `image_1k_api_key` / `image_4k_api_key` /
`video_api_key`（server 层 `_CHAOMO_KEY_FIELDS`，那儿的注释写着「超模没有
可跨能力复用的通用 key」）—— 它**根本没有 `api_key` 这个字段**。

原来两处都只认 `api_key`：
  · `fetch`    → 对超模永远拿到空 Key，「拉取最新模型清单」**必然拉不到**，
                 而它只报一句鉴权/网络失败，人会以为 Key 填错了
  · `identity` → 缓存指纹对超模永远是同一个值，换任何一把 Key 都认不出，
                 会一直拿旧清单用；旧清单里的模型新 Key 可能看不见，
                 不报错，选中了跑起来才「找不到模型」

遍历所有 Key 还有第二个理由：**不同 Key 的可见清单不一样**（坤鸡按令牌
分组过滤、超模按 1K/4K 分组），只用一把等于只看见那一把能看见的那部分。
"""
import unittest

from core.model_catalog import identity

CHAOMO = {"llm_api_key": "sk-a", "image_1k_api_key": "sk-b",
          "image_4k_api_key": "sk-c", "video_api_key": "sk-d"}


def _keys(pc, prov_keys=None):
    """照搬 fetch 里挑 Key 的那一段 —— 那段在函数体中间，抠不出来单测，
    所以这里复刻规则，并由下面那条测试盯着源码别改歪。"""
    ks = [str(v).strip() for k, v in (pc or {}).items()
          if "key" in k.lower() and str(v).strip()]
    if prov_keys:
        ks += [str(v).strip() for v in prov_keys.values() if str(v).strip()]
    return list(dict.fromkeys(ks))


class CatalogKeyTests(unittest.TestCase):
    def test_chaomo_has_no_api_key_field_at_all(self):
        """★ 这条是前提：超模配置里没有 `api_key`，所以只认它就是拿到空。"""
        self.assertNotIn("api_key", CHAOMO)
        self.assertEqual(CHAOMO.get("api_key", ""), "")

    def test_all_key_fields_are_collected(self):
        self.assertEqual(_keys(CHAOMO), ["sk-a", "sk-b", "sk-c", "sk-d"])
        self.assertEqual(_keys({"api_key": "sk-x", "base_url": "https://y"}), ["sk-x"])
        # 类上挂着多把的（坤鸡）也要并进来
        self.assertEqual(_keys({"api_key": "sk-1"}, {"a": "sk-1", "b": "sk-2"}),
                         ["sk-1", "sk-2"])
        # 同一把出现两次只试一次 —— 白拉一趟是花时间，不是免费的
        self.assertEqual(_keys({"api_key": "sk-1", "video_api_key": "sk-1"}), ["sk-1"])

    def test_non_key_fields_are_not_mistaken_for_keys(self):
        """`base_url` / `proxy` 不是 Key。当成 Key 会拿它去发一个必然 401 的请求。"""
        self.assertEqual(_keys({"base_url": "https://y", "proxy": "direct"}), [])

    def test_changing_any_key_invalidates_the_cache(self):
        """★ 换任何一把都要重拉。"""
        base = identity("chaomo", CHAOMO)
        for field in CHAOMO:
            changed = dict(CHAOMO, **{field: "sk-换了"})
            self.assertNotEqual(base, identity("chaomo", changed), field)
        self.assertNotEqual(base, identity("chaomo", dict(CHAOMO,
                                                          base_url="https://z")))

    def test_dict_order_does_not_change_the_fingerprint(self):
        """顺序不该影响指纹 —— 否则改一次配置就白拉一次。"""
        a = identity("chaomo", CHAOMO)
        b = identity("chaomo", {k: CHAOMO[k] for k in reversed(list(CHAOMO))})
        self.assertEqual(a, b)

    def test_the_source_still_walks_every_key(self):
        """盯着源码别改回只认 `api_key`。"""
        import inspect
        from core import model_catalog as MC
        src = inspect.getsource(MC.fetch)
        self.assertIn("'key' in k.lower()", src)
        self.assertNotIn("keys = [prov.session.api_key]", src)


if __name__ == "__main__":
    unittest.main()
