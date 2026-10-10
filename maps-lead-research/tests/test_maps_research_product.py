"""Offline acceptance tests for the canonical product, with adversarial fixtures."""

import csv
import importlib
import json
from pathlib import Path

import pytest

from maps_lead_research.__main__ import main
from maps_lead_research.common import domain, lock, read_json, redact, write_json
from maps_lead_research.config import queries, validate
from maps_lead_research.extract import extract
from maps_lead_research.geography import CITIES, plan
from maps_lead_research.outputs import SCHEMAS, export
from maps_lead_research.pipeline import discover, enrich, initialize, rebuild
from maps_lead_research.providers import (
    AccountBudget,
    BudgetStop,
    Firecrawl,
    ProviderError,
    RapidAPI,
)
from maps_lead_research.registry import Registry, import_history


def campaign(tmp_path, **kw):
    return validate(
        {
            "version": 2,
            "campaign": "test",
            "business_category": "sign company",
            "queries": ["sign company"],
            "geography": {"mode": "cities", "targets": ["Seattle, WA"]},
            "max_net_new_companies": 2,
            "max_maps_requests": 8,
            "max_pages_per_search": 4,
            "maps_interval_seconds": 0,
            "output_directory": str(tmp_path / "run"),
            "historical_registry_path": str(tmp_path / "registry.sqlite"),
            **kw,
        }
    )


def place(pid="a", site="alpha-signs.com", **kw):
    return {
        "place_id": pid,
        "name": site.split(".")[0].title(),
        "website": "https://" + site,
        "phone": "",
        "full_address": "1 Main St, Seattle, WA 98101",
        "city": "Seattle",
        "state": "WA",
        "postal_code": "98101",
        "review_count": 12,
        "category": "Sign shop",
        **kw,
    }


class Maps:
    def __init__(self, *pages):
        self.pages = list(pages)
        self.calls = []

    def page(self, *args):
        self.calls.append(args)
        assert self.pages, "unexpected Maps call"
        item = self.pages.pop(0)
        if isinstance(item, Exception):
            raise item
        return {"data": item}


class FC:
    def __init__(self, balance=100, results=None):
        self.remaining = balance
        self.calls = []
        self.results = results or []

    def balance(self):
        return self.remaining

    def scrape(self, url):
        self.calls.append(url)
        if self.results:
            result = self.results.pop(0)
            if isinstance(result, Exception):
                raise result
        else:
            result = {
                "data": {
                    "markdown": "We provide sign company services for commercial businesses.\nJane Carter — President\njane.carter@"
                    + domain(url)
                    + "\n[Team](/team)"
                }
            }
        self.remaining -= 1
        return result


@pytest.mark.parametrize(
    "bad",
    [
        {"version": True},
        {"version": 1},
        {"campaign": "../no"},
        {"min_reviews": True},
        {"max_reviews": 1},
        {"max_net_new_companies": 0},
        {"max_maps_requests": -1},
        {"max_firecrawl_credit_delta": -1},
        {"max_firecrawl_pages_per_domain": -1},
        {"max_http_pages_per_domain": False},
        {
            "geography": {
                "mode": "nationwide",
                "include_states": ["WA"],
                "exclude_states": ["WA"],
            }
        },
        {"geography": {"mode": "regions", "regions": ["Unknown"]}},
        {"geography": {"mode": "states", "states": []}},
        {"country": "GB", "geography": {"mode": "nationwide"}},
        {"api_key": "secret"},
        {"decision_maker_roles": ["investor"]},
    ],
)
def test_validation_rejects_unsafe_or_ambiguous(tmp_path, bad):
    with pytest.raises(ValueError):
        campaign(tmp_path, **bad)


def test_nationwide_plan_and_defaults(tmp_path):
    c = campaign(tmp_path, geography={"mode": "nationwide"})
    p = plan(c)
    assert set(p["planned_states"]) == set(CITIES)
    assert len({t["region"] for t in p["targets"][:4]}) == 4
    assert len({t["state"] for t in p["targets"][:51]}) == 51
    assert p["targets"][0]["tier"] == 0 and p["targets"][-1]["tier"] > 0
    assert (
        c["max_firecrawl_pages_per_domain"] == 2 and c["max_http_pages_per_domain"] == 1
    )
    filtered = plan(
        campaign(
            tmp_path,
            geography={
                "mode": "regions",
                "regions": ["West"],
                "exclude_states": ["CA"],
            },
        )
    )
    assert "CA" not in filtered["planned_states"]
    assert filtered["planned_regions"] == ["West"]
    assert queries(campaign(tmp_path, suggest_related_queries=True)) == [
        "sign company",
        "sign services",
    ]


