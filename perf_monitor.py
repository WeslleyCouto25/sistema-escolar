import os
import time
from flask import g, request


def current_rss_mb():
    try:
        with open("/proc/self/status", "r", encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("VmRSS:"):
                    return round(int(line.split()[1]) / 1024, 1)
    except Exception:
        pass
    return None


def install_perf_monitor(app):
    slow_ms = int(os.getenv("SLOW_REQUEST_MS", "1800"))
    high_rss_mb = int(os.getenv("HIGH_RSS_LOG_MB", "380"))
    delta_warn_mb = int(os.getenv("RSS_DELTA_LOG_MB", "40"))

    @app.before_request
    def _perf_start():
        g._perf_started = time.perf_counter()
        g._perf_rss_start = current_rss_mb()

    @app.after_request
    def _perf_end(response):
        started = getattr(g, "_perf_started", None)
        elapsed_ms = round((time.perf_counter() - started) * 1000, 1) if started else None
        rss_start = getattr(g, "_perf_rss_start", None)
        rss_end = current_rss_mb()
        rss_delta = None
        if rss_start is not None and rss_end is not None:
            rss_delta = round(rss_end - rss_start, 1)
        content_length = response.content_length
        if content_length is None:
            try:
                content_length = int(response.headers.get("Content-Length", "") or 0) or None
            except Exception:
                content_length = None
        if (
            (elapsed_ms is not None and elapsed_ms >= slow_ms)
            or (rss_end is not None and rss_end >= high_rss_mb)
            or (rss_delta is not None and rss_delta >= delta_warn_mb)
        ):
            app.logger.warning(
                "PERF method=%s path=%s status=%s elapsed_ms=%s rss_start_mb=%s rss_end_mb=%s rss_delta_mb=%s content_length=%s",
                request.method, request.path, response.status_code, elapsed_ms,
                rss_start, rss_end, rss_delta, content_length,
            )
        return response
