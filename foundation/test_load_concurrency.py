import hashlib
import threading
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import MagicMock, patch

from django.core.management.base import CommandError
from django.http import HttpResponse
from django.test import SimpleTestCase

from .management.commands.synthetic_load_baseline import Command, _RequestActivity


class SyntheticLoadConcurrencyTests(SimpleTestCase):
    def test_synchronized_requests_overlap_and_record_response_fingerprints(self):
        activity = _RequestActivity()
        start = threading.Barrier(2, timeout=2)
        inside_request = threading.Barrier(2, timeout=2)

        class Client:
            def get(self, *args, **kwargs):
                inside_request.wait()
                return HttpResponse(b'{"synthetic":true}')

        with patch(
            "foundation.management.commands.synthetic_load_baseline.close_old_connections"
        ), patch(
            "foundation.management.commands.synthetic_load_baseline.connection", MagicMock()
        ), ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = list(executor.map(
                lambda _: Command._run_session(
                    Client(), {"report": "/synthetic/"}, 1,
                    start_barrier=start, activity=activity,
                ), range(2),
            ))
        self.assertEqual(activity.peaks, {"report": 2})
        self.assertEqual(activity.active, {"report": 0})
        samples = [rows[0] for rows in outcomes]
        self.assertEqual(len({row["thread_id"] for row in samples}), 2)
        self.assertTrue(all(
            row["response_sha256"] == hashlib.sha256(b'{"synthetic":true}').hexdigest()
            for row in samples
        ))
        self.assertLessEqual(
            max(row["started_monotonic_ms"] for row in samples),
            min(row["finished_monotonic_ms"] for row in samples),
        )

    def test_failed_response_is_not_counted_as_a_success_and_releases_activity(self):
        activity = _RequestActivity()
        client = MagicMock()
        client.get.return_value = HttpResponse(status=403)
        with patch(
            "foundation.management.commands.synthetic_load_baseline.close_old_connections"
        ), patch(
            "foundation.management.commands.synthetic_load_baseline.connection", MagicMock()
        ), self.assertRaisesRegex(CommandError, "HTTP 403"):
            Command._run_session(client, {"report": "/synthetic/"}, 1, activity=activity)
        self.assertEqual(activity.active, {"report": 0})

    def test_missing_barrier_participant_fails_with_a_bounded_wait(self):
        with patch(
            "foundation.management.commands.synthetic_load_baseline.close_old_connections"
        ), self.assertRaises(threading.BrokenBarrierError):
            Command._run_session(
                MagicMock(), {"report": "/synthetic/"}, 1,
                start_barrier=threading.Barrier(2, timeout=0.01),
            )
