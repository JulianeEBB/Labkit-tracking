from datetime import date, datetime, timedelta
import csv
from functools import wraps
import base64
import io

from flask import Flask, redirect, render_template_string, request, session, url_for
from flask import Response
from werkzeug.security import check_password_hash
import psycopg2
import qrcode

from init_db import initialize_database
from models import AuditLog, SessionLocal
from audit import log_audit_event
from labkit_repo import (
    add_labkit,
    add_labkit_type,
    add_site,
    add_site_contact,
    add_shipment,
    add_labkit_event,
    delete_labkit,
    delete_labkit_type,
    delete_site,
    delete_site_contact,
    get_labkit_by_barcode,
    get_labkit_by_id,
    get_labkit_detail,
    get_user_by_username,
    list_labkit_events,
    get_shipment,
    get_site,
    get_status_history,
    list_labkits,
    list_shipments,
    list_labkit_types,
    ensure_default_users,
    list_site_contacts,
    list_sites,
    list_unassigned_labkits,
    list_labkits_with_names,
    list_shipments_with_counts,
    inventory_overview,
    set_shipment_labkits,
    update_labkit,
    update_labkit_type,
    update_labkit_status,
    update_site,
    update_site_contact,
    update_shipment,
)

# Initialize tables on app startup (Flask 3+ removed before_first_request)
initialize_database()
ensure_default_users()

app = Flask(__name__)
app.secret_key = "change-me-later"


def current_username() -> str:
    """Return the current logged in username or a fallback."""
    return session.get("username") or "system"


def login_required(view_func):
    """Simple decorator to require login for a route."""

    @wraps(view_func)
    def wrapper(*args, **kwargs):
        if not session.get("username"):
            return redirect(url_for("login", next=request.url))
        return view_func(*args, **kwargs)

    return wrapper


@app.before_request
def require_login():
    """Redirect to login if not authenticated, except for public endpoints."""
    public_endpoints = {"login", "logout", "static"}
    if request.endpoint in public_endpoints or request.endpoint is None:
        return
    if not session.get("username"):
        return redirect(url_for("login", next=request.url))

# Config
EXPIRY_WARNING_DAYS = 60

def parse_date(date_str: str):
    """Parse YYYY-MM-DD string to datetime.date or return None."""
    if not date_str:
        return None
    try:
        return datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        return None


def parse_datetime(dt_str: str):
    """Parse ISO-like datetime string to datetime or return None."""
    if not dt_str:
        return None
    try:
        return datetime.fromisoformat(dt_str)
    except ValueError:
        return None


@app.route("/login", methods=["GET", "POST"])
def login():
    error = ""
    next_url = request.args.get("next") or url_for("index")
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        user = get_user_by_username(username) if username else None
        if user and check_password_hash(user["password_hash"], password):
            session["username"] = user["username"]
            session["role"] = user["role"]
            return redirect(next_url)
        error = "Invalid username or password"
    return render_template_string(
        LOGIN_TEMPLATE,
        nav_active="login",
        error=error,
    )


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/")
@login_required
def index():
    message = request.args.get("message", "")
    status_error = request.args.get("error", "")
    history_barcode = request.args.get("history_barcode", "")
    history = get_status_history(history_barcode) if history_barcode else []
    all_kits = list_labkits()
    today = date.today()
    warning_cutoff = today + timedelta(days=EXPIRY_WARNING_DAYS)
    expiring_soon = [
        k
        for k in all_kits
        if k.get("expiry_date") and today <= k["expiry_date"] <= warning_cutoff
    ]
    inventory_rows = inventory_overview()
    return render_template_string(
        TEMPLATE,
        message=message,
        error=status_error,
        nav_active="home",
        sites=list_sites(),
        labkit_types=list_labkit_types(),
        labkits=all_kits,
        history_barcode=history_barcode,
        history=history,
        expiring_count=len(expiring_soon),
        warning_days=EXPIRY_WARNING_DAYS,
        inventory_rows=inventory_rows,
        current_user=session.get("username"),
    )


@app.route("/kit-types")
@login_required
def kit_types():
    message = request.args.get("message", "")
    error = request.args.get("error", "")
    return render_template_string(
        KIT_TYPES_TEMPLATE,
        nav_active="kit_types",
        message=message,
        error=error,
        labkit_types=list_labkit_types(),
        current_user=session.get("username"),
    )


@app.route("/sites")
@login_required
def sites_page():
    message = request.args.get("message", "")
    error = request.args.get("error", "")
    return render_template_string(
        SITES_TEMPLATE,
        nav_active="sites",
        message=message,
        error=error,
        sites=list_sites(),
        current_user=session.get("username"),
    )


@app.route("/sites/<int:site_id>")
@login_required
def site_detail(site_id: int):
    site = get_site(site_id)
    if not site:
        return redirect(url_for("sites_page", error="Site not found"))
    message = request.args.get("message", "")
    error = request.args.get("error", "")
    return render_template_string(
        SITE_DETAIL_TEMPLATE,
        nav_active="sites",
        message=message,
        error=error,
        site=site,
        contacts=list_site_contacts(site_id),
        current_user=session.get("username"),
    )


@app.route("/shipments")
@login_required
def shipments_page():
    message = request.args.get("message", "")
    error = request.args.get("error", "")
    return render_template_string(
        SHIPMENTS_TEMPLATE,
        nav_active="shipments",
        message=message,
        error=error,
        shipments=list_shipments(),
        sites=list_sites(),
        unassigned_labkits=list_unassigned_labkits(),
        status_options=["planned", "packed", "shipped", "delivered", "lost", "returned", "canceled"],
        current_user=session.get("username"),
    )


@app.route("/shipments/<int:shipment_id>")
@login_required
def shipment_detail(shipment_id: int):
    shipment = get_shipment(shipment_id)
    if not shipment:
        return redirect(url_for("shipments_page", error="Shipment not found"))
    message = request.args.get("message", "")
    error = request.args.get("error", "")
    # labkits available for assignment: unassigned + already assigned to this shipment
    unassigned = list_unassigned_labkits()
    assigned = shipment.get("labkits", [])
    combined_options = assigned + [lk for lk in unassigned if lk["id"] not in {a["id"] for a in assigned}]
    selected_ids = {a["id"] for a in assigned}
    return render_template_string(
        SHIPMENT_DETAIL_TEMPLATE,
        nav_active="shipments",
        message=message,
        error=error,
        shipment=shipment,
        labkit_options=combined_options,
        selected_labkit_ids=selected_ids,
        status_options=["planned", "packed", "shipped", "delivered", "lost", "returned", "canceled"],
        sites=list_sites(),
        current_user=session.get("username"),
    )


@app.route("/inventory")
@login_required
def inventory_page():
    site_param = request.args.get("site_id", "").strip()
    kit_type_param = request.args.get("kit_type_id", "").strip()

    site_filter = None
    if site_param:
        if site_param == "none":
            site_filter = 0  # special value meaning central depot
        else:
            try:
                site_filter = int(site_param)
            except ValueError:
                site_filter = None

    kit_type_filter = None
    if kit_type_param:
        try:
            kit_type_filter = int(kit_type_param)
        except ValueError:
            kit_type_filter = None

    data = inventory_overview(site_filter, kit_type_filter)
    return render_template_string(
        INVENTORY_TEMPLATE,
        nav_active="inventory",
        site_id=site_param,
        kit_type_id=kit_type_param,
        sites=list_sites(),
        labkit_types=list_labkit_types(),
        rows=data,
        current_user=session.get("username"),
    )


@app.route("/expiry")
@login_required
def expiry_page():
    all_kits = list_labkits()
    today = date.today()
    warning_cutoff = today + timedelta(days=EXPIRY_WARNING_DAYS)

    expired = []
    soon = []
    fine = []
    for k in all_kits:
        exp = k.get("expiry_date")
        if not exp:
            fine.append(k)
            continue
        if exp < today:
            expired.append(k)
        elif exp <= warning_cutoff:
            soon.append(k)
        else:
            fine.append(k)

    return render_template_string(
        EXPIRY_TEMPLATE,
        nav_active="expiry",
        expired=expired,
        soon=soon,
        fine=fine,
        warning_days=EXPIRY_WARNING_DAYS,
        current_user=session.get("username"),
    )


@app.route("/labkits")
@login_required
def labkits_page():
    message = request.args.get("message", "")
    error = request.args.get("error", "")
    return render_template_string(
        LABKITS_TEMPLATE,
        nav_active="labkits",
        message=message,
        error=error,
        labkits=list_labkits(),
        labkit_types=list_labkit_types(),
        sites=list_sites(),
        status_options=[
            "planned",
            "packed",
            "ready_to_ship",
            "shipped",
            "at_site",
            "used",
            "returned",
            "destroyed",
        ],
        current_user=session.get("username"),
    )


@app.route("/labkits/<int:labkit_id>")
@login_required
def labkit_detail_page(labkit_id: int):
    labkit = get_labkit_detail(labkit_id)
    if not labkit:
        return redirect(url_for("labkits_page", error="Labkit not found"))
    message = request.args.get("message", "")
    error = request.args.get("error", "")
    db_session = SessionLocal()
    try:
        audit_entries = (
            db_session.query(AuditLog)
            .filter_by(entity_type="Labkit", entity_id=labkit_id)
            .order_by(AuditLog.timestamp.desc(), AuditLog.id.desc())
            .all()
        )
    finally:
        db_session.close()
    return render_template_string(
        LABKIT_DETAIL_TEMPLATE,
        nav_active="labkits",
        message=message,
        error=error,
        labkit=labkit,
        events=list_labkit_events(labkit_id),
        audit_entries=audit_entries,
        current_user=session.get("username"),
    )


@app.route("/kit-types/add", methods=["POST"])
def handle_add_kit_type():
    name = request.form.get("name", "").strip()
    description = request.form.get("description", "").strip() or None
    default_expiry_days_raw = request.form.get("default_expiry_days", "").strip()
    default_expiry_days = int(default_expiry_days_raw) if default_expiry_days_raw else None
    if not name:
        return redirect(url_for("kit_types", error="Name is required"))
    add_labkit_type(name, description, default_expiry_days)
    return redirect(url_for("kit_types", message=f"Kit type '{name}' saved."))


@app.route("/kit-types/update", methods=["POST"])
def handle_update_kit_type():
    try:
        kit_type_id = int(request.form.get("id", "0"))
    except ValueError:
        return redirect(url_for("kit_types", error="Invalid kit type id"))
    name = request.form.get("name", "").strip()
    description = request.form.get("description", "").strip() or None
    default_expiry_days_raw = request.form.get("default_expiry_days", "").strip()
    default_expiry_days = int(default_expiry_days_raw) if default_expiry_days_raw else None
    if not kit_type_id or not name:
        return redirect(url_for("kit_types", error="Name and id are required"))
    update_labkit_type(kit_type_id, name, description, default_expiry_days)
    return redirect(url_for("kit_types", message=f"Kit type '{name}' updated."))


