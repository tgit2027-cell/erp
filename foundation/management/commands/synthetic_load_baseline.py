import json
import hashlib
import resource
import statistics
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from pathlib import Path
from time import perf_counter

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import DatabaseError, close_old_connections, connection, transaction
from django.test import Client
from django.utils import timezone

from foundation.access import apply_stock_search, scoped_stock_queryset, stock_page_queryset
from foundation.load_diagnostics import capture_stages
from foundation.models import (
    ApprovedSellingPrice,
    BusinessUnit,
    BusinessUnitRoleAssignment,
    DemoReportSalesFact,
    DemoUserMarker,
    Item,
    Location,
    ProductBrand,
    ReportDataCapability,
    ReportDataGrant,
    ReportPanelGrant,
    ReportingPanel,
    ReportingRole,
    ReportingRoleMembership,
    StockExample,
    UserActionGrant,
    Variant,
)
from foundation.reporting_services import (
    _period_aggregation,
    _sales_line_queryset,
    _sales_page_queryset,
)


# Sized for the expected company: about 100 named ERP users.
MAX_SESSIONS = 100


class Command(BaseCommand):
    help = "Create and measure a synthetic catalog/report/stock workload in an isolated PostgreSQL load-test database."

    def add_arguments(self, parser):
        parser.add_argument("--items", type=int, default=8000)
        parser.add_argument("--sessions", type=int, default=40)
        parser.add_argument("--workers", type=int, default=40)
        parser.add_argument("--requests-per-session", type=int, default=1)
        parser.add_argument("--warmups", type=int, default=1)
        parser.add_argument("--seed", type=int, default=20261017)
        parser.add_argument(
            "--reuse-fixture", action="store_true",
            help="Measure an existing matching disposable load-test fixture without reseeding.",
        )
        parser.add_argument(
            "--output",
            default="generated-artifacts/synthetic-load-baseline.json",
            help="JSON results path (default is ignored generated-artifacts/).",
        )

    def handle(self, *args, **options):
        self._validate_environment(options)
        item_count = options["items"]
        session_count = options["sessions"]
        worker_count = options["workers"]
        request_count = options["requests_per_session"]
        warmups = options["warmups"]

        reuse = options["reuse_fixture"]
        if not reuse and Item.objects.filter(item_code__startswith="LOADTEST-").exists():
            raise CommandError("Load-test catalog rows already exist; run this command in a fresh load-test database.")
        if not reuse and get_user_model().objects.filter(username__startswith="loadtest_").exists():
            raise CommandError("Load-test accounts already exist; run this command in a fresh load-test database.")

        with transaction.atomic():
            entities = BusinessUnit.objects.filter(code="TVL", is_active=True, is_synthetic=True).select_related(
                "legal_entity"
            )
            unit = entities.first()
            brand = ProductBrand.objects.filter(code="TVF", is_synthetic=True).first()
            location = Location.objects.filter(
                code="STIV-G",
                is_synthetic=True,
                business_units=unit,
            ).first() if unit else None
            manager = get_user_model().objects.filter(username="demo_admin", demo_marker__is_synthetic=True).first()
            if not unit or not brand or not location or not manager:
                raise CommandError(
                    "Run migrations and the synthetic demo seed first; expected TVL, TVF, Ground floor, and demo_admin."
                )

            if reuse:
                users = list(get_user_model().objects.filter(
                    username__startswith="loadtest_", demo_marker__is_synthetic=True,
                ).order_by("username"))
                counts = (
                    Item.objects.filter(item_code__startswith="LOADTEST-").count(),
                    Variant.objects.filter(item__item_code__startswith="LOADTEST-").count(),
                    StockExample.objects.filter(item__item_code__startswith="LOADTEST-").count(),
                    DemoReportSalesFact.objects.filter(demo_fixture_key__startswith="loadtest-sales-").count(),
                )
                if counts != (item_count,) * 4 or len(users) != session_count:
                    raise CommandError("Existing disposable fixture does not match requested item/session counts.")
            else:
                self._seed_catalog(item_count, brand, unit, location, options["seed"], manager)
                users = self._seed_sessions(session_count, unit, manager)

        self.stdout.write(
            f"Prepared synthetic workload: {item_count} catalog items, {item_count} stock rows, "
            f"{item_count} report-only facts, {session_count} session users."
        )
        self._verify_denials(users[0], unit)
        explain_plans = self._explain_workload(users[0], unit)

        paths = self._request_paths(unit, item_count)
        clients = []
        for user in users:
            client = Client()
            client.force_login(user, backend="django.contrib.auth.backends.ModelBackend")
            clients.append(client)

        for _ in range(warmups):
            for endpoint, path in paths.items():
                response = clients[0].get(path, HTTP_HOST="localhost")
                if response.status_code != 200:
                    raise CommandError(f"Warm-up request {endpoint} returned HTTP {response.status_code}.")

        database_before = self._database_stats()
        active_sampler = _ActiveConnectionSampler()
        activity = _RequestActivity()
        active_sampler.start()
        process_cpu_before = time.process_time()
        run_started = perf_counter()
        try:
            with ThreadPoolExecutor(max_workers=worker_count) as executor:
                outcomes = []
                # Synchronize each endpoint wave, not just thread-pool creation.
                # Batching also supports session counts not divisible by workers.
                for _ in range(request_count):
                    for endpoint, path in paths.items():
                        for offset in range(0, len(clients), worker_count):
                            batch = clients[offset:offset + worker_count]
                            barrier = threading.Barrier(len(batch), timeout=30)
                            outcomes.extend(executor.map(
                                lambda client: self._run_session(
                                    client, {endpoint: path}, 1,
                                    start_barrier=barrier, activity=activity,
                                ),
                                batch,
                            ))
        finally:
            elapsed_seconds = perf_counter() - run_started
            process_cpu_seconds = time.process_time() - process_cpu_before
            active_sampler.stop()
        if active_sampler.error:
            raise CommandError(f"Could not sample PostgreSQL activity: {active_sampler.error}")

        samples = {name: [] for name in paths}
        response_bytes = {name: 0 for name in paths}
        for outcome in outcomes:
            for sample in outcome:
                samples[sample["endpoint"]].append(sample)
                response_bytes[sample["endpoint"]] += sample["response_bytes"]
        database_after = self._database_stats()
        result = {
            "schema_version": 2,
            "synthetic_only": True,
            "accounting_policy": "No accounting interpretation; report rows are synthetic load-only examples.",
            "measurement_scope": (
                "Django session-cookie test-client requests inside one Python process; excludes TCP, TLS, "
                "reverse-proxy, browser, and external-hosting overhead. Not an SLA or capacity forecast."
            ),
            "environment": {
                "python": sys.version.split()[0],
                "django": getattr(__import__("django"), "get_version")(),
                "postgres_server_version": self._server_version(),
                "database_name": connection.settings_dict["NAME"],
            },
            "dataset": {
                "catalog_items": item_count,
                "variants": item_count,
                "stock_rows": item_count,
                "report_only_sales_facts": item_count,
                "report_scope": "TVL only",
                "user_sessions": session_count,
                "active_permissions": ["read_stock for TVL", "sales report data for TVL"],
                "write_permissions": [],
            },
            "parameters": {
                "workers": worker_count,
                "requests_per_session_per_endpoint": request_count,
                "warmups_per_endpoint": warmups,
                "synthetic_seed": options["seed"],
            },
            "concurrency": {
                "scheduling": "synchronized per-endpoint waves, batched to configured worker count",
                "peak_in_flight_requests": activity.peaks,
                "active_postgres_sampler_note": (
                    "25 ms Python-thread sampling; may miss short SQL or be delayed by the GIL. "
                    "Active SQL is not the same as in-flight authenticated requests."
                ),
            },
            "results": {
                endpoint: self._summarize(values, response_bytes[endpoint], elapsed_seconds)
                for endpoint, values in samples.items()
            },
            "query_plans": explain_plans,
            "resources": {
                "suite_elapsed_ms": round(elapsed_seconds * 1000, 3),
                "python_process_cpu_seconds": round(process_cpu_seconds, 4),
                "python_process_peak_rss_kib": self._peak_rss_kib(),
                "host_memory_snapshot": self._host_memory_snapshot(),
                "max_observed_active_postgres_backends": active_sampler.maximum,
                "postgres_database_counter_delta": {
                    key: database_after[key] - database_before[key] for key in database_before
                },
            },
        }
        output = Path(options["output"]).expanduser()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
        self.stdout.write(self.style.SUCCESS(f"Synthetic baseline written to {output}."))
        for endpoint, metrics in result["results"].items():
            self.stdout.write(
                f"{endpoint}: {metrics['request_count']} requests; "
                f"p50 {metrics['p50_ms']} ms; p95 {metrics['p95_ms']} ms; "
                f"max {metrics['max_ms']} ms; {metrics['requests_per_second']} requests/s."
            )
        self.stdout.write(
            f"Observed {active_sampler.maximum} active PostgreSQL backend(s); "
            f"Python peak RSS {result['resources']['python_process_peak_rss_kib']} KiB."
        )

    @staticmethod
    def _validate_environment(options):
        if settings.APP_ENV != "demo":
            raise CommandError("The load harness requires APP_ENV=demo.")
        if connection.vendor != "postgresql":
            raise CommandError("The load harness requires PostgreSQL.")
        db_name = connection.settings_dict.get("NAME", "")
        if not db_name.startswith("tivoli_erp_loadtest"):
            raise CommandError("Use a separate database named tivoli_erp_loadtest or tivoli_erp_loadtest_*.")
        if connection.settings_dict.get("HOST", "") not in {"", "localhost", "127.0.0.1", "::1"}:
            raise CommandError("The load harness refuses non-local databases.")
        if not 8000 <= options["items"] <= 100_000:
            raise CommandError("--items must be between 8000 and 100000.")
        if not 1 <= options["sessions"] <= MAX_SESSIONS:
            raise CommandError(f"--sessions must be between 1 and {MAX_SESSIONS}.")
        if not 1 <= options["workers"] <= min(options["sessions"], MAX_SESSIONS):
            raise CommandError(f"--workers must be between 1 and the session count, up to {MAX_SESSIONS}.")
        if not 1 <= options["requests_per_session"] <= 100:
            raise CommandError("--requests-per-session must be between 1 and 100.")
        if not 0 <= options["warmups"] <= 20:
            raise CommandError("--warmups must be between 0 and 20.")

    @staticmethod
    def _seed_catalog(item_count, brand, unit, location, seed, approver):
        batch_size = 1000
        for first in range(1, item_count + 1, batch_size):
            last = min(item_count + 1, first + batch_size)
            items = Item.objects.bulk_create(
                [
                    Item(
                        brand=brand,
                        item_code=f"LOADTEST-{index:08d}",
                        description=f"Synthetic load-test catalog item {index:08d}",
                        category_label="Synthetic load test",
                        is_synthetic=True,
                    )
                    for index in range(first, last)
                ],
                batch_size=batch_size,
            )
            variants = Variant.objects.bulk_create(
                [
                    Variant(
                        item=item,
                        sku=f"{item.item_code}-V1",
                        description="Synthetic benchmark variant",
                        is_synthetic=True,
                    )
                    for item in items
                ],
                batch_size=batch_size,
            )
            StockExample.objects.bulk_create(
                [
                    StockExample(
                        item_id=variant.item_id,
                        variant=variant,
                        location=location,
                        responsible_business_unit=unit,
                        quantity=Decimal("1.00"),
                        is_synthetic=True,
                    )
                    for variant in variants
                ],
                batch_size=batch_size,
            )
            now = timezone.now()
            ApprovedSellingPrice.objects.bulk_create(
                [
                    ApprovedSellingPrice(
                        business_unit=unit,
                        variant=variant,
                        amount=Decimal(100 + (index * 13 + seed) % 4901),
                        currency="QAR",
                        revision=1,
                        is_current=True,
                        approved_by=approver,
                        is_synthetic=True,
                    )
                    for index, variant in zip(range(first, last), variants)
                ],
                batch_size=batch_size,
            )
            DemoReportSalesFact.objects.bulk_create(
                [
                    DemoReportSalesFact(
                        demo_fixture_key=f"loadtest-sales-{index:08d}",
                        business_unit=unit,
                        location=location,
                        item=item,
                        category_label="Synthetic load test",
                        quantity=Decimal("1.00"),
                        selling_price=Decimal(100 + ((index * 17 + seed) % 901)),
                        revenue=Decimal(100 + ((index * 17 + seed) % 901)),
                        created_at=now,
                        is_synthetic=True,
                    )
                    for index, item in zip(range(first, last), items)
                ],
                batch_size=batch_size,
            )

    @staticmethod
    def _seed_sessions(session_count, unit, manager):
        user_model = get_user_model()
        role, created = ReportingRole.objects.get_or_create(
            code="loadtest-sales",
            defaults={
                "name": "Synthetic load-test sales role",
                "created_by": manager,
                "is_synthetic": True,
            },
        )
        if not created:
            raise CommandError("The load-test reporting role already exists; use a fresh isolated load-test database.")
        ReportPanelGrant.objects.create(
            role=role,
            panel=ReportingPanel.SALES,
            granted_by=manager,
            is_synthetic=True,
        )
        ReportDataGrant.objects.create(
            role=role,
            capability=ReportDataCapability.SALES,
            business_unit=unit,
            granted_by=manager,
            is_synthetic=True,
        )
        users = []
        for index in range(1, session_count + 1):
            user = user_model.objects.create_user(
                username=f"loadtest_{index:03d}",
                password=None,
                is_active=True,
            )
            DemoUserMarker.objects.create(user=user, is_synthetic=True)
            UserActionGrant.objects.bulk_create([
                UserActionGrant(user=user, business_unit=unit, action=UserActionGrant.Action.READ_STOCK),
                # Sales users also open the quote screen and search the priced catalog.
                UserActionGrant(user=user, business_unit=unit, action=UserActionGrant.Action.CREATE_ORDER),
            ])
            BusinessUnitRoleAssignment.objects.create(
                user=user,
                business_unit=unit,
                role=BusinessUnitRoleAssignment.Role.SALESPERSON,
                assigned_by=manager,
            )
            ReportingRoleMembership.objects.create(
                role=role,
                user=user,
                assigned_by=manager,
                is_synthetic=True,
            )
            users.append(user)
        return users

    @staticmethod
    def _request_paths(unit, item_count):
        today = timezone.localdate()
        search = f"LOADTEST-{item_count:08d}"
        return {
            "catalog_lookup": f"/api/stock/?unit={unit.pk}&search={search}&page=1&page_size=50",
            "permitted_stock_query": f"/api/stock/?unit={unit.pk}&page=1&page_size=50",
            "quote_screen_choices": f"/api/purchasing/quotation-choices/?business_unit_id={unit.pk}",
            # Seven of the eight digits in a mid-catalog code: a substring match on about ten items,
            # which needs the trigram index rather than a prefix scan.
            "quote_item_search": (
                f"/api/purchasing/quotation-items/?business_unit_id={unit.pk}&search={f'{item_count // 2:08d}'[:7]}"
            ),
            "permitted_sales_report": (
                f"/api/reports/sales/?unit={unit.pk}&period=monthly"
                f"&start={today.isoformat()}&end={today.isoformat()}"
                "&page=1&page_size=50"
            ),
        }

    @staticmethod
    def _verify_denials(user, unit):
        other_unit = BusinessUnit.objects.filter(
            legal_entity=unit.legal_entity,
            is_active=True,
            is_synthetic=True,
        ).exclude(pk=unit.pk).first()
        if not other_unit:
            raise CommandError("The synthetic seed must include another business unit for denial checks.")
        client = Client()
        client.force_login(user, backend="django.contrib.auth.backends.ModelBackend")
        stock = client.get(f"/api/stock/?unit={other_unit.pk}", HTTP_HOST="localhost")
        report = client.get(
            f"/api/reports/sales/?unit={other_unit.pk}",
            HTTP_HOST="localhost",
        )
        if stock.status_code != 403 or report.status_code != 403:
            raise CommandError(
                f"Authorization sanity check failed: cross-unit stock={stock.status_code}, "
                f"cross-unit report={report.status_code}; expected 403 for both."
            )

    @staticmethod
    def _explain_workload(user, unit):
        today = timezone.localdate()
        filters = {"start": today, "end": today, "location_id": None}
        sales_lines = _sales_line_queryset([unit], filters)
        stock_rows = scoped_stock_queryset(unit, user)
        item_count = Item.objects.filter(item_code__startswith="LOADTEST-").count()
        search = f"LOADTEST-{item_count:08d}"
        stock_search = apply_stock_search(stock_rows, search)
        sales_rows = _sales_page_queryset(sales_lines, row_limit=50)
        demo_facts = DemoReportSalesFact.objects.filter(
            business_unit=unit, demo_fixture_key__startswith="loadtest-sales-", is_synthetic=True,
        )
        return {
            "supported": connection.vendor == "postgresql",
            "options": "EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)",
            "plans": {
                "stock_list_page": Command._explain(stock_page_queryset(
                    stock_rows, page=1, page_size=50, total_count=stock_rows.count(),
                )),
                "catalog_lookup": Command._explain(stock_page_queryset(
                    stock_search, page=1, page_size=50, total_count=stock_search.count(),
                )),
                "sales_report_page": Command._explain(sales_rows[:50]),
                "sales_report_lines": Command._explain(sales_lines),
                "demo_sales_facts": Command._explain(demo_facts),
                "sales_report_period_aggregate": Command._explain(
                    _period_aggregation(demo_facts, "monthly")
                ),
            },
        }

    @staticmethod
    def _explain(queryset):
        try:
            return json.loads(queryset.explain(analyze=True, buffers=True, format="json"))
        except (DatabaseError, AttributeError, NotImplementedError) as error:
            return {"supported": False, "error": str(error)[:1000]}

    @staticmethod
    def _run_session(client, paths, request_count, *, start_barrier=None, activity=None):
        close_old_connections()
        samples = []
        try:
            for _ in range(request_count):
                for endpoint, path in paths.items():
                    if start_barrier is not None:
                        start_barrier.wait()
                    query_durations = []

                    def record_query(execute, sql, params, many, context):
                        started = perf_counter()
                        try:
                            return execute(sql, params, many, context)
                        finally:
                            query_durations.append((perf_counter() - started) * 1000)

                    if activity is not None:
                        activity.enter(endpoint)
                    try:
                        with connection.execute_wrapper(record_query):
                            with capture_stages() as stages:
                                started = perf_counter()
                                response = client.get(
                                    path,
                                    HTTP_HOST="localhost",
                                    HTTP_X_SYNTHETIC_LOAD_DIAGNOSTICS="1",
                                )
                                finished = perf_counter()
                                duration_ms = (finished - started) * 1000
                    finally:
                        if activity is not None:
                            activity.leave(endpoint)
                    if response.status_code != 200:
                        raise CommandError(
                            f"Authorized request {endpoint} returned HTTP {response.status_code}."
                        )
                    samples.append(
                        {
                            "endpoint": endpoint,
                            "duration_ms": round(duration_ms, 3),
                            "response_bytes": len(response.content),
                            "response_sha256": hashlib.sha256(response.content).hexdigest(),
                            "started_monotonic_ms": round(started * 1000, 3),
                            "finished_monotonic_ms": round(finished * 1000, 3),
                            "thread_id": threading.get_ident(),
                            "sql_query_count": len(query_durations),
                            "sql_duration_ms": round(sum(query_durations), 3),
                            "sql_statement_durations_ms": [
                                round(duration, 3) for duration in query_durations
                            ],
                            "stage_ms": {name: round(value, 3) for name, value in stages.items()},
                        }
                    )
        finally:
            close_old_connections()
        return samples

    @staticmethod
    def _summarize(milliseconds, response_bytes, suite_seconds):
        samples = milliseconds
        ordered = sorted(sample["duration_ms"] for sample in samples)

        def percentile(values, fraction):
            if not values:
                return 0
            position = (len(values) - 1) * fraction
            lower = int(position)
            upper = min(len(values) - 1, lower + 1)
            value = values[lower] + (values[upper] - values[lower]) * (position - lower)
            return round(value, 3)

        def describe(values):
            ordered_values = sorted(values)
            return {
                "mean_ms": round(statistics.fmean(ordered_values), 3) if ordered_values else 0,
                "p50_ms": percentile(ordered_values, 0.5),
                "p95_ms": percentile(ordered_values, 0.95),
                "max_ms": round(max(ordered_values), 3) if ordered_values else 0,
            }

        sql_counts = [sample["sql_query_count"] for sample in samples]
        stage_names = sorted({name for sample in samples for name in sample["stage_ms"]})
        return {
            "request_count": len(ordered),
            "p50_ms": percentile(ordered, 0.50),
            "p95_ms": percentile(ordered, 0.95),
            "max_ms": round(max(ordered), 3) if ordered else 0,
            "mean_ms": round(statistics.fmean(ordered), 3) if ordered else 0,
            "requests_per_second": round(len(ordered) / suite_seconds, 3) if suite_seconds else 0,
            "response_bytes_total": response_bytes,
            "response_bytes_mean": round(response_bytes / len(samples)) if samples else 0,
            "sql_query_count_per_request": {
                "mean": round(statistics.fmean(sql_counts), 3) if sql_counts else 0,
                "min": min(sql_counts) if sql_counts else 0,
                "max": max(sql_counts) if sql_counts else 0,
            },
            "sql_duration_per_request_ms": describe(
                [sample["sql_duration_ms"] for sample in samples]
            ),
            "stage_per_request_ms": {
                stage: describe([sample["stage_ms"].get(stage, 0) for sample in samples])
                for stage in stage_names
            },
            "per_request": samples,
        }

    @staticmethod
    def _database_stats():
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT blks_read, blks_hit, tup_returned, temp_bytes "
                "FROM pg_stat_database WHERE datname = current_database()"
            )
            row = cursor.fetchone()
        return dict(zip(("blks_read", "blks_hit", "tuples_returned", "temp_bytes"), row))

    @staticmethod
    def _server_version():
        with connection.cursor() as cursor:
            cursor.execute("SHOW server_version")
            return cursor.fetchone()[0]

    @staticmethod
    def _peak_rss_kib():
        value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return int(value / 1024) if sys.platform == "darwin" else int(value)

    @staticmethod
    def _host_memory_snapshot():
        meminfo = Path("/proc/meminfo")
        if not meminfo.exists():
            return None
        values = {}
        for line in meminfo.read_text().splitlines():
            if line.startswith(("MemTotal:", "MemAvailable:")):
                key, value, *_ = line.split()
                values[key.rstrip(":").lower() + "_kib"] = int(value)
        return values


