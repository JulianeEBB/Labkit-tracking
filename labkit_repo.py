"""Repository functions for lab kit tracking."""

from typing import Any, Dict, List, Optional

from werkzeug.security import generate_password_hash

from db_config import get_connection


def _rows_to_dicts(cursor) -> List[Dict[str, Any]]:
    """Convert cursor rows to list of dicts using column names."""
    columns = [desc[0] for desc in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def add_site(site_code: str, site_name: str) -> int:
    """Insert a new site and return its id."""
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            "INSERT INTO site (site_code, site_name) VALUES (%s, %s) RETURNING id;",
            (site_code, site_name),
        )
        new_id = cur.fetchone()[0]
        conn.commit()
        return new_id
    finally:
        cur.close()
        conn.close()


def list_labkits_with_names() -> List[Dict[str, Any]]:
    """Return labkits with joined names for export."""
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT
                l.kit_barcode,
                t.name AS labkit_type_name,
                s.site_name,
                l.status,
                l.lot_number,
                l.expiry_date,
                l.created_at
            FROM labkit l
            LEFT JOIN labkit_type t ON l.labkit_type_id = t.id
            LEFT JOIN site s ON l.site_id = s.id
            ORDER BY l.id;
            """
        )
        return _rows_to_dicts(cur)
    finally:
        cur.close()
        conn.close()


def list_shipments_with_counts() -> List[Dict[str, Any]]:
    """Return shipments with kit counts for export."""
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT
                sh.id,
                s.site_name,
                sh.shipped_at,
                sh.carrier,
                sh.tracking_number,
                sh.status,
                COUNT(l.id) AS number_of_kits
            FROM shipment sh
            LEFT JOIN site s ON sh.site_id = s.id
            LEFT JOIN labkit l ON l.shipment_id = sh.id
            GROUP BY sh.id, s.site_name, sh.shipped_at, sh.carrier, sh.tracking_number, sh.status
            ORDER BY sh.id;
            """
        )
        return _rows_to_dicts(cur)
    finally:
        cur.close()
        conn.close()


def list_sites() -> List[Dict[str, Any]]:
    """Return all sites as a list of dictionaries."""
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT id, site_code, site_name,
                   address_line1, address_line2, city, state, postal_code, country,
                   created_at
            FROM site
            ORDER BY id;
            """
        )
        return _rows_to_dicts(cur)
    finally:
        cur.close()
        conn.close()


def get_site(site_id: int) -> Optional[Dict[str, Any]]:
    """Return a single site by id."""
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT id, site_code, site_name,
                   address_line1, address_line2, city, state, postal_code, country,
                   created_at
            FROM site
            WHERE id = %s;
            """,
            (site_id,),
        )
        row = cur.fetchone()
        if not row:
            return None
        columns = [desc[0] for desc in cur.description]
        return dict(zip(columns, row))
    finally:
        cur.close()
        conn.close()