@app.route("/kit-types/delete", methods=["POST"])
def handle_delete_kit_type():
    try:
        kit_type_id = int(request.form.get("id", "0"))
    except ValueError:
        return redirect(url_for("kit_types", error="Invalid kit type id"))
    delete_labkit_type(kit_type_id)
    return redirect(url_for("kit_types", message="Kit type deleted."))


@app.route("/sites/add", methods=["POST"])
def handle_add_site_page():
    site_code = request.form.get("site_code", "").strip()
    site_name = request.form.get("site_name", "").strip()
    address_line1 = request.form.get("address_line1", "").strip() or None
    address_line2 = request.form.get("address_line2", "").strip() or None
    city = request.form.get("city", "").strip() or None
    state = request.form.get("state", "").strip() or None
    postal_code = request.form.get("postal_code", "").strip() or None
    country = request.form.get("country", "").strip() or None
    if not site_code or not site_name:
        return redirect(url_for("sites_page", error="Site code and name are required"))
    try:
        site_id = add_site(site_code, site_name)
    except psycopg2.IntegrityError:
        return redirect(url_for("sites_page", error="Site code already exists"))
    update_site(
        site_id=site_id,
        site_code=site_code,
        site_name=site_name,
        address_line1=address_line1,
        address_line2=address_line2,
        city=city,
        state=state,
        postal_code=postal_code,
        country=country,
    )
    return redirect(url_for("sites_page", message="Site created."))


@app.route("/sites/update", methods=["POST"])
def handle_update_site():
    try:
        site_id = int(request.form.get("id", "0"))
    except ValueError:
        return redirect(url_for("sites_page", error="Invalid site id"))
    site_code = request.form.get("site_code", "").strip()
    site_name = request.form.get("site_name", "").strip()
    address_line1 = request.form.get("address_line1", "").strip() or None
    address_line2 = request.form.get("address_line2", "").strip() or None
    city = request.form.get("city", "").strip() or None
    state = request.form.get("state", "").strip() or None
    postal_code = request.form.get("postal_code", "").strip() or None
    country = request.form.get("country", "").strip() or None
    if not site_id or not site_code or not site_name:
        return redirect(url_for("sites_page", error="Site id, code, and name are required"))
    update_site(
        site_id=site_id,
        site_code=site_code,
        site_name=site_name,
        address_line1=address_line1,
        address_line2=address_line2,
        city=city,
        state=state,
        postal_code=postal_code,
        country=country,
    )
    return redirect(url_for("sites_page", message="Site updated."))


@app.route("/sites/delete", methods=["POST"])
def handle_delete_site():
    try:
        site_id = int(request.form.get("id", "0"))
    except ValueError:
        return redirect(url_for("sites_page", error="Invalid site id"))
    delete_site(site_id)
    return redirect(url_for("sites_page", message="Site deleted."))


@app.route("/sites/<int:site_id>/contacts/add", methods=["POST"])
def handle_add_contact(site_id: int):
    site = get_site(site_id)
    if not site:
        return redirect(url_for("sites_page", error="Site not found"))
    name = request.form.get("name", "").strip()
    role = request.form.get("role", "").strip() or None
    email = request.form.get("email", "").strip() or None
    phone = request.form.get("phone", "").strip() or None
    is_primary = bool(request.form.get("is_primary"))
    if not name:
        return redirect(url_for("site_detail", site_id=site_id, error="Name is required"))
    add_site_contact(site_id, name, role, email, phone, is_primary)
    return redirect(url_for("site_detail", site_id=site_id, message="Contact added."))


@app.route("/sites/<int:site_id>/contacts/update", methods=["POST"])
def handle_update_contact(site_id: int):
    site = get_site(site_id)
    if not site:
        return redirect(url_for("sites_page", error="Site not found"))
    try:
        contact_id = int(request.form.get("id", "0"))
    except ValueError:
        return redirect(url_for("site_detail", site_id=site_id, error="Invalid contact id"))
    name = request.form.get("name", "").strip()
    role = request.form.get("role", "").strip() or None
    email = request.form.get("email", "").strip() or None
    phone = request.form.get("phone", "").strip() or None
    is_primary = bool(request.form.get("is_primary"))
    if not name:
        return redirect(url_for("site_detail", site_id=site_id, error="Name is required"))
    update_site_contact(contact_id, name, role, email, phone, is_primary)
    return redirect(url_for("site_detail", site_id=site_id, message="Contact updated."))


@app.route("/sites/<int:site_id>/contacts/delete", methods=["POST"])
def handle_delete_contact(site_id: int):
    site = get_site(site_id)
    if not site:
        return redirect(url_for("sites_page", error="Site not found"))
    try:
        contact_id = int(request.form.get("id", "0"))
    except ValueError:
        return redirect(url_for("site_detail", site_id=site_id, error="Invalid contact id"))
    delete_site_contact(contact_id)
    return redirect(url_for("site_detail", site_id=site_id, message="Contact deleted."))


@app.route("/shipments/add", methods=["POST"])
def handle_add_shipment():
    site_id_raw = request.form.get("site_id", "").strip()
    site_id = int(site_id_raw) if site_id_raw else None
    shipped_at = parse_datetime(request.form.get("shipped_at", "").strip())
    expected_arrival = parse_date(request.form.get("expected_arrival", "").strip())
    carrier = request.form.get("carrier", "").strip() or None
    tracking_number = request.form.get("tracking_number", "").strip() or None
    status = request.form.get("status", "").strip() or None
    labkit_ids = [int(x) for x in request.form.getlist("labkit_ids") if x]

    shipment_id = add_shipment(
        site_id=site_id,
        shipped_at=shipped_at,
        expected_arrival=expected_arrival,
        carrier=carrier,
        tracking_number=tracking_number,
        status=status,
    )
    set_shipment_labkits(shipment_id, labkit_ids, created_by=current_username())
    if status == "shipped":
        shipment = get_shipment(shipment_id) or {}
        for lk in shipment.get("labkits", []):
            try:
                update_labkit_status(lk["kit_barcode"], "shipped", created_by=current_username())
            except ValueError:
                continue
    else:
        # Log assignment changes already handled; add shipment update event
        for lid in labkit_ids:
            try:
                add_labkit_event(
                    lid,
                    "shipment_updated",
                    f"Shipment {shipment_id} updated",
                    created_by=current_username(),
                )
            except Exception:
                pass
    return redirect(url_for("shipments_page", message="Shipment created."))


@app.route("/shipments/<int:shipment_id>/update", methods=["POST"])
def handle_update_shipment(shipment_id: int):
    shipped_at = parse_datetime(request.form.get("shipped_at", "").strip())
    expected_arrival = parse_date(request.form.get("expected_arrival", "").strip())
    carrier = request.form.get("carrier", "").strip() or None
    tracking_number = request.form.get("tracking_number", "").strip() or None
    status = request.form.get("status", "").strip() or None
    site_id_raw = request.form.get("site_id", "").strip()
    site_id = int(site_id_raw) if site_id_raw else None
    labkit_ids = [int(x) for x in request.form.getlist("labkit_ids") if x]

    update_shipment(
        shipment_id=shipment_id,
        site_id=site_id,
        shipped_at=shipped_at,
        expected_arrival=expected_arrival,
        carrier=carrier,
        tracking_number=tracking_number,
        status=status,
        labkit_ids=labkit_ids,
    )
    if status == "shipped":
        shipment = get_shipment(shipment_id) or {}
        for lk in shipment.get("labkits", []):
            try:
                update_labkit_status(lk["kit_barcode"], "shipped", created_by=current_username())
            except ValueError:
                continue
    else:
        for lid in labkit_ids:
            try:
                add_labkit_event(
                    lid,
                    "shipment_updated",
                    f"Shipment {shipment_id} updated",
                    created_by=current_username(),
                )
            except Exception:
                pass
    return redirect(url_for("shipment_detail", shipment_id=shipment_id, message="Shipment updated."))


