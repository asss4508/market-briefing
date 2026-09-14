import os
import unittest
from datetime import datetime
from unittest.mock import Mock, patch
for key in ('TELEGRAM_BOT_TOKEN', 'TELEGRAM_CHAT_ID', 'ANTHROPIC_API_KEY'):
    os.environ.setdefault(key, 'test-only')
import market_report as report

def row(day='2026-09-14', value='6,684.37', change='-225.54', rate='-3.26'):
    return dict(localTradedAt=day, closePrice=value, compareToPreviousClosePrice=change, fluctuationsRatio=rate)

class ReportTests(unittest.TestCase):
    def test_global_mode_uses_us_indices_without_requiring_korea(self):
        client = Mock()
        client.messages.create.return_value.content = [Mock(text='global report')]
        world = {'S&P 500': '100', '나스닥': '200', '다우존스': '300'}
        with patch.object(report.anthropic, 'Anthropic', return_value=client):
            self.assertEqual(report.generate_report({}, {}, {}, world, [], [], mode='global'), 'global report')
        prompt = client.messages.create.call_args.kwargs['messages'][0]['content']
        self.assertIn('글로벌 모닝 리포트', prompt)
        self.assertIn('글로벌 증시 핵심 요약', prompt)
        self.assertNotIn('<b>🇰🇷 한국 시장 마감</b>', prompt)
        with self.assertRaises(RuntimeError):
            report.generate_report({}, {}, {}, {}, [], [], mode='global')

    def test_global_news_uses_overseas_finance_section(self):
        with patch.object(report.requests, 'get', return_value=Mock(text='')) as get:
            report.get_financial_news(mode='global')
        self.assertIn('/101/262', get.call_args_list[0].args[0])

    def collect(self, rows, hour=0):
        with patch.object(report.requests, 'get', return_value=Mock(json=lambda: rows)):
            return report.get_korean_indices(datetime(2026, 9, 15, hour, tzinfo=report.KST))

    def test_delayed_midnight_report_uses_previous_close(self):
        indices = self.collect([row('2026-09-15'), row()])
        self.assertEqual(report.validate_korean_indices(indices).isoformat(), '2026-09-14')
        self.assertEqual(indices['KOSPI']['rate'], '-3.26%')
        self.assertEqual(indices['KOSDAQ']['change'], '-225.54')

    def test_after_close_accepts_today(self):
        self.assertEqual(self.collect([row('2026-09-15')], 16)['KOSPI']['date'], '2026-09-15')

    def test_retry_and_other_index(self):
        response = Mock(json=lambda: [row()])
        with patch.object(report.requests, 'get', side_effect=[report.requests.Timeout(), response, response]), patch.object(report.time, 'sleep'):
            indices = report.get_korean_indices(datetime(2026, 9, 15, tzinfo=report.KST))
        self.assertEqual(len(indices), 2)

    def test_invalid_stale_or_empty_data_rejected(self):
        for rows in ([], [row('2026-08-01')], [row(value='NaN')], [row(rate='bad')]):
            with patch.object(report.time, 'sleep'):
                self.assertEqual(self.collect(rows), {})

    def test_incomplete_indices_block_model_call(self):
        with patch.object(report.anthropic, 'Anthropic') as client:
            for indices in ({}, {'KOSPI': {'date': '2026-09-14'}}, {'KOSPI': {'date': '2026-09-14'}, 'KOSDAQ': {'date': '2026-09-11'}}):
                with self.assertRaises(RuntimeError):
                    report.generate_report(indices, {}, {}, {}, [], [])
            client.assert_not_called()

    def test_model_receives_trading_date_not_execution_date(self):
        indices = self.collect([row()])
        client = Mock()
        client.messages.create.return_value.content = [Mock(text='report')]
        with patch.object(report.anthropic, 'Anthropic', return_value=client):
            report.generate_report(indices, {}, {}, {}, [], [])
        prompt = client.messages.create.call_args.kwargs['messages'][0]['content']
        self.assertIn('2026', prompt)
        self.assertIn('14', prompt)

if __name__ == '__main__':
    unittest.main()
