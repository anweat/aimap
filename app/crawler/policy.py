"""爬虫资源获取规范:限流 / 重试 / 熔断 的统一策略。

所有数据源(arXiv、图书馆、未来的新源)统一走 FetchPolicy.execute(),
保证对目标站点友好且可控:

  1. SourceLimiter    —— 每来源独立限流(最小请求间隔),遵守站点要求;
  2. RetryPolicy      —— 可恢复错误重试:
                          429:优先 Retry-After,否则指数退避(含抖动);
                          5xx/超时:指数退避;
                          4xx 其他:不重试,直接失败;
  3. CircuitBreaker   —— 连续失败熔断:OPEN 期间直接拒绝请求(不打扰站点),
                          冷却后 HALF_OPEN 试探,成功即恢复。

配置项(settings / .env):
  AIMAP_CRAWL_MIN_INTERVAL     每来源最小间隔(秒),默认 3.0
  AIMAP_CRAWL_MAX_RETRIES      最大重试次数,默认 3
  AIMAP_CRAWL_BACKOFF_BASE     退避基数(秒),默认 2.0
  AIMAP_CRAWL_BACKOFF_MAX      退避上限(秒),默认 60
  AIMAP_CRAWL_CIRCUIT_THRESHOLD 熔断阈值(连续失败次数),默认 5
  AIMAP_CRAWL_CIRCUIT_COOLDOWN  熔断冷却(秒),默认 300
"""
from __future__ import annotations

import random
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from app.config import settings


class PolicyError(Exception):
    """策略层拒绝执行(熔断 OPEN 等)。"""


class RetriesExhausted(Exception):
    """重试耗尽:携带最后一次状态码与尝试次数。"""

    def __init__(self, source: str, status: int | None, attempts: int, last_error: str = ""):
        self.source = source
        self.status = status
        self.attempts = attempts
        self.last_error = last_error
        super().__init__(
            f"[{source}] 请求失败({attempts} 次尝试后放弃)"
            f"{f', 状态码 {status}' if status else ''} {last_error}"
        )


class CircuitOpen(PolicyError):
    def __init__(self, source: str, cooldown_left: float):
        self.source = source
        self.cooldown_left = cooldown_left
        super().__init__(f"[{source}] 熔断中,剩余冷却 {cooldown_left:.0f}s,暂停请求")


# ---------------------------------------------------------------------------
# 限流
# ---------------------------------------------------------------------------
class SourceLimiter:
    """每来源独立限流:两次请求间隔 ≥ min_interval。"""

    def __init__(self, min_interval: float | None = None):
        self.min_interval = min_interval if min_interval is not None else settings.crawl_min_interval
        self._last: dict[str, float] = {}
        self._lock = threading.Lock()

    def wait(self, source: str) -> None:
        """阻塞至允许发起请求。"""
        with self._lock:
            now = time.monotonic()
            last = self._last.get(source, 0.0)
            gap = self.min_interval - (now - last)
            if gap > 0:
                time.sleep(gap)
            self._last[source] = time.monotonic()


# ---------------------------------------------------------------------------
# 重试
# ---------------------------------------------------------------------------
@dataclass
class RetryPolicy:
    max_retries: int | None = None
    backoff_base: float | None = None
    backoff_max: float | None = None
    jitter: float = 0.3  # 抖动比例,避免多个爬虫同时重试

    def __post_init__(self):
        # 显式传入的参数优先;未传时取全局配置
        if self.max_retries is None:
            self.max_retries = settings.crawl_max_retries
        if self.backoff_base is None:
            self.backoff_base = settings.crawl_backoff_base
        if self.backoff_max is None:
            self.backoff_max = settings.crawl_backoff_max

    # 可恢复错误:429 限流、5xx 服务端、超时/连接类网络错误(由调用方以 status=None 表示)
    @staticmethod
    def _recoverable(status: int | None, error_type: str = "") -> bool:
        if status is None:
            et = error_type.lower()
            return "timeout" in et or "connect" in et or "connection" in et
        return status == 429 or status >= 500

    def delay_for(self, attempt: int, status: int | None, retry_after: float | None = None,
                  error_type: str = "") -> float:
        """第 attempt 次重试前的等待时间(0=不重试)。"""
        if not self._recoverable(status, error_type):
            return 0.0
        if retry_after:
            return retry_after
        base = min(self.backoff_base * (2 ** (attempt - 1)), self.backoff_max)
        return base * (1 + random.uniform(-self.jitter, self.jitter))


