"""Bounded crawler execution with per-crawler descendant cleanup.

Windows uses an unnamed, non-inheritable kill-on-close Job Object. POSIX
uses a new session/process group (descendants deliberately calling setsid
can escape that group). Never clean up by executable name.
"""

import multiprocessing
import os
import signal
import time


def _isolated_entry(ready, release, target, args):
    if os.name != "nt":
        os.setsid()
    ready.set()
    # No crawler code may run before the parent has established containment.
    if not release.wait(60):
        return
    target(*args)


class _WindowsJob:
    def __init__(self):
        import ctypes
        from ctypes import wintypes as w

        class BasicLimits(ctypes.Structure):
            _fields_ = [
                ("ProcessTime", ctypes.c_longlong),
                ("JobTime", ctypes.c_longlong),
                ("Flags", w.DWORD),
                ("MinWorkingSet", ctypes.c_size_t),
                ("MaxWorkingSet", ctypes.c_size_t),
                ("ActiveProcessLimit", w.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", w.DWORD),
                ("SchedulingClass", w.DWORD),
            ]

        class ExtendedLimits(ctypes.Structure):
            _fields_ = [
                ("Basic", BasicLimits),
                ("IoCounters", ctypes.c_ulonglong * 6),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        self.ctypes = ctypes
        self.api = ctypes.WinDLL("kernel32", use_last_error=True)
        signatures = {
            "CreateJobObjectW": ([ctypes.c_void_p, w.LPCWSTR], w.HANDLE),
            "SetInformationJobObject": ([w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD], w.BOOL),
            "OpenProcess": ([w.DWORD, w.BOOL, w.DWORD], w.HANDLE),
            "AssignProcessToJobObject": ([w.HANDLE, w.HANDLE], w.BOOL),
            "CloseHandle": ([w.HANDLE], w.BOOL),
        }
        for name, (args, result) in signatures.items():
            function = getattr(self.api, name)
            function.argtypes = args
            function.restype = result
        self.handle = self.api.CreateJobObjectW(None, None)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        limits = ExtendedLimits()
        limits.Basic.Flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not self.api.SetInformationJobObject(
            self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)
        ):
            error = ctypes.WinError(ctypes.get_last_error())
            self.close()
            raise error

    def assign(self, pid):
        # PROCESS_SET_QUOTA | PROCESS_TERMINATE, as required by AssignProcessToJobObject.
        process = self.api.OpenProcess(0x0100 | 0x0001, False, pid)
        if not process:
            raise self.ctypes.WinError(self.ctypes.get_last_error())
        try:
            if not self.api.AssignProcessToJobObject(self.handle, process):
                raise self.ctypes.WinError(self.ctypes.get_last_error())
        finally:
            self.api.CloseHandle(process)

    def close(self):
        if self.handle:
            if not self.api.CloseHandle(self.handle):
                raise self.ctypes.WinError(self.ctypes.get_last_error())
            self.handle = None


def _signal_group(pid, sig):
    try:
        os.killpg(pid, sig)
        return True
    except ProcessLookupError:
        return False


def run_isolated(target, args, timeout, name, cleanup_grace=0.5):
    """Return worker exit code; clean descendants even after a normal return.

    The ready/release handshake fails closed: assignment errors or startup
    timeouts never release the crawler. All parent-side exits run cleanup.
    """
    context = multiprocessing.get_context("spawn")
    ready, release = context.Event(), context.Event()
    process = context.Process(
        target=_isolated_entry, args=(ready, release, target, args), name=name
    )
    job = _WindowsJob() if os.name == "nt" else None
    started = False
    deadline = time.monotonic() + timeout
    try:
        process.start()
        started = True
        if job:
            job.assign(process.pid)
        while not ready.wait(min(0.05, max(0, deadline - time.monotonic()))):
            if not process.is_alive():
                raise RuntimeError(f"爬虫隔离初始化失败，退出码: {process.exitcode}")
            if time.monotonic() >= deadline:
                raise TimeoutError(f"爬虫运行超过 {timeout:g} 秒（隔离初始化阶段）")
        if time.monotonic() >= deadline:
            raise TimeoutError(f"爬虫运行超过 {timeout:g} 秒（隔离初始化阶段）")
        release.set()
        process.join(max(0, deadline - time.monotonic()))
        if process.is_alive():
            raise TimeoutError(f"爬虫运行超过 {timeout:g} 秒，已终止并清理进程组")
        return process.exitcode
    finally:
        try:
            if job:
                job.close()
            elif started:
                # If startup hasn't acknowledged setsid, stop the worker first.
                # Recheck ready afterwards to cover a concurrent acknowledgement.
                if not ready.is_set() and process.is_alive():
                    process.terminate()
                    process.join(5)
                if ready.is_set():
                    if _signal_group(process.pid, signal.SIGTERM):
                        time.sleep(cleanup_grace)
                        _signal_group(process.pid, signal.SIGKILL)
        finally:
            if started:
                process.join(5)
                if process.is_alive():
                    process.kill()
                    process.join(5)
                if process.is_alive():
                    raise RuntimeError(f"无法终止爬虫进程: {process.pid}")
                process.close()
            else:
                process.close()
        print(f"[PROCESS] {name}: 进程组清理完成", flush=True)
