import csv
import json
from pathlib import Path

from maps_lead_research.common import read_json, write_json
from maps_lead_research.input_enrichment import (
    INPUT_FIELDS,
    initialize_input_successor,
    input_plan,
    reconcile_not_sent,
    run_input_enrichment,
)
from maps_lead_research.input_finalization import (
    EXTERNAL_REQUIRED_FIELDS,
    finalize_input_results,
)
from maps_lead_research.providers import ProviderError


ROLES = [
    "owner",
    "founder",
    "president",
    "ceo",
    "principal",
    "general_manager",
    "regional_manager",
    "operations",
    "sales",
]


def source(path, rows):
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=INPUT_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def row(rid, niche, name, website, hint=""):
    value = {field: "" for field in INPUT_FIELDS}
    value.update(
        research_id=rid,
        niche=niche,
        company_name=name,
        website=website,
        root_domain=website.split("//")[-1].strip("/"),
        city="Austin",
        state="TX",
        known_decision_makers=hint,
    )
    return value


class Firecrawl:
    def __init__(self):
        self.credits = 100
        self.search_calls = []
        self.scrape_calls = []

    def balance(self):
        return self.credits

    def search(self, query, limit=10):
        self.search_calls.append((query, limit))
        self.credits -= 2
        host = "alpha.com" if "Alpha" in query else "beta.com"
        return {
            "success": True,
            "creditsUsed": 2,
            "data": {
                "web": [
                    {
                        "url": "https://" + host + "/about",
                        "title": "Leadership",
                        "description": "Meet our owner",
                    }
                ]
            },
        }

    def scrape(self, url):
        self.scrape_calls.append(url)
        self.credits -= 1
        return {"success": True, "creditsUsed": 1, "data": {"markdown": "x" * 1200}}


def campaign(tmp_path):
    inputs = []
    definitions = [
        ("equipment", "equipment.csv", "equipment_enriched.csv"),
        ("fire", "fire.csv", "fire_enriched.csv"),
        ("property", "property.csv", "property_enriched.csv"),
    ]
    source(tmp_path / "equipment.csv", [row("a", "equipment", "Alpha Tools", "https://alpha.com/")])
    source(tmp_path / "fire.csv", [row("b", "fire", "Alpha Fire", "https://alpha.com/")])
    source(tmp_path / "property.csv", [row("c", "property", "Beta Homes", "https://beta.com/")])
    for niche, filename, output in definitions:
        inputs.append(
            {
                "path": str(tmp_path / filename),
                "niche": niche,
                "output_filename": output,
                "decision_maker_roles": ROLES,
            }
        )
    return {
        "version": 1,
        "campaign": "input-test",
        "inputs": inputs,
        "output_directory": str(tmp_path / "run"),
        "historical_registry_path": str(tmp_path / "registry.sqlite"),
        "max_firecrawl_credit_delta": 20,
        "firecrawl_safety_reserve": 10,
        "search_result_limit": 10,
        "max_firecrawl_searches_per_domain": 1,
        "max_firecrawl_scrapes_per_domain": 1,
        "max_http_pages_per_domain": 1,
        "reconcile_runs": [],
    }


def test_exact_input_enrichment_researches_shared_domain_once(tmp_path):
    c = campaign(tmp_path)
    assert input_plan(c)["unique_domains"] == 2
    Path(c["output_directory"]).mkdir()
    (Path(c["output_directory"]) / ".run.lock").touch()
    client = Firecrawl()

    def fetch(url, root):
        name = "Alice Smith" if root == "alpha.com" else "Bob Jones"
        return {
            "success": True,
            "final_url": url,
            "content": f"<html><body><section>{name} — Owner</section></body></html>",
        }

    result = run_input_enrichment(
        c,
        allow_paid=True,
        allow_http=True,
        client=client,
        http_fetcher=fetch,
        budget_path=tmp_path / "account.json",
        sleeper=lambda _: None,
    )
    assert result["valid"] and result["maps_requests"] == 0
    assert len(client.search_calls) == 2
    assert not client.scrape_calls
    for spec in c["inputs"]:
        with (tmp_path / "run" / spec["output_filename"]).open(newline="") as f:
            rows = list(csv.DictReader(f))
        assert len(rows) == 1
        assert rows[0]["primary_evidence_status"] == "official_source_backed"
        assert rows[0]["primary_decision_maker_name"]


