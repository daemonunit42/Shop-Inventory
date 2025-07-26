from flask import Flask, render_template, request, redirect, url_for
from datetime import datetime, timedelta
import json
import os
from pathlib import Path
from twilio.rest import Client

app = Flask(__name__)

# File paths
DATA_DIR = Path(__file__).parent / 'data'
DATA_FILE = DATA_DIR / 'products.json'
SETTINGS_FILE = DATA_DIR / 'settings.json'
CONFIG_FILE = DATA_DIR / 'config.json'

# Ensure data directory exists
DATA_DIR.mkdir(exist_ok=True)

# Default settings
DEFAULT_SETTINGS = {
    'theme': 'light',
    'items_per_page': 10,
    'sms_alerts': True,
    'alert_days': 3,
    'phone_number': '+918072329996'  # Default phone number
}

# Twilio configuration
DEFAULT_CONFIG = {
    'account_sid': 'AC6733eb83107d7a941592307f25489eea',
    'auth_token': 'a593dbd3732e1454be235049ff66d78f',
    'twilio_number': '+13868547912'  # Your Twilio phone number
}

def load_config():
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE, 'r') as f:
            return {**DEFAULT_CONFIG, **json.load(f)}
    return DEFAULT_CONFIG

def load_settings():
    if SETTINGS_FILE.exists():
        with open(SETTINGS_FILE, 'r') as f:
            return {**DEFAULT_SETTINGS, **json.load(f)}
    return DEFAULT_SETTINGS

def save_settings(settings):
    with open(SETTINGS_FILE, 'w') as f:
        json.dump(settings, f, indent=2)

def load_products():
    if DATA_FILE.exists():
        with open(DATA_FILE, 'r') as f:
            data = json.load(f)
            # Convert string dates back to date objects
            for product in data:
                product['manufacture_date'] = datetime.strptime(product['manufacture_date'], '%Y-%m-%d').date()
                product['expiry_date'] = datetime.strptime(product['expiry_date'], '%Y-%m-%d').date()
                product['added_date'] = datetime.strptime(product['added_date'], '%Y-%m-%d').date()
            return data
    return []

def save_products(products_list):
    # Convert date objects to strings for JSON serialization
    serializable_products = []
    for product in products_list:
        serialized = product.copy()
        serialized['manufacture_date'] = product['manufacture_date'].isoformat()
        serialized['expiry_date'] = product['expiry_date'].isoformat()
        serialized['added_date'] = product['added_date'].isoformat()
        serializable_products.append(serialized)
    
    with open(DATA_FILE, 'w') as f:
        json.dump(serializable_products, f, indent=2)

def calculate_urgency(expiry_date):
    today = datetime.now().date()
    days_remaining = (expiry_date - today).days
    
    if days_remaining < 0:
        return 'expired', f"Expired {-days_remaining} days ago"
    elif days_remaining == 0:
        return 'urgent', "Expires today!"
    elif days_remaining <= 3:  # Changed from 7 to 3 for SMS alerts
        return 'urgent', f"Expires in {days_remaining} days"
    elif days_remaining <= 30:
        return 'soon', f"Expires in {days_remaining} days"
    return 'normal', f"Expires in {days_remaining} days"

def send_sms_alert(product):
    config = load_config()
    settings = load_settings()
    
    if not settings.get('sms_alerts', True):
        return False
    
    if not settings.get('phone_number'):
        return False
    
    try:
        client = Client(config['account_sid'], config['auth_token'])
        
        message = client.messages.create(
            body=f"ALERT: Product '{product['name']}' ({product['quantity']} {product['unit']}) is expiring in {product['status_text'].replace('Expires in ', '').replace(' days', '')} days!",
            from_=config['twilio_number'],
            to=settings['phone_number']
        )
        return True
    except Exception as e:
        print(f"Failed to send SMS: {e}")
        return False

