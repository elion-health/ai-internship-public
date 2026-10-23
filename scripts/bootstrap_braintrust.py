"""Create a fresh Braintrust project from the bundled research assets."""

import argparse
import json
import os
import urllib.parse
import urllib.request
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from dotenv import load_dotenv

from ai_internship.public_assets import assets_dir


def bootstrap(api, project: str, root: Path) -> dict:
    existing = api("GET", "project", params={"project_name": project})
    if existing.get("objects"):
        raise ValueError(f"Project {project!r} already exists; choose a fresh project name")
    prompts = [json.loads(p.read_text()) for p in sorted((root / "prompts").glob("*.json"))]
    datasets = [json.loads(p.read_text()) for p in sorted((root / "datasets").glob("*.json"))]
    if not prompts or not datasets:
        raise ValueError("Bundled prompts and datasets are required before creating a project")
    created = api("POST", "project", {"name": project})
    pid = created["id"]
    mapping = {"project": project, "project_id": pid, "prompt_versions": {}, "datasets": {}}
    for prompt in prompts:
        versions = {}
        prompt_id = None
        for version in prompt["versions"]:
            if prompt_id is None:
                result = api(
                    "POST",
                    "prompt",
                    {
                        "project_id": pid,
                        "name": prompt["name"],
                        "slug": prompt["slug"],
                        "prompt_data": version["prompt_data"],
                    },
                )
                prompt_id = result["id"]
            else:
                result = api(
                    "PATCH", f"prompt/{prompt_id}", {"prompt_data": version["prompt_data"]}
                )
            versions[version["version"]] = result["_xact_id"]
        mapping["prompt_versions"][prompt["slug"]] = versions
    for dataset in datasets:
        result = api("POST", "dataset", {"project_id": pid, "name": dataset["name"]})
        did = result["id"]
        for offset in range(0, len(dataset["rows"]), 100):
            events = [
                dict(row, id=str(uuid5(NAMESPACE_URL, f"{dataset['name']}:{offset + i}")))
                for i, row in enumerate(dataset["rows"][offset : offset + 100])
            ]
            api("POST", f"dataset/{did}/insert", {"events": events})
        mapping["datasets"][dataset["name"]] = {"id": did, "rows": len(dataset["rows"])}
    return mapping


def main():
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True, help="New project in your own account")
    parser.add_argument("--output", type=Path, default=Path("data/runs/braintrust-bootstrap.json"))
    args = parser.parse_args()
    key = os.getenv("BRAINTRUST_API_KEY")
    if not key:
        parser.error("Set BRAINTRUST_API_KEY to your own account key")

    def api(method, path, body=None, params=None):
        url = "https://api.braintrust.dev/v1/" + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        request = urllib.request.Request(
            url,
            method=method,
            data=json.dumps(body).encode() if body is not None else None,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=90) as response:
            return json.load(response)

    mapping = bootstrap(api, args.project, assets_dir())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(mapping, indent=2) + "\n")
    print(f"Created {args.project}; version mapping saved to {args.output}")
    print("Set BRAINTRUST_PROJECT to this project. No historical runs or logs were imported.")


if __name__ == "__main__":
    main()
