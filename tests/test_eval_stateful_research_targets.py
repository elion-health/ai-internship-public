import importlib.util
from pathlib import Path

from ai_internship.public_assets import load_dataset, research_targets


def test_examples_cover_exactly_ten_products_in_both_profiles():
    expected = {
        "Suki",
        "FirstHx",
        "Fold Health",
        "Flatiron OncoEMR",
        "Infermedica",
        "AgileMD Clinical Pathways",
        "Cadence",
        "New Lantern",
        "Arrive Health",
        "Abridge",
    }
    for name in ("Marketplace Customers Dataset", "Marketplace Integrations Dataset"):
        rows = load_dataset(name)
        assert len(rows) == 10
        assert {row["input"]["name"] for row in rows} == expected
    assert set(research_targets()) == expected


def test_crawler_and_eval_use_only_bundled_examples():
    targets = research_targets()
    for file, variable, expected in (
        ("eval_stateful_research", "TARGETS", targets),
        ("vendor_fetch_baseline", "VENDORS", {k: v["url"] for k, v in targets.items()}),
    ):
        spec = importlib.util.spec_from_file_location(
            file, Path(__file__).parents[1] / "scripts" / f"{file}.py"
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        assert getattr(module, variable) == expected
