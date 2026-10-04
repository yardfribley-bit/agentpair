import argparse
import datetime
import json
import os
import signal
import sys
import time
from pathlib import Path
from .store import Store, Pipeline
from .adapters import JSONLTail, TraceWatch
from .processes import ProcessSampler, NetworkSampler


def date(value):
    if not value:
        return 0
    return datetime.datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()


def main():
    parser = argparse.ArgumentParser(prog='agentreins_v2', description='Independent streaming WorkBuddy evidence collector')
    parser.add_argument('--data-dir', type=Path, default=Path.home()/'.agentreins_v2')
    commands = parser.add_subparsers(dest='command', required=True)
    collect = commands.add_parser('collect')
    collect.add_argument('--network-log', type=Path, default=Path.home()/'Library/Application Support/AppLens/telemetry/workbuddy-network.jsonl')
    collect.add_argument('--trace-dir', type=Path, default=Path.home()/'.workbuddy/traces')
    collect.add_argument('--since', help='ISO 8601 timestamp; used only for new durable source cursors')
    collect.add_argument('--once', action='store_true', help='Drain currently available evidence, then exit')
    collect.add_argument('--duration', type=float, help='Stop after this many seconds')
    collect.add_argument('--process-snapshots', action='store_true', help='Read-only macOS process/Seatbelt sampling every five seconds')
    collect.add_argument('--network-snapshots', action='store_true', help='Sample scoped macOS sockets; implies process snapshots')
    collect.add_argument('--queue-mib', type=int, default=16)
    collect.add_argument('--quota-mib', type=int, default=512)
    commands.add_parser('status')
    commands.add_parser('audit')
    commands.add_parser('graph')
    events = commands.add_parser('events')
    events.add_argument('--after', type=int, default=0)
    events.add_argument('--task')
    events.add_argument('--limit', type=int, default=300)
    content = commands.add_parser('content')
    content.add_argument('sha256')
    serve = commands.add_parser('serve')
    serve.add_argument('--port', type=int, default=0)
    args = parser.parse_args()
    if hasattr(args, 'queue_mib') and (args.queue_mib < 1 or args.quota_mib < 1):
        parser.error('queue and quota limits must be positive')
    # sqlite journals and all collector artifacts must be private by default.
    os.umask(0o077)
    store = Store(args.data_dir, getattr(args, 'quota_mib', 512)*1024*1024)
    try:
        if args.command == 'status':
            print(json.dumps(store.status(), ensure_ascii=False))
        elif args.command == 'graph':
            from .graph import graph
            print(json.dumps(graph(store), ensure_ascii=False, indent=2))
        elif args.command == 'audit':
            from .audit import audit
            print(json.dumps(audit(store), ensure_ascii=False, indent=2))
        elif args.command == 'events':
            for item in store.events(args.after, args.task, args.limit):
                print(json.dumps(item, ensure_ascii=False))
        elif args.command == 'content':
            print(json.dumps(store.content(args.sha256), ensure_ascii=False))
        elif args.command == 'serve':
            from .web import serve
            serve(store, args.port)
        else:
            pipe = Pipeline(store, args.queue_mib*1024*1024)
            network = JSONLTail(args.network_log, pipe, date(args.since), batch_lines=1)
            traces = TraceWatch(args.trace_dir, pipe, date(args.since))
            processes = ProcessSampler(pipe)
            sockets = NetworkSampler(pipe)
            stopping = False
            def stop(*_):
                nonlocal stopping
                stopping = True
            signal.signal(signal.SIGINT, stop)
            signal.signal(signal.SIGTERM, stop)
            start = time.monotonic()
            trace_at = process_at = 0
            try:
                while not stopping:
                    now = time.monotonic()
                    rejected_before = pipe.rejected
                    changed = network.poll()
                    if args.once or now >= trace_at:
                        changed = traces.poll() or changed
                        trace_at = now + 3
                    if (args.process_snapshots or args.network_snapshots) and now >= process_at:
                        processes.poll()
                        if args.network_snapshots:
                            sockets.poll(processes.latest, processes.sampled_at)
                        process_at = now + 5
                    if pipe.error:
                        raise OSError(pipe.error)
                    if args.once:
                        pipe.flush()
                        if not changed and pipe.rejected == rejected_before:
                            break
                    if args.duration is not None and now-start >= args.duration:
                        break
                    if not changed:
                        time.sleep(.2)
                pipe.flush()
                print(json.dumps(dict(store.status(), network_bytes_read=network.bytes_read,
                                      trace_bytes_read=traces.bytes_read, queue_rejections=pipe.rejected), ensure_ascii=False))
            finally:
                pipe.close()
    finally:
        store.close()


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError) as exc:
        print('agentreins_v2: '+str(exc), file=sys.stderr)
        sys.exit(1)
