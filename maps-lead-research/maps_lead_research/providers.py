"""Paid transports and persistent single-owner provider accounting.

No transport is constructed by offline commands. Firecrawl only uses basic proxy,
HTML/Markdown and no parsers/actions/JSON extraction: one reserved credit/request.
"""

import os
import socket
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests

from .common import lock, now, read_json, stable_id, write_json

ACCOUNT_STATE = (
    Path(__file__).resolve().parents[1] / ".state" / "firecrawl-account.json"
)


class ProviderError(RuntimeError):
    def __init__(self, status=None, error=None):
        self.status = status
        # Retain only known categories, never exception text or provider bodies.
        allowed = {"non_json_response"} | {
            "network_error:" + name
            for name in (
                "ConnectionError",
                "ConnectTimeout",
                "ReadTimeout",
                "Timeout",
                "ProxyError",
                "SSLError",
                "RequestException",
            )
        }
        self.error = error if error in allowed else None
        super().__init__(
            "provider_http_" + str(status) if status else "provider_outcome_unknown"
        )


class BudgetStop(RuntimeError):
    pass


def credentials():
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=False)
    return {k: bool(os.getenv(k)) for k in ["RAPIDAPI_KEY", "FIRECRAWL_API_KEY"]}


def require_live(allow_paid):
    if not allow_paid or os.getenv("MAPS_RESEARCH_OFFLINE") == "1":
        raise ValueError(
            "Paid provider execution requires --allow-paid and a non-test environment"
        )
    credentials()


class RapidAPI:
    def __init__(self, allow_paid=False):
        require_live(allow_paid)
        if not os.getenv("RAPIDAPI_KEY"):
            raise ValueError("RAPIDAPI_KEY is missing")
        from config import RAPIDAPI_HOST

        try:
            socket.getaddrinfo(RAPIDAPI_HOST, 443, type=socket.SOCK_STREAM)
        except OSError:
            raise ValueError(
                "Maps network preflight failed: hostname resolution unavailable. "
                "Check sandbox network access before execution; no Maps request sent."
            ) from None

    def page(self, query, page, country, language):
        from lead_scraper_core.maps import request_maps_page

        result = request_maps_page(
            query, page, country=country.lower(), language=language, max_attempts=1
        )
        if not result.success:
            raise ProviderError(result.http_status, result.error)
        return result.payload


class Firecrawl:
    def __init__(self, allow_paid=False):
        require_live(allow_paid)
        if not os.getenv("FIRECRAWL_API_KEY"):
            raise ValueError("FIRECRAWL_API_KEY is missing")

    def _call(self, method, endpoint, **kwargs):
        try:
            response = requests.request(
                method,
                "https://api.firecrawl.dev/v2/" + endpoint,
                headers={"Authorization": "Bearer " + os.environ["FIRECRAWL_API_KEY"]},
                timeout=45,
                **kwargs,
            )
        except requests.RequestException:
            raise ProviderError() from None
        if response.status_code != 200:
            raise ProviderError(response.status_code)
        try:
            data = response.json()
        except ValueError:
            raise ProviderError() from None
        if data.get("success") is False:
            raise ProviderError() from None
        return data

    def balance(self):
        data = self._call("GET", "team/credit-usage").get("data", {})
        value = data.get("remainingCredits")
        if type(value) not in (int, float) or value < 0:
            raise BudgetStop("Live account balance unavailable")
        return int(value)

    def scrape(self, url):
        from lead_scraper_core.web_enrichment import validate_public_url

        validate_public_url(url)
        return self._call(
            "POST",
            "scrape",
            json={
                "url": url,
                "formats": ["markdown", "html"],
                "proxy": "basic",
                "parsers": [],
                "onlyMainContent": False,
                "timeout": 30000,
            },
        )

    def search(self, query, limit=10):
        """Return unscripted web-search results without scraping result pages."""
        if not isinstance(query, str) or not query.strip():
            raise ValueError("Firecrawl search query is required")
        if type(limit) is not int or not 1 <= limit <= 10:
            raise ValueError("Firecrawl search limit must be 1..10")
        return self._call(
            "POST",
            "search",
            json={
                "query": query,
                "limit": limit,
                "sources": ["web"],
                "country": "US",
                "timeout": 60000,
                "ignoreInvalidURLs": True,
            },
        )