def test_conservative_identity_and_psl(tmp_path):
    assert domain("https://branch.signs.co.uk") == "signs.co.uk"
    assert domain("https://alice.github.io") == "alice.github.io"
    assert not domain("https://facebook.com/company")
    assert not domain("https://user:password@signs.com")
    reg = Registry(tmp_path / "r.sqlite")
    try:
        a, _ = reg.ingest(
            {
                "company_name": "Acme",
                "address": "1 Main, Seattle",
                "root_domain": "acme-signs.com",
            },
            "old",
        )
        assert reg.match({"company_name": "Acme"}) == (None, "")
        assert reg.match({"company_name": "Acme", "address": "2 Main, Boston"}) == (
            None,
            "",
        )
        assert (
            reg.match({"company_name": "Other", "root_domain": "acme-signs.com"})[0]
            == a
        )
        b, _ = reg.ingest({"company_name": "Other", "place_id": "b"}, "old")
        assert reg.match({"place_id": "b", "root_domain": "acme-signs.com"}) == (
            None,
            "identity_conflict",
        )
    finally:
        reg.close()


def test_import_provenance_idempotence_and_no_name_suppression(tmp_path):
    path = tmp_path / "history.csv"
    path.write_text(
        "company_name,website,address,email\nAcme,https://acme-signs.com,1 Main,info@acme-signs.com\nAcme,,,\n"
    )
    reg = Registry(tmp_path / "r.sqlite")
    try:
        r = import_history(reg, [path])
        assert r["accepted_rows"] == 1 and r["quarantined_rows"] == 1
        assert import_history(reg, [path])["unchanged_artifacts"] == 1
        assert not reg.match({"company_name": "Acme"})[0]
        assert reg.rows("SELECT confidence FROM source_rows") == [
            {"confidence": "medium"},
            {"confidence": "low"},
        ]
        assert (
            reg.rows("SELECT research_status FROM companies")[0]["research_status"]
            == "pending"
        )
        assert (
            json.loads(reg.rows("SELECT data FROM emails")[0]["data"])["deliverability"]
            == "unverified"
        )
    finally:
        reg.close()


def test_discovery_company_count_filters_history_pagination_and_rebuild(tmp_path):
    c = campaign(tmp_path, max_reviews=50)
    reg = Registry(c["historical_registry_path"])
    try:
        reg.ingest(
            {
                "company_name": "Known",
                "root_domain": "known-signs.com",
                "place_id": "known",
            },
            "previous",
        )
        reg.db.commit()
        run = initialize(c)
        provider = Maps(
            [
                place("known", "known-signs.com"),
                place("low", "low-signs.com", review_count=0),
                place("high", "high-signs.com", review_count=51),
                place(
                    "closed", "closed-signs.com", business_status="CLOSED_PERMANENTLY"
                ),
                place(),
                place("a2"),
            ],
            [place("b", "beta-signs.com"), place("overflow", "overflow-signs.com")],
        )
        discover(run, reg, provider, sleeper=lambda _: None)
        result = export(run, reg)
        assert len(provider.calls) == 2
        assert result["net_new_companies"] == 2 and result["observed_records"] == 8
        assert result["dispositions"] == {
            "historical_match": 1,
            "objective_exclusion": 4,
            "included": 2,
            "duplicate": 1,
        }
        assert result["valid"]
        provider2 = Maps()
        discover(run, reg, provider2, sleeper=lambda _: None)
        assert not provider2.calls
        dest = tmp_path / "rebuilt"
        rebuilt = rebuild(run, dest)
        assert rebuilt["valid"]
        for filename in SCHEMAS:
            assert (run / filename).read_bytes() == (dest / filename).read_bytes(), (
                filename
            )
    finally:
        reg.close()