def test_reconcile_classifies_only_confirmed_local_placeholder(tmp_path):
    old = tmp_path / "old"
    (old / "receipts" / "research").mkdir(parents=True)
    state = {
        "queue": [{"domain": "example.com"}],
        "research": {
            "example.com": {
                "firecrawl_attempts": 1,
                "attempts": [
                    {
                        "provider": "firecrawl",
                        "status": "pending",
                        "raw_path": "raw/firecrawl/example.json",
                    }
                ],
            }
        },
    }
    write_json(old / "state.json", state)
    write_json(old / "manifest.json", {})
    account = tmp_path / "account.json"
    write_json(account, {"version": 1, "runs": {}})
    c = campaign(tmp_path)
    c["reconcile_runs"] = [str(old)]
    reconciled = reconcile_not_sent(c, account)
    assert len(reconciled) == 1
    updated = read_json(old / "state.json")["research"]["example.com"]
    assert updated["attempts"][0]["status"] == "not_sent"
    assert updated["firecrawl_attempts"] == 0


def test_provider_rejection_is_zero_credit_and_retry_uses_new_receipt(tmp_path):
    c = campaign(tmp_path)
    c["max_firecrawl_scrapes_per_domain"] = 0
    client = Firecrawl()
    original_search = client.search
    attempts = {}

    def rate_limited_once(query, limit=10):
        attempts[query] = attempts.get(query, 0) + 1
        if "Alpha" in query and attempts[query] == 1:
            raise ProviderError(429)
        return original_search(query, limit)

    client.search = rate_limited_once
    account = tmp_path / "account.json"
    run_input_enrichment(
        c,
        allow_paid=True,
        client=client,
        budget_path=account,
        sleeper=lambda _: None,
        search_delay=0,
    )
    run_input_enrichment(
        c,
        allow_paid=True,
        client=client,
        budget_path=account,
        sleeper=lambda _: None,
        search_delay=0,
    )
    alpha_query = next(query for query in attempts if "Alpha" in query)
    assert attempts[alpha_query] == 2
    ledger = next(iter(read_json(account)["runs"].values()))
    failed = [op for op in ledger["operations"].values() if op["status"] == "failed"]
    assert len(failed) == 1 and failed[0]["accounted_credits"] == 0
    assert not [op for op in ledger["operations"].values() if op["status"] == "pending"]
    assert len(list((Path(c["output_directory"]) / "raw" / "firecrawl_search").glob("*.json"))) == 3


def test_successor_inherits_completed_state_without_mutating_source(tmp_path):
    c = campaign(tmp_path)
    c["max_firecrawl_scrapes_per_domain"] = 0
    run_input_enrichment(
        c,
        allow_paid=True,
        client=Firecrawl(),
        budget_path=tmp_path / "account.json",
        sleeper=lambda _: None,
        search_delay=0,
    )
    source_state = read_json(Path(c["output_directory"]) / "state.json")
    successor = initialize_input_successor(
        c["output_directory"], tmp_path / "continuation", 4
    )
    continued_state = read_json(Path(successor["run_dir"]) / "state.json")
    assert continued_state["research"] == source_state["research"]
    assert continued_state["continued_from_run"] == str(Path(c["output_directory"]).resolve())
    manifest = read_json(Path(successor["run_dir"]) / "manifest.json")
    assert manifest["continued_from_run_id"]
    assert successor["campaign"]["max_firecrawl_credit_delta"] == 4
    assert read_json(Path(c["output_directory"]) / "state.json") == source_state


