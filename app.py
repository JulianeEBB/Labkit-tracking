from datetime import date, datetime, timedelta
import csv
from functools import wraps
import base64
import io
import os
import re
import zipfile

from flask import Flask, redirect, render_template_string, request, session, url_for
from flask import Response
from werkzeug.security import check_password_hash
import psycopg2
from barcode import Code39
from barcode.writer import ImageWriter
from reportlab.pdfgen import canvas
from reportlab.graphics.barcode import code39
from reportlab.lib.pagesizes import letter
from reportlab.lib.utils import ImageReader
from pypdf import PdfReader, PdfWriter
from pypdf.generic import BooleanObject, DictionaryObject, NameObject

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
    backfill_missing_barcodes,
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
backfill_missing_barcodes()

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


@app.template_filter("format_ts")
def format_timestamp(value):
    """Format datetime values without fractional seconds for display."""
    if value is None or value == "":
        return ""
    if isinstance(value, datetime):
        return value.isoformat(sep=" ", timespec="seconds")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, str):
        parsed = parse_datetime(value)
        if parsed:
            return parsed.isoformat(sep=" ", timespec="seconds")
    return str(value)


def parse_optional_float(raw_value: str, field_label: str):
    """Parse a float if provided; return (value, error_message)."""
    if raw_value is None:
        return None, None
    trimmed = raw_value.strip()
    if trimmed == "":
        return None, None
    try:
        return float(trimmed), None
    except ValueError:
        return None, f"{field_label} must be a number"


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
        labkits=all_kits[:10],
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
    kit_type_filter_raw = request.args.get("kit_type_id", "").strip()
    site_filter_raw = request.args.get("site_id", "").strip()
    status_filter = request.args.get("status", "").strip()
    labkits = list_labkits()
    if kit_type_filter_raw:
        try:
            kit_type_filter = int(kit_type_filter_raw)
        except ValueError:
            kit_type_filter = None
        if kit_type_filter:
            labkits = [k for k in labkits if k.get("labkit_type_id") == kit_type_filter]
    if site_filter_raw:
        if site_filter_raw == "none":
            labkits = [k for k in labkits if not k.get("site_id")]
        else:
            try:
                site_filter = int(site_filter_raw)
            except ValueError:
                site_filter = None
            if site_filter:
                labkits = [k for k in labkits if k.get("site_id") == site_filter]
    if status_filter:
        labkits = [k for k in labkits if (k.get("status") or "") == status_filter]
    return render_template_string(
        LABKITS_TEMPLATE,
        nav_active="labkits",
        message=message,
        error=error,
        labkits=labkits,
        labkit_types=list_labkit_types(),
        sites=list_sites(),
        kit_type_id=kit_type_filter_raw,
        site_id=site_filter_raw,
        status=status_filter,
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
    standard_weight, weight_error = parse_optional_float(
        request.form.get("standard_weight"), "Standard weight"
    )
    weight_variance, variance_error = parse_optional_float(
        request.form.get("weight_variance"), "Weight variance"
    )
    if weight_error:
        return redirect(url_for("kit_types", error=weight_error))
    if variance_error:
        return redirect(url_for("kit_types", error=variance_error))
    if not name:
        return redirect(url_for("kit_types", error="Name is required"))
    add_labkit_type(name, description, default_expiry_days, None, standard_weight, weight_variance)
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
    standard_weight, weight_error = parse_optional_float(
        request.form.get("standard_weight"), "Standard weight"
    )
    weight_variance, variance_error = parse_optional_float(
        request.form.get("weight_variance"), "Weight variance"
    )
    if weight_error:
        return redirect(url_for("kit_types", error=weight_error))
    if variance_error:
        return redirect(url_for("kit_types", error=variance_error))
    if not kit_type_id or not name:
        return redirect(url_for("kit_types", error="Name and id are required"))
    update_labkit_type(
        kit_type_id,
        name,
        description,
        default_expiry_days,
        None,
        standard_weight,
        weight_variance,
    )
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
    investigator_name = request.form.get("investigator_name", "").strip()
    investigator_room = request.form.get("investigator_room", "").strip() or None
    address_line = request.form.get("address_line", "").strip() or None
    city = request.form.get("city", "").strip() or None
    state = request.form.get("state", "").strip() or None
    postal_code = request.form.get("postal_code", "").strip() or None
    country = request.form.get("country", "").strip() or None
    if not site_code or not site_name:
        return redirect(url_for("sites_page", error="Site code and name are required"))
    if not investigator_name:
        return redirect(url_for("sites_page", error="Investigator name is required"))
    try:
        site_id = add_site(site_code, site_name)
    except psycopg2.IntegrityError:
        return redirect(url_for("sites_page", error="Site code already exists"))
    update_site(
        site_id=site_id,
        site_code=site_code,
        site_name=site_name,
        address_line1=address_line,
        address_line2=None,
        city=city,
        state=state,
        postal_code=postal_code,
        country=country,
    )
    add_site_contact(
        site_id=site_id,
        name=investigator_name,
        role="Investigator",
        email=None,
        phone=None,
        room_number=investigator_room,
        is_primary=True,
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
    address_line = request.form.get("address_line", "").strip() or None
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
        address_line1=address_line,
        address_line2=None,
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
    room_number = request.form.get("room_number", "").strip() or None
    is_primary = bool(request.form.get("is_primary"))
    if not name:
        return redirect(url_for("site_detail", site_id=site_id, error="Name is required"))
    add_site_contact(site_id, name, role, email, phone, room_number, is_primary)
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
    room_number = request.form.get("room_number", "").strip() or None
    is_primary = bool(request.form.get("is_primary"))
    if not name:
        return redirect(url_for("site_detail", site_id=site_id, error="Name is required"))
    update_site_contact(contact_id, name, role, email, phone, room_number, is_primary)
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
                update_labkit_status(
                    lk.get("barcode_value") or lk.get("kit_barcode"),
                    "shipped",
                    created_by=current_username(),
                )
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
                update_labkit_status(
                    lk.get("barcode_value") or lk.get("kit_barcode"),
                    "shipped",
                    created_by=current_username(),
                )
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


def _barcode_data_uri(text: str) -> str:
    """Generate a Code39 barcode as data URI for inline display."""
    if not text:
        return ""
    barcode_obj = Code39(text, writer=ImageWriter(), add_checksum=False)
    buf = io.BytesIO()
    barcode_obj.write(buf, options={"write_text": False, "dpi": 1000})
    encoded = base64.b64encode(buf.getvalue()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def _barcode_png_bytes(text: str) -> bytes:
    """Generate a Code39 barcode image and return PNG bytes."""
    if not text:
        return b""
    barcode_obj = Code39(text, writer=ImageWriter(), add_checksum=False)
    buf = io.BytesIO()
    barcode_obj.write(buf, options={"write_text": False, "dpi": 1000})
    return buf.getvalue()


def _requisition_template_path(kit_type_name: str) -> str:
    """Resolve the PDF template path for a given kit type (robust to slashes/spaces)."""
    base_dir = os.environ.get("REQUISITION_TEMPLATE_DIR", "/Users/julianeschikora/Documents/PDFs")
    name = kit_type_name or ""

    def _normalize(value: str) -> str:
        return re.sub(r"[^a-z0-9]", "", value.lower())

    target_key = _normalize(name)
    # Try to find a PDF whose stem matches the kit type after stripping punctuation/slashes.
    if target_key:
        try:
            for fname in os.listdir(base_dir):
                if not fname.lower().endswith(".pdf"):
                    continue
                stem, _ = os.path.splitext(fname)
                if _normalize(stem) == target_key:
                    return os.path.join(base_dir, fname)
        except FileNotFoundError:
            pass

    # Fallback: sanitize slashes/whitespace to underscores.
    safe_name = re.sub(r"[^\w.-]+", "_", name) or "template"
    return os.path.join(base_dir, f"{safe_name}.pdf")


def _find_field_rect(reader: PdfReader, field_name: str):
    """Return (page_index, rect) for the given field name, or (None, None)."""
    for idx, page in enumerate(reader.pages):
        annots = page.get("/Annots")
        if not annots:
            continue
        for annot_ref in annots:
            annot = annot_ref.get_object()
            if annot.get("/T") == field_name:
                rect = annot.get("/Rect")
                if rect and len(rect) == 4:
                    return idx, [float(v) for v in rect]
    return None, None


def _find_field_rects(reader: PdfReader, field_name: str):
    """Return list of (page_index, rect) for all fields matching the name."""
    matches = []
    for idx, page in enumerate(reader.pages):
        annots = page.get("/Annots")
        if not annots:
            continue
        for annot_ref in annots:
            annot = annot_ref.get_object()
            if annot.get("/T") == field_name:
                rect = annot.get("/Rect")
                if rect and len(rect) == 4:
                    matches.append((idx, [float(v) for v in rect]))
    return matches


def _select_investigator_contact(site_id: int):
    """Pick the investigator/primary contact for a site."""
    if not site_id:
        return None
    contacts = list_site_contacts(site_id) or []
    investigator = next((c for c in contacts if (c.get("role") or "").lower() == "investigator"), None)
    primary = next((c for c in contacts if c.get("is_primary")), None)
    return investigator or primary or (contacts[0] if contacts else None)


@app.route("/labkits/<int:labkit_id>/label")
def labkit_label(labkit_id: int):
    labkit = get_labkit_detail(labkit_id)
    if not labkit:
        return redirect(url_for("labkits_page", error="Labkit not found"))
    barcode_value = labkit.get("barcode_value") or labkit.get("kit_barcode") or ""
    barcode_uri = _barcode_data_uri(barcode_value)
    return render_template_string(
        LABKIT_LABEL_TEMPLATE,
        nav_active="labkits",
        labkit=labkit,
        barcode_uri=barcode_uri,
        barcode_value=barcode_value,
    )


@app.route("/labkits/<int:labkit_id>/label.csv")
def labkit_label_csv(labkit_id: int):
    """Download a minimal CSV with barcode and kit type (no header)."""
    labkit = get_labkit_detail(labkit_id)
    if not labkit:
        return redirect(url_for("labkits_page", error="Labkit not found"))
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([labkit.get("barcode_value") or labkit.get("kit_barcode") or "", labkit.get("labkit_type_name") or ""])
    csv_data = output.getvalue()
    return Response(
        csv_data,
        mimetype="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename=labkit-{labkit_id}-label.csv"},
    )


@app.route("/labkits/<int:labkit_id>/requisition.pdf")
@login_required
def labkit_requisition(labkit_id: int):
    """Generate a filled requisition PDF for a specific labkit."""
    labkit = get_labkit_detail(labkit_id)
    if not labkit:
        return redirect(url_for("labkits_page", error="Labkit not found"))

    barcode_value = labkit.get("barcode_value") or labkit.get("kit_barcode") or ""
    kit_type_name = labkit.get("labkit_type_name") or ""
    template_path = _requisition_template_path(kit_type_name)
    if not os.path.exists(template_path):
        return redirect(
            url_for("labkits_page", error=f"Requisition template for '{kit_type_name}' not found.")
        )

    site = get_site(labkit.get("site_id")) if labkit.get("site_id") else None
    contact = _select_investigator_contact(site["id"]) if site else None
    address_line = (site or {}).get("address_line1") or (site or {}).get("address_line2") or ""

    field_values = {
        "site_number": (site or {}).get("site_code") or "",
        "investigator_name": (contact or {}).get("name") or "",
        "adress_line": address_line,
        "address_line": address_line,  # support template variants with correct spelling
        "investigator_room_number": (contact or {}).get("room_number") or "",
        "city": (site or {}).get("city") or "",
        "state_abbreviation": (site or {}).get("state") or "",
        "postal_code": (site or {}).get("postal_code") or "",
        "country": (site or {}).get("country") or "",
        "kit_type_name": kit_type_name,
        "barcode": "",
        "barcode_number": barcode_value,
    }

    reader = PdfReader(template_path)
    writer = PdfWriter()
    for page in reader.pages:
        writer.add_page(page)

    # Ensure an AcroForm is present before updating fields
    acroform = reader.trailer["/Root"].get("/AcroForm")
    has_fields = False
    if acroform:
        acroform_obj = acroform.get_object()
        has_fields = bool(acroform_obj.get("/Fields"))
        writer_acroform = DictionaryObject()
        for key, value in acroform_obj.items():
            writer_acroform[NameObject(key)] = value
        writer._root_object.update({NameObject("/AcroForm"): writer._add_object(writer_acroform)})
    else:
        writer._root_object.update({NameObject("/AcroForm"): writer._add_object(DictionaryObject())})

    # Only try to write form values if the template actually contains fields
    if has_fields:
        # Apply values to every page in case the field is repeated across pages.
        for page in writer.pages:
            writer.update_page_form_field_values(page, field_values)

    # Ensure form appearances render properly
    acroform = writer._root_object.get("/AcroForm")
    if acroform is not None:
        acroform.update({NameObject("/NeedAppearances"): BooleanObject(True)})

    # Overlay barcode image inside every barcode field bounds (if present)
    if barcode_value:
        matches = _find_field_rects(reader, "barcode")
        for page_idx, rect in matches:
            if not rect:
                continue
            x1, y1, x2, y2 = rect
            img_width = max(1.0, x2 - x1 - 2)  # small padding
            img_height = max(1.0, y2 - y1 - 2)
            page = writer.pages[page_idx]
            page_width = float(page.mediabox.width)
            page_height = float(page.mediabox.height)
            overlay_buf = io.BytesIO()
            c = canvas.Canvas(overlay_buf, pagesize=(page_width, page_height))
            # Draw vector barcode scaled to the target rect for crisp output
            bc = code39.Standard39(barcode_value, checksum=0, barHeight=img_height, stop=1, quiet=1)
            scale_x = img_width / bc.width if bc.width else 1.0
            scale_y = img_height / bc.height if bc.height else 1.0
            c.saveState()
            c.translate(x1 + 1, y1 + 1)
            c.scale(scale_x, scale_y)
            bc.drawOn(c, 0, 0)
            c.restoreState()
            c.save()
            overlay_pdf = PdfReader(io.BytesIO(overlay_buf.getvalue()))
            writer.pages[page_idx].merge_page(overlay_pdf.pages[0])

    output = io.BytesIO()
    writer.write(output)
    output.seek(0)
    filename = f"requisition-{barcode_value or labkit_id}.pdf"
    return Response(
        output.getvalue(),
        mimetype="application/pdf",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@app.route("/labkits/barcodes.csv")
def labkits_barcodes_csv():
    """Download all labkit barcodes and types (two columns, no header)."""
    rows = list_labkits()
    output = io.StringIO()
    writer = csv.writer(output)
    for r in rows:
        writer.writerow([r.get("barcode_value") or r.get("kit_barcode") or "", r.get("labkit_type_name") or ""])
    csv_data = output.getvalue()
    return Response(
        csv_data,
        mimetype="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=labkit-barcodes.csv"},
    )


@app.route("/labkits/add", methods=["POST"])
def handle_add_labkit():
    labkit_type_id_raw = request.form.get("labkit_type_id", "").strip()
    site_id = request.form.get("site_id", "").strip()
    lot_number = request.form.get("lot_number", "").strip() or None
    measured_weight, weight_error = parse_optional_float(
        request.form.get("measured_weight"), "Measured weight"
    )
    expiry_date_str = request.form.get("expiry_date", "").strip()
    status = request.form.get("status", "").strip() or "planned"
    if weight_error:
        return redirect(url_for("labkits_page", error=weight_error))
    expiry = parse_date(expiry_date_str)
    if not labkit_type_id_raw:
        return redirect(url_for("labkits_page", error="Labkit type is required"))
    new_id = add_labkit(
        labkit_type_id=int(labkit_type_id_raw),
        site_id=int(site_id) if site_id else None,
        lot_number=lot_number,
        measured_weight=measured_weight,
        expiry_date=expiry,
        created_by=current_username(),
    )
    created = get_labkit_by_id(new_id) or {}
    display_code = created.get("barcode_value") or created.get("kit_barcode")
    db_session = SessionLocal()
    try:
        log_audit_event(
            db_session,
            entity_type="Labkit",
            entity_id=new_id,
            action="CREATE",
            description=f"Labkit {display_code} created",
        )
        db_session.commit()
    finally:
        db_session.close()
    # Update status if different than default
    if status and status != "planned":
        update_labkit_status(display_code or "", status, created_by=current_username())
    return redirect(url_for("labkits_page", message=f"Labkit '{display_code}' added."))


@app.route("/labkits/update", methods=["POST"])
def handle_update_labkit():
    try:
        labkit_id = int(request.form.get("id", "0"))
    except ValueError:
        return redirect(url_for("labkits_page", error="Invalid labkit id"))
    existing = get_labkit_by_id(labkit_id)
    if not existing:
        return redirect(url_for("labkits_page", error="Labkit not found"))

    barcode = request.form.get("kit_barcode", "").strip() or existing.get("kit_barcode", "")
    barcode_value = request.form.get("barcode_value", "").strip() or existing.get("barcode_value")
    labkit_type_id = request.form.get("labkit_type_id", "").strip()
    site_id = request.form.get("site_id", "").strip()
    lot_number = request.form.get("lot_number", "").strip() or None
    measured_weight, weight_error = parse_optional_float(
        request.form.get("measured_weight"), "Measured weight"
    )
    expiry_date_str = request.form.get("expiry_date", "").strip()
    status = request.form.get("status", "").strip() or existing["status"]
    if weight_error:
        return redirect(url_for("labkits_page", error=weight_error))
    expiry = parse_date(expiry_date_str)
    if not labkit_type_id:
        return redirect(url_for("labkits_page", error="Labkit type is required"))
    update_labkit(
        labkit_id=labkit_id,
        kit_barcode=barcode,
        barcode_value=barcode_value,
        labkit_type_id=int(labkit_type_id),
        site_id=int(site_id) if site_id else None,
        lot_number=lot_number,
        measured_weight=measured_weight,
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
            description=f"Labkit {barcode_value or barcode} updated",
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
        update_labkit_status(barcode_value or barcode, status, created_by=current_username())
    else:
        try:
            add_labkit_event(labkit_id, "updated", "Labkit updated", created_by=current_username())
        except Exception:
            pass
    return redirect(url_for("labkits_page", message=f"Labkit '{barcode_value or barcode}' updated."))


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
            description=f"Labkit {(labkit.get('barcode_value') or labkit.get('kit_barcode'))} deleted",
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
    standard_weight, weight_error = parse_optional_float(
        request.form.get("standard_weight"), "Standard weight"
    )
    weight_variance, variance_error = parse_optional_float(
        request.form.get("weight_variance"), "Weight variance"
    )
    if weight_error:
        return redirect(url_for("index", error=weight_error))
    if variance_error:
        return redirect(url_for("index", error=variance_error))
    if not name:
        return redirect(url_for("index", error="Labkit type name is required"))
    try:
        add_labkit_type(
            name,
            description,
            None,
            None,
            standard_weight,
            weight_variance,
        )
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
        "barcode_value",
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
            r.get("barcode_value") or "",
            r.get("labkit_type_name") or "",
            r.get("site_name") or "",
            r.get("status") or "",
            r.get("lot_number") or "",
            str(r.get("expiry_date") or ""),
            format_timestamp(r.get("created_at") or ""),
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
            format_timestamp(r.get("shipped_at") or ""),
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
    if not barcode:
        return redirect(url_for("index", error="Barcode is required"))
    labkit = get_labkit_by_barcode(barcode)
    if not labkit:
        return redirect(url_for("index", error="Labkit not found"))
    return redirect(url_for("labkit_detail_page", labkit_id=labkit["id"]))


def _fetch_audit_rows(from_date_val, to_date_val, entity_type_filter):
    """Shared helper to query audit rows with optional filters."""
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
        return rows
    finally:
        db_session.close()


@app.route("/export/audit")
@login_required
def export_audit():
    """Export audit log as CSV with optional filters."""
    from_date_str = request.args.get("from_date", "").strip()
    to_date_str = request.args.get("to_date", "").strip()
    entity_type_filter = request.args.get("entity_type", "").strip()

    from_date_val = parse_date(from_date_str)
    to_date_val = parse_date(to_date_str)

    rows = _fetch_audit_rows(from_date_val, to_date_val, entity_type_filter)

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(
        ["timestamp", "user", "entity_type", "entity_id", "action", "field_name", "old_value", "new_value", "description"]
    )
    for r in rows:
        writer.writerow(
            [
                format_timestamp(r.timestamp),
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


@app.route("/export/audit.pdf")
@login_required
def export_audit_pdf():
    """Export audit log as a simple PDF (same filters as CSV)."""
    from_date_str = request.args.get("from_date", "").strip()
    to_date_str = request.args.get("to_date", "").strip()
    entity_type_filter = request.args.get("entity_type", "").strip()

    from_date_val = parse_date(from_date_str)
    to_date_val = parse_date(to_date_str)

    rows = _fetch_audit_rows(from_date_val, to_date_val, entity_type_filter)

    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=letter)
    width, height = letter
    margin = 36
    y = height - margin

    pdf.setFont("Helvetica-Bold", 14)
    pdf.drawString(margin, y, "Audit Log Export")
    y -= 20
    pdf.setFont("Helvetica", 10)
    filter_text = f"Filters - entity: {entity_type_filter or 'All'}, from: {from_date_str or 'Any'}, to: {to_date_str or 'Any'}"
    pdf.drawString(margin, y, filter_text)
    y -= 20

    headers = ["timestamp", "user", "entity", "id", "action", "field", "old", "new", "description"]
    pdf.setFont("Helvetica-Bold", 9)
    pdf.drawString(margin, y, " | ".join(headers))
    y -= 14
    pdf.setFont("Helvetica", 9)

    for r in rows:
        line = " | ".join(
            [
                format_timestamp(r.timestamp),
                r.user or "",
                r.entity_type,
                str(r.entity_id),
                r.action,
                r.field_name or "",
                (r.old_value or "")[:60],
                (r.new_value or "")[:60],
                (r.description or "")[:80],
            ]
        )
        # wrap if necessary
        for chunk in [line[i : i + 150] for i in range(0, len(line), 150)]:
            if y <= margin:
                pdf.showPage()
                y = height - margin
                pdf.setFont("Helvetica", 9)
            pdf.drawString(margin, y, chunk)
            y -= 12

    pdf.showPage()
    pdf.save()
    buffer.seek(0)
    return Response(
        buffer.getvalue(),
        mimetype="application/pdf",
        headers={"Content-Disposition": "attachment; filename=audit_log.pdf"},
    )


@app.route("/export/audit/all")
@login_required
def export_audit_bundle():
    """Export audit log as a ZIP containing both CSV and PDF."""
    from_date_str = request.args.get("from_date", "").strip()
    to_date_str = request.args.get("to_date", "").strip()
    entity_type_filter = request.args.get("entity_type", "").strip()

    from_date_val = parse_date(from_date_str)
    to_date_val = parse_date(to_date_str)
    rows = _fetch_audit_rows(from_date_val, to_date_val, entity_type_filter)

    # CSV
    csv_output = io.StringIO()
    csv_writer = csv.writer(csv_output)
    csv_writer.writerow(
        ["timestamp", "user", "entity_type", "entity_id", "action", "field_name", "old_value", "new_value", "description"]
    )
    for r in rows:
        csv_writer.writerow(
            [
                format_timestamp(r.timestamp),
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
    csv_bytes = csv_output.getvalue().encode("utf-8")

    # PDF
    pdf_buffer = io.BytesIO()
    pdf = canvas.Canvas(pdf_buffer, pagesize=letter)
    width, height = letter
    margin = 36
    y = height - margin

    pdf.setFont("Helvetica-Bold", 14)
    pdf.drawString(margin, y, "Audit Log Export")
    y -= 20
    pdf.setFont("Helvetica", 10)
    filter_text = f"Filters - entity: {entity_type_filter or 'All'}, from: {from_date_str or 'Any'}, to: {to_date_str or 'Any'}"
    pdf.drawString(margin, y, filter_text)
    y -= 20

    headers = ["timestamp", "user", "entity", "id", "action", "field", "old", "new", "description"]
    pdf.setFont("Helvetica-Bold", 9)
    pdf.drawString(margin, y, " | ".join(headers))
    y -= 14
    pdf.setFont("Helvetica", 9)

    for r in rows:
        line = " | ".join(
            [
                format_timestamp(r.timestamp),
                r.user or "",
                r.entity_type,
                str(r.entity_id),
                r.action,
                r.field_name or "",
                (r.old_value or "")[:60],
                (r.new_value or "")[:60],
                (r.description or "")[:80],
            ]
        )
        for chunk in [line[i : i + 150] for i in range(0, len(line), 150)]:
            if y <= margin:
                pdf.showPage()
                y = height - margin
                pdf.setFont("Helvetica", 9)
            pdf.drawString(margin, y, chunk)
            y -= 12

    pdf.showPage()
    pdf.save()
    pdf_bytes = pdf_buffer.getvalue()

    # ZIP
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("audit_log.csv", csv_bytes)
        zf.writestr("audit_log.pdf", pdf_bytes)
    zip_buffer.seek(0)

    return Response(
        zip_buffer.getvalue(),
        mimetype="application/zip",
        headers={"Content-Disposition": "attachment; filename=audit_log_bundle.zip"},
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
        <p class="muted">Download the audit trail (CSV and PDF bundle).</p>
        <a class="btn btn-secondary" href="{{ url_for('export_audit_bundle') }}"><span class="icon">📜</span>Download audit files</a>
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
            <td>{{ s.id }}</td><td>{{ s.site_code }}</td><td>{{ s.site_name }}</td><td>{{ s.created_at|format_ts }}</td>
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
            <td>{{ t.id }}</td><td>{{ t.name }}</td><td>{{ t.description }}</td><td>{{ t.default_expiry_days }}</td><td>{{ t.created_at|format_ts }}</td>
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
            <td>{{ k.id }}</td><td>{{ k.barcode_value or k.kit_barcode }}</td><td>{{ k.labkit_type_id }}</td>
            <td>{{ k.site_id }}</td><td>{{ k.lot_number }}</td><td>{{ k.expiry_date }}</td>
            <td><span class="badge status-{{ k.status|replace(' ', '_') }}">{{ k.status }}</span></td><td>{{ k.created_at|format_ts }}</td><td>{{ k.updated_at|format_ts }}</td>
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
              <td>{{ ev.old_status }}</td><td>{{ ev.new_status }}</td><td>{{ ev.event_time|format_ts }}</td>
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
          <div class="form-field">
            <label>Standard Weight (g)</label>
            <input class="form-control" type="number" name="standard_weight" step="0.01" min="0" placeholder="e.g., 120.5">
          </div>
          <div class="form-field">
            <label>Allowed Variance (± g)</label>
            <input class="form-control" type="number" name="weight_variance" step="0.01" min="0" placeholder="e.g., 5">
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
            <tr>
              <th>ID</th>
              <th>Name</th>
              <th>Description</th>
              <th>Default Expiry Days</th>
              <th>Std Weight (g)</th>
              <th>Variance (g)</th>
              <th>Created</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
          {% for t in labkit_types %}
          <tr>
            <td>{{ t.id }}</td>
            <td>{{ t.name }}</td>
            <td>{{ t.description }}</td>
            <td>{{ t.default_expiry_days }}</td>
            <td>{{ t.standard_weight }}</td>
            <td>{{ t.weight_variance }}</td>
            <td>{{ t.created_at|format_ts }}</td>
            <td class="actions">
              <form method="post" action="{{ url_for('handle_update_kit_type') }}" class="inline-form">
                <input type="hidden" name="id" value="{{ t.id }}">
                <input class="form-control" type="text" name="name" value="{{ t.name }}" required>
                <input class="form-control" type="text" name="description" value="{{ t.description or '' }}">
                <input class="form-control" type="number" name="default_expiry_days" value="{{ t.default_expiry_days or '' }}" min="0">
                <input class="form-control" type="number" name="standard_weight" value="{{ t.standard_weight or '' }}" step="0.01" min="0" placeholder="Standard weight (g)">
                <input class="form-control" type="number" name="weight_variance" value="{{ t.weight_variance or '' }}" step="0.01" min="0" placeholder="Variance (g)">
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
              <option
                value="{{ t.id }}"
                data-expiry="{{ t.default_expiry_days or '' }}"
                data-weight="{{ t.standard_weight or '' }}"
                data-variance="{{ t.weight_variance or '' }}"
              >{{ t.name }}</option>
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
            <input class="form-control" type="text" value="Auto-generated on save" readonly>
          </div>
          <div class="form-field">
            <label>Lot Number</label>
            <input class="form-control" type="text" name="lot_number">
          </div>
          <div class="form-field">
            <label>Measured Weight (g)</label>
            <input class="form-control" type="number" name="measured_weight" id="create-measured-weight" step="0.01" min="0" placeholder="e.g., 118.4">
            <p class="small-text" id="weight-feedback"></p>
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
      <h2><span class="icon">🔍</span>Filter</h2>
      <form method="get" action="{{ url_for('labkits_page') }}" class="stacked">
        <div class="form-row">
          <div class="form-field">
            <label>Kit Type</label>
            <select class="form-control" name="kit_type_id">
              <option value="">All</option>
              {% for t in labkit_types %}
              <option value="{{ t.id }}" {% if kit_type_id|default('') == t.id|string %}selected{% endif %}>{{ t.name }}</option>
              {% endfor %}
            </select>
          </div>
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
            <label>Status</label>
            <select class="form-control" name="status">
              <option value="">All</option>
              {% for st in status_options %}
              <option value="{{ st }}" {% if st == status %}selected{% endif %}>{{ st }}</option>
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
          <p class="eyebrow">Inventory</p>
          <h2><span class="icon">📦</span>Existing Labkits</h2>
        </div>
        <div class="button-row">
          <a class="btn btn-secondary" href="{{ url_for('export_labkits') }}"><span class="icon">📤</span>Export CSV</a>
          <a class="btn btn-secondary" href="{{ url_for('labkits_barcodes_csv') }}"><span class="icon">🖨️</span>Barcodes CSV</a>
        </div>
      </div>
          <div class="table-wrapper">
            <table>
              <thead>
                <tr>
                  <th>ID</th><th>Barcode</th><th>Labkit Type</th><th>Site</th><th>Lot</th><th>Weight (g)</th><th>Expiry</th><th>Status</th><th>Actions</th>
                </tr>
              </thead>
              <tbody>
              {% for k in labkits %}
              <tr>
                <td>{{ k.id }}</td>
            <td>{{ k.barcode_value or k.kit_barcode }}</td>
            <td>{{ k.labkit_type_name }}</td>
            <td>{{ k.site_name or 'Central depot' }}</td>
            <td>{{ k.lot_number }}</td>
            <td>{{ k.measured_weight if k.measured_weight is not none else '' }}</td>
            <td>{{ k.expiry_date }}</td>
                <td><span class="badge status-{{ k.status|replace(' ', '_') }}">{{ k.status }}</span></td>
                <td class="actions">
                  <form method="post" action="{{ url_for('handle_update_labkit') }}" class="inline-form">
                    <input type="hidden" name="id" value="{{ k.id }}">
                <input class="form-control" type="text" name="kit_barcode" value="{{ k.barcode_value or k.kit_barcode }}" readonly>
                <input type="hidden" name="barcode_value" value="{{ k.barcode_value }}">
                <select class="form-control" name="labkit_type_id" required data-kit-id="{{ k.id }}">
                  {% for t in labkit_types %}
                  <option value="{{ t.id }}"
                    data-weight="{{ t.standard_weight or '' }}"
                    data-variance="{{ t.weight_variance or '' }}"
                    {% if t.id == k.labkit_type_id %}selected{% endif %}>{{ t.name }}</option>
                  {% endfor %}
                </select>
                <select class="form-control" name="site_id">
                  <option value="" {% if not k.site_id %}selected{% endif %}>Central depot (no site)</option>
                    {% for s in sites %}
                    <option value="{{ s.id }}" {% if s.id == k.site_id %}selected{% endif %}>{{ s.site_name }}</option>
                    {% endfor %}
                </select>
                <input class="form-control" type="text" name="lot_number" value="{{ k.lot_number or '' }}" placeholder="Lot #">
                <input class="form-control update-weight" data-kit-id="{{ k.id }}" type="number" name="measured_weight" value="{{ k.measured_weight or '' }}" step="0.01" min="0" placeholder="Weight (g)">
                <p class="small-text" id="weight-feedback-{{ k.id }}"></p>
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
              <a class="btn btn-link" href="{{ url_for('labkit_requisition', labkit_id=k.id) }}"><span class="icon">📄</span>Requisition</a>
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
    document.addEventListener('DOMContentLoaded', function() {
      // Shared helper for weight range feedback
      function renderWeightFeedback(typeSelect, weightInput, feedbackEl) {
        if (!typeSelect || !weightInput || !feedbackEl) return;
        const opt = typeSelect.selectedOptions[0];
        if (!opt) {
          feedbackEl.textContent = "";
          feedbackEl.className = "small-text";
          return;
        }
        const standard = parseFloat(opt.dataset.weight);
        const variance = parseFloat(opt.dataset.variance);
        const measured = parseFloat(weightInput.value);

        if (isNaN(measured)) {
          feedbackEl.textContent = "";
          feedbackEl.className = "small-text";
          return;
        }
        if (isNaN(standard) || isNaN(variance)) {
          feedbackEl.textContent = "No standard weight defined for this kit type.";
          feedbackEl.className = "small-text muted";
          return;
        }

        const min = standard - variance;
        const max = standard + variance;
        const within = measured >= min && measured <= max;
        const rangeText = `Expected: ${standard} ± ${variance} g (Range ${min.toFixed(2)}–${max.toFixed(2)} g)`;

        if (within) {
          feedbackEl.textContent = `Within expected range. ${rangeText}`;
          feedbackEl.className = "small-text text-success";
        } else {
          feedbackEl.textContent = `Outside expected range. ${rangeText}`;
          feedbackEl.className = "small-text text-danger";
        }
      }

      // Prefill expiry based on kit type default days when empty (create form)
      const typeSelect = document.getElementById('create-labkit-type');
      const expiryInput = document.getElementById('create-expiry');
      const weightInput = document.getElementById('create-measured-weight');
      const weightFeedback = document.getElementById('weight-feedback');

      if (typeSelect && expiryInput) {
        typeSelect.addEventListener('change', function() {
          const days = parseInt(this.selectedOptions[0].getAttribute('data-expiry'));
          if (!expiryInput.value && !isNaN(days)) {
            const today = new Date();
            today.setDate(today.getDate() + days);
            const iso = today.toISOString().split('T')[0];
            expiryInput.value = iso;
          }
          renderWeightFeedback(typeSelect, weightInput, weightFeedback);
        });
      }

      if (typeSelect) {
        typeSelect.addEventListener('change', function() {
          renderWeightFeedback(typeSelect, weightInput, weightFeedback);
        });
      }
      if (weightInput) {
        weightInput.addEventListener('input', function() {
          renderWeightFeedback(typeSelect, weightInput, weightFeedback);
        });
      }

      // Inline update forms: show same weight range feedback
      const updateWeightInputs = document.querySelectorAll('.update-weight');
      updateWeightInputs.forEach(function(input) {
        const kitId = input.dataset.kitId;
        const select = document.querySelector(`select[data-kit-id="${kitId}"]`);
        const feedback = document.getElementById(`weight-feedback-${kitId}`);
        if (!select || !feedback) return;
        const handler = function() {
          renderWeightFeedback(select, input, feedback);
        };
        select.addEventListener('change', handler);
        input.addEventListener('input', handler);
        handler(); // run once on load to populate when data exists
      });
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
            <label>Address Line</label>
            <input class="form-control" type="text" name="address_line">
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
        <div class="form-row">
          <div class="form-field">
            <label>Investigator Name <span class="muted">(site lead)</span></label>
            <input class="form-control" type="text" name="investigator_name" required placeholder="e.g., Dr. Jane Doe">
          </div>
          <div class="form-field">
            <label>Investigator Room #</label>
            <input class="form-control" type="text" name="investigator_room" placeholder="e.g., B312">
          </div>
        </div>
        <p class="muted small-text">Investigator contact is created automatically with the role "Investigator" and marked as primary for this site.</p>
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
                <input class="form-control" type="text" name="address_line" value="{{ s.address_line1 or '' }}" placeholder="Address line">
                <input class="form-control" type="text" name="city" value="{{ s.city or '' }}" placeholder="City">
                <input class="form-control" type="text" name="state" value="{{ s.state or '' }}" placeholder="State">
                <input class="form-control" type="text" name="postal_code" value="{{ s.postal_code or '' }}" placeholder="Postal code">
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
        {{ site.address_line1 or '' }}<br>
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
        <div class="form-row">
          <div class="form-field">
            <label>Room Number</label>
            <input class="form-control" type="text" name="room_number" placeholder="e.g., B312">
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
          <thead><tr><th>Name</th><th>Role</th><th>Email</th><th>Phone</th><th>Room</th><th>Primary</th><th>Actions</th></tr></thead>
          <tbody>
          {% for c in contacts %}
          <tr>
            <td>{{ c.name }}</td>
            <td>{{ c.role }}</td>
            <td>{{ c.email }}</td>
            <td>{{ c.phone }}</td>
            <td>{{ c.room_number }}</td>
            <td>{{ 'Yes' if c.is_primary else 'No' }}</td>
            <td class="actions">
              <form method="post" action="{{ url_for('handle_update_contact', site_id=site.id) }}" class="inline-form">
                <input type="hidden" name="id" value="{{ c.id }}">
                <input class="form-control" type="text" name="name" value="{{ c.name }}" required>
                <input class="form-control" type="text" name="role" value="{{ c.role or '' }}">
                <input class="form-control" type="email" name="email" value="{{ c.email or '' }}">
                <input class="form-control" type="tel" name="phone" value="{{ c.phone or '' }}">
                <input class="form-control" type="text" name="room_number" value="{{ c.room_number or '' }}" placeholder="Room #">
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
            <option value="{{ lk.id }}">{{ lk.barcode_value or lk.kit_barcode }} ({{ lk.labkit_type_name }})</option>
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
            <td>{{ sh.shipped_at|format_ts }}</td>
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
            <option value="{{ lk.id }}" {% if lk.id in selected_labkit_ids %}selected{% endif %}>{{ lk.barcode_value or lk.kit_barcode }} ({{ lk.labkit_type_name }})</option>
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
            <td>{{ lk.barcode_value or lk.kit_barcode }}</td>
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
    .barcode-img img { width: 260px; height: 100px; object-fit: contain; }
    .barcode-text { font-size: 16px; font-weight: 600; letter-spacing: 0.08em; margin-top: 8px; }
    @media print { button { display: none; } body { margin: 0; } .label-card { box-shadow: none; border: 1px solid #000; } }
  </style>
</head>
<body>
  <div class="label-card">
    <div class="barcode">Kit: {{ barcode_value }}</div>
    <div class="meta">Type: {{ labkit.labkit_type_name }}</div>
    <div class="meta">Expiry: {{ labkit.expiry_date }}</div>
    <div class="meta">Status: {{ labkit.status }}</div>
    <div class="barcode-img"><img src="{{ barcode_uri }}" alt="Barcode"></div>
    <div class="barcode-text">{{ barcode_value }}</div>
  </div>
  <div style="margin-top: 12px; display: flex; gap: 8px;">
    <button class="btn btn-primary" onclick="window.print()">Print</button>
    <a class="btn btn-secondary" href="{{ url_for('labkit_label_csv', labkit_id=labkit.id) }}">Download CSV</a>
  </div>
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
      <h1 class="page-title"><span class="icon">📦</span>Labkit {{ labkit.barcode_value or labkit.kit_barcode }}</h1>
      <p class="subtitle">Full history and metadata for this kit.</p>
    </div>
    {% if message %}<div class="alert success">{{ message }}</div>{% endif %}
    {% if error %}<div class="alert danger">{{ error }}</div>{% endif %}

    <div class="card">
      <h2><span class="icon">🧾</span>Summary</h2>
      <div class="meta-grid">
        <div><p class="eyebrow">Barcode</p><p>{{ labkit.barcode_value or labkit.kit_barcode }}</p></div>
        <div><p class="eyebrow">Type</p><p>{{ labkit.labkit_type_name }}</p></div>
        <div><p class="eyebrow">Site</p><p>{{ labkit.site_name or 'Central depot' }}</p></div>
        <div><p class="eyebrow">Lot</p><p>{{ labkit.lot_number }}</p></div>
        <div><p class="eyebrow">Measured Weight</p><p>{{ labkit.measured_weight if labkit.measured_weight is not none else '—' }} g</p></div>
        <div><p class="eyebrow">Expiry</p><p>{{ labkit.expiry_date }}</p></div>
        <div><p class="eyebrow">Status</p><p><span class="badge status-{{ labkit.status|replace(' ', '_') }}">{{ labkit.status }}</span></p></div>
        <div><p class="eyebrow">Created</p><p>{{ labkit.created_at|format_ts }}</p></div>
        <div><p class="eyebrow">Updated</p><p>{{ labkit.updated_at|format_ts }}</p></div>
      </div>
    </div>

    <div class="card">
      <h2><span class="icon">📄</span>Requisition Form</h2>
      <p class="muted">Download the pre-filled requisition PDF for this kit.</p>
      <a class="btn btn-secondary" href="{{ url_for('labkit_requisition', labkit_id=labkit.id) }}"><span class="icon">📄</span>Download requisition PDF</a>
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
            <td>{{ ev.created_at|format_ts }}</td>
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
            <td>{{ a.timestamp|format_ts }}</td>
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
            <td>{{ k.barcode_value or k.kit_barcode }}</td>
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
            <td>{{ k.barcode_value or k.kit_barcode }}</td>
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
            <td>{{ k.barcode_value or k.kit_barcode }}</td>
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
