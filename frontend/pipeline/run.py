"""
pipeline.run —— Run / Stage 通用基建(域无关)。详见 docs/anchors/run_system_design.md。

一次"造一个 benchmark" = 一个 Run(一个 output/runs/<run_id>/ 目录,自带 manifest/run.log/prompts.jsonl)。
pipeline = 一串命名 Stage;driver 按 --from/--to/--only 跑区间,幂等跳过已完成(manifest 为准)。

这里【只放基建】,不含任何工厂/域知识;具体 stage(whitepaper/world/orders/…)在 pipeline/factory.py 注册。
"""
from __future__ import annotations
from pathlib import Path
from dataclasses import dataclass
from typing import Callable
import json, time, threading, sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))                       # 保证 config 可导入(Tracer 用)
RUNS_DIR = ROOT / "output" / "runs"


def new_run_id(scenario: str) -> str:
    """run_id = <scenario>__<时间戳>,唯一且可排序。"""
    return f"{scenario}__{time.strftime('%Y%m%d-%H%M%S')}"


def _env_snapshot() -> dict:
    """run 启动时快照【工程配置】(模型/并发/端点)进 manifest。让监控显示这一轮【真实跑的】配置,
    而非读 config 此刻的 env(历史 run / 中途改过 env 都会失真)。★api_key 绝不入快照。"""
    try:
        import config
        return {"model": config.MODEL, "llm_concurrency": config.LLM_CONCURRENCY,
                "base_url": config.BASE_URL or ""}
    except Exception:
        return {}


# ════════════════════════════════════════════════════════════════════════════
# Tracer:每次 LLM 调用记到 <run>/prompts.jsonl(线程安全;网络在锁外 → 真并发)
# ════════════════════════════════════════════════════════════════════════════
class Tracer:
    def __init__(self, run: "Run"):
        self.pfile = run.dir / "prompts.jsonl"
        self.n = 0
        self._lock = threading.Lock()

    def chat_json(self, step, messages, **kw):
        import config
        t0 = time.time()
        try:
            out = config.chat_json(messages, **kw)        # 网络调用在锁外 → 真并发
            ok = not (isinstance(out, dict) and "__error__" in out)
        except Exception as e:
            out, ok = {"__error__": str(e)[:120]}, False
        latency_ms = int((time.time() - t0) * 1000)       # ★含 config 内部 3 次重试的总耗时(慢≈逼近超时)
        with self._lock:                                  # 计数 + 写文件串行化
            self.n += 1
            self._log(self.n, step, messages, out, kw, ok, latency_ms)
        return out

    def _log(self, i, step, messages, out, kw, ok=True, latency_ms=0):
        rec = {"i": i, "ts": time.time(), "latency_ms": latency_ms, "ok": ok, "step": step,
               "system": next((m["content"] for m in messages if m["role"] == "system"), ""),
               "user": next((m["content"] for m in messages if m["role"] == "user"), ""),
               "params": {k: v for k, v in kw.items() if k in ("temperature", "max_tokens")},
               "out_preview": json.dumps(out, ensure_ascii=False)[:600] if isinstance(out, (dict, list)) else str(out)[:600]}
        with self.pfile.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")


# ════════════════════════════════════════════════════════════════════════════
# Stage:一道工序。fn(run) 从 run 读 needs 的产物、写本步产物。
# ════════════════════════════════════════════════════════════════════════════
@dataclass
class Stage:
    name: str
    needs: list            # 依赖的前序 stage 名(driver 据此校验能否从此起跑)
    fn: Callable           # fn(run) -> None
    artifact: str          # 产物文件名(NN_<name>.json)


