import itertools
import json
from io import StringIO
from unittest.mock import patch

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.catalog.models import Category, Product
from apps.organizations.models import Organization, OrganizationStatus


@pytest.mark.django_db
def test_benchmark_rejects_database_with_unapplied_migrations():
    with patch(
        "apps.reports.management.commands.benchmark_workloads.unapplied_migration_labels",
        return_value=("accounts.0004_example", "reports.0001_example"),
    ):
        with pytest.raises(CommandError, match="unapplied migrations"):
            call_command("benchmark_workloads", organization="any-organization")


@pytest.mark.django_db
def test_benchmark_rejects_unknown_organization():
    with pytest.raises(CommandError, match="Organization not found"):
        call_command("benchmark_workloads", organization="missing-organization")


@pytest.mark.django_db
@pytest.mark.parametrize("iterations", [1, 101])
def test_benchmark_rejects_invalid_iteration_bounds(iterations):
    Organization.objects.create(
        name="Benchmark Organization",
        slug="benchmark-organization",
        status=OrganizationStatus.ACTIVE,
    )

    with pytest.raises(CommandError, match="Iterations must be between 2 and 100"):
        call_command(
            "benchmark_workloads",
            organization="benchmark-organization",
            iterations=iterations,
        )


@pytest.mark.django_db
def test_benchmark_rejects_dataset_below_required_profile():
    Organization.objects.create(
        name="Small Benchmark Organization",
        slug="small-benchmark-organization",
        status=OrganizationStatus.ACTIVE,
    )
    output = StringIO()

    with pytest.raises(CommandError, match="Dataset does not meet the pilot profile"):
        call_command(
            "benchmark_workloads",
            organization="small-benchmark-organization",
            iterations=2,
            stdout=output,
            json=True,
        )

    payload = json.loads(output.getvalue())
    assert payload["counts"] == {"products": 0, "stock_units": 0, "sales": 0}
    assert set(payload["scale_gaps"]) == {"products", "stock_units", "sales"}


@pytest.mark.django_db
def test_benchmark_reports_pilot_workloads_query_counts_and_tenant_scoping():
    call_command("seed_demo_data", verbosity=0)
    organization = Organization.objects.get(slug="nairobi-mobile-hub")
    other_organization = Organization.objects.create(
        name="Other Benchmark Tenant",
        slug="other-benchmark-tenant",
        status=OrganizationStatus.ACTIVE,
    )
    other_category = Category.objects.create(
        organization=other_organization,
        name="Other Tenant Category",
        code="other-tenant-category",
    )
    Product.objects.create(
        organization=other_organization,
        category=other_category,
        name="Other Tenant Benchmark Product",
        sku="OTHER-BENCHMARK",
        selling_price="100.00",
    )
    expected_product_count = Product.objects.filter(organization=organization).count()
    output = StringIO()

    call_command(
        "benchmark_workloads",
        organization=organization.slug,
        iterations=2,
        max_p95_ms=60_000,
        stdout=output,
        json=True,
    )

    payload = json.loads(output.getvalue())
    assert payload["organization"] == organization.slug
    assert payload["counts"]["products"] == expected_product_count
    assert payload["scale_gaps"] == {}
    assert payload["failed_budgets"] == {}
    assert set(payload["workloads"]) == {
        "product_register",
        "serialized_inventory",
        "sales_register",
        "purchase_register",
        "transfer_register",
        "approval_inbox",
    }
    assert payload["workloads"]["product_register"]["max_queries"] == 1
    assert payload["workloads"]["serialized_inventory"]["max_queries"] == 1
    assert payload["workloads"]["sales_register"]["max_queries"] == 1
    assert payload["workloads"]["purchase_register"]["max_queries"] <= 2
    assert payload["workloads"]["transfer_register"]["max_queries"] <= 2
    assert payload["workloads"]["approval_inbox"]["max_queries"] == 1


@pytest.mark.django_db
def test_benchmark_fails_when_p95_budget_is_exceeded():
    call_command("seed_demo_data", verbosity=0)
    clock = itertools.cycle((0.0, 2.0))

    with patch(
        "apps.reports.management.commands.benchmark_workloads.perf_counter",
        side_effect=lambda: next(clock),
    ):
        with pytest.raises(CommandError, match="P95 read budget exceeded"):
            call_command(
                "benchmark_workloads",
                organization="nairobi-mobile-hub",
                iterations=2,
                max_p95_ms=1,
                json=True,
                stdout=StringIO(),
            )


@pytest.mark.django_db
def test_benchmark_can_include_query_plans():
    call_command("seed_demo_data", verbosity=0)
    output = StringIO()

    call_command(
        "benchmark_workloads",
        organization="nairobi-mobile-hub",
        iterations=2,
        max_p95_ms=60_000,
        stdout=output,
        json=True,
        explain=True,
    )

    payload = json.loads(output.getvalue())
    assert set(payload["plans"]) == set(payload["workloads"])
    assert all(plan.strip() for plan in payload["plans"].values())
