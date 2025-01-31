from flask import Flask, render_template, request, redirect, url_for, session, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from datetime import datetime, timedelta
import os
from werkzeug.utils import secure_filename
import pandas as pd
import pytz


app = Flask(__name__)
app.secret_key = 'supersecretkey'
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{os.path.join(BASE_DIR, "database", "cleanhive.db")}'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)
migrate = Migrate(app, db)  # ✅ Ensure Flask-Migrate is attached

UPLOAD_FOLDER = "uploads"
ALLOWED_EXTENSIONS = {'xlsx'}

if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)

app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

CLEANERS = ["Sebastian", "Wilberto", "Lou"]

# ✅ Restored function: Check if uploaded file is allowed
def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

# ✅ Database Models
class Reservation(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    customer_name = db.Column(db.String(100))
    vehicle_number = db.Column(db.String(10))
    vehicle_class = db.Column(db.String(50))
    pickup_time = db.Column(db.DateTime)
    last_cleaned = db.Column(db.String(50), default="-")
    last_rented = db.Column(db.String(50), default="-")
    cleaned_by = db.Column(db.String(100), default="")
    notes = db.Column(db.Text, default="")
    is_vip = db.Column(db.Boolean, default=False)
    early_pickup = db.Column(db.Boolean, default=False)
    completed = db.Column(db.Boolean, default=False)  # ✅ New column


class CleanupLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    vehicle_number = db.Column(db.String(10), nullable=False)
    cleaned_date = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    cleaned_by = db.Column(db.String(100), nullable=False)
    pickup_time = db.Column(db.DateTime, nullable=True)
    notes = db.Column(db.String(255), nullable=True)  # This should be here

    def __init__(self, vehicle_number, cleaned_date, cleaned_by, pickup_time=None, notes=None):
        self.vehicle_number = vehicle_number
        self.cleaned_date = cleaned_date
        self.cleaned_by = cleaned_by
        self.pickup_time = pickup_time
        self.notes = notes  # Include this field


    
    
    # Add ActiveCleans Table to Track Ongoing Cleans
class ActiveCleans(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    vehicle_number = db.Column(db.String(10), unique=True, nullable=False)
    cleaner = db.Column(db.String(100), nullable=False)
    start_time = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    estimated_completion = db.Column(db.DateTime, nullable=False)  # Based on class average

# ✅ Login Routes
@app.route('/', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        user = request.form.get('user')
        if user in CLEANERS:
            session['user'] = user
            return redirect(url_for('reservation_page'))
    return render_template('login.html')

@app.route('/admin', methods=['POST'])
def admin_login():
    username = request.form.get('admin_username')
    password = request.form.get('admin_password')

    if username == "admin" and password == "password123":
        session['admin'] = True
        return redirect(url_for('admin_dashboard'))
    return redirect(url_for('login'))

# ✅ Reservations Page
@app.route('/reservation')
def reservation_page():
    if 'user' in session:
        return render_template('reservation.html')
    return redirect(url_for('login'))

# ✅ Fetch Reservations for AJAX Updating
@app.route('/fetch_reservations', methods=['GET'])
def fetch_reservations():
    active_cleans = {clean.vehicle_number for clean in ActiveCleans.query.all()}
    reservations = Reservation.query.filter_by(completed=False).order_by(Reservation.pickup_time).all()

    data = []
    for res in reservations:
        # Get the latest cleaned date for the vehicle from logs
        latest_clean = CleanupLog.query.filter_by(vehicle_number=res.vehicle_number).order_by(CleanupLog.cleaned_date.desc()).first()

        # Update the 'last_cleaned' field in reservations if applicable
        res.last_cleaned = latest_clean.cleaned_date.strftime('%Y-%m-%d %H:%M') if latest_clean else "-"
        db.session.commit()

        # Add the reservation data
        data.append({
            "vehicle_number": res.vehicle_number,
            "vehicle_class": res.vehicle_class,
            "pickup_time": res.pickup_time.isoformat(),
            "last_cleaned": res.last_cleaned,
            "last_rented": res.last_rented,
            "cleaned_by": res.cleaned_by,
            "notes": res.notes,
            "is_vip": res.is_vip,
            "early_pickup": res.early_pickup,
            "is_active_clean": res.vehicle_number in active_cleans
        })

    return jsonify(data)



@app.route('/remove-reservation/<vehicle_number>', methods=['POST'])
def remove_reservation(vehicle_number):
    if 'user' not in session:
        return jsonify({"status": "error", "message": "Unauthorized"}), 403

    reservation = Reservation.query.filter_by(vehicle_number=vehicle_number).first()
    if reservation:
        reservation.completed = True  # ✅ Mark as completed
        db.session.commit()
        return jsonify({"status": "success"})
    
    return jsonify({"status": "error", "message": "Reservation not found"}), 404



# ✅ Start Clean Logic
@app.route('/start-clean/<vehicle_number>', methods=['POST'])
def start_clean(vehicle_number):
    if 'user' not in session:
        return jsonify({"status": "error", "message": "Unauthorized"}), 403

    reservation = Reservation.query.filter_by(vehicle_number=vehicle_number).first()
    if reservation:
        cleaner_name = session['user']

        # Check if already active
        existing_clean = ActiveCleans.query.filter_by(vehicle_number=vehicle_number).first()
        if not existing_clean:
            # Get estimated clean time based on vehicle class
            class_average = get_class_clean_time(reservation.vehicle_class)
            estimated_completion = datetime.utcnow() + timedelta(minutes=class_average)

            # Get all selected cleaners from request (if available)
            selected_cleaners = request.form.getlist('cleaner')
            all_cleaners = ", ".join(selected_cleaners) if selected_cleaners else cleaner_name

            # Add to Active Cleans
            new_clean = ActiveCleans(
                vehicle_number=vehicle_number,
                cleaner=all_cleaners,  # ✅ Store all cleaners
                estimated_completion=estimated_completion
            )
            db.session.add(new_clean)
            db.session.commit()

        return jsonify({
            "status": "success",
            "vehicle_number": vehicle_number,
            "cleaners": CLEANERS  # ✅ Return cleaner list for UI
        })

    return jsonify({"status": "error", "message": "Reservation not found"}), 404



# Get estimated clean time based on class
def get_class_clean_time(vehicle_class):
    clean_time_map = {
        "Bcar": 15,
        "15PA": 45,
        "SUV": 30,
        "Truck": 40
    }
    return clean_time_map.get(vehicle_class, 20)  # Default 20 mins if unknown class

# ✅ Finish Clean Logic
@app.route('/finish-clean/<vehicle_number>', methods=['POST'])
def finish_clean(vehicle_number):
    if 'user' not in session:
        return jsonify({"status": "error", "message": "Unauthorized"}), 403

    active_clean = ActiveCleans.query.filter_by(vehicle_number=vehicle_number).first()
    reservation = Reservation.query.filter_by(vehicle_number=vehicle_number).first()

    if active_clean:
        selected_cleaners = request.form.getlist('cleaner')
        all_cleaners = ", ".join(selected_cleaners) if selected_cleaners else active_clean.cleaner

         # Log the clean into CleanupLog
        new_log = CleanupLog(
            vehicle_number=vehicle_number,
            cleaned_date=datetime.utcnow(),
            cleaned_by=all_cleaners,
            pickup_time=reservation.pickup_time if reservation else None  # ✅ Include pickup time
        )
        db.session.add(new_log)

        # Update reservation's status
        if reservation:
            reservation.last_cleaned = datetime.utcnow().strftime('%Y-%m-%d %H:%M')
            reservation.completed = True  # ✅ Mark reservation as completed

        # Remove from ActiveCleans
        db.session.delete(active_clean)
        db.session.commit()

        return jsonify({"status": "success"})

    return jsonify({"status": "error", "message": "Active clean not found"}), 404







@app.route('/fetch_current_cleans', methods=['GET'])
def fetch_current_cleans():
    active_cleans = ActiveCleans.query.all()
    data = [{
        "cleaner": clean.cleaner,
        "vehicle_number": clean.vehicle_number,
        "start_time": clean.start_time.strftime('%Y-%m-%d %H:%M:%S'),
        "estimated_completion": clean.estimated_completion.strftime('%Y-%m-%d %H:%M:%S')
    } for clean in active_cleans]
    return jsonify(data)



# ✅ Admin Dashboard
@app.route('/admin-dashboard')
def admin_dashboard():
    if 'admin' in session:
        reservations = Reservation.query.filter_by(completed=False).order_by(Reservation.pickup_time).all()
        return render_template('admin_dashboard.html', reservations=reservations)
    return redirect(url_for('login'))

@app.route('/completed')
def completed_page():
    if 'admin' in session:
        return render_template('completed.html')
    return redirect(url_for('login'))

@app.route('/fetch_completed_reservations', methods=['GET'])
def fetch_completed_reservations():
    completed_reservations = Reservation.query.filter_by(completed=True).order_by(Reservation.pickup_time).all()

    data = [
        {
            "vehicle_number": res.vehicle_number,
            "vehicle_class": res.vehicle_class,
            "pickup_time": res.pickup_time.isoformat(),
            "last_cleaned": res.last_cleaned,
            "last_rented": res.last_rented,
            "cleaned_by": res.cleaned_by,
            "notes": res.notes,
            "is_vip": res.is_vip,
            "early_pickup": res.early_pickup,
        }
        for res in completed_reservations
    ]
    return jsonify(data)




# ✅ Upload Reservations
@app.route('/upload-reservations', methods=['GET', 'POST'])
def upload_reservations():
    if 'admin' not in session:
        return redirect(url_for('login'))

    if request.method == 'POST':
        file = request.files.get('file')
        if not file or file.filename == '':
            return "No file selected", 400

        if not allowed_file(file.filename):
            return "Invalid file format. Only .xlsx allowed", 400

        filename = secure_filename(file.filename)
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(filepath)
        process_uploaded_reservations(filepath)
        return redirect(url_for('admin_dashboard'))

    return render_template('upload_reservations.html')

# ✅ Toggle VIP Flag
@app.route('/toggle-vip/<int:reservation_id>', methods=['POST'])
def toggle_vip(reservation_id):
    if 'admin' not in session:
        return redirect(url_for('login'))

    reservation = Reservation.query.get(reservation_id)
    if reservation:
        reservation.is_vip = not reservation.is_vip
        db.session.commit()
    return redirect(url_for('admin_dashboard'))

# ✅ Toggle Early Pickup Flag
@app.route('/toggle-early/<int:reservation_id>', methods=['POST'])
def toggle_early(reservation_id):
    if 'admin' not in session:
        return redirect(url_for('login'))

    reservation = Reservation.query.get(reservation_id)
    if reservation:
        reservation.early_pickup = not reservation.early_pickup
        db.session.commit()
    return redirect(url_for('admin_dashboard'))


@app.route('/edit-note/<int:reservation_id>', methods=['POST'])
def edit_note(reservation_id):
    if 'admin' not in session:
        return redirect(url_for('login'))

    reservation = Reservation.query.get(reservation_id)
    if reservation:
        reservation.notes = request.form.get('notes', "")
        db.session.commit()

    return redirect(url_for('admin_dashboard'))


@app.route('/manual-clean')
def manual_clean_page():
    if 'user' not in session:
        return redirect(url_for('login'))
    return render_template('manual_clean.html', current_time=datetime.now())  # ✅ Now it works!



@app.route('/submit-manual-clean', methods=['POST'])
def submit_manual_clean():
    if 'user' not in session:
        return redirect(url_for('login'))

    vehicle_number = request.form.get('vehicle_number')
    cleaner_name = session['user']
    clean_date = datetime.strptime(request.form.get('clean_date'), '%m/%d/%Y, %I:%M %p')

    # Combine logged-in cleaner with additional cleaners
    additional_cleaners = request.form.getlist('additional_cleaners')
    all_cleaners = ", ".join([cleaner_name] + additional_cleaners)

    # Fetch the purpose of the clean
    purpose = request.form.get('purpose', 'Rental')  # Default to "Rental"

    # Create a new log entry
    new_clean = CleanupLog(
        vehicle_number=vehicle_number,
        cleaned_date=clean_date,
        cleaned_by=all_cleaners,
        pickup_time=None,  # Manual entries have no actual pickup time
        notes=purpose  # Store "Rental" or "Sales" for manual entries
    )
    db.session.add(new_clean)
    db.session.commit()

    return redirect(url_for('reservation_page'))




@app.route('/logs')
def logs_page():
    if 'admin' not in session:
        return redirect(url_for('login'))

    cleaners = CLEANERS  # Defined in the app
    return render_template('logs.html', cleaners=cleaners)


@app.route('/fetch-logs', methods=['GET'])
def fetch_logs():
    if 'admin' not in session:
        return jsonify([]), 403

    # Get filters from the query string
    cleaner = request.args.get('cleaner', '').strip()
    vehicle = request.args.get('vehicle', '').strip()
    start_date = request.args.get('start', '').strip()
    end_date = request.args.get('end', '').strip()

    # Base query (default: fetch all)
    query = CleanupLog.query

    if cleaner:
        query = query.filter(CleanupLog.cleaned_by.like(f"%{cleaner}%"))
    if vehicle:
        query = query.filter(CleanupLog.vehicle_number.like(f"%{vehicle}%"))
    if start_date:
        try:
            query = query.filter(CleanupLog.cleaned_date >= datetime.strptime(start_date, "%Y-%m-%d"))
        except ValueError:
            pass  # Invalid date format, skip filtering
    if end_date:
        try:
            query = query.filter(CleanupLog.cleaned_date <= datetime.strptime(end_date, "%Y-%m-%d"))
        except ValueError:
            pass  # Invalid date format, skip filtering

    logs = query.order_by(CleanupLog.cleaned_date.desc()).all()

    # Fetch logs with pickup time and notes
    data = []
    for log in logs:
        data.append({
            "clean_date": log.cleaned_date.isoformat(),  # Ensure ISO format for frontend compatibility
            "vehicle_number": log.vehicle_number,
            "cleaners": log.cleaned_by,
            "notes": log.notes or "-",  # Show notes for manual entries
            "pickup_time": log.pickup_time.isoformat() if log.pickup_time else "-"  # Show "-" for manual entries
        })

    return jsonify(data)






@app.route('/export-logs', methods=['GET'])
def export_logs():
    if 'admin' not in session:
        return redirect(url_for('login'))

    # Fetch the same data as `fetch_logs`
    cleaner = request.args.get('cleaner', '')
    vehicle = request.args.get('vehicle', '')
    start_date = request.args.get('start', '')
    end_date = request.args.get('end', '')

    query = CleanupLog.query
    if cleaner:
        query = query.filter(CleanupLog.cleaned_by.like(f"%{cleaner}%"))
    if vehicle:
        query = query.filter(CleanupLog.vehicle_number.like(f"%{vehicle}%"))
    if start_date:
        query = query.filter(CleanupLog.cleaned_date >= start_date)
    if end_date:
        query = query.filter(CleanupLog.cleaned_date <= end_date)

    logs = query.all()

    # Generate CSV
    csv_data = "Date/Time,Vehicle #,Cleaner(s),Notes\n"
    for log in logs:
        csv_data += f"{log.cleaned_date},{log.vehicle_number},{log.cleaned_by},{log.notes if hasattr(log, 'notes') else ''}\n"

    # Send as a file response
    response = make_response(csv_data)
    response.headers['Content-Disposition'] = 'attachment; filename=logs.csv'
    response.mimetype = 'text/csv'
    return response


@app.route('/stats')
def stats_page():
    if 'admin' not in session:
        return redirect(url_for('login'))
    return render_template('stats.html')


@app.route('/fetch-stats', methods=['GET'])
def fetch_stats():
    if 'admin' not in session:
        return jsonify({}), 403

    # Cleaner Performance
    week_start = (datetime.datetime.now() - datetime.timedelta(days=7)).date()
    cleaner_performance = db.session.query(
        CleanupLog.cleaned_by, db.func.count(CleanupLog.id)
    ).filter(
        CleanupLog.cleaned_date >= week_start
    ).group_by(
        CleanupLog.cleaned_by
    ).all()

    # Daily Cleans
    daily_cleans = db.session.query(
        db.func.date(CleanupLog.cleaned_date), db.func.count(CleanupLog.id)
    ).filter(
        CleanupLog.cleaned_date >= week_start
    ).group_by(
        db.func.date(CleanupLog.cleaned_date)
    ).all()

    # Average Cleaning Times by Class
    cleaning_times = db.session.query(
        Reservation.vehicle_class,
        db.func.avg(
            db.func.julianday(CleanupLog.cleaned_date) - db.func.julianday(Reservation.pickup_time)
        ) * 24 * 60  # Convert to minutes
    ).join(
        CleanupLog, CleanupLog.vehicle_number == Reservation.vehicle_number
    ).group_by(
        Reservation.vehicle_class
    ).all()

    # Busiest Months
    busiest_months = db.session.query(
        db.func.strftime('%Y-%m', CleanupLog.cleaned_date),
        db.func.count(CleanupLog.id)
    ).group_by(
        db.func.strftime('%Y-%m', CleanupLog.cleaned_date)
    ).order_by(
        db.func.count(CleanupLog.id).desc()
    ).limit(12).all()

    data = {
        "cleanerPerformance": [{"cleaner": c[0], "cleans": c[1]} for c in cleaner_performance],
        "dailyCleans": [{"date": d[0], "count": d[1]} for d in daily_cleans],
        "cleaningTimes": [{"vehicle_class": c[0], "average_time": round(c[1])} for c in cleaning_times],
        "busiestMonths": [{"month": m[0], "total_cleans": m[1]} for m in busiest_months]
    }

    return jsonify(data)



@app.route('/current-clean')
def current_clean_page():
    if 'admin' not in session:
        return redirect(url_for('login'))
    return render_template('current_clean.html')




# ✅ Process Excel Uploads
def process_uploaded_reservations(file_path):
    try:
        df = pd.read_excel(file_path)
    except Exception as e:
        print(f"Error loading Excel file: {e}")
        return

    required_columns = {'Name', 'Class', 'Unit #', 'Pickup Date'}
    if not required_columns.issubset(df.columns):
        print("Error: Missing required columns")
        return

    df = df[['Name', 'Class', 'Unit #', 'Pickup Date']]
    df.columns = ['customer_name', 'vehicle_class', 'vehicle_number', 'pickup_time']
    df['customer_name'] = df['customer_name'].astype(str)
    df['vehicle_number'] = df['vehicle_number'].astype(str)
    df['pickup_time'] = pd.to_datetime(df['pickup_time'], errors='coerce')
    df = df.dropna(subset=['pickup_time'])

    for _, row in df.iterrows():
        new_reservation = Reservation(
            customer_name=row['customer_name'],
            vehicle_class=row['vehicle_class'],
            vehicle_number=row['vehicle_number'],
            pickup_time=row['pickup_time'],
            last_cleaned="-",
            last_rented="-",
            cleaned_by=""
        )

        if not Reservation.query.filter_by(vehicle_number=new_reservation.vehicle_number).first():
            db.session.add(new_reservation)

    db.session.commit()

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    
    # Get port from environment variable (Render provides this)
    port = int(os.environ.get("PORT", 5000))  # Default to 5000 if PORT is not set
    
    # Run Flask on 0.0.0.0 to make it accessible on Render
    app.run(host="0.0.0.0", port=port, debug=True)
