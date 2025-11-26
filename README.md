# Labkit-tracking  
System to track lab kits for clinical research  

## What is this  
Labkit-tracking is a lightweight web application to manage lab kits used in a clinical trial.  
It helps to:

- Create and manage different lab kit types (per visit / study visit)  
- Track individual lab kits: lot number, expiry date, QR-code/ barcode, and status  
- Assign kits to sites and shipments, and track which kits were sent where  
- Maintain an audit trail of all important changes (kit creation, updates, status changes, shipments, deletions)  
- Export database backups (SQL dump) and audit logs for transparency and compliance  

The idea is to provide a simple, easy-to-use management tool for trial coordinators and site staff — minimal setup required.

---

## ✅ Main features  

- Kit type management  
- Kit lifecycle and status tracking (planned → shipped → received → used / expired / cancelled)  
- Kit lot number, expiry date, and barcode/QR code support  
- Site and shipment assignment  
- Audit trail log of all CRUD and status events  
- Full database export and backup support  
- Simple, web-based UI for inventory oversight  

---

## 🧰 Tech stack  

- **Python 3**  
- **Flask** web framework  
- **PostgreSQL** database  
- Standard HTML/CSS + possibly minimal JS (for UI)  
- Uses virtual environment (venv) to isolate dependencies  

---

## 🚀 How to install and run (on your “server” machine, e.g. Lenovo)  

```bash
# 1. Clone repo  
git clone https://github.com/JulianeEBB/Labkit-tracking.git  
cd Labkit-tracking  

# 2. Create virtual environment & activate  
sudo apt update  
sudo apt install python3-venv -y       # (only if not installed)  
python3 -m venv /full/path/to/Labkit-tracking/venv  
source /full/path/to/Labkit-tracking/venv/bin/activate  

# 3. Install dependencies  
pip install -r requirements.txt  

# 4. Initialize database (optional, if first time)  
python3 init_db.py  

# 5. Run the application  
python3 app.py  
