"""
How to cite FM2: one place for the text shown in Settings › How to cite (and README).
Exports and figures carry only the software name and version (provenance), not the citation
text (decision 19 Nov 2026). Keep CITATION.cff in step with these values.

DOIs (Zenodo): CONCEPT_DOI always resolves to the latest release; VERSION_DOIS maps each archived
release to its own DOI. A version's DOI exists only after its release is published, so a new version
falls back to the concept DOI until it is added here (and in CITATION.cff).
"""

from __future__ import annotations

from typing import Optional

from . import __version__

TITLE = "Fermentation Manager 2 (FM2)"
AUTHOR_FAMILY = "Isotti"
AUTHOR_GIVEN = "Michele"
YEAR = 2026
URL = "https://github.com/micheleisotti/fermentation-manager-2"
ORCID = "https://orcid.org/0009-0002-7727-4942"
CONCEPT_DOI: Optional[str] = "10.5281/zenodo.24469780"
VERSION_DOIS: dict[str, str] = {
    "2.3.1": "10.5281/zenodo.24469781",
}
LICENSE_ID = "AGPL-3.0-or-later"
PLEASE_CITE = "If FM2 was used to record or analyse data in a publication, please cite it."


def doi_for(version: str = __version__) -> Optional[str]:
    """DOI of that version if archived, otherwise the concept DOI (latest), otherwise None."""
    return VERSION_DOIS.get(version) or CONCEPT_DOI


def _link(version: str) -> str:
    d = doi_for(version)
    return f"https://doi.org/{d}" if d else URL


def reference(version: str = __version__) -> str:
    """Plain-text reference (APA style)."""
    return (f"{AUTHOR_FAMILY}, {AUTHOR_GIVEN[0]}. ({YEAR}). {TITLE} (Version {version}) "
            f"[Computer software]. {_link(version)}")


def bibtex(version: str = __version__) -> str:
    fields = [("author", f"{AUTHOR_FAMILY}, {AUTHOR_GIVEN}"), ("title", TITLE), ("version", version),
              ("year", str(YEAR)), ("url", URL)]
    d = doi_for(version)
    if d:
        fields.append(("doi", d))
    body = ",\n".join(f"  {k} = {{{v}}}" for k, v in fields)
    return f"@software{{fm2_{version.replace('.', '_')},\n{body}\n}}"


def info(version: str = __version__) -> dict:
    return {"title": TITLE, "orcid": ORCID, "version": version, "year": YEAR, "url": URL, "doi": doi_for(version), "version_doi": VERSION_DOIS.get(version),
            "concept_doi": CONCEPT_DOI, "license": LICENSE_ID,
            "please_cite": PLEASE_CITE, "reference": reference(version), "bibtex": bibtex(version)}


def figure_metadata(fmt: str) -> Optional[dict]:
    """Software name and version inside the figure file (provenance only, nothing visible, no citation text)."""
    f = fmt.lower()
    sw = f"{TITLE} v{__version__}"
    if f == "png":
        return {"Software": sw}
    if f in ("pdf", "svg"):
        return {"Creator": sw}
    return None
