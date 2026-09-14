"""Pre-arm twice-daily KST reports; prepare shortly before each delivery."""
import argparse
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import time

KST = timezone(timedelta(hours=9))
ROOT = Path(__file__).resolve().parent
LEDGER = ROOT / 'data/report_delivery.json'
EARLY = timedelta(hours=3)
PREPARE = timedelta(minutes=3)
LATE = timedelta(minutes=30)


def choose_slot(now, ledger):
    now = now.astimezone(KST)
    for hour, mode in ((6, 'global'), (18, 'korea')):
        target = now.replace(hour=hour, minute=0, second=0, microsecond=0)
        key = f'{target.date().isoformat()}/{mode}'
        if target - EARLY <= now <= target + LATE and key not in ledger:
            return target, mode, key
    return None


def wait_until(target, now_fn=None, sleep_fn=time.sleep):
    now_fn = now_fn or (lambda: datetime.now(KST))
    while True:
        remaining = (target - now_fn()).total_seconds()
        if remaining <= 0:
            return
        sleep_fn(min(remaining, 60))


def save_ledger(ledger):
    LEDGER.parent.mkdir(exist_ok=True)
    LEDGER.write_text(json.dumps(ledger, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    if os.environ.get('GITHUB_ACTIONS') != 'true':
        return
    def git(*args):
        subprocess.run(['git', *args], cwd=ROOT, check=True)
    git('config', 'user.name', 'github-actions[bot]')
    git('config', 'user.email', 'github-actions[bot]@users.noreply.github.com')
    git('add', 'data/report_delivery.json')
    git('commit', '-m', 'chore: checkpoint market report delivery')
    for attempt in range(3):
        git('pull', '--rebase', 'origin', 'master')
        result = subprocess.run(['git', 'push', 'origin', 'HEAD:master'], cwd=ROOT)
        if result.returncode == 0:
            return
        time.sleep(2)
    raise RuntimeError('Could not persist report delivery state')


def deliver_slot(target, mode, key, ledger, *, prepare_fn, send_fn,
                 now_fn=None, wait_fn=wait_until, persist_fn=save_ledger):
    now_fn = now_fn or (lambda: datetime.now(KST))
    wait_fn(target - PREPARE)
    report = prepare_fn(mode)
    if now_fn() > target + LATE:
        raise RuntimeError('Report preparation missed the delivery window')
    # Persist before sending: a timeout/partial Telegram delivery must not cause
    # automatic duplicate messages. Uncertain outcomes require log inspection.
    ledger[key] = {'status': 'sending', 'target': target.isoformat()}
    persist_fn(ledger)
    wait_fn(target)
    if now_fn() > target + LATE:
        raise RuntimeError('Delivery checkpoint missed the delivery window')
    send_fn(report)
    ledger[key] = {'status': 'sent', 'target': target.isoformat(), 'sent_at': now_fn().isoformat()}
    persist_fn(ledger)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('gate', 'run'))
    args = parser.parse_args()
    ledger = json.loads(LEDGER.read_text(encoding='utf-8')) if LEDGER.exists() else {}
    slot = choose_slot(datetime.now(KST), ledger)
    if args.command == 'gate':
        output = f'proceed={str(slot is not None).lower()}\n'
        print(output, end='')
        if os.environ.get('GITHUB_OUTPUT'):
            with open(os.environ['GITHUB_OUTPUT'], 'a', encoding='utf-8') as stream:
                stream.write(output)
        return
    if slot is None:
        print('No pending report in the delivery window')
        return
    target, mode, key = slot
    print(f'Armed {mode} report for {target.isoformat()}', flush=True)
    import market_report
    def prepare(mode):
        path = ROOT / '.prepared_report.html'
        subprocess.run([sys.executable, str(ROOT / 'market_report.py'), '--mode', mode,
                        '--output', str(path)], cwd=ROOT, check=True, timeout=600)
        return path.read_text(encoding='utf-8')
    deliver_slot(target, mode, key, ledger, prepare_fn=prepare, send_fn=market_report.send_telegram)


if __name__ == '__main__':
    main()