def update_site(
    site_id: int,
    site_code: str,
    site_name: str,
    address_line1: Optional[str],
    address_line2: Optional[str],
    city: Optional[str],
    state: Optional[str],
    postal_code: Optional[str],
    country: Optional[str],
) -> None:
    """Update site details."""
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            UPDATE site
            SET site_code = %s,
                site_name = %s,
                address_line1 = %s,
                address_line2 = %s,
                city = %s,
                state = %s,
                postal_code = %s,
                country = %s
            WHERE id = %s;
            """,
            (
                site_code,
                site_name,
                address_line1,
                address_line2,
                city,
                state,
                postal_code,
                country,
                site_id,
            ),
        )
        conn.commit()
    finally:
        cur.close()
        conn.close()


def delete_site(site_id: int) -> None:
    """Delete a site."""
    conn = get_connection()
    cur = conn.cursor()
    try:
        # Delete dependent labkits (and their events) first
        cur.execute("SELECT id FROM labkit WHERE site_id = %s;", (site_id,))
        labkit_ids = [row[0] for row in cur.fetchall()]
        cur.close()
        conn.close()
        for lid in labkit_ids:
            delete_labkit(lid)
        conn = get_connection()
        cur = conn.cursor()
        # Remove contacts referencing this site
        cur.execute("DELETE FROM site_contact WHERE site_id = %s;", (site_id,))
        # Null out shipments referencing this site to satisfy FK
        cur.execute("UPDATE shipment SET site_id = NULL WHERE site_id = %s;", (site_id,))
        cur.execute("DELETE FROM site WHERE id = %s;", (site_id,))
        conn.commit()
    finally:
        cur.close()
        conn.close()


def add_labkit_type(
    name: str, description: Optional[str] = None, default_expiry_days: Optional[int] = None
) -> int:
    """Insert a new labkit type and return its id."""
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            INSERT INTO labkit_type (name, description, default_expiry_days)
            VALUES (%s, %s, %s)
            RETURNING id;
            """,
            (name, description, default_expiry_days),
        )
        new_id = cur.fetchone()[0]
        conn.commit()
        return new_id
    finally:
        cur.close()
        conn.close()


def list_labkit_types() -> List[Dict[str, Any]]:
    """Return all labkit types as a list of dictionaries."""
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT id, name, description, default_expiry_days, created_at
            FROM labkit_type
            ORDER BY id;
            """
        )
        return _rows_to_dicts(cur)
    finally:
        cur.close()
        conn.close()


def update_labkit_type(
    labkit_type_id: int,
    name: str,
    description: Optional[str],
    default_expiry_days: Optional[int],
) -> None:
    """Update a labkit type."""
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            UPDATE labkit_type
            SET name = %s,
                description = %s,
                default_expiry_days = %s
            WHERE id = %s;
            """,
            (name, description, default_expiry_days, labkit_type_id),
        )
        conn.commit()
    finally:
        cur.close()
        conn.close()


def delete_labkit_type(labkit_type_id: int) -> None:
    """Delete a labkit type."""
    conn = get_connection()
    cur = conn.cursor()
    try:
        # Delete dependent labkits first to satisfy FK constraints
        cur.execute("SELECT id FROM labkit WHERE labkit_type_id = %s;", (labkit_type_id,))
        labkit_ids = [row[0] for row in cur.fetchall()]
        cur.close()
        conn.close()
        # reuse existing delete_labkit helper for cascade cleanup
        for lid in labkit_ids:
            delete_labkit(lid)
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("DELETE FROM labkit_type WHERE id = %s;", (labkit_type_id,))
        conn.commit()
    finally:
        cur.close()
        conn.close()


def add_labkit(
    kit_barcode: str,
    labkit_type_id: int,
    site_id: Optional[int],
    lot_number: Optional[str],
    expiry_date,
    created_by: Optional[str] = None,
) -> int:
    """Insert a new labkit and return its id."""
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            INSERT INTO labkit (
                kit_barcode, labkit_type_id, site_id, lot_number, expiry_date, status
            )
            VALUES (%s, %s, %s, %s, %s, 'planned')
            RETURNING id;
            """,
            (kit_barcode, labkit_type_id, site_id, lot_number, expiry_date),
        )
        new_id = cur.fetchone()[0]
        conn.commit()
        add_labkit_event(new_id, "created", "Labkit created", created_by=created_by or "system")
        return new_id
    finally:
        cur.close()
        conn.close()


def get_labkit_by_barcode(barcode: str) -> Optional[Dict[str, Any]]:
    """Return a labkit by barcode, or None if not found."""
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT id, kit_barcode, labkit_type_id, site_id, lot_number,
                   expiry_date, status, created_at, updated_at
            FROM labkit
            WHERE kit_barcode = %s;
            """,
            (barcode,),
        )
        row = cur.fetchone()
        if not row:
            return None
        columns = [desc[0] for desc in cur.description]
        return dict(zip(columns, row))
    finally:
        cur.close()
        conn.close()