def test_maps_retry_accounting_and_uncertain_resume(tmp_path):
    c = campaign(tmp_path, max_maps_requests=2)
    run = initialize(c)
    reg = Registry(c["historical_registry_path"])
    try:
        m = Maps(ProviderError(429), [place()])
        discover(run, reg, m, sleeper=lambda _: None)
        assert export(run, reg)["maps_request_attempts"] == 2
        assert len(m.calls) == 2
    finally:
        reg.close()
    other = tmp_path / "other"
    other.mkdir()
    c = campaign(
        other,
        geography={"mode": "cities", "targets": ["Seattle, WA", "Phoenix, AZ"]},
        queries=["sign company", "sign shop"],
    )
    run = initialize(c)
    reg = Registry(c["historical_registry_path"])
    try:
        m = Maps(ProviderError(error="network_error:ConnectionError"))
        discover(run, reg, m, sleeper=lambda _: None)
        assert len(m.calls) == 1
        receipt = read_json(run / "receipts/maps/000001.json")
        assert receipt["error"] == "network_error:ConnectionError"
        assert receipt["status"] == "pending"
        fresh = Maps()
        discover(run, reg, fresh, sleeper=lambda _: None)
        assert not fresh.calls
        assert read_json(run / "manifest.json")["discovery_status"] == "needs_attention"
        assert (
            read_json(run / "manifest.json")["discovery_stop_reason"]
            == "uncertain_request"
        )
    finally:
        reg.close()


def test_maps_network_preflight_and_safe_error_categories(monkeypatch):
    import socket
    from maps_lead_research import providers

    monkeypatch.setattr(providers, "require_live", lambda _: None)
    monkeypatch.setenv("RAPIDAPI_KEY", "fixture")

    def fail_dns(*args, **kwargs):
        raise socket.gaierror("sensitive diagnostic details")

    monkeypatch.setattr(socket, "getaddrinfo", fail_dns)
    with pytest.raises(ValueError, match="no Maps request sent") as exc:
        RapidAPI(True)
    assert "sensitive" not in str(exc.value)
    assert ProviderError(error="secret provider body").error is None


def test_budget_reserve_delta_lock_and_pending(tmp_path):
    c = campaign(tmp_path, max_firecrawl_credit_delta=2, firecrawl_safety_reserve=10)
    fc = FC(11)
    path = tmp_path / "account.json"
    with AccountBudget(fc, "run", c, path) as b:
        with pytest.raises(RuntimeError), lock(str(path) + ".lock"):
            pass
        b.scrape("https://alpha-signs.com", tmp_path / "raw.json")
        assert b.used() == 1
        with pytest.raises(BudgetStop, match="reserve"):
            b.scrape("https://beta-signs.com", tmp_path / "raw2.json")
    assert fc.remaining == 10
    fc = FC(100)
    with AccountBudget(fc, "delta", c, path) as b:
        for i in range(2):
            b.scrape("https://alpha-signs.com/" + str(i), tmp_path / f"{i}.json")
        with pytest.raises(BudgetStop, match="delta"):
            b.scrape("https://alpha-signs.com/3", tmp_path / "3.json")
    bad = FC(100, [ProviderError()])
    with AccountBudget(bad, "uncertain", c, path) as b:
        with pytest.raises(ProviderError):
            b.scrape("https://alpha-signs.com", tmp_path / "pending.json")
    with pytest.raises(BudgetStop, match="Uncertain"):
        with AccountBudget(fc, "next", c, path):
            pass


def test_contact_email_pattern_provenance_and_former_rejection(tmp_path):
    c = campaign(tmp_path, max_contacts_per_company=5, max_emails_per_company=5)
    markdown = """We provide sign company services across Washington.
Jane Carter — President
jane.carter@alpha-signs.com
Alan Wilson — General Manager
alan.wilson@alpha-signs.com
Rita Hayes — Marketing Director
Former CEO — Fred Smith
info@alpha-signs.com"""
    e = extract(
        "co1",
        "alpha-signs.com",
        [
            {
                "url": "https://alpha-signs.com/team",
                "markdown": markdown,
                "timestamp": "2026-09-08",
            }
        ],
        c,
    )
    assert {p["name"] for p in e["contacts"]} == {
        "Jane Carter",
        "Alan Wilson",
        "Rita Hayes",
    }
    statuses = {e["email"]: e["status"] for e in e["emails"]}
    assert statuses["jane.carter@alpha-signs.com"] == "public_named_email"
    assert statuses["rita.hayes@alpha-signs.com"] == "inferred_pattern"
    assert statuses["info@alpha-signs.com"] == "public_general_email"
    assert all(e["deliverability"] == "unverified" for e in e["emails"])
    assert e["source_relevance"] == "confirmed"


