#!/usr/bin/env python3
"""
validate_contracts.py - validate JSON artefacts against the shared contracts.

    python validate_contracts.py candidates  path/to/candidates.json
    python validate_contracts.py events      path/to/engine_stdout.ndjson
    python validate_contracts.py manifest    path/to/manifest.json
    python validate_contracts.py drives      path/to/drives.json
    python validate_contracts.py rest <def>  path/to/response.json     (def = scan_session, recovered_file, ...)

Every team runs this in CI against its mocks and real outputs, so a contract
change breaks the build of whichever side forgot to update.
Requires: pip install jsonschema referencing
"""
import json
import os
import sys

from jsonschema import Draft202012Validator
from referencing import Registry, Resource

HERE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "schemas")
FILES = {
    "candidates": "candidates.schema.json",
    "events": "engine-events.schema.json",
    "manifest": "recover-manifest.schema.json",
    "drives": "drives.schema.json",
    "rest": "rest-api.schema.json",
}


def _registry():
    resources = []
    for name in FILES.values():
        schema = json.load(open(os.path.join(HERE, name), encoding="utf-8"))
        res = Resource.from_contents(schema)
        resources.append((schema["$id"], res))
        resources.append((name, res))  # relative refs such as "candidates.schema.json#/$defs/extent"
    return Registry().with_resources(resources)


REGISTRY = _registry()


def validator_for(kind, definition=None):
    schema = json.load(open(os.path.join(HERE, FILES[kind]), encoding="utf-8"))
    if definition:
        schema = {"$schema": schema["$schema"], "$ref": f"{schema['$id']}#/$defs/{definition}"}
    return Draft202012Validator(schema, registry=REGISTRY)


def validate(kind, instance, definition=None):
    """Returns a list of human readable errors (empty == valid)."""
    v = validator_for(kind, definition)
    return [f"{'/'.join(map(str, e.absolute_path)) or '<root>'}: {e.message}" for e in v.iter_errors(instance)]


def validate_ndjson(path):
    errors = []
    v = validator_for("events")
    with open(path, encoding="utf-8") as fh:
        for i, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError as exc:
                errors.append(f"line {i}: invalid JSON ({exc})")
                continue
            errs = list(v.iter_errors(ev))
            if errs:
                errors.append(f"line {i} ({ev.get('event')}): {errs[0].message[:200]}")
    return errors


def main(argv):
    if len(argv) < 3:
        print(__doc__)
        return 2
    kind = argv[1]
    if kind == "events":
        errors = validate_ndjson(argv[2])
    elif kind == "rest":
        errors = validate("rest", json.load(open(argv[3], encoding="utf-8")), argv[2])
    else:
        errors = validate(kind, json.load(open(argv[2], encoding="utf-8")))
    for e in errors[:50]:
        print("INVALID:", e)
    print("OK" if not errors else f"{len(errors)} error(s)")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