def _qr_data_uri(text: str) -> str:
    """Generate a QR code as data URI for inline display."""
    img = qrcode.make(text)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    encoded = base64.b64encode(buf.getvalue()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


@app.route("/labkits/<int:labkit_id>/label")
def labkit_label(labkit_id: int):
    labkit = get_labkit_detail(labkit_id)
    if not labkit:
        return redirect(url_for("labkits_page", error="Labkit not found"))
    absolute_url = url_for("labkit_label", labkit_id=labkit_id, _external=True)
    qr_payload = labkit.get("kit_barcode") or ""
    if absolute_url:
        qr_payload = f"{labkit.get('kit_barcode','')}|{absolute_url}"
    qr_uri = _qr_data_uri(qr_payload)
    return render_template_string(
        LABKIT_LABEL_TEMPLATE,
        nav_active="labkits",
        labkit=labkit,
        qr_uri=qr_uri,
    )


@app.route("/labkits/add", methods=["POST"])
def handle_add_labkit():
    barcode = request.form.get("kit_barcode", "").strip()
    labkit_type_id = request.form.get("labkit_type_id", "").strip()
    site_id = request.form.get("site_id", "").strip()
    lot_number = request.form.get("lot_number", "").strip() or None
    expiry_date_str = request.form.get("expiry_date", "").strip()
    status = request.form.get("status", "").strip() or "planned"
    expiry = parse_date(expiry_date_str)
    if not barcode or not labkit_type_id:
        return redirect(url_for("labkits_page", error="Barcode and labkit type are required"))
    new_id = add_labkit(
        kit_barcode=barcode,
        labkit_type_id=int(labkit_type_id),
        site_id=int(site_id) if site_id else None,
        lot_number=lot_number,
        expiry_date=expiry,
        created_by=current_username(),
    )
    db_session = SessionLocal()
    try:
        log_audit_event(
            db_session,
            entity_type="Labkit",
            entity_id=new_id,
            action="CREATE",
            description=f"Labkit {barcode} created",
        )
        db_session.commit()
    finally:
        db_session.close()
    # Update status if different than default
    if status and status != "planned":
        update_labkit_status(barcode, status, created_by=current_username())
    return redirect(url_for("labkits_page", message=f"Labkit '{barcode}' added."))


@app.route("/labkits/update", methods=["POST"])
def handle_update_labkit():
    try:
        labkit_id = int(request.form.get("id", "0"))
    except ValueError:
        return redirect(url_for("labkits_page", error="Invalid labkit id"))
    existing = get_labkit_by_id(labkit_id)
    if not existing:
        return redirect(url_for("labkits_page", error="Labkit not found"))

    barcode = request.form.get("kit_barcode", "").strip()
    labkit_type_id = request.form.get("labkit_type_id", "").strip()
    site_id = request.form.get("site_id", "").strip()
    lot_number = request.form.get("lot_number", "").strip() or None
    expiry_date_str = request.form.get("expiry_date", "").strip()
    status = request.form.get("status", "").strip() or existing["status"]
    expiry = parse_date(expiry_date_str)
    if not barcode or not labkit_type_id:
        return redirect(url_for("labkits_page", error="Barcode and labkit type are required"))
    update_labkit(
        labkit_id=labkit_id,
        kit_barcode=barcode,
        labkit_type_id=int(labkit_type_id),
        site_id=int(site_id) if site_id else None,
        lot_number=lot_number,
        expiry_date=expiry,
        status=status,
    )
    # also record status event if changed
    db_session = SessionLocal()
    try:
        log_audit_event(
            db_session,
            entity_type="Labkit",
            entity_id=labkit_id,
            action="UPDATE",
            description=f"Labkit {barcode} updated",
        )
        if status != existing["status"]:
            log_audit_event(
                db_session,
                entity_type="Labkit",
                entity_id=labkit_id,
                action="STATUS_CHANGE",
                field_name="status",
                old_value=existing["status"],
                new_value=status,
                description=f"Status changed from {existing['status']} to {status}",
            )
        db_session.commit()
    finally:
        db_session.close()
    if status != existing["status"]:
        update_labkit_status(barcode, status, created_by=current_username())
    else:
        try:
            add_labkit_event(labkit_id, "updated", "Labkit updated", created_by=current_username())
        except Exception:
            pass
    return redirect(url_for("labkits_page", message=f"Labkit '{barcode}' updated."))


@app.route("/labkits/delete", methods=["POST"])
def handle_delete_labkit():
    try:
        labkit_id = int(request.form.get("id", "0"))
    except ValueError:
        return redirect(url_for("labkits_page", error="Invalid labkit id"))
    labkit = get_labkit_by_id(labkit_id)
    if not labkit:
        return redirect(url_for("labkits_page", error="Labkit not found"))
    db_session = SessionLocal()
    try:
        log_audit_event(
            db_session,
            entity_type="Labkit",
            entity_id=labkit_id,
            action="DELETE",
            description=f"Labkit {labkit.get('kit_barcode')} deleted",
        )
        db_session.commit()
    finally:
        db_session.close()
    delete_labkit(labkit_id)
    return redirect(url_for("labkits_page", message="Labkit deleted."))


@app.route("/add_site", methods=["POST"])
def handle_add_site():
    site_code = request.form.get("site_code", "").strip()
    site_name = request.form.get("site_name", "").strip()
    if not site_code or not site_name:
        return redirect(url_for("index", error="Site code and name are required"))
    try:
        add_site(site_code, site_name)
        msg = f"Site '{site_code}' added."
    except psycopg2.IntegrityError:
        msg = f"Site '{site_code}' already exists."
    return redirect(url_for("index", message=msg))


@app.route("/add_labkit_type", methods=["POST"])
def handle_add_labkit_type():
    name = request.form.get("name", "").strip()
    description = request.form.get("description", "").strip() or None
    if not name:
        return redirect(url_for("index", error="Labkit type name is required"))
    try:
        add_labkit_type(name, description)
        msg = f"Labkit type '{name}' added."
    except psycopg2.IntegrityError:
        msg = f"Labkit type '{name}' already exists."
    return redirect(url_for("index", message=msg))


@app.route("/add_labkit", methods=["POST"])
def handle_add_labkit_legacy():
    """Legacy endpoint retained for compatibility; redirects to /labkits."""
    return redirect(url_for("labkits_page"))


@app.route("/update_status", methods=["POST"])
def handle_update_status():
    barcode = request.form.get("barcode", "").strip()
    new_status = request.form.get("new_status", "").strip()
    if not barcode or not new_status:
        return redirect(url_for("index", error="Barcode and new status are required"))
    try:
        existing = get_labkit_by_barcode(barcode)
        update_labkit_status(barcode, new_status, created_by=current_username())
        if existing:
            db_session = SessionLocal()
            try:
                log_audit_event(
                    db_session,
                    entity_type="Labkit",
                    entity_id=existing["id"],
                    action="STATUS_CHANGE",
                    field_name="status",
                    old_value=existing["status"],
                    new_value=new_status,
                    description=f"Status changed from {existing['status']} to {new_status}",
                )
                db_session.commit()
            finally:
                db_session.close()
        msg = f"Status for '{barcode}' updated to '{new_status}'."
        return redirect(url_for("index", message=msg, history_barcode=barcode))
    except ValueError as exc:
        return redirect(url_for("index", error=str(exc)))


@app.route("/labkits/<int:labkit_id>/events/add", methods=["POST"])
def handle_add_labkit_note(labkit_id: int):
    labkit = get_labkit_detail(labkit_id)
    if not labkit:
        return redirect(url_for("labkits_page", error="Labkit not found"))
    note = request.form.get("description", "").strip()
    if note:
        add_labkit_event(labkit_id, "note", note, created_by=current_username())
    return redirect(url_for("labkit_detail_page", labkit_id=labkit_id, message="Note added."))


@app.route("/export/labkits")
def export_labkits():
    rows = list_labkits_with_names()
    headers = [
        "kit_barcode",
        "labkit_type",
        "site",
        "status",
        "lot_number",
        "expiry_date",
        "created_at",
    ]
    lines = [",".join(headers)]
    for r in rows:
        values = [
            r.get("kit_barcode") or "",
            r.get("labkit_type_name") or "",
            r.get("site_name") or "",
            r.get("status") or "",
            r.get("lot_number") or "",
            str(r.get("expiry_date") or ""),
            str(r.get("created_at") or ""),
        ]
        # naive CSV escaping for commas/quotes
        escaped = []
        for v in values:
            v = v.replace('"', '""')
            if "," in v or '"' in v:
                v = f'"{v}"'
            escaped.append(v)
        lines.append(",".join(escaped))
    csv_data = "\n".join(lines)
    return Response(
        csv_data,
        mimetype="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=labkits.csv"},
    )


@app.route("/export/shipments")
def export_shipments():
    rows = list_shipments_with_counts()
    headers = [
        "id",
        "site",
        "shipped_at",
        "carrier",
        "tracking_number",
        "status",
        "number_of_kits",
    ]
    lines = [",".join(headers)]
    for r in rows:
        values = [
            str(r.get("id") or ""),
            r.get("site_name") or "",
            str(r.get("shipped_at") or ""),
            r.get("carrier") or "",
            r.get("tracking_number") or "",
            r.get("status") or "",
            str(r.get("number_of_kits") or "0"),
        ]
        escaped = []
        for v in values:
            v = v.replace('"', '""')
            if "," in v or '"' in v:
                v = f'"{v}"'
            escaped.append(v)
        lines.append(",".join(escaped))
    csv_data = "\n".join(lines)
    return Response(
        csv_data,
        mimetype="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=shipments.csv"},
    )


@app.route("/history", methods=["POST"])
def handle_history():
    barcode = request.form.get("history_barcode", "").strip()
    return redirect(url_for("index", history_barcode=barcode))


@app.route("/export/audit")
@login_required
def export_audit():
    """Export audit log as CSV with optional filters."""
    from_date_str = request.args.get("from_date", "").strip()
    to_date_str = request.args.get("to_date", "").strip()
    entity_type_filter = request.args.get("entity_type", "").strip()

    from_date_val = parse_date(from_date_str)
    to_date_val = parse_date(to_date_str)

    db_session = SessionLocal()
    try:
        query = db_session.query(AuditLog)
        if entity_type_filter:
            query = query.filter(AuditLog.entity_type == entity_type_filter)
        if from_date_val:
            start_dt = datetime.combine(from_date_val, datetime.min.time())
            query = query.filter(AuditLog.timestamp >= start_dt)
        if to_date_val:
            end_dt = datetime.combine(to_date_val + timedelta(days=1), datetime.min.time())
            query = query.filter(AuditLog.timestamp < end_dt)
        query = query.order_by(AuditLog.timestamp.desc(), AuditLog.id.desc())
        rows = query.all()
    finally:
        db_session.close()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(
        ["timestamp", "user", "entity_type", "entity_id", "action", "field_name", "old_value", "new_value", "description"]
    )
    for r in rows:
        writer.writerow(
            [
                r.timestamp,
                r.user or "",
                r.entity_type,
                r.entity_id,
                r.action,
                r.field_name or "",
                r.old_value or "",
                r.new_value or "",
                r.description or "",
            ]
        )
    csv_data = output.getvalue()
    return Response(
        csv_data,
        mimetype="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=audit_log.csv"},
    )