def test_domain_reuse_two_pages_breadth_and_offline_rebuild(tmp_path):
    c = campaign(tmp_path, max_firecrawl_credit_delta=10)
    run = initialize(c)
    reg = Registry(c["historical_registry_path"])
    try:
        discover(
            run,
            reg,
            Maps([place(), place("a2"), place("b", "beta-signs.com")]),
            sleeper=lambda _: None,
        )
        fc = FC()
        state = enrich(
            run, reg, fc, budget_path=tmp_path / "account.json", sleeper=lambda _: None
        )
        assert len(fc.calls) == 4
        assert all(url.endswith("/") for url in fc.calls[:2])
        assert all(r["status"] == "complete" for r in state["research"].values())
        result = export(run, reg)
        assert result["valid"] and result["output_counts"]["people_contacts.csv"] == 2
        dest = tmp_path / "rebuilt"
        assert rebuild(run, dest)["valid"]
        for name in SCHEMAS:
            assert (run / name).read_bytes() == (dest / name).read_bytes(), name
        c2 = campaign(
            tmp_path,
            output_directory=str(tmp_path / "run2"),
            campaign="second",
            max_firecrawl_credit_delta=10,
        )
        run2 = initialize(c2)
        discover(run2, reg, Maps([place()], []), sleeper=lambda _: None)
        fc2 = FC()
        state2 = enrich(
            run2,
            reg,
            fc2,
            budget_path=tmp_path / "account.json",
            sleeper=lambda _: None,
        )
        assert not fc2.calls and state2["research"]["alpha-signs.com"]["reused"]
    finally:
        reg.close()


def test_zero_budget_backlog_and_http_uses_same_evidence(tmp_path):
    c = campaign(tmp_path)
    run = initialize(c)
    reg = Registry(c["historical_registry_path"])
    try:
        discover(
            run,
            reg,
            Maps([place(), place("b", "beta-signs.com")]),
            sleeper=lambda _: None,
        )

        def http(url, root):
            return {
                "success": True,
                "final_url": url,
                "content": "<p>We provide sign company services.</p><p>Jane Carter — President</p>",
            }

        state = enrich(run, reg, None, http)
        assert all(r["status"] == "backlog" for r in state["research"].values())
        assert export(run, reg)["output_counts"]["firecrawl_backlog.csv"] == 2
        assert all(r["http_attempts"] == 1 for r in state["research"].values())
    finally:
        reg.close()


def test_cli_offline_and_redaction(tmp_path, monkeypatch, capsys):
    c = campaign(tmp_path)
    path = tmp_path / "campaign.json"
    write_json(path, c)
    assert main(["validate", str(path)]) == 0
    assert main(["plan", str(path)]) == 0
    assert main(["discover", str(path)]) == 2
    for cls in [RapidAPI, Firecrawl]:
        with pytest.raises(ValueError):
            cls(True)
    monkeypatch.setenv("FIRECRAWL_API_KEY", "fc-secret-123456")
    assert "fc-secret-123456" not in json.dumps(
        redact({"message": "Bearer fc-secret-123456", "api_key": "fc-secret-123456"})
    )
    assert main(["keys"]) == 0
    assert "fc-secret-123456" not in capsys.readouterr().out


def test_import_large_fields_sqlite_and_preserved_official_pages(tmp_path):
    import sqlite3

    source = tmp_path / "source"
    source.mkdir()
    with (source / "large.csv").open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["company_name", "website", "notes"])
        writer.writeheader()
        writer.writerow(
            {
                "company_name": "Signs",
                "website": "https://example.com",
                "notes": "x" * 150000,
            }
        )
    db = sqlite3.connect(source / "leads.db")
    db.execute("CREATE TABLE leads(business_name,place_id,website)")
    db.execute("INSERT INTO leads VALUES('Signs','place1','https://example.com')")
    db.commit()
    db.close()
    write_json(
        source / "page.json",
        {
            "status": "success",
            "requested_url": "https://example.com/team",
            "root_domain": "example.com",
            "timestamp": "2026-09-08",
            "markdown": "We provide commercial sign manufacturing services.\nJane Carter — President",
        },
    )
    reg = Registry(tmp_path / "r.sqlite")
    try:
        result = import_history(reg, [source])
        assert result["accepted_rows"] == 2 and not result["errors"]
        assert result["research_evidence"]["page_artifacts_imported"] == 1
        assert reg.evidence("example.com")["status"] == "complete"
        second = import_history(reg, [source])
        assert second["unchanged_artifacts"] == 2
        assert second["research_evidence"]["page_artifacts_unchanged"] == 1
        assert reg.rows("SELECT COUNT(*) AS n FROM companies")[0]["n"] == 1
    finally:
        reg.close()


