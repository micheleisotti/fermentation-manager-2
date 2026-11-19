"""How-to-cite text: reference, BibTeX, DOI handling and the metadata keys each figure format accepts."""

import io

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pytest

from fm2 import __version__, citation


def test_reference_and_bibtex_without_and_with_doi(monkeypatch):
    assert citation.DOI is None
    ref = citation.reference("9.9.9")
    assert "Isotti, M. (2026)" in ref and "Version 9.9.9" in ref and citation.URL in ref
    assert "doi" not in citation.bibtex("9.9.9").lower().replace("@software", "")
    monkeypatch.setattr(citation, "DOI", "10.5281/zenodo.1234567")
    assert "https://doi.org/10.5281/zenodo.1234567" in citation.reference("9.9.9")
    bib = citation.bibtex("9.9.9")
    assert "doi = {10.5281/zenodo.1234567}" in bib and "version = {9.9.9}" in bib and bib.count("{") == bib.count("}")


def test_info_matches_package_version():
    i = citation.info()
    assert i["version"] == __version__ and i["license"] == "AGPL-3.0-or-later"


@pytest.mark.parametrize("fmt,magic", [("png", b"\x89PNG"), ("pdf", b"%PDF"), ("svg", b"<?xml")])
def test_figure_metadata_accepted_and_written(fmt, magic):
    fig, ax = plt.subplots()
    ax.plot([0, 1], [0, 1])
    buf = io.BytesIO()
    fig.savefig(buf, format=fmt, metadata=citation.figure_metadata(fmt))
    plt.close(fig)
    data = buf.getvalue()
    assert data[:len(magic)] == magic and b"Fermentation Manager 2" in data and b"cite" not in data


def test_figure_metadata_unknown_format_is_none():
    assert citation.figure_metadata("tiff") is None