def check_expiry_alerts():
    products = load_products()
    settings = load_settings()
    alert_days = settings.get('alert_days', 3)
    alerted_products = []
    
    for product in products:
        expiry_date = product['expiry_date']
        today = datetime.now().date()
        days_remaining = (expiry_date - today).days
        
        if days_remaining == alert_days and product['id'] not in alerted_products:
            urgency, status_text = calculate_urgency(expiry_date)
            product_copy = product.copy()
            product_copy.update({
                'urgency': urgency,
                'status_text': status_text
            })
            if send_sms_alert(product_copy):
                alerted_products.append(product['id'])
    
    return alerted_products

@app.route('/')
def index():
    settings = load_settings()
    search_query = request.args.get('search', '').lower()
    products_with_urgency = []
    products = load_products()
    
    # Check for expiry alerts on each page load
    check_expiry_alerts()
    
    for product in products:
        expiry_date = product['expiry_date']
        urgency, status_text = calculate_urgency(expiry_date)
        product_copy = product.copy()
        product_copy.update({
            'urgency': urgency,
            'status_text': status_text,
            'expiry_formatted': expiry_date.strftime('%d/%m/%Y'),
            'manufacture_formatted': product['manufacture_date'].strftime('%d/%m/%Y')
        })
        products_with_urgency.append(product_copy)
    
    # Filter products based on search query
    if search_query:
        products_with_urgency = [p for p in products_with_urgency 
                               if search_query in p['name'].lower()]
    
    # Sort by urgency and expiry date
    sorted_products = sorted(products_with_urgency, key=lambda x: (
        {'expired': 0, 'urgent': 1, 'soon': 2, 'normal': 3}[x['urgency']],
        x['expiry_date']
    ))
    
    return render_template('index.html', 
                         products=sorted_products, 
                         now=datetime.now().strftime('%d/%m/%Y %H:%M:%S'),
                         total_products=len(products),
                         search_query=search_query,
                         settings=settings)

@app.route('/add', methods=['POST'])
def add_product():
    products = load_products()
    new_id = max((p['id'] for p in products), default=0) + 1
    
    product = {
        'id': new_id,
        'name': request.form['name'],
        'quantity': int(request.form['quantity']),
        'unit': request.form['unit'],
        'manufacture_date': datetime.strptime(request.form['manufacture_date'], '%Y-%m-%d').date(),
        'expiry_date': datetime.strptime(request.form['expiry_date'], '%Y-%m-%d').date(),
        'added_date': datetime.now().date()
    }
    
    products.append(product)
    save_products(products)
    
    # Check if this product needs an immediate alert
    expiry_date = product['expiry_date']
    today = datetime.now().date()
    days_remaining = (expiry_date - today).days
    settings = load_settings()
    
    if days_remaining <= settings.get('alert_days', 3):
        urgency, status_text = calculate_urgency(expiry_date)
        product_copy = product.copy()
        product_copy.update({
            'urgency': urgency,
            'status_text': status_text
        })
        send_sms_alert(product_copy)
    
    return redirect('/')

@app.route('/delete/<int:product_id>')
def delete_product(product_id):
    products = load_products()
    products = [p for p in products if p['id'] != product_id]
    save_products(products)
    return redirect('/')

@app.route('/update_quantity/<int:product_id>', methods=['POST'])
def update_quantity(product_id):
    products = load_products()
    new_quantity = int(request.form['quantity'])
    new_unit = request.form['unit']
    
    for product in products:
        if product['id'] == product_id:
            product['quantity'] = new_quantity
            product['unit'] = new_unit
            break
    
    save_products(products)
    return redirect('/')

@app.route('/change_theme', methods=['POST'])
def change_theme():
    settings = load_settings()
    settings['theme'] = request.form['theme']
    save_settings(settings)
    return redirect('/')

@app.route('/settings', methods=['GET', 'POST'])
def manage_settings():
    settings = load_settings()
    
    if request.method == 'POST':
        settings['sms_alerts'] = 'sms_alerts' in request.form
        settings['phone_number'] = request.form['phone_number']
        settings['alert_days'] = int(request.form['alert_days'])
        save_settings(settings)
        return redirect('/')
    
    return render_template('settings.html', settings=settings)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)