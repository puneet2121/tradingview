"""Bounded SEC submissions/XML ingestion. These are disclosures, never live trades."""
from datetime import date
import re
import time
from urllib.parse import urlparse
import xml.etree.ElementTree as ET

import requests
from django.db import transaction
from django.utils import timezone

from .config import options
from .models import InstitutionalHolding, InsiderTransaction, SecFiling
from .providers import BudgetExhausted, ProviderError, decimal, timestamp


def xml_document(raw):
    if len(raw) > 4_000_000 or b"\x00" in raw or b"<!DOCTYPE" in raw.upper() or b"<!ENTITY" in raw.upper():
        raise ProviderError("SEC XML is too large or contains unsupported declarations.")
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        raise ProviderError("SEC returned invalid XML.") from None
    for element in root.iter():
        if isinstance(element.tag, str):
            element.tag = element.tag.rsplit("}", 1)[-1]
    return root


def field(root, path, default=""):
    element = root.find(path)
    return element.text.strip() if element is not None and element.text else default


def parse_insiders(root):
    ticker = field(root, "issuer/issuerTradingSymbol").upper()
    if not re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,14}", ticker):
        raise ProviderError("SEC filing has no supported issuer ticker.")
    names = [field(element, "reportingOwnerId/rptOwnerName") for element in root.findall("reportingOwner")]
    output = []
    for tag, derivative in [("nonDerivativeTransaction", False), ("derivativeTransaction", True)]:
        for item in root.iter(tag):
            output.append({"ticker": ticker, "owner_name": "; ".join(names)[:200], "derivative": derivative,
                "transaction_date": date.fromisoformat(field(item, "transactionDate/value")),
                "code": field(item, "transactionCoding/transactionCode")[:4], "security": field(item, "securityTitle/value")[:200],
                "quantity": decimal(field(item, "transactionAmounts/transactionShares/value") or None, nullable=True),
                "price": decimal(field(item, "transactionAmounts/transactionPricePerShare/value") or None, nullable=True),
                "line_number": len(output) + 1})
    return output


def parse_holdings(root):
    output = []
    for item in root.iter("infoTable"):
        output.append({"issuer": field(item, "nameOfIssuer")[:200], "cusip": field(item, "cusip")[:12],
            "security_class": field(item, "titleOfClass")[:100], "put_call": field(item, "putCall")[:8],
            "shares_type": field(item, "shrsOrPrnAmt/sshPrnamtType")[:8],
            "shares": decimal(field(item, "shrsOrPrnAmt/sshPrnamt")), "reported_value": decimal(field(item, "value")),
            "value_unit": "As reported", "line_number": len(output) + 1})
    return output


class SecClient:
    def __init__(self, guard=lambda: None, budget=12):
        self.guard, self.remaining, self.last_request = guard, budget, 0

    def get(self, url, *, json=False):
        user_agent = options().get("sec_user_agent", "")
        if not user_agent or "@" not in user_agent or "\n" in user_agent or "\r" in user_agent:
            raise ProviderError("SEC_USER_AGENT requires an identifying name and contact email.")
        parts = urlparse(url)
        if parts.scheme != "https" or parts.netloc not in {"data.sec.gov", "www.sec.gov"} or ".." in parts.path:
            raise ProviderError("Unsupported SEC endpoint.")
        self.guard()
        if self.remaining <= 0:
            raise BudgetExhausted
        self.remaining -= 1
        time.sleep(max(0, 0.25 - (time.monotonic() - self.last_request)))
        self.last_request = time.monotonic()
        try:
            with requests.get(url, headers={"User-Agent": user_agent, "Accept": "application/json" if json else "application/xml"},
                              timeout=(5, 15), allow_redirects=False, stream=True) as response:
                if response.status_code != 200:
                    raise ProviderError(f"SEC unavailable (HTTP {response.status_code}); retry deferred.")
                parts, length = [], 0
                for chunk in response.iter_content(65536):
                    length += len(chunk)
                    if length > 4_000_000:
                        raise ProviderError("SEC response exceeds the import size limit.")
                    parts.append(chunk)
                raw = b"".join(parts)
        except requests.RequestException:
            raise ProviderError("SEC connection unavailable.") from None
        if json:
            import json as parser
            try:
                return parser.loads(raw)
            except ValueError:
                raise ProviderError("Invalid SEC JSON.") from None
        return raw

    def sync(self, cik):
        payload = self.get(f"https://data.sec.gov/submissions/CIK{cik}.json", json=True)
        recent = payload.get("filings", {}).get("recent", {})
        added = 0
        for i, form in enumerate(recent.get("form", [])):
            if form not in {"4", "4/A", "13F-HR", "13F-HR/A"}:
                continue
            accession = recent["accessionNumber"][i]
            if not re.fullmatch(r"\d{10}-\d{2}-\d{6}", accession) or SecFiling.objects.filter(accession=accession).exists():
                continue
            base = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession.replace('-', '')}/"
            document = recent["primaryDocument"][i].split("/")[-1]
            if not re.fullmatch(r"[A-Za-z0-9_.-]+\.xml", document):
                continue
            metadata = {"cik": cik, "filer": str(payload.get("name", ""))[:200], "form": form,
                        "filed_at": timestamp(recent["acceptanceDateTime"][i]),
                        "report_date": date.fromisoformat(recent["reportDate"][i]) if recent["reportDate"][i] else None,
                        "url": base + document}
            root = xml_document(self.get(base + document))
            if form.startswith("4"):
                insiders, holdings = parse_insiders(root), []
            else:
                report_date = field(root, ".//reportCalendarOrQuarter")
                if report_date:
                    from datetime import datetime
                    try:
                        metadata["report_date"] = datetime.strptime(report_date, "%m-%d-%Y").date()
                    except ValueError:
                        metadata["report_date"] = date.fromisoformat(report_date)
                index = self.get(base + "index.json", json=True)
                holdings, insiders = [], []
                for item in index.get("directory", {}).get("item", []):
                    name = item.get("name", "")
                    if name == document or not re.fullmatch(r"[A-Za-z0-9_.-]+\.xml", name):
                        continue
                    candidate = xml_document(self.get(base + name))
                    if candidate.tag == "informationTable":
                        holdings.extend(parse_holdings(candidate))
                declared = field(root, ".//tableEntryTotal")
                if not declared or int(declared) != len(holdings):
                    raise ProviderError("13F information table is incomplete; filing not imported.")
                if not holdings:
                    raise ProviderError("13F information table unavailable.")
                for index, row in enumerate(holdings, start=1):
                    row["line_number"] = index
            self.guard()
            with transaction.atomic():
                filing, created = SecFiling.objects.get_or_create(accession=accession, defaults={**metadata,
                    "status": "Amendment: review source; excluded from change comparisons" if form.endswith("/A") else "Imported"})
                if created:
                    InsiderTransaction.objects.bulk_create([InsiderTransaction(filing=filing, **row) for row in insiders])
                    InstitutionalHolding.objects.bulk_create([InstitutionalHolding(filing=filing, **row) for row in holdings])
                    added += 1
            if added >= 2:
                break
        return added