TEMPLATE = """
<!doctype html>
<html>
<head>
  <title>Lab Kit Tracking</title>
  <link rel="stylesheet" href="{{ url_for('static', filename='style.css') }}">
</head>
<body>
  <header class="topbar">
    <div class="nav-inner">
      <div class="brand">Lab Kit Tracking</div>
      <nav class="nav-links">
        <a href="{{ url_for('index') }}" class="nav-link {{ 'active' if nav_active == 'home' else '' }}"><span class="icon">📊</span>Home</a>
        <a href="{{ url_for('labkits_page') }}" class="nav-link {{ 'active' if nav_active == 'labkits' else '' }}"><span class="icon">📦</span>Labkits</a>
        <a href="{{ url_for('kit_types') }}" class="nav-link {{ 'active' if nav_active == 'kit_types' else '' }}"><span class="icon">🧪</span>Kit Types</a>
        <a href="{{ url_for('sites_page') }}" class="nav-link {{ 'active' if nav_active == 'sites' else '' }}"><span class="icon">🏥</span>Sites</a>
        <a href="{{ url_for('shipments_page') }}" class="nav-link {{ 'active' if nav_active == 'shipments' else '' }}"><span class="icon">🚚</span>Shipments</a>
        <a href="{{ url_for('inventory_page') }}" class="nav-link {{ 'active' if nav_active == 'inventory' else '' }}"><span class="icon">📋</span>Inventory</a>
        <a href="{{ url_for('expiry_page') }}" class="nav-link {{ 'active' if nav_active == 'expiry' else '' }}"><span class="icon">⏳</span>Expiry</a>
      </nav>
      <div class="user-chip">
        <span class="pill muted">Logged in as {{ current_user }}</span>
        <a href="{{ url_for('logout') }}" class="nav-link logout">Logout</a>
      </div>
    </div>
  </header>

  <main class="container">
    <div class="page-header">
      <h1 class="page-title"><span class="icon">📊</span>Dashboard</h1>
      <p class="subtitle">Quick overview of your lab kits, sites, and expiry watch.</p>
    </div>
    {% if message %}<div class="alert success">{{ message }}</div>{% endif %}
    {% if error %}<div class="alert danger">{{ error }}</div>{% endif %}

    <div class="section-grid">
      <div class="card highlight">
        <div class="card-header">
          <div>
            <p class="eyebrow">Expiry watch</p>
            <h2><span class="icon">⏳</span>Upcoming Expiry</h2>
          </div>
          <a class="btn btn-secondary" href="{{ url_for('expiry_page') }}">View details</a>
        </div>
        <p class="stat"><span class="stat-number">{{ expiring_count }}</span> kits expiring within {{ warning_days }} days.</p>
        <p class="muted">Stay ahead by preparing replacements or reallocations.</p>
      </div>

      <div class="card">
        <div class="card-header">
          <div>
            <p class="eyebrow">Inventory</p>
            <h2><span class="icon">📊</span>Site Snapshot</h2>
          </div>
        </div>
        <div class="table-wrapper">
          <table>
            <thead><tr><th>Site</th><th>Kit Type</th><th>Available</th></tr></thead>
            <tbody>
            {% for row in inventory_rows[:8] %}
            <tr>
              <td>{{ row.site_name }}</td>
              <td>{{ row.labkit_type_name }}</td>
              <td>{{ row.available_count }}</td>
            </tr>
            {% endfor %}
            </tbody>
          </table>
        </div>
        <p class="muted small-text">Showing first {{ inventory_rows|length if inventory_rows|length < 8 else 8 }} rows.</p>
      </div>

      <div class="card">
        <div class="card-header">
          <div>
            <p class="eyebrow">Audit trail</p>
            <h2><span class="icon">📜</span>Export Audit Log</h2>
          </div>
        </div>
        <p class="muted">Download the full audit trail as CSV.</p>
        <a class="btn btn-secondary" href="{{ url_for('export_audit') }}"><span class="icon">📜</span>Download audit CSV</a>
      </div>
    </div>

    <div class="card">
      <div class="card-header">
        <div>
          <p class="eyebrow">Sites</p>
          <h2><span class="icon">🏥</span>All Sites</h2>
        </div>
      </div>
      <div class="table-wrapper">
        <table>
          <thead>
            <tr><th>ID</th><th>Site Code</th><th>Site Name</th><th>Created</th></tr>
          </thead>
          <tbody>
          {% for s in sites %}
          <tr>
            <td>{{ s.id }}</td><td>{{ s.site_code }}</td><td>{{ s.site_name }}</td><td>{{ s.created_at }}</td>
          </tr>
          {% endfor %}
          </tbody>
        </table>
      </div>
    </div>

    <div class="card">
      <div class="card-header">
        <div>
          <p class="eyebrow">Kit Types</p>
          <h2><span class="icon">🧪</span>Labkit Types</h2>
        </div>
      </div>
      <div class="table-wrapper">
        <table>
          <thead>
            <tr><th>ID</th><th>Name</th><th>Description</th><th>Default Expiry Days</th><th>Created</th></tr>
          </thead>
          <tbody>
          {% for t in labkit_types %}
          <tr>
            <td>{{ t.id }}</td><td>{{ t.name }}</td><td>{{ t.description }}</td><td>{{ t.default_expiry_days }}</td><td>{{ t.created_at }}</td>
          </tr>
          {% endfor %}
          </tbody>
        </table>
      </div>
    </div>

    <div class="card">
      <div class="card-header">
        <div>
          <p class="eyebrow">Inventory</p>
          <h2><span class="icon">📦</span>Labkits</h2>
        </div>
      </div>
      <div class="table-wrapper">
        <table>
          <thead>
            <tr>
              <th>ID</th><th>Barcode</th><th>Labkit Type ID</th><th>Site ID</th>
              <th>Lot</th><th>Expiry</th><th>Status</th><th>Created</th><th>Updated</th>
            </tr>
          </thead>
          <tbody>
          {% for k in labkits %}
          <tr>
            <td>{{ k.id }}</td><td>{{ k.kit_barcode }}</td><td>{{ k.labkit_type_id }}</td>
            <td>{{ k.site_id }}</td><td>{{ k.lot_number }}</td><td>{{ k.expiry_date }}</td>
            <td><span class="badge status-{{ k.status|replace(' ', '_') }}">{{ k.status }}</span></td><td>{{ k.created_at }}</td><td>{{ k.updated_at }}</td>
          </tr>
          {% endfor %}
          </tbody>
        </table>
      </div>
    </div>

    <div class="card">
      <div class="card-header">
        <div>
          <p class="eyebrow">History</p>
          <h2><span class="icon">📜</span>Status History</h2>
        </div>
      </div>
      <form method="post" action="{{ url_for('handle_history') }}" class="stacked">
        <div class="form-row">
          <div class="form-field">
            <label>Barcode</label>
            <input class="form-control" type="text" name="history_barcode" value="{{ history_barcode }}">
          </div>
          <div class="form-field align-end">
            <button type="submit" class="btn btn-secondary"><span class="icon">🔍</span>Show History</button>
          </div>
        </div>
      </form>
      {% if history_barcode %}
        <p class="muted">History for {{ history_barcode }}:</p>
        <div class="table-wrapper">
          <table>
            <thead><tr><th>Old Status</th><th>New Status</th><th>Event Time</th></tr></thead>
            <tbody>
            {% for ev in history %}
            <tr>
              <td>{{ ev.old_status }}</td><td>{{ ev.new_status }}</td><td>{{ ev.event_time }}</td>
            </tr>
            {% endfor %}
            </tbody>
          </table>
        </div>
      {% endif %}
    </div>
  </main>
</body>
</html>
"""


KIT_TYPES_TEMPLATE = """
<!doctype html>
<html>
<head>
  <title>Kit Types</title>
  <link rel="stylesheet" href="{{ url_for('static', filename='style.css') }}">
</head>
<body>
  <header class="topbar">
    <div class="nav-inner">
      <div class="brand">Lab Kit Tracking</div>
      <nav class="nav-links">
        <a href="{{ url_for('index') }}" class="nav-link {{ 'active' if nav_active == 'home' else '' }}"><span class="icon">📊</span>Home</a>
        <a href="{{ url_for('labkits_page') }}" class="nav-link {{ 'active' if nav_active == 'labkits' else '' }}"><span class="icon">📦</span>Labkits</a>
        <a href="{{ url_for('kit_types') }}" class="nav-link {{ 'active' if nav_active == 'kit_types' else '' }}"><span class="icon">🧪</span>Kit Types</a>
        <a href="{{ url_for('sites_page') }}" class="nav-link {{ 'active' if nav_active == 'sites' else '' }}"><span class="icon">🏥</span>Sites</a>
        <a href="{{ url_for('shipments_page') }}" class="nav-link {{ 'active' if nav_active == 'shipments' else '' }}"><span class="icon">🚚</span>Shipments</a>
        <a href="{{ url_for('inventory_page') }}" class="nav-link {{ 'active' if nav_active == 'inventory' else '' }}"><span class="icon">📋</span>Inventory</a>
        <a href="{{ url_for('expiry_page') }}" class="nav-link {{ 'active' if nav_active == 'expiry' else '' }}"><span class="icon">⏳</span>Expiry</a>
      </nav>
      <div class="user-chip">
        <span class="pill muted">Logged in as {{ current_user }}</span>
        <a href="{{ url_for('logout') }}" class="nav-link logout">Logout</a>
      </div>
    </div>
  </header>

  <main class="container">
    <div class="page-header">
      <h1 class="page-title"><span class="icon">🧪</span>Kit Types</h1>
      <p class="subtitle">Define reusable kit types and their default expiry windows.</p>
    </div>
    {% if message %}<div class="alert success">{{ message }}</div>{% endif %}
    {% if error %}<div class="alert danger">{{ error }}</div>{% endif %}

    <div class="card">
      <h2><span class="icon">➕</span>Create Kit Type</h2>
      <form method="post" action="{{ url_for('handle_add_kit_type') }}" class="stacked">
        <div class="form-row">
          <div class="form-field">
            <label>Name</label>
            <input class="form-control" type="text" name="name" required>
          </div>
          <div class="form-field">
            <label>Description</label>
            <input class="form-control" type="text" name="description">
          </div>
          <div class="form-field">
            <label>Default Expiry Days</label>
            <input class="form-control" type="number" name="default_expiry_days" min="0">
          </div>
        </div>
        <button type="submit" class="btn btn-primary"><span class="icon">➕</span>Save Kit Type</button>
      </form>
    </div>

    <div class="card">
      <div class="card-header">
        <div>
          <p class="eyebrow">Existing</p>
          <h2><span class="icon">🧪</span>Existing Kit Types</h2>
        </div>
      </div>
      <div class="table-wrapper">
        <table>
          <thead>
            <tr><th>ID</th><th>Name</th><th>Description</th><th>Default Expiry Days</th><th>Created</th><th>Actions</th></tr>
          </thead>
          <tbody>
          {% for t in labkit_types %}
          <tr>
            <td>{{ t.id }}</td>
            <td>{{ t.name }}</td>
            <td>{{ t.description }}</td>
            <td>{{ t.default_expiry_days }}</td>
            <td>{{ t.created_at }}</td>
            <td class="actions">
              <form method="post" action="{{ url_for('handle_update_kit_type') }}" class="inline-form">
                <input type="hidden" name="id" value="{{ t.id }}">
                <input class="form-control" type="text" name="name" value="{{ t.name }}" required>
                <input class="form-control" type="text" name="description" value="{{ t.description or '' }}">
                <input class="form-control" type="number" name="default_expiry_days" value="{{ t.default_expiry_days or '' }}" min="0">
                <button type="submit" class="btn btn-secondary"><span class="icon">✏️</span>Update</button>
              </form>
              <form method="post" action="{{ url_for('handle_delete_kit_type') }}" onsubmit="return confirm('Delete this kit type?');" class="inline-form">
                <input type="hidden" name="id" value="{{ t.id }}">
                <button type="submit" class="btn btn-danger"><span class="icon">🗑️</span>Delete</button>
              </form>
            </td>
          </tr>
          {% endfor %}
          </tbody>
        </table>
      </div>
    </div>
  </main>
</body>
</html>
"""


