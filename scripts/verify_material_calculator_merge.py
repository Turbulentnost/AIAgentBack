"""Проверка суммирования одинаковой номенклатуры из разных спецификаций."""
from __future__ import annotations

import sys

import httpx

BASE = "http://127.0.0.1:5454/api/v1"
EMAIL = "bugata.pavel@local.dev"
PASSWORD = "Bugata2026!"


def material_keys(materials: list[dict]) -> set[str]:
    keys: set[str] = set()
    for material in materials:
        if material.get("produced_in_process"):
            continue
        code = (material.get("code") or "").strip().lower()
        name = (material.get("name") or "").strip().lower()
        nom = (material.get("nomenclature_key") or "").strip()
        if nom:
            keys.add(f"nom:{nom}")
        elif code:
            keys.add(f"code:{code}")
        elif name:
            keys.add(f"name:{name}")
    return keys


def main() -> int:
    with httpx.Client(base_url=BASE, timeout=120.0) as client:
        login = client.post("/auth/login", json={"email": EMAIL, "password": PASSWORD})
        login.raise_for_status()
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

        listed = client.get("/agents/document-analysis/resource-specs?limit=200", headers=headers)
        listed.raise_for_status()
        items = [row for row in listed.json().get("items", []) if row.get("materials_count", 0) > 0]

        spec_materials: dict[str, set[str]] = {}
        for row in items[:80]:
            detail = client.get(
                f"/agents/document-analysis/resource-specs/{row['ref_key']}",
                headers=headers,
            )
            if detail.status_code != 200:
                continue
            spec = detail.json().get("spec") or {}
            spec_materials[row["ref_key"]] = material_keys(spec.get("materials") or [])

        pair: tuple[str, str, str] | None = None
        for i, ref_a in enumerate(spec_materials):
            for ref_b in list(spec_materials)[i + 1 :]:
                shared = spec_materials[ref_a] & spec_materials[ref_b]
                if shared:
                    pair = (ref_a, ref_b, next(iter(shared)))
                    break
            if pair:
                break

        if pair is None:
            print("SKIP: no overlapping materials found in first 80 specs")
            return 0

        ref_a, ref_b, shared_key = pair
        only_a = client.post(
            "/agents/document-analysis/material-calculator",
            headers=headers,
            json={"items": [{"spec_ref_key": ref_a, "quantity": 2}]},
        ).json()["lines"]
        only_b = client.post(
            "/agents/document-analysis/material-calculator",
            headers=headers,
            json={"items": [{"spec_ref_key": ref_b, "quantity": 3}]},
        ).json()["lines"]
        combined = client.post(
            "/agents/document-analysis/material-calculator",
            headers=headers,
            json={"items": [{"spec_ref_key": ref_a, "quantity": 2}, {"spec_ref_key": ref_b, "quantity": 3}]},
        )
        combined.raise_for_status()
        both = combined.json()["lines"]

        def index(lines: list[dict]) -> dict[str, float]:
            out: dict[str, float] = {}
            for line in lines:
                code = (line.get("code") or "").strip().lower()
                name = (line.get("name") or "").strip().lower()
                nom = (line.get("nomenclature_key") or "").strip()
                if nom:
                    key = f"nom:{nom}"
                elif code:
                    key = f"code:{code}"
                else:
                    key = f"name:{name}"
                out[key] = float(line.get("total_qty") or 0)
            return out

        idx_a = index(only_a)
        idx_b = index(only_b)
        idx_both = index(both)

        if shared_key not in idx_a or shared_key not in idx_b:
            print(f"SKIP: shared key {shared_key} not in isolated results")
            return 0

        expected = round(idx_a[shared_key] + idx_b[shared_key], 4)
        actual = round(idx_both.get(shared_key, -1), 4)
        if actual != expected:
            print(f"FAIL: shared material not summed: expected {expected}, got {actual}, key={shared_key}")
            return 1

        # Ensure combined result has no duplicate aggregate keys
        if len(both) != len({(line.get("code") or "", line.get("name") or "") for line in both}):
            print("FAIL: duplicate rows in combined result")
            return 1

        print(f"OK: shared material summed correctly ({shared_key}: {idx_a[shared_key]} + {idx_b[shared_key]} = {actual})")
        return 0


if __name__ == "__main__":
    sys.exit(main())
