import os
import socket
import time


from lai_runtime.netaudit import NetworkMonitor, process_tree, sockets_of, violations

HDR = "  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode\n"


def _row(local, remote, st, inode):
    return f"   0: {local} {remote} {st} 00000000:00000000 00:00000000 00000000  1000 0 {inode} 1\n"


def _proc(tmp_path):
    proc = tmp_path / "proc"
    for pid, ppid in ((100, 1), (101, 100), (200, 1)):
        d = proc / str(pid)
        (d / "fd").mkdir(parents=True)
        (d / "net").mkdir()
        (d / "stat").write_text(f"{pid} (ollama) S {ppid} 0 0\n")
    # 100: loopback dinleme + loopback bağlantı + dış TCP 8.8.8.8:443
    os.symlink("socket:[11]", proc / "100/fd/3")
    os.symlink("socket:[12]", proc / "100/fd/4")
    os.symlink("socket:[13]", proc / "100/fd/5")
    os.symlink("/dev/null", proc / "100/fd/0")
    tcp = HDR + _row("0100007F:2CAB", "00000000:0000", "0A", 11) + \
        _row("0100007F:2CAB", "0100007F:9C40", "01", 12) + \
        _row("0A00000A:D431", "08080808:01BB", "02", 13)
    # 101 (çocuk): UDP DNS 1.1.1.1:53 ve IPv6 ::1 bağlantısı
    os.symlink("socket:[21]", proc / "101/fd/7")
    os.symlink("socket:[22]", proc / "101/fd/8")
    udp = HDR + _row("0A00000A:A000", "01010101:0035", "07", 21)
    tcp6 = HDR + _row("00000000000000000000000001000000:2CAB",
                      "00000000000000000000000001000000:9C41", "01", 22)
    for pid in ("100", "101", "200"):
        (proc / pid / "net/tcp").write_text(tcp)
        (proc / pid / "net/udp").write_text(udp)
        (proc / pid / "net/tcp6").write_text(tcp6)
        (proc / pid / "net/udp6").write_text(HDR)
    # 200 ilgisiz süreç: dış bağlantısı olsa da denetlenmez
    os.symlink("socket:[13]", proc / "200/fd/3")
    return proc


def test_process_tree(tmp_path):
    proc = _proc(tmp_path)
    assert sorted(process_tree(100, proc)) == [100, 101]


def test_sockets_and_violations(tmp_path):
    proc = _proc(tmp_path)
    conns = sockets_of([100, 101], proc)
    assert {(c.proto, c.remote[0], c.remote[1]) for c in conns} == {
        ("tcp", "0.0.0.0", 0), ("tcp", "127.0.0.1", 40000), ("tcp", "8.8.8.8", 443),
        ("udp", "1.1.1.1", 53), ("tcp6", "::1", 40001)}
    v = violations(conns)
    assert len(v) == 2
    assert any("8.8.8.8:443" in x for x in v)
    assert any("1.1.1.1:53" in x and "DNS" in x for x in v)


def test_wildcard_listen_is_violation(tmp_path):
    proc = _proc(tmp_path)
    (proc / "100/net/tcp").write_text(HDR + _row("00000000:2CAB", "00000000:0000", "0A", 11))
    v = violations(sockets_of([100], proc))
    assert v and "dinleme" in v[0]


def test_real_monitor_sees_only_loopback():
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen()
    with NetworkMonitor(os.getpid(), interval=0.02) as mon:
        c = socket.create_connection(srv.getsockname())
        a, _ = srv.accept()
        time.sleep(0.15)
        c.close(); a.close()
    srv.close()
    assert mon.samples > 0
    assert any(x.remote[0] == "127.0.0.1" for x in mon.seen)
    assert mon.violations() == []
