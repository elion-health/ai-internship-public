"""Seed identity nodes from bundled labels, never answer relationships."""

import argparse
import asyncio
from uuid import NAMESPACE_URL, uuid5

from neo4j import AsyncGraphDatabase

from ai_internship.config import Settings
from ai_internship.public_assets import load_dataset


def catalog_nodes(rows: list[dict]) -> list[dict]:
    nodes = {}
    # Establish target identities first so integration names reuse them.
    for row in rows:
        inp = row["input"]
        name = inp.get("name") or row.get("metadata", {}).get("product_name")
        mid = inp.get("product_id") or row.get("metadata", {}).get("product_id")
        if not name:
            continue
        uid = mid or str(uuid5(NAMESPACE_URL, f"Product:{name.casefold()}"))
        nodes[("Product", name.casefold())] = {
            "uuid": uid,
            "seed_id": uid,
            "marketplace_id": mid,
            "name": name,
            "label": "Product",
        }
    for row in rows:
        for profile, label in [("customers", "HealthSystem"), ("integrations", "Product")]:
            for value in (row.get("expected") or {}).get(profile, []):
                name = value["name"]
                uid = str(uuid5(NAMESPACE_URL, f"{label}:{name.casefold()}"))
                nodes.setdefault(
                    (label, name.casefold()),
                    {
                        "uuid": uid,
                        "seed_id": uid,
                        "marketplace_id": None,
                        "name": name,
                        "label": label,
                    },
                )
    return list(nodes.values())


async def main_async(args):
    rows = load_dataset("Marketplace Customers Dataset") + load_dataset(
        "Marketplace Integrations Dataset"
    )
    nodes = catalog_nodes(rows)
    if args.dry_run:
        print(f"{len(nodes)} identity nodes, 0 relationships; no database writes")
        return
    settings = Settings()
    driver = AsyncGraphDatabase.driver(
        settings.neo4j_uri, auth=(settings.neo4j_user, settings.neo4j_password)
    )
    try:
        await driver.execute_query(
            "CREATE CONSTRAINT public_seed_id IF NOT EXISTS "
            "FOR (n:Entity) REQUIRE n.seed_id IS UNIQUE"
        )
        for label in ("Product", "HealthSystem"):
            await driver.execute_query(
                f"UNWIND $nodes AS item MERGE (n:Entity:{label} {{seed_id: item.seed_id}}) "
                "ON CREATE SET n.uuid = item.uuid, n.name = item.name, "
                'n.marketplace_id = item.marketplace_id, n.group_id = "ai-internship", '
                'n.summary = "", n.created_at = datetime()',
                nodes=[n for n in nodes if n["label"] == label],
            )
    finally:
        await driver.close()
    print(f"Seeded {len(nodes)} identity nodes; no relationships or source evidence inserted")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    asyncio.run(main_async(parser.parse_args()))


if __name__ == "__main__":
    main()
