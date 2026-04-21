"""
One-shot fix: replaces all ::type cast syntax in lead_lifecycle.py
with CAST(... AS type) form so SQLAlchemy text() can parse it.

Usage:
    python3 scripts/fix_lead_lifecycle_casts.py
"""
import pathlib

TARGET = pathlib.Path(__file__).parent.parent / "app/services/lead_lifecycle.py"

src = TARGET.read_text()

src = src.replace(
    "raw_payload_json::jsonb->>'phone_number_to'",
    "CAST(raw_payload_json AS jsonb)->>'phone_number_to'",
)
src = src.replace(
    "context::jsonb->>'from'",
    "CAST(context AS jsonb)->>'from'",
)
src = src.replace(
    "context::jsonb->>'reason'",
    "CAST(context AS jsonb)->>'reason'",
)
src = src.replace(
    "    ROUND(AVG(\n",
    "    ROUND(CAST(AVG(\n",
)
src = src.replace(
    "    )::numeric, 1)",
    "    ) AS numeric), 1)",
)
src = src.replace(
    "    ROUND(\n        EXTRACT(EPOCH FROM (NOW() - ca.first_contact_at)) / 86400.0\n    )::int",
    "    CAST(ROUND(\n        EXTRACT(EPOCH FROM (NOW() - ca.first_contact_at)) / 86400.0\n    ) AS int)",
)

TARGET.write_text(src)

remaining = [(i + 1, line) for i, line in enumerate(src.splitlines()) if "::" in line]
if remaining:
    print("WARNING — remaining :: lines:")
    for lineno, line in remaining:
        print(f"  {lineno}: {line.rstrip()}")
else:
    print("All casts fixed. No :: remaining.")
