import json

import pytest
from axe_playwright_python.sync_playwright import Axe
from django.core.management import call_command
from django.urls import reverse
from playwright.sync_api import sync_playwright

from apps.organizations.models import Plan


pytestmark = [pytest.mark.browser, pytest.mark.django_db(transaction=True)]


@pytest.fixture
def browser_plan():
    return Plan.objects.create(name="Browser Pilot", code="browser-pilot", monthly_price=0)


@pytest.fixture
def page(browser_plan):
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page()
        page.set_default_timeout(5_000)
        yield page
        browser.close()


@pytest.fixture
def demo_page(browser_plan):
    call_command("seed_demo_data")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page()
        page.set_default_timeout(5_000)
        yield page
        browser.close()


def _assert_no_serious_accessibility_violations(page):
    results = Axe().run(page)
    violations = [
        violation
        for violation in results.response.get("violations", [])
        if violation.get("impact") in {"serious", "critical"}
    ]
    details = [
        {
            "id": violation.get("id"),
            "impact": violation.get("impact"),
            "description": violation.get("description"),
            "nodes": [
                {
                    "target": node.get("target"),
                    "html": node.get("html"),
                    "summary": node.get("failureSummary"),
                }
                for node in violation.get("nodes", [])
            ],
        }
        for violation in violations
    ]
    assert not violations, json.dumps(details, indent=2)


def _login_demo_owner(page, base_url):
    page.goto(f"{base_url}{reverse('login')}")
    page.get_by_label("Email address").fill("brian@nairobi-mobile-hub.test")
    page.get_by_label("Password").fill("DemoPass123!")
    page.get_by_role("button", name="Sign in to MobiPOS").click()
    page.wait_for_url(f"{base_url}/")


def test_owner_can_register_then_sign_in_with_email_from_a_new_session(page, live_server):
    base_url = live_server.url

    page.goto(f"{base_url}{reverse('register-organization')}")
    page.get_by_label("Organization name").fill("Browser Telecom")
    page.get_by_label("First name").fill("Browser")
    page.get_by_label("Last name").fill("Owner")
    page.locator('input[name="email"]').fill("browser.owner@example.com")
    page.get_by_label("Password").fill("BrowserSecure123!")
    page.get_by_role("button", name="Create organization").click()

    page.wait_for_url(f"{base_url}/")
    assert page.get_by_text("Browser Telecom", exact=True).first.is_visible()
    assert page.get_by_text("Owner setup", exact=True).is_visible()

    page.context.clear_cookies()
    page.goto(f"{base_url}{reverse('login')}")
    page.get_by_label("Email address").fill("browser.owner@example.com")
    page.get_by_label("Password").fill("BrowserSecure123!")
    page.get_by_role("button", name="Sign in to MobiPOS").click()

    page.wait_for_url(f"{base_url}/")
    assert page.get_by_text("Browser Telecom", exact=True).first.is_visible()
    assert page.get_by_text("Owner setup", exact=True).is_visible()


def test_public_and_authenticated_core_pages_have_no_serious_axe_violations(page, live_server):
    base_url = live_server.url

    for route_name in ("dashboard", "login", "register-organization"):
        page.goto(f"{base_url}{reverse(route_name)}")
        _assert_no_serious_accessibility_violations(page)

    page.goto(f"{base_url}{reverse('register-organization')}")
    page.get_by_label("Organization name").fill("Accessible Telecom")
    page.get_by_label("First name").fill("Accessible")
    page.get_by_label("Last name").fill("Owner")
    page.locator('input[name="email"]').fill("accessible.owner@example.com")
    page.get_by_label("Password").fill("AccessibleSecure123!")
    page.get_by_role("button", name="Create organization").click()
    page.wait_for_url(f"{base_url}/")

    for route_name in ("dashboard", "help-center", "notification-list", "approval-inbox"):
        page.goto(f"{base_url}{reverse(route_name)}")
        _assert_no_serious_accessibility_violations(page)


def test_authenticated_dashboard_remains_usable_at_mobile_width(page, live_server):
    base_url = live_server.url
    page.set_viewport_size({"width": 390, "height": 844})

    page.goto(f"{base_url}{reverse('register-organization')}")
    page.get_by_label("Organization name").fill("Mobile Telecom")
    page.get_by_label("First name").fill("Mobile")
    page.get_by_label("Last name").fill("Owner")
    page.locator('input[name="email"]').fill("mobile.owner@example.com")
    page.get_by_label("Password").fill("MobileSecure123!")
    page.get_by_role("button", name="Create organization").click()
    page.wait_for_url(f"{base_url}/")

    assert page.get_by_text("Owner setup", exact=True).is_visible()
    assert page.get_by_role("link", name="Help").is_visible()
    assert page.locator("body").evaluate("element => element.scrollWidth <= window.innerWidth")


def test_owner_operational_workspaces_load_without_server_or_accessibility_errors(demo_page, live_server):
    page = demo_page
    base_url = live_server.url
    _login_demo_owner(page, base_url)

    workspace_routes = (
        "transfer-list",
        "transfer-create",
        "purchase-create",
        "pos-cart",
        "expense-create",
        "repair-create",
        "approval-inbox",
        "operational-report",
    )
    for route_name in workspace_routes:
        response = page.goto(f"{base_url}{reverse(route_name)}")
        assert response is not None
        assert response.status < 500, f"{route_name} returned HTTP {response.status}"
        assert page.locator("main").is_visible(), f"{route_name} did not render the application workspace"
        _assert_no_serious_accessibility_violations(page)

    page.goto(f"{base_url}{reverse('transfer-create')}")
    cancel_link = page.get_by_role("link", name="Cancel")
    assert cancel_link.get_attribute("href") == reverse("transfer-list")