LABKITS_TEMPLATE = """
<!doctype html>
<html>
<head>
  <title>Labkits</title>
  <link rel="stylesheet" href="{{ url_for('static', filename='style.css') }}">
</head>
<body>
  <header class="topbar">
    <div class="nav-inner">
      <div class="brand">Lab Kit Tracking</div>
      <nav class="nav-links">
        <a href="{{ url_for('index') }}" class="nav-link {{ 'active' if nav_active == 'home' else '' }}"><span class="icon">📊</span>Home</a>
        <a href="{{ url_for('labkits_page') }}" class="nav-link {{ 'active' if nav_active == 'labkits' else '' }}"><span class="icon">📦</span>Labkits</a>
        <a href="{{ url_for('kit_types') }}" class="nav-link {{ 'active' if nav_active == 'kit_types' else '' }}"><span class="icon">🧪</span>Kit Types</a>
        <a href="{{ url_for('sites_page') }}" class="nav-link {{ 'active' if nav_active == 'sites' else '' }}"><span class="icon">🏥</span>Sites</a>
        <a href="{{ url_for('shipments_page') }}" class="nav-link {{ 'active' if nav_active == 'shipments' else '' }}"><span class="icon">🚚</span>Shipments</a>
        <a href="{{ url_for('inventory_page') }}" class="nav-link {{ 'active' if nav_active == 'inventory' else '' }}"><span class="icon">📋</span>Inventory</a>
        <a href="{{ url_for('expiry_page') }}" class="nav-link {{ 'active' if nav_active == 'expiry' else '' }}"><span class="icon">⏳</span>Expiry</a>
      </nav>
      <div class="user-chip">
        <span class="pill muted">Logged in as {{ current_user }}</span>
        <a href="{{ url_for('logout') }}" class="nav-link logout">Logout</a>
      </div>
    </div>
  </header>

  <main class="container">
    <div class="page-header">
      <h1 class="page-title"><span class="icon">📦</span>Labkits</h1>
      <p class="subtitle">Create, update, and track every labkit across your network.</p>
    </div>
    {% if message %}<div class="alert success">{{ message }}</div>{% endif %}
    {% if error %}<div class="alert danger">{{ error }}</div>{% endif %}

    <div class="card">
      <h2><span class="icon">➕</span>Create Labkit</h2>
      <form method="post" action="{{ url_for('handle_add_labkit') }}" id="create-labkit-form" class="stacked">
        <div class="form-row">
          <div class="form-field">
            <label>Kit Type</label>
            <select class="form-control" name="labkit_type_id" id="create-labkit-type" required>
              <option value="">-- choose --</option>
              {% for t in labkit_types %}
              <option value="{{ t.id }}" data-expiry="{{ t.default_expiry_days or '' }}">{{ t.name }}</option>
              {% endfor %}
            </select>
          </div>
          <div class="form-field">
            <label>Site</label>
            <select class="form-control" name="site_id">
              <option value="">Central depot (no site)</option>
              {% for s in sites %}
              <option value="{{ s.id }}">{{ s.site_name }}</option>
              {% endfor %}
            </select>
          </div>
        </div>
        <div class="form-row">
          <div class="form-field">
            <label>Barcode</label>
            <input class="form-control" type="text" name="kit_barcode" required>
          </div>
          <div class="form-field">
            <label>Lot Number</label>
            <input class="form-control" type="text" name="lot_number">
          </div>
        </div>
        <div class="form-row">
          <div class="form-field">
            <label>Expiry Date</label>
            <input class="form-control" type="date" name="expiry_date" id="create-expiry">
          </div>
          <div class="form-field">
            <label>Status</label>
            <select class="form-control" name="status">
              {% for st in status_options %}
              <option value="{{ st }}">{{ st }}</option>
              {% endfor %}
            </select>
          </div>
        </div>
        <button type="submit" class="btn btn-primary"><span class="icon">➕</span>Save Labkit</button>
      </form>
    </div>

    <div class="card">
      <div class="card-header">
        <div>
          <p class="eyebrow">Inventory</p>
          <h2><span class="icon">📦</span>Existing Labkits</h2>
        </div>
        <a class="btn btn-secondary" href="{{ url_for('export_labkits') }}"><span class="icon">📤</span>Export CSV</a>
      </div>
      <div class="table-wrapper">
        <table>
          <thead>
            <tr>
              <th>ID</th><th>Barcode</th><th>Labkit Type</th><th>Site</th><th>Lot</th><th>Expiry</th><th>Status</th><th>Actions</th>
            </tr>
          </thead>
          <tbody>
          {% for k in labkits %}
          <tr>
            <td>{{ k.id }}</td>
            <td>{{ k.kit_barcode }}</td>
            <td>{{ k.labkit_type_name }}</td>
            <td>{{ k.site_name or 'Central depot' }}</td>
            <td>{{ k.lot_number }}</td>
            <td>{{ k.expiry_date }}</td>
            <td><span class="badge status-{{ k.status|replace(' ', '_') }}">{{ k.status }}</span></td>
            <td class="actions">
              <form method="post" action="{{ url_for('handle_update_labkit') }}" class="inline-form">
                <input type="hidden" name="id" value="{{ k.id }}">
                <input class="form-control" type="text" name="kit_barcode" value="{{ k.kit_barcode }}" required>
                <select class="form-control" name="labkit_type_id" required>
                  {% for t in labkit_types %}
                  <option value="{{ t.id }}" {% if t.id == k.labkit_type_id %}selected{% endif %}>{{ t.name }}</option>
                  {% endfor %}
                </select>
                <select class="form-control" name="site_id">
                  <option value="" {% if not k.site_id %}selected{% endif %}>Central depot (no site)</option>
                  {% for s in sites %}
                  <option value="{{ s.id }}" {% if s.id == k.site_id %}selected{% endif %}>{{ s.site_name }}</option>
                  {% endfor %}
                </select>
                <input class="form-control" type="text" name="lot_number" value="{{ k.lot_number or '' }}" placeholder="Lot #">
                <input class="form-control" type="date" name="expiry_date" value="{{ k.expiry_date }}">
                <select class="form-control" name="status">
                  {% for st in status_options %}
                  <option value="{{ st }}" {% if st == k.status %}selected{% endif %}>{{ st }}</option>
                  {% endfor %}
                </select>
                <button type="submit" class="btn btn-secondary"><span class="icon">✏️</span>Update</button>
              </form>
              <form method="post" action="{{ url_for('handle_delete_labkit') }}" onsubmit="return confirm('Delete this labkit?');" class="inline-form">
                <input type="hidden" name="id" value="{{ k.id }}">
                <button type="submit" class="btn btn-danger"><span class="icon">🗑️</span>Delete</button>
              </form>
              <a class="btn btn-link" href="{{ url_for('labkit_detail_page', labkit_id=k.id) }}"><span class="icon">📄</span>Details</a>
              <a class="btn btn-link" href="{{ url_for('labkit_label', labkit_id=k.id) }}" target="_blank"><span class="icon">🖨️</span>Print label</a>
            </td>
          </tr>
          {% endfor %}
          </tbody>
        </table>
      </div>
    </div>
  </main>

  <script>
    // Prefill expiry based on kit type default days when empty
    document.addEventListener('DOMContentLoaded', function() {
      const typeSelect = document.getElementById('create-labkit-type');
      const expiryInput = document.getElementById('create-expiry');
      if (typeSelect && expiryInput) {
        typeSelect.addEventListener('change', function() {
          const days = parseInt(this.selectedOptions[0].getAttribute('data-expiry'));
          if (!expiryInput.value && !isNaN(days)) {
            const today = new Date();
            today.setDate(today.getDate() + days);
            const iso = today.toISOString().split('T')[0];
            expiryInput.value = iso;
          }
        });
      }
    });
  </script>
</body>
</html>
"""

# Sites list template
SITES_TEMPLATE = """
<!doctype html>
<html>
<head>
  <title>Sites</title>
  <link rel="stylesheet" href="{{ url_for('static', filename='style.css') }}">
</head>
<body>
  <header class="topbar">
    <div class="nav-inner">
      <div class="brand">Lab Kit Tracking</div>
      <nav class="nav-links">
        <a href="{{ url_for('index') }}" class="nav-link {{ 'active' if nav_active == 'home' else '' }}"><span class="icon">📊</span>Home</a>
        <a href="{{ url_for('labkits_page') }}" class="nav-link {{ 'active' if nav_active == 'labkits' else '' }}"><span class="icon">📦</span>Labkits</a>
        <a href="{{ url_for('kit_types') }}" class="nav-link {{ 'active' if nav_active == 'kit_types' else '' }}"><span class="icon">🧪</span>Kit Types</a>
        <a href="{{ url_for('sites_page') }}" class="nav-link {{ 'active' if nav_active == 'sites' else '' }}"><span class="icon">🏥</span>Sites</a>
        <a href="{{ url_for('shipments_page') }}" class="nav-link {{ 'active' if nav_active == 'shipments' else '' }}"><span class="icon">🚚</span>Shipments</a>
        <a href="{{ url_for('inventory_page') }}" class="nav-link {{ 'active' if nav_active == 'inventory' else '' }}"><span class="icon">📋</span>Inventory</a>
        <a href="{{ url_for('expiry_page') }}" class="nav-link {{ 'active' if nav_active == 'expiry' else '' }}"><span class="icon">⏳</span>Expiry</a>
      </nav>
      <div class="user-chip">
        <span class="pill muted">Logged in as {{ current_user }}</span>
        <a href="{{ url_for('logout') }}" class="nav-link logout">Logout</a>
      </div>
    </div>
  </header>

  <main class="container">
    <div class="page-header">
      <h1 class="page-title"><span class="icon">🏥</span>Sites</h1>
      <p class="subtitle">Manage research locations and keep contact info current.</p>
    </div>
    {% if message %}<div class="alert success">{{ message }}</div>{% endif %}
    {% if error %}<div class="alert danger">{{ error }}</div>{% endif %}

    <div class="card">
      <h2><span class="icon">➕</span>Create Site</h2>
      <form method="post" action="{{ url_for('handle_add_site_page') }}" class="stacked">
        <div class="form-row">
          <div class="form-field">
            <label>Site Code</label>
            <input class="form-control" type="text" name="site_code" required>
          </div>
          <div class="form-field">
            <label>Site Name</label>
            <input class="form-control" type="text" name="site_name" required>
          </div>
        </div>
        <div class="form-row">
          <div class="form-field">
            <label>Address Line 1</label>
            <input class="form-control" type="text" name="address_line1">
          </div>
          <div class="form-field">
            <label>Address Line 2</label>
            <input class="form-control" type="text" name="address_line2">
          </div>
        </div>
        <div class="form-row">
          <div class="form-field">
            <label>City</label>
            <input class="form-control" type="text" name="city">
          </div>
          <div class="form-field">
            <label>State</label>
            <input class="form-control" type="text" name="state">
          </div>
          <div class="form-field">
            <label>Postal Code</label>
            <input class="form-control" type="text" name="postal_code">
          </div>
          <div class="form-field">
            <label>Country</label>
            <input class="form-control" type="text" name="country">
          </div>
        </div>
        <button type="submit" class="btn btn-primary"><span class="icon">➕</span>Save Site</button>
      </form>
    </div>

    <div class="card">
      <div class="card-header">
        <div>
          <p class="eyebrow">Directory</p>
          <h2><span class="icon">🏥</span>Existing Sites</h2>
        </div>
      </div>
      <div class="table-wrapper">
        <table>
          <thead><tr><th>ID</th><th>Code</th><th>Name</th><th>City</th><th>Country</th><th>Actions</th></tr></thead>
          <tbody>
          {% for s in sites %}
          <tr>
            <td>{{ s.id }}</td>
            <td>{{ s.site_code }}</td>
            <td>{{ s.site_name }}</td>
            <td>{{ s.city }}</td>
            <td>{{ s.country }}</td>
            <td class="actions">
              <form method="post" action="{{ url_for('handle_update_site') }}" class="inline-form">
                <input type="hidden" name="id" value="{{ s.id }}">
                <input class="form-control" type="text" name="site_code" value="{{ s.site_code }}" required>
                <input class="form-control" type="text" name="site_name" value="{{ s.site_name }}" required>
                <input class="form-control" type="text" name="city" value="{{ s.city or '' }}" placeholder="City">
                <input class="form-control" type="text" name="country" value="{{ s.country or '' }}" placeholder="Country">
                <button type="submit" class="btn btn-secondary"><span class="icon">✏️</span>Update</button>
              </form>
              <form method="post" action="{{ url_for('handle_delete_site') }}" onsubmit="return confirm('Delete this site?');" class="inline-form">
                <input type="hidden" name="id" value="{{ s.id }}">
                <button type="submit" class="btn btn-danger"><span class="icon">🗑️</span>Delete</button>
              </form>
              <a class="btn btn-link" href="{{ url_for('site_detail', site_id=s.id) }}"><span class="icon">📄</span>Details</a>
            </td>
          </tr>
          {% endfor %}
          </tbody>
        </table>
      </div>
    </div>
  </main>
</body>
</html>
"""