def list_labkits() -> List[Dict[str, Any]]:
    """Return all labkits as a list of dictionaries with names resolved."""
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT
                l.id,
                l.kit_barcode,
                l.labkit_type_id,
                t.name AS labkit_type_name,
                l.site_id,
                s.site_name,
                l.lot_number,
                l.expiry_date,
                l.status,
                l.created_at,
                l.updated_at
            FROM labkit l
            LEFT JOIN labkit_type t ON l.labkit_type_id = t.id
            LEFT JOIN site s ON l.site_id = s.id
            ORDER BY l.id;
            """
        )
        return _rows_to_dicts(cur)
    finally:
        cur.close()
        conn.close()


def update_labkit_status(barcode: str, new_status: str, created_by: Optional[str] = None) -> None:
    """
    Update a labkit status and record the status change event.

    Raises ValueError if the labkit is not found.
    """
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT id, status FROM labkit WHERE kit_barcode = %s;",
            (barcode,),
        )
        row = cur.fetchone()
        if not row:
            raise ValueError(f"Labkit with barcode '{barcode}' not found.")

        labkit_id, old_status = row

        cur.execute(
            """
            INSERT INTO labkit_status_event (labkit_id, old_status, new_status)
            VALUES (%s, %s, %s);
            """,
            (labkit_id, old_status, new_status),
        )
        cur.execute(
            """
            UPDATE labkit
            SET status = %s,
                updated_at = NOW()
            WHERE id = %s;
            """,
            (new_status, labkit_id),
        )
        conn.commit()
        try:
            add_labkit_event(
                labkit_id,
                "status_changed",
                f"Status {old_status} -> {new_status}",
                created_by=created_by or "system",
            )
        except Exception:
            pass
    finally:
        cur.close()
        conn.close()


def get_labkit_by_id(labkit_id: int) -> Optional[Dict[str, Any]]:
    """Return a labkit by id."""
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT id, kit_barcode, labkit_type_id, site_id, lot_number,
                   expiry_date, status, created_at, updated_at
            FROM labkit
            WHERE id = %s;
            """,
            (labkit_id,),
        )
        row = cur.fetchone()
        if not row:
            return None
        columns = [desc[0] for desc in cur.description]
        return dict(zip(columns, row))
    finally:
        cur.close()
        conn.close()


def get_labkit_detail(labkit_id: int) -> Optional[Dict[str, Any]]:
    """Return labkit with joined names for label/detail views."""
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT
                l.id,
                l.kit_barcode,
                l.labkit_type_id,
                t.name AS labkit_type_name,
                l.site_id,
                s.site_name,
                l.lot_number,
                l.expiry_date,
                l.status,
                l.created_at,
                l.updated_at
            FROM labkit l
            LEFT JOIN labkit_type t ON l.labkit_type_id = t.id
            LEFT JOIN site s ON l.site_id = s.id
            WHERE l.id = %s;
            """,
            (labkit_id,),
        )
        row = cur.fetchone()
        if not row:
            return None
        columns = [desc[0] for desc in cur.description]
        return dict(zip(columns, row))
    finally:
        cur.close()
        conn.close()


def update_labkit(
    labkit_id: int,
    kit_barcode: str,
    labkit_type_id: int,
    site_id: Optional[int],
    lot_number: Optional[str],
    expiry_date,
    status: str,
) -> None:
    """Update an existing labkit."""
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            UPDATE labkit
            SET kit_barcode = %s,
                labkit_type_id = %s,
                site_id = %s,
                lot_number = %s,
                expiry_date = %s,
                status = %s,
                updated_at = NOW()
            WHERE id = %s;
            """,
            (kit_barcode, labkit_type_id, site_id, lot_number, expiry_date, status, labkit_id),
        )
        conn.commit()
    finally:
        cur.close()
        conn.close()