# ---------------------------------------------------------------------------
# 熔断
# ---------------------------------------------------------------------------
class CircuitBreaker:
    """连续失败熔断:CLOSED → OPEN(冷却) → HALF_OPEN(试探) → CLOSED。"""

    CLOSED, OPEN, HALF_OPEN = "closed", "open", "half_open"

    def __init__(self, threshold: int | None = None, cooldown: float | None = None):
        self.threshold = threshold if threshold is not None else settings.crawl_circuit_threshold
        self.cooldown = cooldown if cooldown is not None else settings.crawl_circuit_cooldown
        self.state = self.CLOSED
        self.failures = 0
        self.opened_at = 0.0

    def allow(self) -> bool:
        """当前是否允许请求。"""
        now = time.monotonic()
        if self.state == self.OPEN:
            if now - self.opened_at >= self.cooldown:
                self.state = self.HALF_OPEN  # 冷却结束,放行一次试探
                return True
            return False
        return True

    def record_success(self) -> None:
        self.failures = 0
        if self.state == self.HALF_OPEN:
            self.state = self.CLOSED

    def record_failure(self) -> None:
        self.failures += 1
        if self.state == self.HALF_OPEN:
            self.state = self.OPEN
            self.opened_at = time.monotonic()
        elif self.failures >= self.threshold:
            self.state = self.OPEN
            self.opened_at = time.monotonic()

    def cooldown_left(self) -> float:
        if self.state != self.OPEN:
            return 0.0
        return max(0.0, self.cooldown - (time.monotonic() - self.opened_at))


# ---------------------------------------------------------------------------
# 组合策略
# ---------------------------------------------------------------------------
class FetchPolicy:
    """爬虫统一请求策略:限流 + 熔断 + 重试。

    用法:
      policy = FetchPolicy()
      try:
          data = policy.execute("arxiv", lambda: http_get(url))
      except RetriesExhausted as e:
          ...   # 429 停止 / 重试耗尽
      except CircuitOpen as e:
          ...   # 熔断中,稍后重试
    """

    def __init__(self, limiter: SourceLimiter | None = None,
                 retry: RetryPolicy | None = None,
                 breaker: CircuitBreaker | None = None):
        self.limiter = limiter or SourceLimiter()
        self.retry = retry or RetryPolicy()
        self.breaker = breaker or CircuitBreaker()

    def execute(self, source: str, fn: Callable[[], Any],
                *, on_retry: Callable[[int, int | None, str], None] | None = None) -> Any:
        """执行带策略的请求。fn 返回 (status_code, retry_after, payload) 三元组。"""
        if not self.breaker.allow():
            raise CircuitOpen(source, self.breaker.cooldown_left())

        attempts = 0
        while True:
            self.limiter.wait(source)
            err_type = ""
            try:
                status, retry_after, payload = fn()
            except Exception as e:
                status, retry_after, payload = None, None, None
                err_type = type(e).__name__
                if not self.retry._recoverable(None, err_type):
                    self.breaker.record_failure()
                    raise

            if status is not None and 200 <= status < 300:
                self.breaker.record_success()
                return payload

            attempts += 1
            self.breaker.record_failure()
            delay = self.retry.delay_for(attempts, status, retry_after, err_type)
            if delay <= 0 or attempts > self.retry.max_retries:
                raise RetriesExhausted(source, status, attempts)
            if on_retry:
                on_retry(attempts, status, f"等待 {delay:.1f}s 重试")
            time.sleep(delay)


def get_fetch_policy() -> FetchPolicy:
    """全局默认策略(各爬虫可共享熔断状态)。"""
    return _GLOBAL_POLICY


_GLOBAL_POLICY = FetchPolicy()
