"""Langfuse 流量监听器 — 轮询 Langfuse API 检查新 traces。

用法:
    python monitor_langfuse.py
"""

import sys
import time
from datetime import datetime


class Colors:
    HEADER = "\033[95m"
    BLUE = "\033[94m"
    CYAN = "\033[96m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    RED = "\033[91m"
    BOLD = "\033[1m"
    END = "\033[0m"


class LangfuseMonitor:
    """使用 Langfuse SDK 轮询新 traces。"""

    def __init__(self, host: str = "http://127.0.0.1:3001"):
        self.host = host
        self.seen_ids: set = set()
        self.poll_interval = 3

    def check_new_traces(self) -> list:
        try:
            from src.config import settings
            from langfuse import Langfuse

            lf = Langfuse(
                public_key=settings.langfuse_public_key,
                secret_key=settings.langfuse_secret_key,
                base_url=self.host,
            )

            resp = lf.api.trace.list(limit=50)
            traces = resp.data if hasattr(resp, "data") else []

            new_traces = []
            for trace in traces:
                tid = getattr(trace, "id", "")
                if tid and tid not in self.seen_ids:
                    self.seen_ids.add(tid)
                    new_traces.append(trace)

            return new_traces

        except Exception:
            return []

    def print_trace(self, trace):
        ts = datetime.now().strftime("%H:%M:%S")

        try:
            name = getattr(trace, "name", "?")
            trace_id = getattr(trace, "id", "?")
            latency = getattr(trace, "latency", 0)
            observations = getattr(trace, "observations", [])

            print(f"\n{Colors.BOLD}{Colors.GREEN}[{ts}] [TRACE] 捕获到 Langfuse Trace!{Colors.END}")
            print(f"  {Colors.BOLD}Trace ID:{Colors.END} {Colors.CYAN}{trace_id}{Colors.END}")
            print(f"  {Colors.BOLD}Name:{Colors.END} {name}")
            print(f"  {Colors.BOLD}Latency:{Colors.END} {latency:.1f}s")
            print(f"  {Colors.BOLD}Observations:{Colors.END} {len(observations)}")

            # Try to extract input/output
            try:
                input_data = getattr(trace, "input", None)
                if input_data and isinstance(input_data, dict):
                    msgs = input_data.get("messages", [])
                    for msg in msgs:
                        if isinstance(msg, dict) and msg.get("type") == "human":
                            content = msg.get("content", "")
                            if content:
                                print(f"  {Colors.BOLD}Query:{Colors.END} {content[:200]}")
                                break
            except Exception:
                pass

            print(f"  {Colors.CYAN}{'-' * 60}{Colors.END}")

        except Exception as e:
            print(f"\n{Colors.YELLOW}[!] 解析 trace 失败: {e}{Colors.END}")


def main():
    host = "http://127.0.0.1:3001"
    print(f"\n{Colors.BOLD}{Colors.GREEN}[+] Langfuse Monitor 已启动{Colors.END}")
    print(f"  Langfuse: {Colors.BOLD}{host}{Colors.END}")
    print(f"  轮询间隔: {Colors.BOLD}3 秒{Colors.END}")
    print(f"{Colors.CYAN}{'=' * 50}{Colors.END}\n")

    monitor = LangfuseMonitor(host=host)

    try:
        while True:
            new_traces = monitor.check_new_traces()
            for trace in new_traces:
                monitor.print_trace(trace)
            time.sleep(monitor.poll_interval)
    except KeyboardInterrupt:
        print(f"\n{Colors.YELLOW}[!] 已停止监听{Colors.END}")
        sys.exit(0)


if __name__ == "__main__":
    main()