# Site detail with contacts
SITE_DETAIL_TEMPLATE = """
<!doctype html>
<html>
<head>
  <title>Site Detail</title>
  <link rel="stylesheet" href="{{ url_for('static', filename='style.css') }}">
</head>
<body>
  <header class="topbar">
    <div class="nav-inner">
      <div class="brand">Lab Kit Tracking</div>
      <nav class="nav-links">
        <a href="{{ url_for('index') }}" class="nav-link {{ 'active' if nav_active == 'home' else '' }}"><span class="icon">📊</span>Home</a>
        <a href="{{ url_for('labkits_page') }}" class="nav-link {{ 'active' if nav_active == 'labkits' else '' }}"><span class="icon">📦</span>Labkits</a>
        <a href="{{ url_for('kit_types') }}" class="nav-link {{ 'active' if nav_active == 'kit_types' else '' }}"><span class="icon">🧪</span>Kit Types</a>
        <a href="{{ url_for('sites_page') }}" class="nav-link {{ 'active' if nav_active == 'sites' else '' }}"><span class="icon">🏥</span>Sites</a>
        <a href="{{ url_for('shipments_page') }}" class="nav-link {{ 'active' if nav_active == 'shipments' else '' }}"><span class="icon">🚚</span>Shipments</a>
        <a href="{{ url_for('inventory_page') }}" class="nav-link {{ 'active' if nav_active == 'inventory' else '' }}"><span class="icon">📋</span>Inventory</a>
        <a href="{{ url_for('expiry_page') }}" class="nav-link {{ 'active' if nav_active == 'expiry' else '' }}"><span class="icon">⏳</span>Expiry</a>
      </nav>
      <div class="user-chip">
        <span class="pill muted">Logged in as {{ current_user }}</span>
        <a href="{{ url_for('logout') }}" class="nav-link logout">Logout</a>
      </div>
    </div>
  </header>

  <main class="container">
    <div class="page-header">
      <h1 class="page-title"><span class="icon">🏥</span>Site: {{ site.site_name }} ({{ site.site_code }})</h1>
      <p class="subtitle">Location details and on-site contacts.</p>
    </div>
    {% if message %}<div class="alert success">{{ message }}</div>{% endif %}
    {% if error %}<div class="alert danger">{{ error }}</div>{% endif %}

    <div class="card">
      <h2><span class="icon">📍</span>Address</h2>
      <p class="muted">
        {{ site.address_line1 or '' }} {{ site.address_line2 or '' }}<br>
        {{ site.city or '' }} {{ site.state or '' }} {{ site.postal_code or '' }}<br>
        {{ site.country or '' }}
      </p>
    </div>

    <div class="card">
      <h2><span class="icon">➕</span>Add Contact</h2>
      <form method="post" action="{{ url_for('handle_add_contact', site_id=site.id) }}" class="stacked">
        <div class="form-row">
          <div class="form-field">
            <label>Name</label>
            <input class="form-control" type="text" name="name" required>
          </div>
          <div class="form-field">
            <label>Role</label>
            <input class="form-control" type="text" name="role">
          </div>
        </div>
        <div class="form-row">
          <div class="form-field">
            <label>Email</label>
            <input class="form-control" type="email" name="email">
          </div>
          <div class="form-field">
            <label>Phone</label>
            <input class="form-control" type="tel" name="phone">
          </div>
        </div>
        <label class="checkbox">
          <input type="checkbox" name="is_primary" value="1"> Primary contact
        </label>
        <button type="submit" class="btn btn-primary"><span class="icon">➕</span>Add Contact</button>
      </form>
    </div>

    <div class="card">
      <div class="card-header">
        <div>
          <p class="eyebrow">People</p>
          <h2><span class="icon">👥</span>Contacts</h2>
        </div>
      </div>
      <div class="table-wrapper">
        <table>
          <thead><tr><th>Name</th><th>Role</th><th>Email</th><th>Phone</th><th>Primary</th><th>Actions</th></tr></thead>
          <tbody>
          {% for c in contacts %}
          <tr>
            <td>{{ c.name }}</td>
            <td>{{ c.role }}</td>
            <td>{{ c.email }}</td>
            <td>{{ c.phone }}</td>
            <td>{{ 'Yes' if c.is_primary else 'No' }}</td>
            <td class="actions">
              <form method="post" action="{{ url_for('handle_update_contact', site_id=site.id) }}" class="inline-form">
                <input type="hidden" name="id" value="{{ c.id }}">
                <input class="form-control" type="text" name="name" value="{{ c.name }}" required>
                <input class="form-control" type="text" name="role" value="{{ c.role or '' }}">
                <input class="form-control" type="email" name="email" value="{{ c.email or '' }}">
                <input class="form-control" type="tel" name="phone" value="{{ c.phone or '' }}">
                <label class="checkbox inline"><input type="checkbox" name="is_primary" value="1" {% if c.is_primary %}checked{% endif %}> Primary</label>
                <button type="submit" class="btn btn-secondary"><span class="icon">✏️</span>Update</button>
              </form>
              <form method="post" action="{{ url_for('handle_delete_contact', site_id=site.id) }}" onsubmit="return confirm('Delete this contact?');" class="inline-form">
                <input type="hidden" name="id" value="{{ c.id }}">
                <button type="submit" class="btn btn-danger"><span class="icon">🗑️</span>Delete</button>
              </form>
            </td>
          </tr>
          {% endfor %}
          </tbody>
        </table>
      </div>
    </div>

    <p><a class="btn btn-link" href="{{ url_for('sites_page') }}">Back to sites</a></p>
  </main>
</body>
</html>
"""


# Shipments list
SHIPMENTS_TEMPLATE = """
<!doctype html>
<html>
<head>
  <title>Shipments</title>
  <link rel="stylesheet" href="{{ url_for('static', filename='style.css') }}">
</head>
<body>
  <header class="topbar">
    <div class="nav-inner">
      <div class="brand">Lab Kit Tracking</div>
      <nav class="nav-links">
        <a href="{{ url_for('index') }}" class="nav-link {{ 'active' if nav_active == 'home' else '' }}"><span class="icon">📊</span>Home</a>
        <a href="{{ url_for('labkits_page') }}" class="nav-link {{ 'active' if nav_active == 'labkits' else '' }}"><span class="icon">📦</span>Labkits</a>
        <a href="{{ url_for('kit_types') }}" class="nav-link {{ 'active' if nav_active == 'kit_types' else '' }}"><span class="icon">🧪</span>Kit Types</a>
        <a href="{{ url_for('sites_page') }}" class="nav-link {{ 'active' if nav_active == 'sites' else '' }}"><span class="icon">🏥</span>Sites</a>
        <a href="{{ url_for('shipments_page') }}" class="nav-link {{ 'active' if nav_active == 'shipments' else '' }}"><span class="icon">🚚</span>Shipments</a>
        <a href="{{ url_for('inventory_page') }}" class="nav-link {{ 'active' if nav_active == 'inventory' else '' }}"><span class="icon">📋</span>Inventory</a>
        <a href="{{ url_for('expiry_page') }}" class="nav-link {{ 'active' if nav_active == 'expiry' else '' }}"><span class="icon">⏳</span>Expiry</a>
      </nav>
      <div class="user-chip">
        <span class="pill muted">Logged in as {{ current_user }}</span>
        <a href="{{ url_for('logout') }}" class="nav-link logout">Logout</a>
      </div>
    </div>
  </header>

  <main class="container">
    <div class="page-header">
      <h1 class="page-title"><span class="icon">🚚</span>Shipments</h1>
      <p class="subtitle">Track shipments and assign labkits to each delivery.</p>
    </div>
    {% if message %}<div class="alert success">{{ message }}</div>{% endif %}
    {% if error %}<div class="alert danger">{{ error }}</div>{% endif %}

    <div class="card">
      <h2><span class="icon">➕</span>Create Shipment</h2>
      <form method="post" action="{{ url_for('handle_add_shipment') }}" class="stacked">
        <div class="form-row">
          <div class="form-field">
            <label>Site</label>
            <select class="form-control" name="site_id">
              <option value="">(None)</option>
              {% for s in sites %}
              <option value="{{ s.id }}">{{ s.site_name }}</option>
              {% endfor %}
            </select>
          </div>
          <div class="form-field">
            <label>Shipped At</label>
            <input class="form-control" type="datetime-local" name="shipped_at">
          </div>
          <div class="form-field">
            <label>Expected Arrival</label>
            <input class="form-control" type="date" name="expected_arrival">
          </div>
        </div>
        <div class="form-row">
          <div class="form-field">
            <label>Carrier</label>
            <input class="form-control" type="text" name="carrier">
          </div>
          <div class="form-field">
            <label>Tracking #</label>
            <input class="form-control" type="text" name="tracking_number">
          </div>
          <div class="form-field">
            <label>Status</label>
            <select class="form-control" name="status">
              <option value="">(none)</option>
              {% for st in status_options %}
              <option value="{{ st }}">{{ st }}</option>
              {% endfor %}
            </select>
          </div>
        </div>
        <div class="form-field">
          <label>Labkits</label>
          <select class="form-control" name="labkit_ids" multiple>
            {% for lk in unassigned_labkits %}
            <option value="{{ lk.id }}">{{ lk.kit_barcode }} ({{ lk.labkit_type_name }})</option>
            {% endfor %}
          </select>
          <p class="muted small-text">Hold Cmd/Ctrl to select multiple kits.</p>
        </div>
        <button type="submit" class="btn btn-primary"><span class="icon">➕</span>Create Shipment</button>
      </form>
    </div>

    <div class="card">
      <div class="card-header">
        <div>
          <p class="eyebrow">Log</p>
          <h2><span class="icon">🚚</span>Existing Shipments</h2>
        </div>
        <a class="btn btn-secondary" href="{{ url_for('export_shipments') }}"><span class="icon">📤</span>Export CSV</a>
      </div>
      <div class="table-wrapper">
        <table>
          <thead><tr><th>ID</th><th>Site</th><th>Shipped At</th><th>Expected Arrival</th><th>Carrier</th><th>Tracking #</th><th>Status</th><th>Actions</th></tr></thead>
          <tbody>
          {% for sh in shipments %}
          <tr>
            <td>{{ sh.id }}</td>
            <td>{{ sh.site_name }}</td>
            <td>{{ sh.shipped_at }}</td>
            <td>{{ sh.expected_arrival }}</td>
            <td>{{ sh.carrier }}</td>
            <td>{{ sh.tracking_number }}</td>
            <td><span class="badge status-{{ sh.status|replace(' ', '_') }}">{{ sh.status }}</span></td>
            <td><a class="btn btn-link" href="{{ url_for('shipment_detail', shipment_id=sh.id) }}"><span class="icon">📄</span>Details</a></td>
          </tr>
          {% endfor %}
          </tbody>
        </table>
      </div>
    </div>
  </main>
</body>
</html>
"""