def test_recover_maps_response_after_local_commit_crash(tmp_path):
    c = campaign(tmp_path, max_net_new_companies=1)
    run = initialize(c)
    reg = Registry(c["historical_registry_path"])
    try:
        discover(run, reg, Maps([place()]), sleeper=lambda _: None)
        receipt = next((run / "receipts/maps").glob("*.json"))
        data = read_json(receipt)
        data["status"] = "pending"
        write_json(receipt, data)
        provider = Maps()
        discover(run, reg, provider, sleeper=lambda _: None)
        assert not provider.calls and read_json(receipt)["status"] == "success"
        assert export(run, reg)["valid"]
    finally:
        reg.close()


def test_recover_account_receipt_after_raw_response_commit(tmp_path):
    c = campaign(tmp_path, max_firecrawl_credit_delta=2)
    account = tmp_path / "account.json"
    raw = tmp_path / "page.json"
    fc = FC()
    with AccountBudget(fc, "run", c, account) as b:
        b.scrape("https://example.com", raw)
    data = read_json(account)
    op = next(iter(data["runs"]["run"]["operations"].values()))
    op["status"] = "pending"
    write_json(account, data)
    with AccountBudget(fc, "run", c, account) as b:
        b.scrape("https://example.com", raw)
        assert b.used() == 1
    assert len(fc.calls) == 1


def test_cross_campaign_resume_keeps_domain_operation_ceiling(tmp_path):
    c = campaign(tmp_path, max_net_new_companies=1, max_firecrawl_credit_delta=1)
    run = initialize(c)
    reg = Registry(c["historical_registry_path"])
    account = tmp_path / "account.json"
    try:
        discover(run, reg, Maps([place()]), sleeper=lambda _: None)
        fc = FC()
        enrich(run, reg, fc, budget_path=account, sleeper=lambda _: None)
        assert len(fc.calls) == 1
        c2 = campaign(
            tmp_path,
            campaign="next",
            output_directory=str(tmp_path / "next"),
            max_firecrawl_credit_delta=3,
        )
        run2 = initialize(c2)
        fc2 = FC()
        state = enrich(
            run2, reg, fc2, backlog=True, budget_path=account, sleeper=lambda _: None
        )
        assert len(fc2.calls) == 1 and fc2.calls[0].endswith("/team")
        assert state["research"]["alpha-signs.com"]["firecrawl_attempts"] == 2
        assert export(run2, reg)["valid"]
    finally:
        reg.close()


def test_rebuild_without_exports_or_aggregate_state(tmp_path):
    c = campaign(tmp_path, max_net_new_companies=1, max_firecrawl_credit_delta=3)
    run = initialize(c)
    reg = Registry(c["historical_registry_path"])
    try:
        discover(run, reg, Maps([place()]), sleeper=lambda _: None)
        enrich(
            run,
            reg,
            FC(),
            budget_path=tmp_path / "account.json",
            sleeper=lambda _: None,
        )
        baseline = export(run, reg)
        expected = {name: (run / name).read_bytes() for name in SCHEMAS}
        # These are test-generated artifacts, not user data.
        (run / "state.json").unlink()
        for name in SCHEMAS:
            (run / name).unlink()
        dest = tmp_path / "rebuild"
        result = rebuild(run, dest)
        assert result["valid"] and result["output_counts"] == baseline["output_counts"]
        assert all(
            (dest / name).read_bytes() == data for name, data in expected.items()
        )
    finally:
        reg.close()


def test_exact_combined_title_phone_and_no_false_bot_completion(tmp_path):
    from maps_lead_research.pipeline import page_from_payload

    c = campaign(tmp_path)
    e = extract(
        "co1",
        "example.com",
        [
            {
                "url": "https://example.com/team",
                "timestamp": "2026-09-08",
                "markdown": "Jane Carter — President & CEO | (206) 555-0100",
            }
        ],
        c,
    )
    assert e["contacts"][0]["exact_title"] == "President & CEO"
    assert e["contacts"][0]["public_business_phone"] == "(206) 555-0100"
    assert (
        page_from_payload(
            {"data": {"markdown": "Access denied. Verify you are human."}},
            "https://example.com",
            "today",
            "raw.json",
        )
        is None
    )


def test_reconciliation_detects_dropped_raw_record(tmp_path):
    c = campaign(tmp_path, max_net_new_companies=1)
    run = initialize(c)
    reg = Registry(c["historical_registry_path"])
    try:
        discover(run, reg, Maps([place(), 42]), sleeper=lambda _: None)
        result = export(run, reg)
        assert result["observed_records"] == 2 and result["valid"]
        state = read_json(run / "state.json")
        state["observations"].pop()
        write_json(run / "state.json", state)
        assert "raw_observation_count_mismatch" in export(run, reg)["errors"]
    finally:
        reg.close()


