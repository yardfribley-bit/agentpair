"""Scoped post-observation OS snapshots, never pretend they are historical ETW."""
import ctypes
import os
import subprocess
import sys
import time
from .adapters import event


class ProcessSampler:
    def __init__(self, pipeline):
        self.pipeline = pipeline
        self.lib = None
        self.error = None
        self.latest = {}
        self.sampled_at = 0
        if sys.platform == "darwin":
            try:
                self.lib = ctypes.CDLL('/usr/lib/system/libsystem_sandbox.dylib', use_errno=True)
                self.lib.sandbox_check.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int]
                self.lib.sandbox_check.restype = ctypes.c_int
            except OSError:
                pass

    def poll(self):
        source = "os:process_snapshot"
        now = time.time()
        if sys.platform != "darwin":
            if self.error is None:
                self.error = "native process adapter not implemented on this platform"
                self.pipeline.submit(source, None, [event(source, "unsupported", "coverage.gap", {"reason": self.error})])
            return
        try:
            output = subprocess.run(['ps', '-axo', 'pid=,ppid=,uid=,rss=,lstart=,comm='],
                                    capture_output=True, text=True, timeout=4, check=True).stdout
            processes = {}
            for line in output.splitlines():
                p = line.strip().split(None, 9)
                if len(p) != 10:
                    continue
                pid, parent, uid, rss = map(int, p[:4])
                processes[pid] = {"pid": pid, "ppid": parent, "uid": uid, "rss_kib": rss,
                                  "start_identity": " ".join(p[4:9]), "executable": p[9]}
            roots = {pid for pid, p in processes.items() if '/WorkBuddy.app/' in p['executable']}
            scoped = set(roots)
            for _ in range(32):
                children = {pid for pid, p in processes.items() if p['ppid'] in scoped}
                if children <= scoped:
                    break
                scoped |= children
            events = []
            for pid in sorted(scoped):
                p = processes[pid]
                identity = str(pid) + ":" + p['start_identity']
                p.update(process_instance_id=identity, capture_mode="periodic_snapshot",
                         relation="observed_ppid", memory_content_observed=False)
                if self.lib:
                    ctypes.set_errno(0)
                    result = self.lib.sandbox_check(pid, None, 0)
                    error = ctypes.get_errno()
                    p['seatbelt'] = {"status": 'enabled' if result == 1 else 'not_enabled' if result == 0 and error == 0 else 'unknown',
                                     "result": result, "errno": error, "mechanism": "sandbox_check"}
                else:
                    p['seatbelt'] = {"status": "unknown"}
                events.append(event(source, [identity, int(now // 5)], "process.snapshot", p, when=now))
            self.latest = {pid: processes[pid] for pid in scoped}
            self.sampled_at = now
            self.pipeline.submit(source, {"sampled_at": now}, events)
        except (OSError, subprocess.SubprocessError) as exc:
            reason = type(exc).__name__ + ": process sampling unavailable"
            if self.error != reason:
                self.error = reason
                self.pipeline.submit(source, None, [event(source, ["sampling_error", reason], "coverage.gap", {"reason": reason})])


def parse_lsof_fields(output, processes):
    """Parse lsof field records; remote addresses remain observed strings, no DNS lookup."""
    rows, pid, current = [], None, None
    for line in output.splitlines():
        if not line:
            continue
        field, value = line[0], line[1:]
        if field == 'p':
            pid = int(value) if value.isdigit() else None
        elif field == 'f':
            current = {'pid': pid, 'fd': value}
            if pid in processes:
                current['process_instance_id'] = processes[pid]['process_instance_id']
                rows.append(current)
        elif current is not None:
            if field in {'t', 'P', 'n'}:
                current[{'t':'address_family', 'P':'protocol', 'n':'endpoint'}[field]] = value
            elif field == 'T' and value.startswith('ST='):
                current['state'] = value[3:]
    for row in rows:
        endpoint = row.get('endpoint', '')
        if '->' in endpoint:
            row['local_endpoint'], row['remote_endpoint'] = endpoint.split('->', 1)
            row['direction'] = 'not_determined'
        else:
            row['local_endpoint'] = endpoint
    return rows


class NetworkSampler:
    def __init__(self, pipeline):
        self.pipeline = pipeline
        self.error = None

    def poll(self, processes, process_sampled_at):
        source, now = 'os:network_snapshot', time.time()
        if not processes or now-process_sampled_at > 10:
            reason = 'no_recent_scoped_process_snapshot'
            if self.error != reason:
                self.error = reason
                self.pipeline.submit(source, None, [event(source, reason, 'coverage.gap', {'reason': reason})])
            return
        try:
            # -a intersects PID and internet filters. Without it lsof includes
            # unrelated connections. -nP avoids DNS and service-name resolution.
            result = subprocess.run(['/usr/sbin/lsof', '-nP', '-a', '-p',
                ','.join(str(pid) for pid in sorted(processes)), '-i', '-FpfPtTn'],
                capture_output=True, text=True, timeout=4)
            if result.returncode not in (0, 1) or result.stderr.strip():
                raise OSError('lsof could not verify connection coverage')
            rows = parse_lsof_fields(result.stdout, processes)
            events = [event(source, ['sample', int(now//5)], 'network.sample', {
                'scoped_process_count': len(processes), 'connection_count': len(rows),
                'capture_mode': 'periodic_snapshot', 'short_lived_connections_may_be_missed': True,
                'payload_observed': False, 'bytes_observed': False}, when=now)]
            for row in rows:
                row.update(capture_mode='periodic_snapshot', relation='observed_pid_socket',
                           tool_call_id=None, tool_correlation='not_observed',
                           payload_observed=False, bytes_observed=False)
                events.append(event(source, [row, int(now//5)], 'network.connection', row, when=now))
            self.pipeline.submit(source, {'sampled_at': now}, events)
            self.error = None
        except (OSError, subprocess.SubprocessError) as exc:
            reason = type(exc).__name__+': network sampling unavailable'
            if self.error != reason:
                self.error = reason
                self.pipeline.submit(source, None, [event(source, reason, 'coverage.gap', {'reason': reason})])
