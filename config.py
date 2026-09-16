"""
config.py —— 复用 self_instruct_demo 的 API 思路:OpenAI 兼容客户端 + .env。
本项目(memory_bench_factory)的所有 LLM 调用都走这里。

注意:.env 里的 MODEL 是 deepseek 推理模型(会先吐一段隐藏 reasoning_content,
再给正文 content)。所以:
  - max_tokens 要给足,否则 token 预算被 reasoning 吃光、正文为空;
  - 解析 JSON 时要剥掉模型可能加的 ```json 代码块包裹。
"""
import os
import re
import sys
import json
import threading
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI
import httpx

# 显式指向本文件同目录的 .env,这样不管从哪个 cwd 跑都能找到。
load_dotenv(Path(__file__).parent / ".env")

API_KEY = os.getenv("OPENAI_API_KEY")
BASE_URL = os.getenv("OPENAI_BASE_URL")
MODEL = os.getenv("MODEL")

if not API_KEY:
    sys.exit("[配置错误] 未找到 OPENAI_API_KEY,请检查 .env。")
if not MODEL:
    sys.exit("[配置错误] 未找到 MODEL,请检查 .env。")

# 盲判别只做短 JSON 抽取，允许固定使用不展开 reasoning 的模型；不设置时
# 沿用主模型，保持任意 OpenAI 兼容端点可直接运行。
DISCRIMINATOR_MODEL = os.getenv("DISCRIMINATOR_MODEL") or MODEL

# 大型关系/事件 JSON 需要稳定吐正文；只允许显式固定路由，不做运行时模型 fallback。
STRUCTURE_MODEL = os.getenv("STRUCTURE_MODEL") or MODEL

# ★socket 级超时(防死连接,不防慢代理):read 缺省 580s,代理持续发数据就不触发。
#   总响应时间由 chat() 的 DEADLINE_S 截止;这里只管 connect/write/pool 快速失败。
HTTP_READ_TIMEOUT_S = float(os.getenv("LLM_HTTP_READ_TIMEOUT_S", "580"))
client = OpenAI(api_key=API_KEY, base_url=BASE_URL,
                timeout=httpx.Timeout(HTTP_READ_TIMEOUT_S, connect=15.0,
                                      write=30.0, pool=15.0),
                max_retries=0)   # 重试逻辑在 chat_json 里(带退避+日志),SDK 层不重复重试

# ★全局 LLM 在飞并发上限:无论上层 pmap 怎么嵌套/并行,真正打到 API 的请求数 ≤ LLM_CONCURRENCY。
#   这一个数 = "API 能同时扛多少不抽风"的旋钮(env: LLM_CONCURRENCY,默认 8)。
LLM_CONCURRENCY = int(os.getenv("LLM_CONCURRENCY", "8"))
_LLM_SEM = threading.BoundedSemaphore(LLM_CONCURRENCY)
DEADLINE_S = int(os.getenv("LLM_DEADLINE_S", "600"))
MIN_COMPLETION_TOKENS = int(os.getenv("LLM_MIN_COMPLETION_TOKENS", "0"))


def _completion_text(response):
    """提取正文；空正文时保留服务端停止原因与 token 证据。"""
    choice = response.choices[0]
    text = choice.message.content or ""
    if text:
        return text
    usage = getattr(response, "usage", None)
    details = getattr(usage, "completion_tokens_details", None) if usage else None
    raise ValueError(
        "LLM 返回空正文:"
        f"finish_reason={getattr(choice, 'finish_reason', None)},"
        f"completion_tokens={getattr(usage, 'completion_tokens', None)},"
        f"reasoning_tokens={getattr(details, 'reasoning_tokens', None)}")


def chat(messages, temperature=0.7, top_p=1.0, max_tokens=4096, model=None):
    """薄封装:信号量限并发 + daemon 线程限总时间(DEADLINE_S)。"""
    effective_max_tokens = max(max_tokens, MIN_COMPLETION_TOKENS)
    effective_model = model or MODEL
    with _LLM_SEM:
        rv = [None, None]
        def _do():
            try:
                rv[0] = client.chat.completions.create(
                    model=effective_model, messages=messages,
                    temperature=temperature, top_p=top_p,
                    max_tokens=effective_max_tokens)
            except Exception as e:
                rv[1] = e
        t = threading.Thread(target=_do, daemon=True)
        t.start()
        t.join(timeout=DEADLINE_S)
        if t.is_alive():
            raise TimeoutError(f"LLM 调用超总截止 {DEADLINE_S}s")
        if rv[1] is not None:
            raise rv[1]
    return _completion_text(rv[0])


def _strip_code_fence(text):
    """剥掉 ```json ... ``` 这类代码块包裹,只留里面的内容。"""
    text = (text or "").strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    return m.group(1).strip() if m else text


def chat_json(messages, temperature=0.7, max_tokens=4096, retries=3,
              retry_delay_base=3.0, model=None, strict_json=False):
    """调用 LLM 并把回复解析成 JSON 对象。

    任何异常(LLM API timeout / network / JSON 解析失败)都触发指数退避重试。
    推理模型偶尔会在 JSON 外面带点话或裹代码块,做"剥壳 + 抓第一个 {..}/[..]" 容错。
    ``strict_json=True`` 时只接受去掉代码围栏后的完整 JSON，不抓子串、不修语法；
    与 ``retries=1`` 合用可形成单次、无修复的硬协议。
    """
    import time as _time
    last_err = None
    raw = ""
    for attempt in range(retries):
        try:
            raw = chat(messages, temperature=temperature, max_tokens=max_tokens,
                       model=model)
            cleaned = _strip_code_fence(raw)
            try:
                return json.loads(cleaned)
            except json.JSONDecodeError as e:
                last_err = e
                if strict_json:
                    raise
                m = re.search(r"(\{.*\}|\[.*\])", cleaned, re.DOTALL)
                if m:
                    try:
                        return json.loads(m.group(1))
                    except json.JSONDecodeError as e2:
                        last_err = e2
                # ★ json_repair 兜底:治 reasoning 模型偶发的少逗号/多逗号等 JSON 语法手滑
                try:
                    from json_repair import repair_json
                    obj = repair_json(cleaned, return_objects=True)
                    if obj not in (None, "", {}, []):
                        return obj
                except Exception as e3:
                    last_err = e3
        except Exception as e:
            # 任何异常(API timeout / network / 解析失败)指数退避重试
            last_err = e
            if attempt < retries - 1:
                delay = retry_delay_base * (2 ** attempt)
                print(f"[chat_json] 第 {attempt+1}/{retries} 次失败 "
                      f"({type(e).__name__}: {str(e)[:80]}), {delay:.1f}s 后重试...")
                _time.sleep(delay)
                continue
    raise ValueError(
        f"chat_json {retries} 次重试后仍失败。最后错误: {last_err}\n"
        f"--- 原始输出前 800 字 ---\n{raw[:800]}"
    )


from concurrent.futures import ThreadPoolExecutor


def pmap(fn, items, workers: int = 6):
    """把【彼此独立的 LLM 调用】并发跑(网络 I/O 密集 → 线程池即可,GIL 在等网络时释放)。
    保序返回 list;workers 控并发上限(别太大,免得把抽风的 API 挤爆)。fn 内异常会向上抛。"""
    items = list(items)
    if not items:
        return []
    with ThreadPoolExecutor(max_workers=max(1, min(workers, len(items)))) as ex:
        return list(ex.map(fn, items))
