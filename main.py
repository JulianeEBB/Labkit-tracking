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
        kit_barcode="KITBARCODE001",
        labkit_type_id=lkt_id,
        site_id=site_id,
        lot_number="LOT2025A",
        expiry_date=date(2025, 12, 31),
    )
    kit2_id = add_labkit(
        kit_barcode="KITBARCODE002",
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
    print("Updating status of KITBARCODE001 to 'packed' and then 'shipped'...")
    update_labkit_status("KITBARCODE001", "packed")
    update_labkit_status("KITBARCODE001", "shipped")

    kit = get_labkit_by_barcode("KITBARCODE001")
    print("\nUpdated KITBARCODE001:")
    print(kit)

    print("\nDone.")


if __name__ == "__main__":
    main()