class _RequestActivity:
    """Request overlap, independently of whether PostgreSQL is executing SQL."""

    def __init__(self):
        self.lock = threading.Lock()
        self.active = {}
        self.peaks = {}

    def enter(self, endpoint):
        with self.lock:
            self.active[endpoint] = self.active.get(endpoint, 0) + 1
            self.peaks[endpoint] = max(self.peaks.get(endpoint, 0), self.active[endpoint])

    def leave(self, endpoint):
        with self.lock:
            self.active[endpoint] -= 1


class _ActiveConnectionSampler:
    def __init__(self):
        self.stop_event = threading.Event()
        self.maximum = 0
        self.error = None
        self.thread = threading.Thread(target=self._run, name="postgres-activity-sampler", daemon=True)

    def start(self):
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        self.thread.join(timeout=3)
        close_old_connections()

    def _run(self):
        close_old_connections()
        try:
            while not self.stop_event.is_set():
                with connection.cursor() as cursor:
                    cursor.execute(
                        "SELECT COUNT(*) FROM pg_stat_activity "
                        "WHERE datname = current_database() AND state = 'active'"
                    )
                    self.maximum = max(self.maximum, int(cursor.fetchone()[0]))
                self.stop_event.wait(0.025)
        except Exception as error:  # surfaced to the command after the worker batch
            self.error = str(error)
        finally:
            close_old_connections()