def test_zero_balance_never_attempts_scrape(tmp_path):
    c = campaign(tmp_path, max_firecrawl_credit_delta=5, firecrawl_safety_reserve=0)
    fc = FC(0)
    with AccountBudget(fc, "zero", c, tmp_path / "account.json") as b:
        assert b.run["starting_balance"] == 0
        with pytest.raises(BudgetStop):
            b.scrape("https://example.com", tmp_path / "raw.json")
    assert not fc.calls


def test_free_public_business_email_and_profile_link(tmp_path):
    c = campaign(tmp_path)
    markup = '<div><a href="/team/jane">Jane Carter</a> — President <a href="https://www.linkedin.com/in/jane-carter">Profile</a></div><p>Contact our office: signsbusiness@gmail.com</p><footer>Designed by random@gmail.com</footer>'
    evidence = extract(
        "co1",
        "example.com",
        [
            {
                "url": "https://example.com/team",
                "html": markup,
                "timestamp": "2026-09-08",
            }
        ],
        c,
    )
    assert (
        evidence["contacts"][0]["professional_profile_url"]
        == "https://www.linkedin.com/in/jane-carter"
    )
    assert evidence["contacts"][0]["team_bio_url"] == "https://example.com/team/jane"
    assert {e["email"] for e in evidence["emails"]} == {"signsbusiness@gmail.com"}
    assert evidence["emails"][0]["status"] == "public_general_email"


def test_cli_batch_discovers_then_reuses_shared_research(tmp_path, monkeypatch, capsys):
    import maps_lead_research.__main__ as cli

    configs = [
        campaign(
            tmp_path,
            campaign=f"campaign-{i}",
            output_directory=str(tmp_path / f"run-{i}"),
            max_net_new_companies=1,
            max_firecrawl_credit_delta=5,
        )
        for i in range(2)
    ]
    files = []
    for i, c in enumerate(configs):
        path = tmp_path / f"campaign-{i}.json"
        write_json(path, c)
        files.append(str(path))
    maps = Maps([place()], [place()], [])
    fc = FC()
    events = []

    def factory_maps(_):
        events.append("discovery")
        return maps

    def factory_fc(_):
        events.append("enrichment")
        return fc

    monkeypatch.setattr(cli, "RapidAPI", factory_maps)
    monkeypatch.setattr(cli, "Firecrawl", factory_fc)

    def offline_discover(run, reg, provider):
        return discover(run, reg, provider, sleeper=lambda _: None)

    def offline_enrich(run, reg, client, http, backlog=False):
        return enrich(
            run,
            reg,
            client,
            http,
            backlog,
            budget_path=tmp_path / "account.json",
            sleeper=lambda _: None,
        )

    monkeypatch.setattr(cli, "discover", offline_discover)
    monkeypatch.setattr(cli, "enrich", offline_enrich)
    assert main(["batch", *files, "--allow-paid"]) == 0
    assert events == ["discovery", "discovery", "enrichment", "enrichment"]
    assert len(fc.calls) == 2
    assert read_json(tmp_path / "run-1/manifest.json")["net_new_companies"] == 0
    assert read_json(tmp_path / "run-1/state.json")["research"]["alpha-signs.com"][
        "reused"
    ]
    capsys.readouterr()


def test_firecrawl_rate_limit_and_balance_failure_recovery(tmp_path):
    c = campaign(
        tmp_path,
        max_net_new_companies=1,
        max_firecrawl_pages_per_domain=2,
        max_firecrawl_credit_delta=4,
    )
    run = initialize(c)
    reg = Registry(c["historical_registry_path"])
    try:
        discover(run, reg, Maps([place()]), sleeper=lambda _: None)
        fc = FC(results=[ProviderError(429)])
        state = enrich(
            run, reg, fc, budget_path=tmp_path / "account.json", sleeper=lambda _: None
        )
        assert (
            len(fc.calls) == 2
            and state["research"]["alpha-signs.com"]["firecrawl_attempts"] == 2
        )
        assert export(run, reg)["output_counts"]["firecrawl_backlog.csv"] == 1
        fresh = FC()
        enrich(
            run,
            reg,
            fresh,
            budget_path=tmp_path / "account.json",
            sleeper=lambda _: None,
        )
        assert not fresh.calls
    finally:
        reg.close()

    class FlakyBalance(FC):
        def __init__(self):
            super().__init__()
            self.balance_calls = 0

        def balance(self):
            self.balance_calls += 1
            if self.balance_calls == 3:
                raise BudgetStop("balance_unavailable")
            return self.remaining

    fc = FlakyBalance()
    account = tmp_path / "flaky.json"
    raw = tmp_path / "flaky-raw.json"
    with AccountBudget(fc, "flaky", c, account) as b:
        with pytest.raises(BudgetStop):
            b.scrape("https://example.com", raw)
    with AccountBudget(fc, "flaky", c, account) as b:
        b.scrape("https://example.com", raw)
    assert len(fc.calls) == 1


