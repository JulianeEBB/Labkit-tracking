from datetime import date
from init_db import initialize_database
from labkit_repo import (
    add_site,
    list_sites,
    add_labkit_type,
    list_labkit_types,
    add_labkit,
    list_labkits,
    update_labkit_status,
    get_labkit_by_barcode,
)


def main():
    print("Initializing database (tables)...")
    initialize_database()
    print("Done.\n")

    # --- Sites ---
    print("Adding a test site...")
    site_id = add_site("SITE01", "Example Oncology Center")
    print(f"Created site with id = {site_id}")

    print("Current sites:")
    for s in list_sites():
        print("  ", s)
    print()

    # --- Labkit types ---
    print("Adding a labkit type...")
    lkt_id = add_labkit_type("Screening kit", "Basic screening visit kit")
    print(f"Created labkit_type with id = {lkt_id}")

    print("Current labkit types:")
    for t in list_labkit_types():
        print("  ", t)
    print()

    # --- Labkits ---
    print("Adding labkits...")
    kit1_id = add_labkit(
        labkit_type_id=lkt_id,
        site_id=site_id,
        lot_number="LOT2025A",
        expiry_date=date(2025, 12, 31),
    )
    kit2_id = add_labkit(
        labkit_type_id=lkt_id,
        site_id=site_id,
        lot_number="LOT2025B",
        expiry_date=date(2025, 11, 30),
    )
    print(f"Created labkits with ids = {kit1_id}, {kit2_id}\n")

    print("All labkits:")
    for k in list_labkits():
        print("  ", k)
    print()

    # --- Status change example ---
    first_code = None
    if list_labkits():
        first = list_labkits()[0]
        first_code = first.get("barcode_value") or first.get("kit_barcode")
    if first_code:
        print(f"Updating status of {first_code} to 'packed' and then 'shipped'...")
        update_labkit_status(first_code, "packed")
        update_labkit_status(first_code, "shipped")

        kit = get_labkit_by_barcode(first_code)
        print(f"\nUpdated {first_code}:")
        print(kit)

    print("\nDone.")


if __name__ == "__main__":
    main()