# Shipment detail
SHIPMENT_DETAIL_TEMPLATE = """
<!doctype html>
<html>
<head>
  <title>Shipment Detail</title>
  <link rel="stylesheet" href="{{ url_for('static', filename='style.css') }}">
</head>
<body>
  <header class="topbar">
    <div class="nav-inner">
      <div class="brand">Lab Kit Tracking</div>
      <nav class="nav-links">
        <a href="{{ url_for('index') }}" class="nav-link {{ 'active' if nav_active == 'home' else '' }}"><span class="icon">📊</span>Home</a>
        <a href="{{ url_for('labkits_page') }}" class="nav-link {{ 'active' if nav_active == 'labkits' else '' }}"><span class="icon">📦</span>Labkits</a>
        <a href="{{ url_for('kit_types') }}" class="nav-link {{ 'active' if nav_active == 'kit_types' else '' }}"><span class="icon">🧪</span>Kit Types</a>
        <a href="{{ url_for('sites_page') }}" class="nav-link {{ 'active' if nav_active == 'sites' else '' }}"><span class="icon">🏥</span>Sites</a>
        <a href="{{ url_for('shipments_page') }}" class="nav-link {{ 'active' if nav_active == 'shipments' else '' }}"><span class="icon">🚚</span>Shipments</a>
        <a href="{{ url_for('inventory_page') }}" class="nav-link {{ 'active' if nav_active == 'inventory' else '' }}"><span class="icon">📋</span>Inventory</a>
        <a href="{{ url_for('expiry_page') }}" class="nav-link {{ 'active' if nav_active == 'expiry' else '' }}"><span class="icon">⏳</span>Expiry</a>
      </nav>
      <div class="user-chip">
        <span class="pill muted">Logged in as {{ current_user }}</span>
        <a href="{{ url_for('logout') }}" class="nav-link logout">Logout</a>
      </div>
    </div>
  </header>

  <main class="container">
    <div class="page-header">
      <h1 class="page-title"><span class="icon">🚚</span>Shipment {{ shipment.id }}</h1>
      <p class="subtitle">Update shipment metadata and assigned labkits.</p>
    </div>
    {% if message %}<div class="alert success">{{ message }}</div>{% endif %}
    {% if error %}<div class="alert danger">{{ error }}</div>{% endif %}

    <div class="card">
      <h2><span class="icon">✏️</span>Edit Shipment</h2>
      <form method="post" action="{{ url_for('handle_update_shipment', shipment_id=shipment.id) }}" class="stacked">
        <div class="form-row">
          <div class="form-field">
            <label>Site</label>
            <select class="form-control" name="site_id">
              <option value="">(None)</option>
              {% for s in sites %}
              <option value="{{ s.id }}" {% if s.id == shipment.site_id %}selected{% endif %}>{{ s.site_name }}</option>
              {% endfor %}
            </select>
          </div>
          <div class="form-field">
            <label>Shipped At</label>
            <input class="form-control" type="datetime-local" name="shipped_at" value="{{ shipment.shipped_at|replace(' ', 'T') if shipment.shipped_at }}">
          </div>
          <div class="form-field">
            <label>Expected Arrival</label>
            <input class="form-control" type="date" name="expected_arrival" value="{{ shipment.expected_arrival }}">
          </div>
        </div>
        <div class="form-row">
          <div class="form-field">
            <label>Carrier</label>
            <input class="form-control" type="text" name="carrier" value="{{ shipment.carrier or '' }}">
          </div>
          <div class="form-field">
            <label>Tracking #</label>
            <input class="form-control" type="text" name="tracking_number" value="{{ shipment.tracking_number or '' }}">
          </div>
          <div class="form-field">
            <label>Status</label>
            <select class="form-control" name="status">
              <option value="">(none)</option>
              {% for st in status_options %}
              <option value="{{ st }}" {% if st == shipment.status %}selected{% endif %}>{{ st }}</option>
              {% endfor %}
            </select>
          </div>
        </div>
        <div class="form-field">
          <label>Labkits</label>
          <select class="form-control" name="labkit_ids" multiple>
            {% for lk in labkit_options %}
            <option value="{{ lk.id }}" {% if lk.id in selected_labkit_ids %}selected{% endif %}>{{ lk.kit_barcode }} ({{ lk.labkit_type_name }})</option>
            {% endfor %}
          </select>
          <p class="muted small-text">Hold Cmd/Ctrl to multi-select.</p>
        </div>
        <button type="submit" class="btn btn-primary"><span class="icon">✏️</span>Update Shipment</button>
      </form>
    </div>

    <div class="card">
      <div class="card-header">
        <div>
          <p class="eyebrow">Contents</p>
          <h2><span class="icon">📦</span>Assigned Labkits</h2>
        </div>
      </div>
      <div class="table-wrapper">
        <table>
          <thead><tr><th>ID</th><th>Barcode</th><th>Type</th><th>Status</th></tr></thead>
          <tbody>
          {% for lk in shipment.labkits %}
          <tr>
            <td>{{ lk.id }}</td>
            <td>{{ lk.kit_barcode }}</td>
            <td>{{ lk.labkit_type_name }}</td>
            <td><span class="badge status-{{ lk.status|replace(' ', '_') }}">{{ lk.status }}</span></td>
          </tr>
          {% endfor %}
          </tbody>
        </table>
      </div>
    </div>

    <p><a class="btn btn-link" href="{{ url_for('shipments_page') }}">Back to shipments</a></p>
  </main>
</body>
</html>
"""



# Labkit label (print friendly)
LABKIT_LABEL_TEMPLATE = """
<!doctype html>
<html>
<head>
  <title>Labkit Label</title>
  <link rel="stylesheet" href="{{ url_for('static', filename='style.css') }}">
  <style>
    body { background: #fff; }
    .label-card { border: 1px solid #111827; padding: 18px; width: 340px; border-radius: 12px; }
    .barcode { font-size: 18px; font-weight: 700; margin-bottom: 6px; }
    .meta { margin: 6px 0; font-size: 14px; }
    img { width: 180px; height: 180px; }
    @media print { button { display: none; } body { margin: 0; } .label-card { box-shadow: none; border: 1px solid #000; } }
  </style>
</head>
<body>
  <div class="label-card">
    <div class="barcode">Kit: {{ labkit.kit_barcode }}</div>
    <div class="meta">Type: {{ labkit.labkit_type_name }}</div>
    <div class="meta">Expiry: {{ labkit.expiry_date }}</div>
    <div class="meta">Status: {{ labkit.status }}</div>
    <div><img src="{{ qr_uri }}" alt="QR code"></div>
  </div>
  <button class="btn btn-primary" onclick="window.print()">Print</button>
</body>
</html>
"""


LABKIT_DETAIL_TEMPLATE = """
<!doctype html>
<html>
<head>
  <title>Labkit Detail</title>
  <link rel="stylesheet" href="{{ url_for('static', filename='style.css') }}">
</head>
<body>
  <header class="topbar">
    <div class="nav-inner">
      <div class="brand">Lab Kit Tracking</div>
      <nav class="nav-links">
        <a href="{{ url_for('index') }}" class="nav-link {{ 'active' if nav_active == 'home' else '' }}"><span class="icon">📊</span>Home</a>
        <a href="{{ url_for('labkits_page') }}" class="nav-link {{ 'active' if nav_active == 'labkits' else '' }}"><span class="icon">📦</span>Labkits</a>
        <a href="{{ url_for('kit_types') }}" class="nav-link {{ 'active' if nav_active == 'kit_types' else '' }}"><span class="icon">🧪</span>Kit Types</a>
        <a href="{{ url_for('sites_page') }}" class="nav-link {{ 'active' if nav_active == 'sites' else '' }}"><span class="icon">🏥</span>Sites</a>
        <a href="{{ url_for('shipments_page') }}" class="nav-link {{ 'active' if nav_active == 'shipments' else '' }}"><span class="icon">🚚</span>Shipments</a>
        <a href="{{ url_for('inventory_page') }}" class="nav-link {{ 'active' if nav_active == 'inventory' else '' }}"><span class="icon">📋</span>Inventory</a>
        <a href="{{ url_for('expiry_page') }}" class="nav-link {{ 'active' if nav_active == 'expiry' else '' }}"><span class="icon">⏳</span>Expiry</a>
      </nav>
      <div class="user-chip">
        <span class="pill muted">Logged in as {{ current_user }}</span>
        <a href="{{ url_for('logout') }}" class="nav-link logout">Logout</a>
      </div>
    </div>
  </header>

  <main class="container">
    <div class="page-header">
      <h1 class="page-title"><span class="icon">📦</span>Labkit {{ labkit.kit_barcode }}</h1>
      <p class="subtitle">Full history and metadata for this kit.</p>
    </div>
    {% if message %}<div class="alert success">{{ message }}</div>{% endif %}
    {% if error %}<div class="alert danger">{{ error }}</div>{% endif %}

    <div class="card">
      <h2><span class="icon">🧾</span>Summary</h2>
      <div class="meta-grid">
        <div><p class="eyebrow">Type</p><p>{{ labkit.labkit_type_name }}</p></div>
        <div><p class="eyebrow">Site</p><p>{{ labkit.site_name or 'Central depot' }}</p></div>
        <div><p class="eyebrow">Lot</p><p>{{ labkit.lot_number }}</p></div>
        <div><p class="eyebrow">Expiry</p><p>{{ labkit.expiry_date }}</p></div>
        <div><p class="eyebrow">Status</p><p><span class="badge status-{{ labkit.status|replace(' ', '_') }}">{{ labkit.status }}</span></p></div>
        <div><p class="eyebrow">Created</p><p>{{ labkit.created_at }}</p></div>
        <div><p class="eyebrow">Updated</p><p>{{ labkit.updated_at }}</p></div>
      </div>
    </div>

    <div class="card">
      <div class="card-header">
        <div>
          <p class="eyebrow">Timeline</p>
          <h2><span class="icon">🕑</span>History</h2>
        </div>
      </div>
      <div class="table-wrapper">
        <table>
          <thead><tr><th>When</th><th>Type</th><th>Description</th><th>By</th></tr></thead>
          <tbody>
          {% for ev in events %}
          <tr>
            <td>{{ ev.created_at }}</td>
            <td>{{ ev.event_type }}</td>
            <td>{{ ev.description }}</td>
            <td>{{ ev.created_by }}</td>
          </tr>
          {% endfor %}
          </tbody>
        </table>
      </div>
    </div>

    <div class="card">
      <div class="card-header">
        <div>
          <p class="eyebrow">Audit trail</p>
          <h2><span class="icon">📜</span>Audit Log</h2>
        </div>
      </div>
      <div class="table-wrapper">
        <table>
          <thead><tr><th>Timestamp</th><th>User</th><th>Action</th><th>Field</th><th>Old</th><th>New</th><th>Description</th></tr></thead>
          <tbody>
          {% for a in audit_entries %}
          <tr>
            <td>{{ a.timestamp }}</td>
            <td>{{ a.user }}</td>
            <td>{{ a.action }}</td>
            <td>{{ a.field_name }}</td>
            <td>{{ a.old_value }}</td>
            <td>{{ a.new_value }}</td>
            <td>{{ a.description }}</td>
          </tr>
          {% endfor %}
          </tbody>
        </table>
      </div>
    </div>

    <div class="card">
      <h2><span class="icon">📝</span><span class="icon">📝</span>Add Note</h2>
      <form method="post" action="{{ url_for('handle_add_labkit_note', labkit_id=labkit.id) }}" class="stacked">
        <textarea class="form-control" name="description" required></textarea>
        <button type="submit" class="btn btn-primary"><span class="icon">📝</span><span class="icon">📝</span>Add Note</button>
      </form>
    </div>

    <p><a class="btn btn-link" href="{{ url_for('labkits_page') }}">Back to labkits</a></p>
  </main>
</body>
</html>
"""