# ════════════════════════════════════════════════════════════════════════════
# Run:一次造 benchmark 的全部状态(目录 + manifest + logger + tracer + 产物读写)
# ════════════════════════════════════════════════════════════════════════════
class Run:
    def __init__(self, scenario: str, run_id: str, tag=None, config_meta: dict | None = None):
        self.scenario = scenario
        self.run_id = run_id
        self.tag = tag
        self.dir = RUNS_DIR / run_id
        self.dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self.log = self._make_logger()
        self.tracer = Tracer(self)
        self.manifest = self._load_or_init_manifest(tag, config_meta or {})
        self.manifest["env"] = _env_snapshot()      # 工程配置快照(模型/并发/端点),供监控集中展示
        self._save_manifest()

    # ── 产物读写(stage 只跟 Run 打交道,不碰路径)──
    def write(self, artifact: str, obj):
        (self.dir / artifact).write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")

    def read(self, artifact: str):
        return json.loads((self.dir / artifact).read_text(encoding="utf-8"))

    def has(self, artifact: str) -> bool:
        return (self.dir / artifact).exists()

    # ── manifest(单一真相)──
    def _load_or_init_manifest(self, tag, config_meta) -> dict:
        mf = self.dir / "manifest.json"
        if mf.exists():
            m = json.loads(mf.read_text(encoding="utf-8"))
            m["config"].update({k: v for k, v in config_meta.items() if v is not None})  # 本次调用的新配置覆盖
            if tag is not None:
                m["tag"] = tag
        else:
            m = {"run_id": self.run_id, "scenario": self.scenario, "tag": tag,
                 "created": time.strftime("%Y-%m-%dT%H:%M:%S"), "status": "running",
                 "config": config_meta, "stages": {}, "algo": {}, "llm_calls": 0}
        self.manifest = m
        self._save_manifest()
        return m

    def _save_manifest(self):
        self.manifest["llm_calls"] = self.tracer.n if hasattr(self, "tracer") else 0
        (self.dir / "manifest.json").write_text(
            json.dumps(self.manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    def start_stage(self, name: str):
        """E:进入 stage 时打 started_ts + current_stage,让监控不靠解析日志、并能 live 计时。"""
        self.manifest["stages"].setdefault(name, {})["started_ts"] = time.time()
        self.manifest["current_stage"] = name
        self.manifest["status"] = "running"
        self._save_manifest()

    def mark(self, stage_name: str, artifact: str, elapsed: float):
        self.manifest["stages"].setdefault(stage_name, {}).update({       # ★merge:保留 started_ts
            "done": True, "ts": time.strftime("%H:%M:%S"),
            "elapsed_s": round(elapsed, 1), "artifact": artifact})
        self._save_manifest()

    def is_done(self, stage_name: str) -> bool:
        return bool(self.manifest["stages"].get(stage_name, {}).get("done"))

    def set_algo(self, **kw):
        """记算法元数据(active_lines / entities / questions …),让 run 自描述到算法层。"""
        self.manifest.setdefault("algo", {}).update(kw)
        self._save_manifest()

    def set_status(self, status: str):
        self.manifest["status"] = status
        self._save_manifest()

    # ── logger:tee 到 stdout + <run>/run.log(带时间戳;线程安全)──
    def _make_logger(self):
        lf = self.dir / "run.log"

        def log(msg):
            line = f"[{time.strftime('%H:%M:%S')}] {msg}"
            with self._lock:
                print(line, flush=True)
                with lf.open("a", encoding="utf-8") as f:
                    f.write(line + "\n")
        return log


# ════════════════════════════════════════════════════════════════════════════
# _run_stage:跑一道【已注册 stage】的统一记账(start_stage→计时→mark;失败置 status)。
#   drive(幂等区间)与闭环 driver(直调绕过幂等)共用这一份,记账逻辑不再两处拷贝。
# ════════════════════════════════════════════════════════════════════════════
def _run_stage(run: Run, name: str, fn, artifact: str):
    """按 drive 同款记账跑一个【已注册 stage 函数】:start_stage + 计时 + mark + 失败置 status。
    闭环 driver 用它直调 stage(绕开 drive 的幂等跳过,B3),但 stage 体本身零复制——
    diversity / ckpt 续渲 / set_algo 全归各 stage 独占,两条编排路径不再分叉。"""
    t = time.time()
    run.start_stage(name)
    run.log(f"→ stage: {name}")
    try:
        fn(run)
    except BaseException as e:
        run.set_status("failed")
        run.log(f"✗ stage {name} 失败:{type(e).__name__}: {str(e)[:200]}")
        raise
    run.mark(name, artifact, time.time() - t)


# ════════════════════════════════════════════════════════════════════════════
# driver:按 stage 序列跑 [from,to] 区间(或 only 单步);幂等跳过已完成(除非 force)
# ════════════════════════════════════════════════════════════════════════════
def drive(run: Run, stages: list, from_stage=None, to_stage=None, only=None, force=False):
    names = [s.name for s in stages]
    by_name = {s.name: s for s in stages}
    for nm in ([only] if only else names[(names.index(from_stage) if from_stage else 0):
                                          (names.index(to_stage) if to_stage else len(names) - 1) + 1]):
        st = by_name[nm]
        for need in st.needs:                              # 依赖校验:前序产物必须在
            if not run.has(by_name[need].artifact):
                raise SystemExit(f"✗ stage『{nm}』需要前序『{need}』产物 {by_name[need].artifact},但不存在。"
                                 f"先跑 --to {need}(或 --from {need})。")
        if run.is_done(nm) and not force:
            run.log(f"⏭  跳过 {nm}(已完成;--force 重跑)")
            continue
        _run_stage(run, nm, st.fn, st.artifact)   # 记账执行(start_stage→计时→mark→失败置status);与闭环 driver 共用一份
    run.manifest["current_stage"] = ""            # 全部完成,无 current
    run.set_status("done")


# ════════════════════════════════════════════════════════════════════════════
# list_runs:扫 output/runs/*/manifest.json(新→旧),给 --list-runs 用
# ════════════════════════════════════════════════════════════════════════════
def list_runs() -> list:
    if not RUNS_DIR.exists():
        return []
    out = []
    for d in sorted(RUNS_DIR.iterdir(), reverse=True):
        mf = d / "manifest.json"
        if mf.exists():
            try:
                out.append(json.loads(mf.read_text(encoding="utf-8")))
            except Exception:
                pass
    return out


def latest_run_for(scenario: str):
    for m in list_runs():
        if m.get("scenario") == scenario:
            return m["run_id"]
    return None
