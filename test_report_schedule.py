import copy
from datetime import datetime, timedelta
import unittest
from unittest.mock import Mock
import report_schedule as schedule


def at(hour, minute=0, day=15):
    return datetime(2026, 9, day, hour, minute, tzinfo=schedule.KST)


class ScheduleTests(unittest.TestCase):
    def test_early_wakeup_and_two_independent_slots(self):
        ledger = {'2026-09-14/korea': {'status': 'sent'}}
        for hour, mode in ((6, 'global'), (18, 'korea')):
            if mode == 'korea':
                ledger['2026-09-15/global'] = {'status': 'sent'}
            target, actual, key = schedule.choose_slot(at(hour - 3), ledger)
            self.assertEqual((target, actual), (at(hour), mode))
            self.assertIsNone(schedule.choose_slot(at(hour), {**ledger, key: {'status': 'sent'}}))
            self.assertIsNone(schedule.choose_slot(at(hour), {**ledger, key: {'status': 'sending'}}))
        self.assertEqual(schedule.choose_slot(at(15), {'2026-09-15/global': {}})[1], 'korea')

    def test_catches_up_actual_delayed_runs(self):
        for now, mode in ((at(7, 37), 'global'), (at(8, 59), 'global'),
                          (at(22, 14), 'korea'), (at(0), 'korea')):
            self.assertEqual(schedule.choose_slot(now, {})[1], mode)
        self.assertIsNotNone(schedule.choose_slot(at(6, day=16), {'2026-09-15/global': {}}))
        self.assertIsNotNone(schedule.choose_slot(at(6, 30), {}))

    def test_midnight_catchup_preserves_previous_date(self):
        self.assertEqual(schedule.choose_slot(at(0), {})[2], '2026-09-14/korea')
        self.assertIsNone(schedule.choose_slot(at(2, 59), {'2026-09-14/korea': {'status': 'sent'}}))

    def test_expired_slots_are_replaced_by_next_report(self):
        self.assertEqual(schedule.choose_slot(at(18), {})[2], '2026-09-15/korea')
        self.assertEqual(schedule.choose_slot(at(6), {})[2], '2026-09-15/global')

    def test_pending_report_precedes_future_report(self):
        self.assertEqual(schedule.choose_slot(at(15), {})[1], 'global')
        self.assertEqual(schedule.choose_slot(at(15), {'2026-09-15/global': {'status': 'sent'}})[1], 'korea')

    def test_uncertain_morning_does_not_block_evening(self):
        ledger = {'2026-09-15/global': {'status': 'sending'}}
        self.assertEqual(schedule.choose_slot(at(18), ledger)[1], 'korea')

    def test_naive_time_is_rejected(self):
        with self.assertRaises(ValueError):
            schedule.choose_slot(datetime(2026, 9, 15), {})

    def test_wait_stops_at_target_with_bounded_sleeps(self):
        current = [at(3)]
        sleeps = []
        def advance(seconds):
            sleeps.append(seconds)
            current[0] += timedelta(seconds=seconds)
        schedule.wait_until(at(6), now_fn=lambda: current[0], sleep_fn=advance)
        self.assertEqual(current[0], at(6))
        self.assertTrue(all(0 < value <= 60 for value in sleeps))

    def test_preparation_precedes_exact_delivery_and_checkpoints(self):
        current = [at(3)]
        state = {}
        snapshots = []
        def wait(target):
            current[0] = max(current[0], target)
        def prepare(mode):
            self.assertEqual(current[0], at(5, 57))
            self.assertEqual(mode, 'global')
            current[0] += timedelta(minutes=1)
            return 'report'
        def send(report):
            self.assertEqual(current[0], at(6))
            self.assertEqual(report, 'report')
            self.assertEqual(state['key']['status'], 'sending')
        schedule.deliver_slot(at(6), 'global', 'key', state,
            prepare_fn=prepare, send_fn=send, now_fn=lambda: current[0], wait_fn=wait,
            persist_fn=lambda state: snapshots.append(copy.deepcopy(state)))
        self.assertEqual([entry['key']['status'] for entry in snapshots], ['sending', 'sent'])

    def test_preparation_failure_does_not_mark_sent(self):
        state = {}
        send = Mock()
        with self.assertRaises(RuntimeError):
            schedule.deliver_slot(at(6), 'global', 'key', state,
                prepare_fn=Mock(side_effect=RuntimeError()), send_fn=send,
                wait_fn=Mock(), persist_fn=Mock())
        self.assertEqual(state, {})
        send.assert_not_called()

    def test_ambiguous_send_failure_is_not_automatically_repeated(self):
        state = {}
        with self.assertRaises(RuntimeError):
            schedule.deliver_slot(at(6), 'global', '2026-09-15/global', state,
                prepare_fn=lambda mode: 'report', send_fn=Mock(side_effect=RuntimeError()),
                now_fn=lambda: at(6), wait_fn=Mock(), persist_fn=Mock())
        self.assertIsNone(schedule.choose_slot(at(6, 10), state))

    def test_excessively_late_preparation_never_sends(self):
        send = Mock()
        with self.assertRaises(RuntimeError):
            schedule.deliver_slot(at(6), 'global', 'key', {},
                prepare_fn=lambda mode: 'report', send_fn=send,
                now_fn=lambda: at(18), wait_fn=Mock(), persist_fn=Mock())
        send.assert_not_called()

    def test_late_report_is_actually_delivered(self):
        send = Mock()
        state = {}
        schedule.deliver_slot(at(6), 'global', 'key', state,
            prepare_fn=lambda mode: 'report', send_fn=send,
            now_fn=lambda: at(7, 37), wait_fn=Mock(), persist_fn=Mock())
        send.assert_called_once_with('report')
        self.assertEqual(state['key']['status'], 'sent')

    def test_workflow_respects_runner_budget_and_current_ledger(self):
        import yaml
        config = yaml.load((schedule.ROOT / '.github/workflows/market_report.yml').read_text(encoding='utf-8-sig'), Loader=yaml.BaseLoader)
        self.assertEqual(config['concurrency']['cancel-in-progress'], 'false')
        job = config['jobs']['report']
        self.assertEqual(job['steps'][0]['with']['ref'], 'master')
        self.assertGreater(int(job['timeout-minutes']), schedule.EARLY.total_seconds() / 60 + 10)
        self.assertEqual(config['on']['schedule'][0]['cron'], '7,27,47 * * * *')

if __name__ == '__main__':
    unittest.main()