INVENTORY_TEMPLATE = """
<!doctype html>
<html>
<head>
  <title>Inventory</title>
  <link rel="stylesheet" href="{{ url_for('static', filename='style.css') }}">
</head>
<body>
  <header class="topbar">
    <div class="nav-inner">
      <div class="brand">Lab Kit Tracking</div>
      <nav class="nav-links">
        <a href="{{ url_for('index') }}" class="nav-link {{ 'active' if nav_active == 'home' else '' }}"><span class="icon">📊</span>Home</a>
        <a href="{{ url_for('labkits_page') }}" class="nav-link {{ 'active' if nav_active == 'labkits' else '' }}"><span class="icon">📦</span>Labkits</a>
        <a href="{{ url_for('kit_types') }}" class="nav-link {{ 'active' if nav_active == 'kit_types' else '' }}"><span class="icon">🧪</span>Kit Types</a>
        <a href="{{ url_for('sites_page') }}" class="nav-link {{ 'active' if nav_active == 'sites' else '' }}"><span class="icon">🏥</span>Sites</a>
        <a href="{{ url_for('shipments_page') }}" class="nav-link {{ 'active' if nav_active == 'shipments' else '' }}"><span class="icon">🚚</span>Shipments</a>
        <a href="{{ url_for('inventory_page') }}" class="nav-link {{ 'active' if nav_active == 'inventory' else '' }}"><span class="icon">📋</span>Inventory</a>
        <a href="{{ url_for('expiry_page') }}" class="nav-link {{ 'active' if nav_active == 'expiry' else '' }}"><span class="icon">⏳</span>Expiry</a>
      </nav>
      <div class="user-chip">
        <span class="pill muted">Logged in as {{ current_user }}</span>
        <a href="{{ url_for('logout') }}" class="nav-link logout">Logout</a>
      </div>
    </div>
  </header>

  <main class="container">
    <div class="page-header">
      <h1 class="page-title"><span class="icon">📋</span>Inventory</h1>
      <p class="subtitle">See available counts by site and kit type.</p>
    </div>

    <div class="card">
      <h2><span class="icon">🔍</span>Filter</h2>
      <form method="get" action="{{ url_for('inventory_page') }}" class="stacked">
        <div class="form-row">
          <div class="form-field">
            <label>Site</label>
            <select class="form-control" name="site_id">
              <option value="">All</option>
              <option value="none" {% if site_id == 'none' %}selected{% endif %}>Central depot</option>
              {% for s in sites %}
              <option value="{{ s.id }}" {% if site_id|default('') == s.id|string %}selected{% endif %}>{{ s.site_name }}</option>
              {% endfor %}
            </select>
          </div>
          <div class="form-field">
            <label>Kit Type</label>
            <select class="form-control" name="kit_type_id">
              <option value="">All</option>
              {% for t in labkit_types %}
              <option value="{{ t.id }}" {% if kit_type_id|default('') == t.id|string %}selected{% endif %}>{{ t.name }}</option>
              {% endfor %}
            </select>
          </div>
        </div>
        <button type="submit" class="btn btn-primary">Filter</button>
      </form>
    </div>

    <div class="card">
      <div class="card-header">
        <div>
          <p class="eyebrow">Current levels</p>
          <h2><span class="icon">📋</span>Availability</h2>
        </div>
      </div>
      <div class="table-wrapper">
        <table>
          <thead><tr><th>Site</th><th>Kit Type</th><th>Available Count</th></tr></thead>
          <tbody>
          {% for row in rows %}
          <tr>
            <td>{{ row.site_name }}</td>
            <td>{{ row.labkit_type_name }}</td>
            <td>{{ row.available_count }}</td>
          </tr>
          {% endfor %}
          </tbody>
        </table>
      </div>
    </div>
  </main>
</body>
</html>
"""

EXPIRY_TEMPLATE = """
<!doctype html>
<html>
<head>
  <title>Expiry Overview</title>
  <link rel="stylesheet" href="{{ url_for('static', filename='style.css') }}">
</head>
<body>
  <header class="topbar">
    <div class="nav-inner">
      <div class="brand">Lab Kit Tracking</div>
      <nav class="nav-links">
        <a href="{{ url_for('index') }}" class="nav-link {{ 'active' if nav_active == 'home' else '' }}"><span class="icon">📊</span>Home</a>
        <a href="{{ url_for('labkits_page') }}" class="nav-link {{ 'active' if nav_active == 'labkits' else '' }}"><span class="icon">📦</span>Labkits</a>
        <a href="{{ url_for('kit_types') }}" class="nav-link {{ 'active' if nav_active == 'kit_types' else '' }}"><span class="icon">🧪</span>Kit Types</a>
        <a href="{{ url_for('sites_page') }}" class="nav-link {{ 'active' if nav_active == 'sites' else '' }}"><span class="icon">🏥</span>Sites</a>
        <a href="{{ url_for('shipments_page') }}" class="nav-link {{ 'active' if nav_active == 'shipments' else '' }}"><span class="icon">🚚</span>Shipments</a>
        <a href="{{ url_for('inventory_page') }}" class="nav-link {{ 'active' if nav_active == 'inventory' else '' }}"><span class="icon">📋</span>Inventory</a>
        <a href="{{ url_for('expiry_page') }}" class="nav-link {{ 'active' if nav_active == 'expiry' else '' }}"><span class="icon">⏳</span>Expiry</a>
      </nav>
      <div class="user-chip">
        <span class="pill muted">Logged in as {{ current_user }}</span>
        <a href="{{ url_for('logout') }}" class="nav-link logout">Logout</a>
      </div>
    </div>
  </header>

  <main class="container">
    <div class="page-header">
      <h1 class="page-title"><span class="icon">⏳</span>Expiry Overview</h1>
      <p class="subtitle">Spot expired and soon-to-expire kits at a glance.</p>
    </div>

    <div class="card warning">
      <div class="card-header">
        <div>
          <p class="eyebrow">Priority</p>
          <h2><span class="icon">⚠️</span>Expired Kits</h2>
        </div>
      </div>
      <div class="table-wrapper">
        <table>
          <thead><tr><th>Barcode</th><th>Kit Type</th><th>Site</th><th>Expiry</th><th>Status</th></tr></thead>
          <tbody>
          {% for k in expired %}
          <tr class="row-alert">
            <td>{{ k.kit_barcode }}</td>
            <td>{{ k.labkit_type_name }}</td>
            <td>{{ k.site_name or 'Central depot' }}</td>
            <td>{{ k.expiry_date }}</td>
            <td><span class="badge status-destroyed">Expired</span> <span class="badge status-{{ k.status|replace(' ', '_') }}">{{ k.status }}</span></td>
          </tr>
          {% endfor %}
          </tbody>
        </table>
      </div>
    </div>

    <div class="card caution">
      <div class="card-header">
        <div>
          <p class="eyebrow">Upcoming</p>
          <h2><span class="icon">⏳</span>Expiring within {{ warning_days }} days</h2>
        </div>
      </div>
      <div class="table-wrapper">
        <table>
          <thead><tr><th>Barcode</th><th>Kit Type</th><th>Site</th><th>Expiry</th><th>Status</th></tr></thead>
          <tbody>
          {% for k in soon %}
          <tr>
            <td>{{ k.kit_barcode }}</td>
            <td>{{ k.labkit_type_name }}</td>
            <td>{{ k.site_name or 'Central depot' }}</td>
            <td>{{ k.expiry_date }}</td>
            <td><span class="badge status-warning">Expiring soon</span> <span class="badge status-{{ k.status|replace(' ', '_') }}">{{ k.status }}</span></td>
          </tr>
          {% endfor %}
          </tbody>
        </table>
      </div>
    </div>

    <div class="card">
      <div class="card-header">
        <div>
          <p class="eyebrow">Healthy stock</p>
          <h2><span class="icon">✅</span>Other Kits</h2>
        </div>
      </div>
      <div class="table-wrapper">
        <table>
          <thead><tr><th>Barcode</th><th>Kit Type</th><th>Site</th><th>Expiry</th><th>Status</th></tr></thead>
          <tbody>
          {% for k in fine %}
          <tr>
            <td>{{ k.kit_barcode }}</td>
            <td>{{ k.labkit_type_name }}</td>
            <td>{{ k.site_name or 'Central depot' }}</td>
            <td>{{ k.expiry_date }}</td>
            <td><span class="badge status-{{ k.status|replace(' ', '_') }}">{{ k.status }}</span></td>
          </tr>
          {% endfor %}
          </tbody>
        </table>
      </div>
    </div>
  </main>
</body>
</html>
"""

LOGIN_TEMPLATE = """
<!doctype html>
<html>
<head>
  <title>Login</title>
  <link rel="stylesheet" href="{{ url_for('static', filename='style.css') }}">
</head>
<body class="auth">
  <div class="auth-card">
    <h1 class="page-title">Lab Kit Tracking</h1>
    <p class="subtitle">Please sign in to continue.</p>
    {% if error %}<div class="alert danger">{{ error }}</div>{% endif %}
    <form method="post" class="stacked">
      <label>Username</label>
      <input class="form-control" type="text" name="username" required>
      <label>Password</label>
      <input class="form-control" type="password" name="password" required>
      <button type="submit" class="btn btn-primary full-width"><span class="icon">🔐</span>Login</button>
    </form>
  </div>
</body>
</html>
"""


if __name__ == "__main__":
    app.run(debug=True, port=5000)
