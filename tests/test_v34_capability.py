# -*- coding: utf-8 -*-
"""第 0 章：模型能力档位冻结，以及它对提示词的实际影响。

不冻结的话，第九环节会照着「六类机制随便挑」写，而模型做不出来 ——
表现是转场糊掉，或者干脆变成一个长镜头，而任务标成功。
这是典型的「不报错、只是错」，只能靠肉眼在成片里发现。
"""
import shutil
import unittest

from core import pipeline_v34 as P, run_v34 as R
from core.executor import Job
from test_v34_run import EP1, PARAMS, FakeLLM, new_project


class DetectTests(unittest.TestCase):

    def test_known_multishot_model_is_reliable(self):
        self.assertEqual(R.detect_capability("seedance2.5-4-1-720p"), "RELIABLE")
        self.assertEqual(R.detect_capability("paisio/seedance2.5-00-480p"), "RELIABLE")

    def test_unknown_model_is_not_assumed_capable(self):
        """★ 认不出的一律 UNKNOWN，按有限档走。

        「不假设模型有多镜头能力」是这一层的默认立场 ——
        假设它行而它不行，出来的是糊掉的转场，钱已经花了。
        """
        for m in ("", "seed-2.0", "gpt-image-2", "某个没见过的模型"):
            self.assertEqual(R.detect_capability(m), "UNKNOWN", m)


class AllowedMechanismTests(unittest.TestCase):

    def test_downgrade_only_goes_toward_safer(self):
        """降级只往更稳的方向走，档位越低机制越少，且始终是子集。"""
        rel = set(R.allowed_mechanisms("RELIABLE"))
        lim = set(R.allowed_mechanisms("LIMITED"))
        uns = set(R.allowed_mechanisms("UNSUPPORTED"))
        self.assertTrue(uns < lim < rel, f"{uns} / {lim} / {rel}")
        self.assertEqual(uns, {"NATIVE_CUT"}, "最低档该只剩最稳的硬切")

    def test_unknown_behaves_like_limited(self):
        self.assertEqual(set(R.allowed_mechanisms("UNKNOWN")),
                         set(R.allowed_mechanisms("LIMITED")))

    def test_garbage_level_falls_back_instead_of_crashing(self):
        self.assertEqual(set(R.allowed_mechanisms("胡写的")),
                         set(R.allowed_mechanisms("UNKNOWN")))


class FreezeTests(unittest.TestCase):

    def setUp(self):
        self.pj = new_project()

    def tearDown(self):
        shutil.rmtree(self.pj.root, ignore_errors=True)

    def test_freeze_writes_the_execution_contract(self):
        cap = R.freeze_capability(self.pj, PARAMS, "seedance2.5-4-1-720p",
                                  log=lambda *a: None)
        self.assertEqual(cap["native_multishot_support"], "RELIABLE")
        self.assertEqual(cap["transition_execution_mode"], "MODEL_NATIVE_ONLY")
        self.assertEqual(cap["external_transition_editing"], "FORBIDDEN")
        self.assertEqual(R.capability_of(self.pj)["seg_duration"], 15)

    def test_freeze_is_sticky(self):
        """★ 冻结不是每次现算：中途换模型会让前后两段用不同的转场策略，
        接起来就是断的。要换得显式改配置。"""
        R.freeze_capability(self.pj, PARAMS, "seedance2.5-4-1-720p", log=lambda *a: None)
        again = R.freeze_capability(self.pj, PARAMS, "某个弱模型", log=lambda *a: None)
        self.assertEqual(again["native_multishot_support"], "RELIABLE",
                         "换了模型就把冻结的档位冲掉了")

    def test_capability_reaches_the_prompt(self):
        """★ 冻结了但没进提示词，等于白冻。"""
        llm = FakeLLM()
        q = lambda *a, **k: None
        R.freeze_capability(self.pj, PARAMS, "seedance2.5-4-1-720p", log=q)
        for sid in ("n1", "n2"):
            R.run_stage(self.pj, sid, llm=llm, params=PARAMS, log=q)
        for sid in ("n3", "n4", "n4b", "n5", "n6", "n7", "n8", "n9"):
            R.run_stage(self.pj, sid, llm=llm, params=PARAMS, episode=EP1, log=q)
        user = llm.user_for("n9")
        self.assertIn("seedance2.5-4-1-720p", user)
        self.assertIn("RELIABLE", user)
        self.assertIn("VFX_THREAD_TRANSITION", user, "高档位没放开全部机制")

    def test_weak_model_narrows_the_menu_in_the_prompt(self):
        """★ 低档位必须让提示词里能选的机制变少，否则闸门没生效。"""
        llm = FakeLLM()
        q = lambda *a, **k: None
        R.freeze_capability(self.pj, PARAMS, "没见过的模型", log=q)
        for sid in ("n1", "n2"):
            R.run_stage(self.pj, sid, llm=llm, params=PARAMS, log=q)
        for sid in ("n3", "n4", "n4b", "n5", "n6", "n7", "n8", "n9"):
            R.run_stage(self.pj, sid, llm=llm, params=PARAMS, episode=EP1, log=q)
        block = llm.user_for("n9").split("### 六类原生机制")[0]
        self.assertIn("UNKNOWN", block)
        self.assertNotIn("VFX_THREAD_TRANSITION", block.split("允许使用的转场机制")[-1],
                         "未知档位不该放开 VFX 转场")

    def test_not_frozen_yet_says_so_and_falls_back_to_safest(self):
        llm = FakeLLM()
        q = lambda *a, **k: None
        for sid in ("n1", "n2"):
            R.run_stage(self.pj, sid, llm=llm, params=PARAMS, log=q)
        for sid in ("n3", "n4", "n4b", "n5", "n6", "n7", "n8", "n9"):
            R.run_stage(self.pj, sid, llm=llm, params=PARAMS, episode=EP1, log=q)
        user = llm.user_for("n9")
        self.assertIn("还没冻结能力档位", user)
        self.assertIn("最保守", user)


class PipelineFreezeTests(unittest.TestCase):

    def setUp(self):
        self.pj = new_project()
        self.pj.save_meta(dict(self.pj.meta(), system="v34"))

    def tearDown(self):
        shutil.rmtree(self.pj.root, ignore_errors=True)

    def test_freeze_is_the_first_step_and_uses_the_video_chain_model(self):
        """★ 冻结必须排在第九环节之前，而且档位要来自真正会用的那个视频模型。"""
        steps = P.plan(self.pj)
        self.assertEqual(steps[0]["stage"], "n0")

        llm = FakeLLM()
        job = Job("pipeline", 1, 1, project_root=self.pj.root)
        P.run(job, self.pj, llm_factory=lambda: llm,
              provider_factory=lambda kind: [
                  {"provider": "paisio", "api_key": "k",
                   "model": "seedance2.5-4-1-720p" if kind == "video" else "gpt-image-2"}],
              params=PARAMS, concurrency=1, ep_concurrency=1, seg_concurrency=1,
              include_produce=False, include_deliver=False)
        cap = R.capability_of(self.pj)
        self.assertEqual(cap["target_video_model"], "seedance2.5-4-1-720p",
                         "拿了出图模型去判多镜头能力")
        self.assertEqual(cap["native_multishot_support"], "RELIABLE")


if __name__ == "__main__":
    unittest.main()