def delete_labkit(labkit_id: int) -> None:
    """Delete a labkit."""
    conn = get_connection()
    cur = conn.cursor()
    try:
        # Remove dependent events/status rows to satisfy FK constraints
        cur.execute("DELETE FROM labkit_status_event WHERE labkit_id = %s;", (labkit_id,))
        cur.execute("DELETE FROM labkit_event WHERE labkit_id = %s;", (labkit_id,))
        # Clear shipment link if present
        cur.execute("UPDATE labkit SET shipment_id = NULL WHERE id = %s;", (labkit_id,))
        cur.execute("DELETE FROM labkit WHERE id = %s;", (labkit_id,))
        conn.commit()
    finally:
        cur.close()
        conn.close()


def list_site_contacts(site_id: int) -> List[Dict[str, Any]]:
    """Return all contacts for a site."""
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT id, site_id, name, role, email, phone, is_primary
            FROM site_contact
            WHERE site_id = %s
            ORDER BY id;
            """,
            (site_id,),
        )
        return _rows_to_dicts(cur)
    finally:
        cur.close()
        conn.close()


def add_site_contact(
    site_id: int,
    name: str,
    role: Optional[str],
    email: Optional[str],
    phone: Optional[str],
    is_primary: bool = False,
) -> int:
    """Add a contact to a site and return its id."""
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            INSERT INTO site_contact (site_id, name, role, email, phone, is_primary)
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING id;
            """,
            (site_id, name, role, email, phone, is_primary),
        )
        new_id = cur.fetchone()[0]
        conn.commit()
        return new_id
    finally:
        cur.close()
        conn.close()


def update_site_contact(
    contact_id: int,
    name: str,
    role: Optional[str],
    email: Optional[str],
    phone: Optional[str],
    is_primary: bool,
) -> None:
    """Update a site contact."""
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            UPDATE site_contact
            SET name = %s,
                role = %s,
                email = %s,
                phone = %s,
                is_primary = %s
            WHERE id = %s;
            """,
            (name, role, email, phone, is_primary, contact_id),
        )
        conn.commit()
    finally:
        cur.close()
        conn.close()


def delete_site_contact(contact_id: int) -> None:
    """Delete a site contact."""
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute("DELETE FROM site_contact WHERE id = %s;", (contact_id,))
        conn.commit()
    finally:
        cur.close()
        conn.close()


def get_status_history(barcode: str) -> List[Dict[str, Any]]:
    """Return status history events for a given labkit barcode, ordered by time."""
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute("SELECT id FROM labkit WHERE kit_barcode = %s;", (barcode,))
        row = cur.fetchone()
        if not row:
            return []
        labkit_id = row[0]
        cur.execute(
            """
            SELECT old_status, new_status, event_time
            FROM labkit_status_event
            WHERE labkit_id = %s
            ORDER BY event_time;
            """,
            (labkit_id,),
        )
        return _rows_to_dicts(cur)
    finally:
        cur.close()
        conn.close()


def add_labkit_event(
    labkit_id: int,
    event_type: str,
    description: Optional[str] = None,
    created_by: Optional[str] = None,
) -> int:
    """Add an event for a labkit and return its id."""
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            INSERT INTO labkit_event (labkit_id, event_type, description, created_by)
            VALUES (%s, %s, %s, %s)
            RETURNING id;
            """,
            (labkit_id, event_type, description, created_by),
        )
        new_id = cur.fetchone()[0]
        conn.commit()
        return new_id
    finally:
        cur.close()
        conn.close()