def test_search_only_skips_http_and_scrape_and_selects_named_snippet_email(tmp_path):
    c = campaign(tmp_path)
    client = Firecrawl()

    def search(query, limit=10):
        client.search_calls.append((query, limit))
        client.credits -= 2
        if "Alpha" in query:
            root, company, person = "alpha.com", "Alpha Tools", "Alice Smith"
        else:
            root, company, person = "beta.com", "Beta Homes", "Bob Jones"
        return {
            "success": True,
            "creditsUsed": 2,
            "data": {
                "web": [
                    {
                        "url": f"https://www.linkedin.com/in/{person.lower().replace(' ', '-')}",
                        "title": f"{person} - Owner at {company}",
                        "description": f"{person} is Owner at {company}",
                    },
                    {
                        "url": f"https://{root}/team",
                        "title": f"{person} - Owner",
                        "description": f"Contact {person} at {person.split()[0].lower()}@{root}",
                    },
                ]
            },
        }

    client.search = search
    result = run_input_enrichment(
        c,
        allow_paid=True,
        allow_http=False,
        allow_scrape=False,
        client=client,
        budget_path=tmp_path / "account.json",
        sleeper=lambda _: None,
        search_delay=0,
    )
    assert result["valid"]
    assert not client.scrape_calls
    assert read_json(Path(c["output_directory"]) / "manifest.json")[
        "last_execution_usage"
    ]["ending_http_requests"] == 0
    with (Path(c["output_directory"]) / "equipment_enriched.csv").open(
        newline=""
    ) as stream:
        result_row = next(csv.DictReader(stream))
    assert result_row["primary_decision_maker_name"] == "Alice Smith"
    assert result_row["primary_selected_email"] == "alice@alpha.com"
    assert result_row["primary_email_provenance"].startswith(
        "firecrawl_search_public_person_email"
    )


def _external_csv(path, rows):
    fields = sorted(EXTERNAL_REQUIRED_FIELDS)
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for values in rows:
            row_value = {field: "" for field in fields}
            row_value.update(values)
            writer.writerow(row_value)


def test_finalize_external_results_prefers_official_and_holds_equal_conflict(tmp_path):
    c = campaign(tmp_path)
    c["max_firecrawl_scrapes_per_domain"] = 0
    run_input_enrichment(
        c,
        allow_paid=True,
        allow_scrape=False,
        client=Firecrawl(),
        budget_path=tmp_path / "account.json",
        sleeper=lambda _: None,
        search_delay=0,
    )
    common = [
        {
            "research_key": "alpha.com",
            "root_domain": "alpha.com",
            "research_status": "completed",
            "primary_decision_maker_name": "Alice Smith",
            "primary_decision_maker_title": "Owner",
            "primary_role_category": "owner",
            "primary_evidence_status": "official_source_backed",
            "primary_evidence_url": "https://alpha.com/team",
            "primary_named_work_email": "alice@alpha.com",
            "primary_email_provenance": "public_person_email",
            "primary_email_evidence_url": "https://alpha.com/team",
        },
        {
            "research_key": "beta.com",
            "root_domain": "beta.com",
            "research_status": "completed",
            "primary_decision_maker_name": "Bob Jones",
            "primary_decision_maker_title": "Owner",
            "primary_role_category": "owner",
            "primary_evidence_status": "official_source_backed",
            "primary_evidence_url": "https://beta.com/about",
        },
    ]
    perplexity = tmp_path / "perplexity.csv"
    supergrok = tmp_path / "supergrok.csv"
    _external_csv(perplexity, common)
    _external_csv(
        supergrok,
        [
            common[0],
            {
                **common[1],
                "primary_decision_maker_name": "Carol Adams",
                "primary_evidence_url": "https://beta.com/team",
            },
        ],
    )
    output = tmp_path / "final"
    result = finalize_input_results(
        c["output_directory"], perplexity, supergrok, output
    )
    assert result["valid"]
    with (output / "equipment_enriched.csv").open(newline="") as stream:
        alpha = next(csv.DictReader(stream))
    assert alpha["primary_decision_maker_name"] == "Alice Smith"
    assert alpha["primary_selected_email"] == "alice@alpha.com"
    with (output / "property_enriched.csv").open(newline="") as stream:
        beta = next(csv.DictReader(stream))
    assert not beta["primary_decision_maker_name"]
    assert "conflict_held_unresolved" in beta["external_qa_flags"]