def test_preserved_fixture_end_to_end(tmp_path):
    fixtures = Path(__file__).parent / "fixtures/maps_research"
    c = campaign(
        tmp_path,
        business_category="commercial signs",
        max_net_new_companies=1,
        max_firecrawl_credit_delta=2,
    )
    run = initialize(c)
    reg = Registry(c["historical_registry_path"])
    try:
        discover(
            run,
            reg,
            Maps(read_json(fixtures / "maps.json")["data"]),
            sleeper=lambda _: None,
        )
        fc = FC(
            results=[
                read_json(fixtures / "homepage.json"),
                read_json(fixtures / "team.json"),
            ]
        )
        enrich(
            run, reg, fc, budget_path=tmp_path / "account.json", sleeper=lambda _: None
        )
        result = export(run, reg)
        assert result["valid"] and result["net_new_companies"] == 1
        assert result["output_counts"]["people_contacts.csv"] == 2
        with (run / "companies.csv").open() as f:
            row = next(csv.DictReader(f))
        assert row["source_relevance"] == "confirmed"
        assert (
            read_json(run / "manifest.json")["firecrawl_usage"]["ending_balance"] == 98
        )
    finally:
        reg.close()


def test_location_aliases_zero_values_and_zip_plus_four(tmp_path):
    c = campaign(
        tmp_path,
        max_net_new_companies=1,
        min_reviews=0,
        geography={"mode": "zip_codes", "targets": ["98101"]},
    )
    run = initialize(c)
    reg = Registry(c["historical_registry_path"])
    try:
        state = discover(
            run,
            reg,
            Maps(
                [
                    place(
                        state="Washington",
                        postal_code="98101-1234",
                        review_count=None,
                        reviews=0,
                        rating=0,
                        latitude=0,
                        longitude=0,
                    )
                ]
            ),
            sleeper=lambda _: None,
        )
        row = state["observations"][0]
        assert (
            row["disposition"] == "included"
            and row["state"] == "WA"
            and row["zip_code"] == "98101"
        )
        assert (
            row["latitude"] == 0
            and row["longitude"] == 0
            and row["rating"] == 0
            and row["review_count"] == "0"
        )
    finally:
        reg.close()


def test_backlog_obeys_requested_niche_and_source_filters(tmp_path):
    c = campaign(tmp_path, max_net_new_companies=1)
    reg = Registry(c["historical_registry_path"])
    try:
        for name, category, reviews in [
            ("Sign Company", "Sign shop", 10),
            ("Dental Clinic", "Dentist", 10),
            ("Low Review Signs", "Sign shop", 1),
        ]:
            reg.ingest(
                {
                    "company_name": name,
                    "category": category,
                    "root_domain": name.lower().replace(" ", "") + ".com",
                    "state": "WA",
                    "city": "Seattle",
                    "review_count": str(reviews),
                    "country": "US",
                },
                "historical",
            )
        reg.db.commit()
        run = initialize(c)
        state = enrich(run, reg, backlog=True)
        assert [job["domain"] for job in state["queue"]] == ["signcompany.com"]
    finally:
        reg.close()


def test_nationwide_expands_beyond_curated_metros_offline(tmp_path):
    c = campaign(tmp_path, geography={"mode": "nationwide"}, max_maps_requests=1000)
    targets = plan(c)["targets"]
    assert len(targets) >= 1000 and any(t["tier"] >= 5 for t in targets)
    assert len({t["value"].casefold() for t in targets}) == len(targets)
    assert len({t["state"] for t in targets[:51]}) == 51


def test_source_backed_parent_identity_and_cycle_rejection(tmp_path):
    reg = Registry(tmp_path / "r.sqlite")
    try:
        parent, _ = reg.ingest(
            {"company_name": "Parent", "root_domain": "parent-signs.com"}, "old"
        )
        child, _ = reg.ingest({"company_name": "Branch", "place_id": "branch"}, "new")
        with pytest.raises(ValueError):
            reg.relate(child, parent, "", "2026-09-08")
        reg.relate(child, parent, "https://parent-signs.com/locations", "2026-09-08")
        assert reg.match({"place_id": "branch"}) == (parent, "source_backed_parent")
        with pytest.raises(ValueError):
            reg.relate(parent, child, "https://parent-signs.com/about", "2026-09-08")
    finally:
        reg.close()