def list_labkit_events(labkit_id: int) -> List[Dict[str, Any]]:
    """Return labkit events ordered by newest first."""
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT id, labkit_id, event_type, description, created_at, created_by
            FROM labkit_event
            WHERE labkit_id = %s
            ORDER BY created_at DESC, id DESC;
            """,
            (labkit_id,),
        )
        return _rows_to_dicts(cur)
    finally:
        cur.close()
        conn.close()


def inventory_overview(
    site_filter: Optional[int] = None, kit_type_filter: Optional[int] = None
) -> List[Dict[str, Any]]:
    """Aggregate available labkits by site and kit type."""
    conn = get_connection()
    cur = conn.cursor()
    try:
        conditions = ["l.status IN ('ready_to_ship', 'shipped', 'at_site')"]
        params: List[Any] = []
        if site_filter is not None:
            if site_filter == 0:
                conditions.append("l.site_id IS NULL")
            else:
                conditions.append("l.site_id = %s")
                params.append(site_filter)
        if kit_type_filter is not None:
            conditions.append("l.labkit_type_id = %s")
            params.append(kit_type_filter)

        where_clause = ""
        if conditions:
            where_clause = "WHERE " + " AND ".join(conditions)

        query = f"""
            SELECT
                COALESCE(s.site_name, 'Central depot') AS site_name,
                lt.name AS labkit_type_name,
                COUNT(*) AS available_count
            FROM labkit l
            LEFT JOIN site s ON l.site_id = s.id
            LEFT JOIN labkit_type lt ON l.labkit_type_id = lt.id
            {where_clause}
            GROUP BY COALESCE(s.site_name, 'Central depot'), lt.name
            ORDER BY site_name, labkit_type_name;
        """
        cur.execute(query, params)
        return _rows_to_dicts(cur)
    finally:
        cur.close()
        conn.close()


# Shipment-related helpers
def list_unassigned_labkits() -> List[Dict[str, Any]]:
    """Labkits without a shipment assignment."""
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT l.id, l.kit_barcode, t.name AS labkit_type_name, l.status
            FROM labkit l
            LEFT JOIN labkit_type t ON l.labkit_type_id = t.id
            WHERE l.shipment_id IS NULL
            ORDER BY l.id;
            """
        )
        return _rows_to_dicts(cur)
    finally:
        cur.close()
        conn.close()


def list_shipments() -> List[Dict[str, Any]]:
    """List shipments with basic info and site name."""
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT sh.id, sh.site_id, s.site_name, sh.shipped_at, sh.expected_arrival,
                   sh.carrier, sh.tracking_number, sh.status
            FROM shipment sh
            LEFT JOIN site s ON sh.site_id = s.id
            ORDER BY sh.id;
            """
        )
        return _rows_to_dicts(cur)
    finally:
        cur.close()
        conn.close()


def get_shipment(shipment_id: int) -> Optional[Dict[str, Any]]:
    """Return shipment detail with assigned labkits."""
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT sh.id, sh.site_id, s.site_name, sh.shipped_at, sh.expected_arrival,
                   sh.carrier, sh.tracking_number, sh.status
            FROM shipment sh
            LEFT JOIN site s ON sh.site_id = s.id
            WHERE sh.id = %s;
            """,
            (shipment_id,),
        )
        shipment_row = cur.fetchone()
        if not shipment_row:
            return None
        shipment = dict(zip([d[0] for d in cur.description], shipment_row))

        cur.execute(
            """
            SELECT l.id, l.kit_barcode, t.name AS labkit_type_name, l.status
            FROM labkit l
            LEFT JOIN labkit_type t ON l.labkit_type_id = t.id
            WHERE l.shipment_id = %s
            ORDER BY l.id;
            """,
            (shipment_id,),
        )
        shipment["labkits"] = _rows_to_dicts(cur)
        return shipment
    finally:
        cur.close()
        conn.close()


