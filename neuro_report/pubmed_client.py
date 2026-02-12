from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from datetime import date

import requests

from .models import Study

EUTILS_BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"

COUNTRY_MARKERS = {
    "germany": "Deutschland",
    "deutschland": "Deutschland",
    "usa": "USA",
    "united states": "USA",
    "canada": "Kanada",
    "uk": "Vereinigtes Königreich",
    "united kingdom": "Vereinigtes Königreich",
    "france": "Frankreich",
    "italy": "Italien",
    "spain": "Spanien",
    "netherlands": "Niederlande",
    "sweden": "Schweden",
    "norway": "Norwegen",
    "denmark": "Dänemark",
    "switzerland": "Schweiz",
    "austria": "Österreich",
    "japan": "Japan",
    "china": "China",
    "korea": "Südkorea",
    "australia": "Australien",
    "india": "Indien",
    "brazil": "Brasilien",
}


class PubMedClient:
    def __init__(self, email: str, timeout_seconds: int = 30) -> None:
        self.email = email
        self.timeout_seconds = timeout_seconds

    def search_pmids(self, start_date: date, end_date: date, max_results: int) -> list[str]:
        query = self._build_query()
        params = {
            "db": "pubmed",
            "term": query,
            "retmax": str(max_results),
            "retmode": "json",
            "sort": "pub date",
            "mindate": start_date.isoformat(),
            "maxdate": end_date.isoformat(),
            "datetype": "pdat",
            "email": self.email,
        }
        response = requests.get(f"{EUTILS_BASE}/esearch.fcgi", params=params, timeout=self.timeout_seconds)
        response.raise_for_status()
        payload = response.json()
        return payload.get("esearchresult", {}).get("idlist", [])

    def fetch_studies(self, pmids: list[str]) -> list[Study]:
        if not pmids:
            return []
        studies: list[Study] = []
        chunk_size = 100
        for i in range(0, len(pmids), chunk_size):
            batch = pmids[i : i + chunk_size]
            params = {
                "db": "pubmed",
                "id": ",".join(batch),
                "retmode": "xml",
                "email": self.email,
            }
            response = requests.get(f"{EUTILS_BASE}/efetch.fcgi", params=params, timeout=self.timeout_seconds)
            response.raise_for_status()
            studies.extend(self._parse_efetch_xml(response.text))
        return studies

    @staticmethod
    def _build_query() -> str:
        stroke_terms = "(stroke OR ischemic stroke OR intracerebral hemorrhage OR subarachnoid hemorrhage OR thrombectomy OR thrombolysis)"
        emergency_terms = "(emergency OR acute OR critical care OR neurocritical care OR emergency department)"
        clinical_terms = "(randomized OR trial OR cohort OR registry OR meta-analysis OR guideline)"
        exclude_terms = "NOT (animals[MeSH Terms] NOT humans[MeSH Terms]) NOT (mouse OR mice OR rat OR in vitro OR preclinical)"
        return f"{stroke_terms} AND {emergency_terms} AND {clinical_terms} {exclude_terms}"

    def _parse_efetch_xml(self, xml_text: str) -> list[Study]:
        root = ET.fromstring(xml_text)
        studies: list[Study] = []
        for article in root.findall(".//PubmedArticle"):
            pmid = self._text(article.find(".//PMID"))
            title = self._text(article.find(".//ArticleTitle"))
            journal = self._text(article.find(".//Journal/Title"))
            abstract_parts = []
            for node in article.findall(".//AbstractText"):
                if node.text:
                    abstract_parts.append(node.text.strip())
            abstract = " ".join(abstract_parts)
            publication_types = [
                el.text.strip()
                for el in article.findall(".//PublicationTypeList/PublicationType")
                if el.text
            ]
            affiliations = [
                el.text.strip()
                for el in article.findall(".//AffiliationInfo/Affiliation")
                if el.text
            ]
            doi = None
            for id_node in article.findall(".//ArticleId"):
                if id_node.attrib.get("IdType") == "doi" and id_node.text:
                    doi = id_node.text.strip()
                    break
            publication_date = self._extract_date(article)
            countries = self._extract_countries(affiliations)

            studies.append(
                Study(
                    pmid=pmid,
                    title=title,
                    journal=journal,
                    publication_date=publication_date,
                    abstract=abstract,
                    publication_types=publication_types,
                    affiliations=affiliations,
                    country_hints=countries,
                    doi=doi,
                )
            )
        return studies

    @staticmethod
    def _text(node: ET.Element | None) -> str:
        if node is None or node.text is None:
            return ""
        return node.text.strip()

    @staticmethod
    def _extract_date(article: ET.Element) -> date | None:
        year_node = article.find(".//PubDate/Year")
        month_node = article.find(".//PubDate/Month")
        day_node = article.find(".//PubDate/Day")
        if year_node is None or year_node.text is None:
            return None

        year = int(year_node.text)
        month = 1
        day = 1

        if month_node is not None and month_node.text:
            raw_month = month_node.text.strip()
            month_map = {
                "jan": 1,
                "feb": 2,
                "mar": 3,
                "apr": 4,
                "may": 5,
                "jun": 6,
                "jul": 7,
                "aug": 8,
                "sep": 9,
                "oct": 10,
                "nov": 11,
                "dec": 12,
            }
            if raw_month.isdigit():
                month = int(raw_month)
            else:
                month = month_map.get(raw_month[:3].lower(), 1)

        if day_node is not None and day_node.text and day_node.text.isdigit():
            day = int(day_node.text)

        try:
            return date(year, month, day)
        except ValueError:
            return date(year, month, 1)

    @staticmethod
    def _extract_countries(affiliations: list[str]) -> list[str]:
        hits: set[str] = set()
        for aff in affiliations:
            low = aff.lower()
            matched = False
            for marker, normalized in COUNTRY_MARKERS.items():
                if marker in low:
                    hits.add(normalized)
                    matched = True
            if not matched:
                tail = aff.split(",")[-1].strip()
                if re.fullmatch(r"[A-Za-z .-]{3,}", tail):
                    hits.add(tail)
        return sorted(hits)
