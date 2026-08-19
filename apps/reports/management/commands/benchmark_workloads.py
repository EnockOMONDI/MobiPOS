import json
import math
from time import perf_counter

from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.db.models import Prefetch
from django.test.utils import CaptureQueriesContext

from apps.catalog.models import Product
from apps.inventory.models import StockUnit
from apps.operations.models import ApprovalRequest, ApprovalStatus
from apps.organizations.models import Organization
from apps.purchasing.models import PurchaseOrder, PurchaseOrderLine
from apps.sales.models import Sale
from apps.transfers.models import StockTransfer, StockTransferLine


PROFILE_MINIMUMS = {
    "pilot": {"products": 10, "stock_units": 50, "sales": 20},
    "10x": {"products": 100, "stock_units": 500, "sales": 200},
}


def unapplied_migration_labels():
    executor = MigrationExecutor(connection)
    targets = executor.loader.graph.leaf_nodes()
    return tuple(
        f"{migration.app_label}.{migration.name}"
        for migration, backwards in executor.migration_plan(targets)
        if not backwards
    )


def percentile(values, percentile_value):
    ordered = sorted(values)
    index = max(0, math.ceil((percentile_value / 100) * len(ordered)) - 1)
    return ordered[index]


def workload_querysets(organization):
    return {
        "product_register": lambda: Product.objects.filter(
            organization=organization,
        ).select_related("category", "brand").order_by("name")[:25],
        "serialized_inventory": lambda: StockUnit.objects.filter(
            organization=organization,
        ).select_related("product", "location", "location__branch").order_by("-created_at")[:25],
        "sales_register": lambda: Sale.objects.filter(
            organization=organization,
        ).select_related("customer", "location", "location__branch", "created_by").order_by("-created_at")[:25],
        "purchase_register": lambda: PurchaseOrder.objects.filter(
            organization=organization,
        ).select_related("supplier", "destination", "destination__branch").prefetch_related(
            Prefetch("lines", queryset=PurchaseOrderLine.objects.select_related("product"))
        ).order_by("-created_at")[:25],
        "transfer_register": lambda: StockTransfer.objects.filter(
            organization=organization,
        ).select_related("source", "destination", "requested_by").prefetch_related(
            Prefetch("lines", queryset=StockTransferLine.objects.select_related("product", "stock_unit"))
        ).order_by("-created_at")[:25],
        "approval_inbox": lambda: ApprovalRequest.objects.filter(
            organization=organization,
            status=ApprovalStatus.PENDING,
        ).select_related("requested_by", "branch", "policy").order_by("-created_at")[:25],
    }


class Command(BaseCommand):
    help = "Measure read-only pilot workload latency against the configured database."

    def add_arguments(self, parser):
        parser.add_argument("--organization", required=True, help="Organization slug to benchmark.")
        parser.add_argument("--iterations", type=int, default=10)
        parser.add_argument("--max-p95-ms", type=float, default=1500)
        parser.add_argument("--required-profile", choices=PROFILE_MINIMUMS, default="pilot")
        parser.add_argument("--json", action="store_true")
        parser.add_argument("--explain", action="store_true", help="Include database query plans.")

    def handle(self, *args, **options):
        unapplied = unapplied_migration_labels()
        if unapplied:
            preview = ", ".join(unapplied[:8])
            if len(unapplied) > 8:
                preview = f"{preview}, and {len(unapplied) - 8} more"
            raise CommandError(
                "The configured database has unapplied migrations and cannot be benchmarked safely: "
                f"{preview}. Apply and verify migrations before benchmarking."
            )
        organization = Organization.objects.filter(slug=options["organization"]).first()
        if not organization:
            raise CommandError("Organization not found.")
        iterations = options["iterations"]
        if iterations < 2 or iterations > 100:
            raise CommandError("Iterations must be between 2 and 100.")

        counts = {
            "products": Product.objects.filter(organization=organization).count(),
            "stock_units": StockUnit.objects.filter(organization=organization).count(),
            "sales": Sale.objects.filter(organization=organization).count(),
        }
        required = PROFILE_MINIMUMS[options["required_profile"]]
        missing_scale = {
            key: {"actual": counts[key], "required": minimum}
            for key, minimum in required.items()
            if counts[key] < minimum
        }

        results = {}
        plans = {}
        for name, queryset_factory in workload_querysets(organization).items():
            timings = []
            query_counts = []
            for _ in range(iterations):
                with CaptureQueriesContext(connection) as captured:
                    started = perf_counter()
                    list(queryset_factory())
                timings.append((perf_counter() - started) * 1000)
                query_counts.append(len(captured))
            results[name] = {
                "p50_ms": round(percentile(timings, 50), 2),
                "p95_ms": round(percentile(timings, 95), 2),
                "max_ms": round(max(timings), 2),
                "max_queries": max(query_counts),
            }
            if options["explain"]:
                plans[name] = queryset_factory().explain()

        failed_budgets = {
            name: result["p95_ms"]
            for name, result in results.items()
            if result["p95_ms"] > options["max_p95_ms"]
        }
        payload = {
            "database_vendor": connection.vendor,
            "organization": organization.slug,
            "required_profile": options["required_profile"],
            "counts": counts,
            "scale_gaps": missing_scale,
            "max_p95_ms": options["max_p95_ms"],
            "failed_budgets": failed_budgets,
            "workloads": results,
        }
        if plans:
            payload["plans"] = plans

        if options["json"]:
            self.stdout.write(json.dumps(payload))
        else:
            self.stdout.write(f"Database: {connection.vendor}; organization: {organization.slug}")
            self.stdout.write(f"Dataset counts: {counts}")
            for name, result in results.items():
                self.stdout.write(
                    f"{name}: p50={result['p50_ms']}ms p95={result['p95_ms']}ms "
                    f"max={result['max_ms']}ms queries<={result['max_queries']}"
                )
            if plans:
                for name, plan in plans.items():
                    self.stdout.write(f"\nQuery plan: {name}\n{plan}")

        if missing_scale:
            raise CommandError(
                f"Dataset does not meet the {options['required_profile']} profile: {missing_scale}"
            )
        if failed_budgets:
            raise CommandError(f"P95 read budget exceeded: {failed_budgets}")
