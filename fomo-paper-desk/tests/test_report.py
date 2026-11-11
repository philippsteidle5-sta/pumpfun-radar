from datetime import datetime, timezone
import csv
import io

from fastapi.testclient import TestClient

from desk.api import create_app
from desk.clock import ManualClock
from desk.db import Database
from desk.report import build_report, report_csv
from desk.runtime import Desk


def test_report_boundaries_totals_testdata_and_csv(tmp_path):
    db = Database(str(tmp_path / 'report.sqlite'))
    desk = Desk(db, clock=ManualClock(datetime(2026, 11, 7, 12, tzinfo=timezone.utc)))
    desk.open_night()
    # Prior-day entries close during the requested owner day. TESTDATA and
    # unbooked or next-day exits must not alter the reported realized result.
    for mint, close, pnl, testdata, booked in [
        ('a', '2026-11-06T23:00:00.000000Z', '3.10', 0, 1),
        ('b', '2026-11-07T22:59:59.000000Z', '-1.20', 0, 1),
        ('c', '2026-11-07T23:00:00.000000Z', '999', 0, 1),
        ('d', '2026-11-07T12:00:00.000000Z', '999', 1, 1),
        ('e', '2026-11-07T12:00:00.000000Z', '999', 0, 0),
    ]:
        db.con.execute('''INSERT INTO positions
            (mint, name, opened_at, entry_price, size_usd, peak_price, stop_price,
             stop_pct, initial_stop_pct, mode, status, source, night_id,
             closed_at, pnl_usd, fees_usd, testdata, pnl_booked)
             VALUES (?, ?, '2026-11-05T12:00:00Z', '1', '50', '1', '.94',
             '6', '6', 'PAPER', 'closed', 'simulated', '2026-11-05', ?, ?, '.95', ?, ?)''',
             (mint, '=HYPERLINK("https://example.test")', close, pnl, testdata, booked))
    report = build_report(desk, '2026-11-07')
    assert report['closed_trades'] == 2
    assert report['net_pnl_usd'] == '1.90'
    assert report['fees_usd'] == '1.90'
    assert report['testdata_closed_trades'] == 1
    assert report['win_rate_pct'] == '50.00'
    records = list(csv.DictReader(io.StringIO(report_csv(report).lstrip('\ufeff'))))
    assert len(records) == 2
    assert records[0]['name'].startswith("'=")
    db.close()


def test_report_auth_validation_and_no_data(tmp_path):
    db = Database(str(tmp_path / 'api.sqlite'))
    desk = Desk(db)
    desk.open_night()
    with TestClient(create_app(desk, 'report-test-token')) as http:
        assert http.get('/api/report').status_code == 401
        assert http.get('/api/positions').status_code == 401
        headers = {'Authorization': 'Bearer report-test-token'}
        assert http.get('/api/report?date=invalid', headers=headers).status_code == 422
        assert http.get('/api/report?format=html', headers=headers).status_code == 422
        result = http.get('/api/report', headers=headers)
        assert result.status_code == 200
        assert result.json()['win_rate_pct'] is None
        export = http.get('/api/report?format=csv', headers=headers)
        assert export.status_code == 200
        assert 'text/csv' in export.headers['content-type']
        assert 'report-test-token' not in export.text
    db.close()