def test_explicit_international_city_plan(tmp_path):
    c = campaign(
        tmp_path,
        country="GB",
        geography={"mode": "cities", "targets": ["London, England"]},
    )
    assert plan(c)["targets"][0]["value"] == "London, England"
    assert plan(c)["targets"][0]["state"] == ""


def test_redaction_preserves_business_domain_keys():
    result = redact(
        {
            "research": {
                "keystone-signs.com": {"status": "complete"},
                "secretgarden.com": {"status": "complete"},
            },
            "X-RapidAPI-Key": "sensitive",
            "api_key": "sensitive",
        }
    )
    assert result["research"]["keystone-signs.com"]["status"] == "complete"
    assert result["research"]["secretgarden.com"]["status"] == "complete"
    assert result["X-RapidAPI-Key"] == result["api_key"] == "[REDACTED]"


def test_receipts_freeze_cross_campaign_history_and_company_metadata(tmp_path):
    c = campaign(tmp_path, max_net_new_companies=1)
    run = initialize(c)
    reg = Registry(c["historical_registry_path"])
    try:
        # Another campaign can finish while this initialized campaign is paused.
        reg.ingest(
            {
                "company_name": "Known later",
                "root_domain": "alpha-signs.com",
                "timestamp": "2026-01-01",
            },
            "other-campaign",
        )
        reg.db.commit()
        discover(run, reg, Maps([place()], []), sleeper=lambda _: None)
        result = export(run, reg)
        assert result["net_new_companies"] == 0
        dest = tmp_path / "rebuilt"
        rebuilt = rebuild(run, dest)
        assert rebuilt["net_new_companies"] == 0
        assert (run / "already_found.csv").read_bytes() == (
            dest / "already_found.csv"
        ).read_bytes()
    finally:
        reg.close()
    other = tmp_path / "fresh"
    other.mkdir()
    c = campaign(other, max_net_new_companies=1)
    run = initialize(c)
    reg = Registry(c["historical_registry_path"])
    try:
        discover(run, reg, Maps([place()]), sleeper=lambda _: None)
        export(run, reg)
        before = (run / "companies.csv").read_bytes()
        reg.ingest(
            {
                "place_id": "a",
                "company_name": "Changed later",
                "root_domain": "alpha-signs.com",
                "phone": "+12065550101",
                "timestamp": "2099-01-01",
            },
            "later",
        )
        reg.db.commit()
        export(run, reg)
        assert (run / "companies.csv").read_bytes() == before
        rebuilt = other / "rebuilt"
        rebuild(run, rebuilt)
        assert (rebuilt / "companies.csv").read_bytes() == before
    finally:
        reg.close()


def test_email_cap_applies_to_contact_export_fields_too(tmp_path):
    c = campaign(tmp_path, max_contacts_per_company=2, max_emails_per_company=1)
    evidence = extract(
        "co1",
        "example.com",
        [
            {
                "url": "https://example.com/team",
                "timestamp": "2026-09-08",
                "markdown": "Jane Carter — President\njane.carter@example.com\nAlan Wilson — General Manager\nalan.wilson@example.com",
            }
        ],
        c,
    )
    assert len(evidence["emails"]) == 1
    assert evidence["emails"][0]["email"] == "jane.carter@example.com"
    assert [p["public_work_email"] for p in evidence["contacts"]] == [
        "jane.carter@example.com",
        "",
    ]


def test_source_geography_and_broad_property_management_are_objective(tmp_path):
    from maps_lead_research.pipeline import objective_reason

    c = campaign(tmp_path, business_category="property management")
    row = {
        "company_name": "Rental Managers",
        "state": "WA",
        "city": "Seattle",
        "review_count": "10",
        "provider_country": "CA",
    }
    assert objective_reason(row, c) == "outside_requested_country"
    row["provider_country"] = "US"
    assert not objective_reason(row, c)
    e = extract(
        "co1",
        "example.com",
        [
            {
                "url": "https://example.com",
                "timestamp": "2026-09-08",
                "markdown": "We provide property management services for single-family rental homes.",
            }
        ],
        c,
    )
    assert e["source_relevance"] == "confirmed" and "single_family" in e["service_mix"]
