"""Linux user separation and seccomp for AutoDL's native container environment."""
from __future__ import annotations

import ctypes
import errno
import os
import resource


def restrict_process(uid: int, cpus: int, seconds: int, scratch: str):
    if os.geteuid() != 0 or uid < 10000:
        raise RuntimeError("classification isolation requires root and a dedicated unprivileged UID")
    # The child cannot escape its process group; cleanup kills the entire group.
    if os.getsid(0) != os.getpid():
        os.setsid()
    os.setgroups([])
    os.setgid(uid)
    os.setuid(uid)
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_NOFILE, (256, 256))
    resource.setrlimit(resource.RLIMIT_NPROC, (128, 128))
    resource.setrlimit(resource.RLIMIT_FSIZE, (64 * 1024 * 1024, 64 * 1024 * 1024))
    resource.setrlimit(resource.RLIMIT_CPU, (max(1, cpus * seconds), max(1, cpus * seconds)))
    cores = sorted(os.sched_getaffinity(0))[:cpus]
    os.sched_setaffinity(0, cores)
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(38, 1, 0, 0, 0):  # PR_SET_NO_NEW_PRIVS
        raise RuntimeError("cannot disable privilege escalation")
    # Landlock prevents writes outside the bounded scratch directory, including
    # /tmp and /dev/shm. Reads still obey ordinary Unix private-label permissions.
    libc.syscall.restype = ctypes.c_long
    write_access = (1 << 1) | sum(1 << bit for bit in range(4, 13))
    attribute = ctypes.c_uint64(write_access)
    ruleset = libc.syscall(444, ctypes.byref(attribute), ctypes.sizeof(attribute), 0)
    if ruleset < 0:
        raise RuntimeError("Landlock unavailable; cannot safely run submitted code")
    class PathRule(ctypes.Structure):
        _pack_ = 1
        _fields_ = [("allowed_access", ctypes.c_uint64), ("parent_fd", ctypes.c_int32)]
    try:
        # NVIDIA names CUDA helper threads through /proc/self/task/*/comm.
        for path, access in ((scratch, write_access), ("/dev", 1 << 1), ("/proc/self/task", 1 << 1)):
            descriptor = os.open(path, os.O_PATH | os.O_CLOEXEC)
            try:
                rule = PathRule(access, descriptor)
                if libc.syscall(445, ruleset, 1, ctypes.byref(rule), 0):
                    raise RuntimeError("cannot configure filesystem isolation")
            finally:
                os.close(descriptor)
        if libc.syscall(446, ruleset, 0):
            raise RuntimeError("cannot activate filesystem isolation")
    finally:
        os.close(ruleset)
    # Fail closed if the host cannot apply the syscall filter. GPU ioctl and
    # ordinary file/thread operations remain available; all new sockets are denied.
    seccomp = ctypes.CDLL("libseccomp.so.2", use_errno=True)
    seccomp.seccomp_init.argtypes = [ctypes.c_uint32]
    seccomp.seccomp_init.restype = ctypes.c_void_p
    seccomp.seccomp_syscall_resolve_name.argtypes = [ctypes.c_char_p]
    seccomp.seccomp_syscall_resolve_name.restype = ctypes.c_int
    seccomp.seccomp_rule_add.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_int, ctypes.c_uint]
    class Argument(ctypes.Structure):
        _fields_ = [("arg", ctypes.c_uint), ("op", ctypes.c_uint),
                    ("datum_a", ctypes.c_uint64), ("datum_b", ctypes.c_uint64)]
    seccomp.seccomp_rule_add_array.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_int,
                                              ctypes.c_uint, ctypes.POINTER(Argument)]
    seccomp.seccomp_load.argtypes = [ctypes.c_void_p]
    seccomp.seccomp_release.argtypes = [ctypes.c_void_p]
    context = seccomp.seccomp_init(0x7FFF0000)  # SCMP_ACT_ALLOW
    if not context:
        raise RuntimeError("cannot initialize seccomp")
    try:
        # CUDA communicates with the local driver through Unix sockets. Permit
        # that address family; no IP, raw packet, netlink or VSOCK sockets.
        for name in ("socket", "socketpair"):
            number = seccomp.seccomp_syscall_resolve_name(name.encode())
            rule = Argument(0, 1, 1, 0)  # SCMP_CMP_NE, AF_UNIX
            if seccomp.seccomp_rule_add_array(context, 0x00050000 | errno.EPERM, number, 1, ctypes.byref(rule)):
                raise RuntimeError("cannot restrict socket address families")
        for name in ("ptrace", "process_vm_readv", "process_vm_writev", "mount", "umount2",
                     "unshare", "setns", "setsid", "setpgid", "bpf", "perf_event_open",
                     "keyctl", "add_key", "request_key", "userfaultfd", "io_uring_setup"):
            number = seccomp.seccomp_syscall_resolve_name(name.encode())
            if number >= 0 and seccomp.seccomp_rule_add(context, 0x00050000 | errno.EPERM, number, 0):
                raise RuntimeError("cannot install seccomp rule")
        if seccomp.seccomp_load(context):
            raise RuntimeError("cannot activate seccomp")
    finally:
        seccomp.seccomp_release(context)