def add_shipment(
    site_id: Optional[int],
    shipped_at,
    expected_arrival,
    carrier: Optional[str],
    tracking_number: Optional[str],
    status: Optional[str],
) -> int:
    """Create a shipment and return its id."""
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            INSERT INTO shipment (site_id, shipped_at, expected_arrival, carrier, tracking_number, status)
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING id;
            """,
            (site_id, shipped_at, expected_arrival, carrier, tracking_number, status),
        )
        shipment_id = cur.fetchone()[0]
        conn.commit()
        return shipment_id
    finally:
        cur.close()
        conn.close()


def set_shipment_labkits(shipment_id: int, labkit_ids: List[int], created_by: Optional[str] = None) -> None:
    """Assign a set of labkits to a shipment (clears previous assignments for this shipment)."""
    conn = get_connection()
    cur = conn.cursor()
    try:
        # Find currently assigned labkits for this shipment
        cur.execute("SELECT id, kit_barcode FROM labkit WHERE shipment_id = %s;", (shipment_id,))
        existing = _rows_to_dicts(cur)
        existing_ids = {row["id"] for row in existing}

        # Compute removals and additions
        new_ids = set(labkit_ids)
        to_remove = existing_ids - new_ids
        to_add = new_ids - existing_ids

        # Clear removed
        if to_remove:
            cur.execute(
                "UPDATE labkit SET shipment_id = NULL WHERE id = ANY(%s);",
                (list(to_remove),),
            )
            for lid in to_remove:
                try:
                    add_labkit_event(
                        lid,
                        "removed_from_shipment",
                        f"Removed from shipment {shipment_id}",
                        created_by=created_by or "system",
                    )
                except Exception:
                    pass

        # Assign added
        if to_add:
            cur.execute(
                "UPDATE labkit SET shipment_id = %s WHERE id = ANY(%s);",
                (shipment_id, list(to_add)),
            )
            # Need barcodes for events
            cur.execute(
                "SELECT id FROM labkit WHERE id = ANY(%s);",
                (list(to_add),),
            )
            for row in cur.fetchall():
                lid = row[0]
                try:
                    add_labkit_event(
                        lid,
                        "added_to_shipment",
                        f"Added to shipment {shipment_id}",
                        created_by=created_by or "system",
                    )
                except Exception:
                    pass

        conn.commit()
    finally:
        cur.close()
        conn.close()


def update_shipment(
    shipment_id: int,
    site_id: Optional[int],
    shipped_at,
    expected_arrival,
    carrier: Optional[str],
    tracking_number: Optional[str],
    status: Optional[str],
    labkit_ids: List[int],
) -> None:
    """Update shipment details and labkit assignments."""
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            UPDATE shipment
            SET site_id = %s,
                shipped_at = %s,
                expected_arrival = %s,
                carrier = %s,
                tracking_number = %s,
                status = %s
            WHERE id = %s;
            """,
            (site_id, shipped_at, expected_arrival, carrier, tracking_number, status, shipment_id),
        )
        # Clear existing links for this shipment
        cur.execute("UPDATE labkit SET shipment_id = NULL WHERE shipment_id = %s;", (shipment_id,))
        if labkit_ids:
            cur.execute(
                "UPDATE labkit SET shipment_id = %s WHERE id = ANY(%s);",
                (shipment_id, labkit_ids),
            )
        conn.commit()
    finally:
        cur.close()
        conn.close()


# User helpers
def get_user_by_username(username: str) -> Optional[Dict[str, Any]]:
    """Fetch a user by username."""
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT id, username, password_hash, role
            FROM app_user
            WHERE username = %s;
            """,
            (username,),
        )
        row = cur.fetchone()
        if not row:
            return None
        columns = [desc[0] for desc in cur.description]
        return dict(zip(columns, row))
    finally:
        cur.close()
        conn.close()


def ensure_default_users() -> None:
    """Create default users if they do not already exist."""
    defaults = [
        ("Juliane", "Juliane1234", "admin"),
        ("Nina", "Nina123", "coordinator"),
        ("Adrian", "Adrian123", "admin"),
        ("Maren", "Maren123", "coordinator"),
    ]
    conn = get_connection()
    cur = conn.cursor()
    try:
        for username, password, role in defaults:
            cur.execute(
                """
                INSERT INTO app_user (username, password_hash, role)
                VALUES (%s, %s, %s)
                ON CONFLICT (username) DO NOTHING;
                """,
                (username, generate_password_hash(password, method="pbkdf2:sha256"), role),
            )
        conn.commit()
    finally:
        cur.close()
        conn.close()
