"""Windows süreç işlemleri: Job Object (uygulama kapanınca çocuk ölür), süreç zamanları.

Bu modül Linux'ta içe aktarılabilir; Windows API'lerine yalnız çağrıldığında erişir.
"""
from __future__ import annotations

import ctypes
import subprocess
import time
from ctypes import wintypes

JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
JobObjectExtendedLimitInformation = 9
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
PROCESS_TERMINATE = 0x0001
PROCESS_SET_QUOTA = 0x0100
SYNCHRONIZE = 0x00100000
STILL_ACTIVE = 259
CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_NO_WINDOW = 0x08000000


class _IO_COUNTERS(ctypes.Structure):
    _fields_ = [(n, ctypes.c_ulonglong) for n in (
        "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
        "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]


class _BASIC_LIMIT(ctypes.Structure):
    _fields_ = [("PerProcessUserTimeLimit", ctypes.c_longlong),
                ("PerJobUserTimeLimit", ctypes.c_longlong),
                ("LimitFlags", ctypes.c_uint32), ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", ctypes.c_uint32),
                ("Affinity", ctypes.c_size_t), ("PriorityClass", ctypes.c_uint32),
                ("SchedulingClass", ctypes.c_uint32)]


class _EXT_LIMIT(ctypes.Structure):
    _fields_ = [("BasicLimitInformation", _BASIC_LIMIT), ("IoInfo", _IO_COUNTERS),
                ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]


class WindowsOps:
    name = "windows"

    def __init__(self, kernel32=None):
        self._k = kernel32
        self.job = None
        self.last_job_flags = 0

    @property
    def k(self):
        if self._k is None:
            self._k = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
        return self._k

    def attach_job(self, proc_handle) -> None:
        """Süreci KILL_ON_JOB_CLOSE iş nesnesine bağlar: uygulama ölünce çocuk da ölür."""
        job = self.k.CreateJobObjectW(None, None)
        if not job:
            raise OSError("CreateJobObjectW başarısız")
        info = _EXT_LIMIT()
        info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        self.last_job_flags = info.BasicLimitInformation.LimitFlags
        if not self.k.SetInformationJobObject(job, JobObjectExtendedLimitInformation,
                                              ctypes.byref(info), ctypes.sizeof(info)):
            raise OSError("SetInformationJobObject başarısız")
        if not self.k.AssignProcessToJobObject(job, proc_handle):
            raise OSError("AssignProcessToJobObject başarısız")
        self.job = job  # tutamaç açık kaldıkça çocuk yaşar; süreç ölünce sistem kapatır

    def spawn(self, argv: list[str], env: dict, log_file) -> subprocess.Popen:
        p = subprocess.Popen(argv, env=env, stdin=subprocess.DEVNULL, stdout=log_file,
                             stderr=subprocess.STDOUT,
                             creationflags=CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW)
        try:
            self.attach_job(int(p._handle))  # type: ignore[attr-defined]
        except OSError:
            p.kill()
            raise
        return p

    def _open(self, pid: int, access: int):
        return self.k.OpenProcess(access, False, pid)

    def alive(self, pid: int) -> bool:
        h = self._open(pid, PROCESS_QUERY_LIMITED_INFORMATION)
        if not h:
            return False
        try:
            code = wintypes.DWORD()
            return bool(self.k.GetExitCodeProcess(h, ctypes.byref(code))) and code.value == STILL_ACTIVE
        finally:
            self.k.CloseHandle(h)

    def start_id(self, pid: int) -> str | None:
        h = self._open(pid, PROCESS_QUERY_LIMITED_INFORMATION)
        if not h:
            return None
        try:
            c, e, k, u = (wintypes.FILETIME() for _ in range(4))
            if not self.k.GetProcessTimes(h, ctypes.byref(c), ctypes.byref(e), ctypes.byref(k),
                                          ctypes.byref(u)):
                return None
            return str((c.dwHighDateTime << 32) | c.dwLowDateTime)
        finally:
            self.k.CloseHandle(h)

    def owner_matches(self, pid: int, owner: str, binary: str) -> bool:
        # Windows'ta başka sürecin ortamı okunamaz: pid + oluşturma zamanı + çalıştırılabilir yol
        h = self._open(pid, PROCESS_QUERY_LIMITED_INFORMATION)
        if not h:
            return False
        try:
            buf = ctypes.create_unicode_buffer(32768)
            size = wintypes.DWORD(len(buf))
            if not self.k.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
                return False
            return buf.value.lower() == str(binary).lower()
        finally:
            self.k.CloseHandle(h)

    def terminate(self, pid: int, timeout: float) -> None:
        if self.job:
            self.k.TerminateJobObject(self.job, 1)  # serve + runner çocukları birlikte
        else:
            h = self._open(pid, PROCESS_TERMINATE | SYNCHRONIZE)
            if h:
                try:
                    self.k.TerminateProcess(h, 1)
                finally:
                    self.k.CloseHandle(h)
        end = time.time() + timeout
        while time.time() < end and self.alive(pid):
            time.sleep(0.05)