class AccountBudget:
    """Caller holds account lock across the research queue; state survives all runs.

    Independent old scripts/hosts cannot participate in this local lock. Live
    delta polling also detects their spend, but cannot prevent their execution.
    """

    def __init__(self, client, run_id, config, path=ACCOUNT_STATE, sleeper=time.sleep):
        self.client = client
        self.run_id = run_id
        self.config = config
        self.path = Path(path)
        self.sleeper = sleeper

    def __enter__(self):
        self.guard = lock(str(self.path) + ".lock")
        self.guard.__enter__()
        try:
            self.state = read_json(self.path, {"version": 1, "runs": {}})
            # Recover local response commits after a crash; never resend an uncertain scrape.
            for r in self.state["runs"].values():
                for op in r["operations"].values():
                    if op["status"] == "pending":
                        raw = read_json(op["raw_path"], {})
                        if raw.get("success"):
                            op["status"] = "received"
                            payload = raw.get("payload", {})
                            reported = payload.get("creditsUsed") or (
                                payload.get("data") or {}
                            ).get("creditsUsed")
                            op["provider_reported_credits"] = reported
                            reserved = int(op.get("reserved_credits", 1))
                            op["accounted_credits"] = max(
                                reserved, int(reported or reserved)
                            )
                        elif raw.get("http_status") is not None:
                            op["status"] = "failed"
            self.save()
            # Never spend around an unresolved operation, even from a different campaign.
            if any(
                op["status"] == "pending"
                for r in self.state["runs"].values()
                for op in r["operations"].values()
            ):
                raise BudgetStop(
                    "Uncertain Firecrawl operation; reconcile preserved receipt before further spending"
                )
            balance = self.client.balance()
            self.run = self.state["runs"].setdefault(
                self.run_id,
                {
                    "starting_balance": balance,
                    "ending_balance": balance,
                    "operations": {},
                    "started_at": now(),
                },
            )
            self.run["ending_balance"] = balance
            # A provider-confirmed non-200 response has no successful payload.
            # Keep its receipt, but do not consume the campaign's credit delta;
            # the live balance remains the backstop if a provider ever bills one.
            for op in self.run["operations"].values():
                raw = read_json(op.get("raw_path", ""), {})
                if (
                    op.get("status") == "failed"
                    and op.get("http_status") is not None
                    and not raw.get("success")
                ):
                    op["accounted_credits"] = 0
                    op["billing_reconciliation"] = "provider_confirmed_non_200"
            self.save()
            return self
        except BaseException:
            self.guard.__exit__(None, None, None)
            raise

    def __exit__(self, *exc):
        self.guard.__exit__(*exc)

    def save(self):
        write_json(self.path, self.state)

    def used(self):
        # Sum reservations prevents eventually-consistent balance reporting/replenishment from reopening budget.
        return max(
            sum(
                op.get("accounted_credits", 1) for op in self.run["operations"].values()
            ),
            self.run["starting_balance"] - self.run["ending_balance"],
        )

    def scrape(self, url, raw_path):
        rid = stable_id("fc", self.run_id, url, str(raw_path))
        prior = self.run["operations"].get(rid)
        if prior:
            if prior["status"] in ["success", "received"] and Path(raw_path).exists():
                return read_json(raw_path)["payload"]
            raise BudgetStop(
                "Operation already attempted; use a new bounded retry receipt"
            )
        balance = self.client.balance()
        self.run["ending_balance"] = balance
        self.save()
        if self.used() + 1 > self.config["max_firecrawl_credit_delta"]:
            raise BudgetStop("firecrawl_credit_delta")
        if balance - 1 < self.config["firecrawl_safety_reserve"]:
            raise BudgetStop("firecrawl_reserve")
        op = {
            "id": rid,
            "url": url,
            "raw_path": str(Path(raw_path).resolve()),
            "status": "pending",
            "requested_at": now(),
            "accounted_credits": 1,
            "reserved_credits": 1,
            "operation_type": "scrape",
            "balance_before": balance,
        }
        self.run["operations"][rid] = op
        self.save()
        try:
            payload = self.client.scrape(url)
        except ProviderError as exc:
            # No automatic retry for network timeouts: provider may have billed/completed.
            op["status"] = "failed" if exc.status is not None else "pending"
            op["http_status"] = exc.status
            write_json(
                raw_path,
                {
                    "url": url,
                    "success": False,
                    "http_status": exc.status,
                    "requested_at": op["requested_at"],
                },
            )
            self.save()
            try:
                self.run["ending_balance"] = self.client.balance()
                self.save()
            except (ProviderError, BudgetStop):
                pass
            raise
        write_json(
            raw_path,
            {
                "url": url,
                "success": True,
                "payload": payload,
                "requested_at": op["requested_at"],
            },
        )
        reported = payload.get("creditsUsed") or (payload.get("data") or {}).get(
            "creditsUsed"
        )
        op.update(
            status="received",
            provider_reported_credits=reported,
            accounted_credits=max(1, int(reported or 1)),
        )
        self.save()
        try:
            self.run["ending_balance"] = self.client.balance()
        except (ProviderError, BudgetStop):
            raise BudgetStop(
                "Balance unavailable after request; raw evidence saved"
            ) from None
        op.update(status="success", balance_after=self.run["ending_balance"])
        self.save()
        if (
            self.used() > self.config["max_firecrawl_credit_delta"]
            or self.run["ending_balance"] < self.config["firecrawl_safety_reserve"]
        ):
            raise BudgetStop(
                "Provider balance crossed configured limit; inspect account ledger"
            )
        return payload

    def search(self, query, raw_path, limit=10):
        """Account for one Firecrawl Search request (up to ten results = 2 credits)."""
        reserved = 2
        rid = stable_id("fc_search", self.run_id, query, str(raw_path))
        prior = self.run["operations"].get(rid)
        if prior:
            if prior["status"] in ["success", "received"] and Path(raw_path).exists():
                return read_json(raw_path)["payload"]
            raise BudgetStop(
                "Operation already attempted; use a new bounded retry receipt"
            )
        balance = self.client.balance()
        self.run["ending_balance"] = balance
        self.save()
        if self.used() + reserved > self.config["max_firecrawl_credit_delta"]:
            raise BudgetStop("firecrawl_credit_delta")
        if balance - reserved < self.config["firecrawl_safety_reserve"]:
            raise BudgetStop("firecrawl_reserve")
        op = {
            "id": rid,
            "query": query,
            "raw_path": str(Path(raw_path).resolve()),
            "status": "pending",
            "requested_at": now(),
            "accounted_credits": reserved,
            "reserved_credits": reserved,
            "operation_type": "search",
            "balance_before": balance,
        }
        self.run["operations"][rid] = op
        self.save()
        try:
            payload = self.client.search(query, limit=limit)
        except ProviderError as exc:
            op["status"] = "failed" if exc.status is not None else "pending"
            op["http_status"] = exc.status
            write_json(
                raw_path,
                {
                    "query": query,
                    "success": False,
                    "http_status": exc.status,
                    "requested_at": op["requested_at"],
                },
            )
            self.save()
            try:
                self.run["ending_balance"] = self.client.balance()
                self.save()
            except (ProviderError, BudgetStop):
                pass
            raise
        write_json(
            raw_path,
            {
                "query": query,
                "success": True,
                "payload": payload,
                "requested_at": op["requested_at"],
            },
        )
        reported = payload.get("creditsUsed") or (payload.get("data") or {}).get(
            "creditsUsed"
        )
        op.update(
            status="received",
            provider_reported_credits=reported,
            accounted_credits=max(reserved, int(reported or reserved)),
        )
        self.save()
        try:
            self.run["ending_balance"] = self.client.balance()
        except (ProviderError, BudgetStop):
            raise BudgetStop(
                "Balance unavailable after request; raw evidence saved"
            ) from None
        op.update(status="success", balance_after=self.run["ending_balance"])
        self.save()
        if (
            self.used() > self.config["max_firecrawl_credit_delta"]
            or self.run["ending_balance"] < self.config["firecrawl_safety_reserve"]
        ):
            raise BudgetStop(
                "Provider balance crossed configured limit; inspect account ledger"
            )
        return payload

    def search_batch(self, requests_to_send, limit=10, max_workers=4):
        """Run a small search batch concurrently with durable serial accounting.

        Every operation is reserved and committed to the account ledger before
        any provider request is sent. Raw responses are then written by each
        worker before the shared ledger is finalized. This keeps crash recovery
        conservative while avoiding three serial network round trips per query.
        """
        if type(max_workers) is not int or not 1 <= max_workers <= 8:
            raise ValueError("max_workers must be 1..8")
        if not requests_to_send:
            return [], ""
        reserved = 2
        # __enter__ and the preceding batch's completion already established a
        # live balance. Avoid a redundant balance round trip before every call.
        balance = self.run["ending_balance"]
        outcomes = []
        queued = []
        stop = ""
        for request in requests_to_send:
            query = request["query"]
            raw_path = Path(request["raw_path"])
            rid = stable_id("fc_search", self.run_id, query, str(raw_path))
            prior = self.run["operations"].get(rid)
            if prior:
                if prior["status"] in ["success", "received"] and raw_path.exists():
                    outcomes.append(
                        {"request": request, "payload": read_json(raw_path)["payload"]}
                    )
                    continue
                stop = "Operation already attempted; use a new bounded retry receipt"
                break
            if self.used() + reserved > self.config["max_firecrawl_credit_delta"]:
                stop = "firecrawl_credit_delta"
                break
            pending_reserved = sum(
                int(op.get("reserved_credits", op.get("accounted_credits", 1)))
                for op in self.run["operations"].values()
                if op.get("status") == "pending"
            )
            if balance - pending_reserved - reserved < self.config["firecrawl_safety_reserve"]:
                stop = "firecrawl_reserve"
                break
            op = {
                "id": rid,
                "query": query,
                "raw_path": str(raw_path.resolve()),
                "status": "pending",
                "requested_at": now(),
                "accounted_credits": reserved,
                "reserved_credits": reserved,
                "operation_type": "search",
                "balance_before": balance,
            }
            self.run["operations"][rid] = op
            queued.append((request, op, raw_path))
        # One durable ledger commit covers the whole bounded batch before send.
        self.save()

        def send(item):
            request, op, raw_path = item
            try:
                payload = self.client.search(request["query"], limit=limit)
            except ProviderError as exc:
                write_json(
                    raw_path,
                    {
                        "query": request["query"],
                        "success": False,
                        "http_status": exc.status,
                        "requested_at": op["requested_at"],
                    },
                )
                return {"request": request, "error_status": exc.status}
            write_json(
                raw_path,
                {
                    "query": request["query"],
                    "success": True,
                    "payload": payload,
                    "requested_at": op["requested_at"],
                },
            )
            return {"request": request, "payload": payload}

        if queued:
            with ThreadPoolExecutor(max_workers=min(max_workers, len(queued))) as pool:
                sent = list(pool.map(send, queued))
            for outcome, (_, op, _) in zip(sent, queued):
                if "payload" in outcome:
                    payload = outcome["payload"]
                    reported = payload.get("creditsUsed") or (
                        payload.get("data") or {}
                    ).get("creditsUsed")
                    op.update(
                        status="received",
                        provider_reported_credits=reported,
                        accounted_credits=max(reserved, int(reported or reserved)),
                    )
                else:
                    status = outcome.get("error_status")
                    op["status"] = "failed" if status is not None else "pending"
                    op["http_status"] = status
                    if status is not None:
                        op["accounted_credits"] = 0
                        op["billing_reconciliation"] = "provider_confirmed_non_200"
                outcomes.append(outcome)
            self.save()
            try:
                self.run["ending_balance"] = self.client.balance()
            except (ProviderError, BudgetStop):
                return outcomes, stop or "Balance unavailable after request; raw evidence saved"
            for _, op, _ in queued:
                if op["status"] == "received":
                    op.update(status="success", balance_after=self.run["ending_balance"])
            self.save()
            if any(op["status"] == "pending" for _, op, _ in queued):
                stop = stop or "uncertain_provider_outcome"
            if (
                self.used() > self.config["max_firecrawl_credit_delta"]
                or self.run["ending_balance"] < self.config["firecrawl_safety_reserve"]
            ):
                stop = stop or "Provider balance crossed configured limit; inspect account ledger"
        return outcomes, stop
