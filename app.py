from flask import Flask, render_template, request, redirect, url_for, jsonify, flash
from datetime import datetime, timedelta
import json
import os
import requests
from pathlib import Path
from twilio.rest import Client
import numpy as np
import cv2

# ... inside detect_item function ...

# Convert PIL Image to NumPy array (BGR for OpenCV/EasyOCR)

app = Flask(__name__)
app.secret_key = 'your_secret_key_here'   
 
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
    #'phone_number': '+91Enter Your Mobile Number Here ! ',
    #'ai_enabled': True,
    #'openrouter_api_key': 'Get Bot token from the open router model : mistral 7b instruct '
}

# Twilio configuration
DEFAULT_CONFIG = {
    #'account_sid': 'account sid ',
    #'auth_token': 'acount auth token',
    #'twilio_number': 'the twillo number! not your number'
}

def load_config():
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, 'r') as f:
                return {**DEFAULT_CONFIG, **json.load(f)}
        except (json.JSONDecodeError, IOError):
            return DEFAULT_CONFIG
    return DEFAULT_CONFIG

def load_settings():
    if SETTINGS_FILE.exists():
        try:
            with open(SETTINGS_FILE, 'r') as f:
                return {**DEFAULT_SETTINGS, **json.load(f)}
        except (json.JSONDecodeError, IOError):
            return DEFAULT_SETTINGS
    return DEFAULT_SETTINGS

def save_settings(settings):
    try:
        with open(SETTINGS_FILE, 'w') as f:
            json.dump(settings, f, indent=2)
        return True
    except IOError:
        return False

def load_products():
    if DATA_FILE.exists():
        try:
            with open(DATA_FILE, 'r') as f:
                data = json.load(f)
                products = []
                for product in data:
                    try:
                        product['manufacture_date'] = datetime.strptime(product['manufacture_date'], '%Y-%m-%d').date()
                        product['expiry_date'] = datetime.strptime(product['expiry_date'], '%Y-%m-%d').date()
                        product['added_date'] = datetime.strptime(product['added_date'], '%Y-%m-%d').date()
                        products.append(product)
                    except (KeyError, ValueError):
                        continue
                return products
        except (json.JSONDecodeError, IOError):
            return []
    return []

def save_products(products_list):
    serializable_products = []
    for product in products_list:
        try:
            serialized = product.copy()
            serialized['manufacture_date'] = product['manufacture_date'].isoformat()
            serialized['expiry_date'] = product['expiry_date'].isoformat()
            serialized['added_date'] = product['added_date'].isoformat()
            serializable_products.append(serialized)
        except (KeyError, AttributeError):
            continue
    
    try:
        with open(DATA_FILE, 'w') as f:
            json.dump(serializable_products, f, indent=2)
        return True
    except IOError:
        return False

def calculate_urgency(expiry_date):
    today = datetime.now().date()
    try:
        days_remaining = (expiry_date - today).days
    except TypeError:
        return 'error', "Invalid date format"
    
    if days_remaining < 0:
        return 'expired', f"Expired {-days_remaining} days ago"
    elif days_remaining == 0:
        return 'urgent', "Expires today!"
    elif days_remaining <= 3:
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
        try:
            days_remaining = (expiry_date - today).days
        except TypeError:
            continue
        
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

def query_ai_assistant(prompt, products):
    settings = load_settings()
    if not settings.get('ai_enabled', True):
        return {"error": "AI assistant is currently disabled in settings"}, False
    
    api_key = settings.get('openrouter_api_key')
    if not api_key:
        return {"error": "No API key configured for AI assistant"}, False
    
    # Prepare product data for context
    product_context = []
    for product in products:
        try:
            product_context.append({
                'id': product['id'],
                'name': product['name'],
                'quantity': product['quantity'],
                'unit': product['unit'],
                'manufacture_date': product['manufacture_date'].strftime('%Y-%m-%d'),
                'expiry_date': product['expiry_date'].strftime('%Y-%m-%d'),
                'status': calculate_urgency(product['expiry_date'])[1]
            })
        except (KeyError, AttributeError):
            continue
    
    system_prompt = f"""You are an inventory management assistant. You help manage a product inventory with the following capabilities:
    - Add new products (name, quantity, unit, manufacture date, expiry date)
    - Update product quantities
    - Delete products
    - Search for products
    - Check expiry status
    
    Current inventory has {len(products)} products. Here's the current inventory:
    {json.dumps(product_context, indent=2)}
    
    When responding to commands:
    1. For product additions, return JSON with all required fields
    2. For updates/deletions, specify the product ID
    3. For queries, provide clear information
    4. Always confirm actions
    
    Today's date is {datetime.now().strftime('%Y-%m-%d')}
    """
    
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    
    payload = {
        "model": "mistralai/mistral-7b-instruct",
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.3
    }
    
    try:
        response = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers=headers,
            json=payload,
            timeout=30
        )
        
        if response.status_code == 200:
            try:
                response_data = response.json()
                content = response_data['choices'][0]['message']['content']
                try:
                    # Try to parse JSON response first
                    parsed = json.loads(content)
                    return parsed, True
                except json.JSONDecodeError:
                    # If not JSON, return as plain text
                    return {"response": content}, True
            except (KeyError, IndexError, ValueError) as e:
                return {"error": f"Unexpected AI response format: {str(e)}"}, False
        else:
            return {"error": f"AI request failed with status {response.status_code}"}, False
    except requests.exceptions.Timeout:
        return {"error": "AI request timed out"}, False
    except Exception as e:
        return {"error": f"Error communicating with AI service: {str(e)}"}, False

@app.route('/')
def index():
    try:
        settings = load_settings()
        search_query = request.args.get('search', '').lower()
        products_with_urgency = []
        products = load_products()
        
        alerted_products = check_expiry_alerts()
        if alerted_products:
            flash(f"SMS alerts sent for {len(alerted_products)} products", 'success')
        
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
        
        if search_query:
            products_with_urgency = [p for p in products_with_urgency 
                                   if search_query in p['name'].lower()]
        
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
    except Exception as e:
        flash(f"Error loading page: {str(e)}", 'danger')
        return render_template('index.html', 
                             products=[], 
                             now=datetime.now().strftime('%d/%m/%Y %H:%M:%S'),
                             total_products=0,
                             search_query='',
                             settings=load_settings())

@app.route('/add', methods=['POST'])
def add_product():
    try:
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
        if not save_products(products):
            raise Exception("Failed to save products")
        
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
            if send_sms_alert(product_copy):
                flash(f"Product added and SMS alert sent for {product['name']}", 'success')
        
        flash(f"Product '{product['name']}' added successfully", 'success')
        return redirect('/')
    except KeyError as e:
        flash(f"Missing required field: {str(e)}", 'danger')
        return redirect('/')
    except ValueError as e:
        flash(f"Invalid data format: {str(e)}", 'danger')
        return redirect('/')
    except Exception as e:
        flash(f"Error adding product: {str(e)}", 'danger')
        return redirect('/')

@app.route('/delete/<int:product_id>')
def delete_product(product_id):
    try:
        products = load_products()
        product_to_delete = next((p for p in products if p['id'] == product_id), None)
        
        if not product_to_delete:
            flash("Product not found", 'danger')
            return redirect('/')
            
        products = [p for p in products if p['id'] != product_id]
        
        if not save_products(products):
            raise Exception("Failed to save products")
            
        flash(f"Product '{product_to_delete['name']}' deleted successfully", 'success')
        return redirect('/')
    except Exception as e:
        flash(f"Error deleting product: {str(e)}", 'danger')
        return redirect('/')

@app.route('/update/<int:product_id>', methods=['POST'])
def update_product(product_id):
    try:
        products = load_products()
        product_found = None
        
        for product in products:
            if product['id'] == product_id:
                product_found = product
                break
        
        if not product_found:
            flash("Product not found", 'danger')
            return redirect('/')
            
        # Update all fields including dates
        product_found['name'] = request.form['name']
        product_found['quantity'] = int(request.form['quantity'])
        product_found['unit'] = request.form['unit']
        product_found['manufacture_date'] = datetime.strptime(request.form['manufacture_date'], '%Y-%m-%d').date()
        product_found['expiry_date'] = datetime.strptime(request.form['expiry_date'], '%Y-%m-%d').date()
        
        if not save_products(products):
            raise Exception("Failed to save products")
            
        # Recalculate urgency after update
        expiry_date = product_found['expiry_date']
        urgency, status_text = calculate_urgency(expiry_date)
        product_found['urgency'] = urgency
        product_found['status_text'] = status_text
        
        flash("Product updated successfully", 'success')
        return redirect('/')
    except ValueError as e:
        flash(f"Invalid data format: {str(e)}", 'danger')
        return redirect('/')
    except Exception as e:
        flash(f"Error updating product: {str(e)}", 'danger')
        return redirect('/')

@app.route('/change_theme', methods=['POST'])
def change_theme():
    try:
        settings = load_settings()
        settings['theme'] = request.form['theme']
        if not save_settings(settings):
            raise Exception("Failed to save settings")
        return redirect('/')
    except Exception as e:
        flash(f"Error changing theme: {str(e)}", 'danger')
        return redirect('/')

@app.route('/settings', methods=['GET', 'POST'])
def manage_settings():
    try:
        settings = load_settings()
        
        if request.method == 'POST':
            settings['sms_alerts'] = 'sms_alerts' in request.form
            settings['phone_number'] = request.form['phone_number']
            settings['alert_days'] = int(request.form['alert_days'])
            settings['ai_enabled'] = 'ai_enabled' in request.form
            settings['openrouter_api_key'] = request.form['openrouter_api_key']
            
            if not save_settings(settings):
                raise Exception("Failed to save settings")
                
            flash("Settings saved successfully", 'success')
            return redirect('/')
        
        return render_template('settings.html', settings=settings)
    except ValueError:
        flash("Invalid alert days value", 'danger')
        return redirect('/settings')
    except Exception as e:
        flash(f"Error loading settings: {str(e)}", 'danger')
        return redirect('/settings')

@app.route('/ai_command', methods=['POST'])
def handle_ai_command():
    try:
        command = request.form.get('command', '').strip()
        if not command:
            return jsonify({'error': 'No command provided'}), 400
        
        products = load_products()
        ai_response, success = query_ai_assistant(command, products)
        
        if not success:
            return jsonify(ai_response), 400
        
        if isinstance(ai_response, dict) and ('action' in ai_response or 'response' in ai_response):
            return jsonify(ai_response)
        
        try:
            response_content = ai_response['response'] if 'response' in ai_response else ai_response
            
            if isinstance(response_content, str):
                try:
                    action_data = json.loads(response_content)
                except json.JSONDecodeError:
                    return jsonify({'response': response_content})
            else:
                action_data = response_content
            
            if isinstance(action_data, dict):
                if 'action' in action_data:
                    if action_data['action'] == 'add':
                        if not all(k in action_data for k in ['name', 'quantity', 'manufacture_date', 'expiry_date']):
                            return jsonify({'error': "AI response missing required fields for adding a product"}), 400
                        
                        try:
                            manufacture_date = datetime.strptime(action_data['manufacture_date'], '%Y-%m-%d').date()
                            expiry_date = datetime.strptime(action_data['expiry_date'], '%Y-%m-%d').date()
                        except ValueError:
                            return jsonify({'error': "Invalid date format. Use YYYY-MM-DD"}), 400
                        
                        new_product = {
                            'name': action_data.get('name', ''),
                            'quantity': int(action_data.get('quantity', 1)),
                            'unit': action_data.get('unit', 'pcs'),
                            'manufacture_date': manufacture_date,
                            'expiry_date': expiry_date
                        }
                        return jsonify({
                            'response': f"Ready to add: {new_product['name']} (Qty: {new_product['quantity']} {new_product['unit']})",
                            'action': 'add',
                            'product': new_product
                        })
                    elif action_data['action'] == 'delete' and 'id' in action_data:
                        return jsonify({
                            'response': f"Ready to delete product ID {action_data['id']}",
                            'action': 'delete',
                            'product_id': action_data['id']
                        })
                    elif action_data['action'] == 'update' and 'id' in action_data:
                        return jsonify({
                            'response': f"Ready to update product ID {action_data['id']}",
                            'action': 'update',
                            'product_id': action_data['id'],
                            'updates': action_data.get('updates', {})
                        })
        
        except Exception as e:
            print(f"Error processing AI response: {str(e)}")
        
        return jsonify({'response': ai_response.get('response', 'No response from AI')})
    except Exception as e:
        return jsonify({'error': f"Error processing AI command: {str(e)}"}), 500

@app.route('/execute_ai_action', methods=['POST'])
def execute_ai_action():
    try:
        action = request.form.get('action')
        
        if action == 'add':
            products = load_products()
            new_id = max((p['id'] for p in products), default=0) + 1
            
            try:
                product = {
                    'id': new_id,
                    'name': request.form['name'],
                    'quantity': int(request.form['quantity']),
                    'unit': request.form.get('unit', 'pcs'),
                    'manufacture_date': datetime.strptime(request.form['manufacture_date'], '%Y-%m-%d').date(),
                    'expiry_date': datetime.strptime(request.form['expiry_date'], '%Y-%m-%d').date(),
                    'added_date': datetime.now().date()
                }
                
                products.append(product)
                if not save_products(products):
                    raise Exception("Failed to save products")
                    
                return jsonify({
                    'success': True, 
                    'message': f"Added {product['name']}",
                    'refresh': True
                })
            except ValueError:
                return jsonify({'success': False, 'message': "Invalid quantity or date format"}), 400
            except Exception as e:
                return jsonify({'success': False, 'message': f"Error adding product: {str(e)}"}), 400
        
        elif action == 'delete':
            try:
                product_id = int(request.form['product_id'])
                products = load_products()
                product_name = next((p['name'] for p in products if p['id'] == product_id), 'Unknown')
                products = [p for p in products if p['id'] != product_id]
                
                if not save_products(products):
                    raise Exception("Failed to save products")
                    
                return jsonify({
                    'success': True, 
                    'message': f"Deleted product {product_name}",
                    'refresh': True
                })
            except ValueError:
                return jsonify({'success': False, 'message': "Invalid product ID"}), 400
            except Exception as e:
                return jsonify({'success': False, 'message': f"Error deleting product: {str(e)}"}), 400
        
        elif action == 'update':
            try:
                product_id = int(request.form['product_id'])
                updates = json.loads(request.form['updates'])
                products = load_products()
                product_found = False
                
                for product in products:
                    if product['id'] == product_id:
                        for key, value in updates.items():
                            if key in ['manufacture_date', 'expiry_date']:
                                product[key] = datetime.strptime(value, '%Y-%m-%d').date()
                            else:
                                product[key] = value
                        product_found = True
                        break
                
                if not product_found:
                    return jsonify({'success': False, 'message': "Product not found"}), 404
                    
                if not save_products(products):
                    raise Exception("Failed to save products")
                    
                return jsonify({
                    'success': True, 
                    'message': f"Updated product ID {product_id}",
                    'refresh': True
                })
            except ValueError:
                return jsonify({'success': False, 'message': "Invalid product ID or date format"}), 400
            except json.JSONDecodeError:
                return jsonify({'success': False, 'message': "Invalid updates format"}), 400
            except Exception as e:
                return jsonify({'success': False, 'message': f"Error updating product: {str(e)}"}), 400
        
        return jsonify({'success': False, 'message': 'Invalid action'}), 400
    except Exception as e:
        return jsonify({'success': False, 'message': f"Error executing action: {str(e)}"}), 500
    

# ADD THIS NEW ROUTE (after @app.route('/execute_ai_action'))


@app.route('/detect_item', methods=['POST'])
def detect_item():
    try:
        import base64
        import easyocr
        import cv2
        import numpy as np
        import re

        # Get image from frontend
        image_data = request.json['image'].split(',')[1]
        image_bytes = base64.b64decode(image_data)
        nparr = np.frombuffer(image_bytes, np.uint8)
        frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)

        if frame is None:
            return jsonify({'error': 'Invalid image'}), 400

        # Load EasyOCR once (cached)
        if not hasattr(detect_item, "reader"):
            detect_item.reader = easyocr.Reader(['en'], gpu=False)  # Add 'hi','fr' etc. if needed

        reader = detect_item.reader

        # === STRONG BUT FAST PREPROCESSING ===
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8,8))
        contrast = clahe.apply(gray)
        denoised = cv2.fastNlMeansDenoising(contrast, h=10)
        sharpen_kernel = np.array([[-1,-1,-1], [-1,9,-1], [-1,-1,-1]])
        sharpened = cv2.filter2D(denoised, -1, sharpen_kernel)
        preprocessed = cv2.resize(sharpened, None, fx=1.5, fy=1.5, interpolation=cv2.INTER_LINEAR)

        # === RUN OCR ===
        ocr_results = reader.readtext(
            preprocessed,
            paragraph=False,
            width_ths=0.8,
            height_ths=0.8,
            text_threshold=0.6
        )

        # Get all text with decent confidence
        texts = [text for (_, text, prob) in ocr_results if prob > 0.5]

        if not texts:
            return jsonify({
                'name': '',
                'message': 'No text detected — try better lighting or closer photo'
            })

        # Combine all detected text into one clean name
        detected_name = " ".join(texts)
        detected_name = re.sub(r'\s+', ' ', detected_name).strip()  # Remove extra spaces

        # Optional: Uppercase or clean common OCR mistakes
        # detected_name = detected_name.upper()
        # detected_name = detected_name.replace('0', 'O').replace('1', 'I')

        return jsonify({
            'name': detected_name,
            'message': 'Detected successfully'
        })

    except Exception as e:
        print("OCR Error:", e)
        import traceback
        traceback.print_exc()
        return jsonify({'error': 'Detection failed', 'details': str(e)}), 500
    try:
        import base64
        import easyocr
        import cv2
        import numpy as np

        # Get base64 image from frontend
        image_data = request.json['image'].split(',')[1]
        image_bytes = base64.b64decode(image_data)
        nparr = np.frombuffer(image_bytes, np.uint8)
        frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)

        if frame is None:
            return jsonify({'error': 'Invalid image'}), 400

        # Load EasyOCR reader once and reuse
        if not hasattr(detect_item, "reader"):
            detect_item.reader = easyocr.Reader(['en'], gpu=False)  # Add more langs if needed, e.g. ['en','hi']

        reader = detect_item.reader

        # === FAST & EFFECTIVE PREPROCESSING ===
        # 1. Convert to grayscale
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

        # 2. Boost contrast with CLAHE (great for text on packaging)
        clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8,8))
        contrast = clahe.apply(gray)

        # 3. Light denoising to reduce JPEG noise
        denoised = cv2.fastNlMeansDenoising(contrast, h=10)

        # 4. Sharpen to make text bolder and clearer
        sharpen_kernel = np.array([[-1,-1,-1],
                                   [-1, 9,-1],
                                   [-1,-1,-1]])
        sharpened = cv2.filter2D(denoised, -1, sharpen_kernel)

        # Optional: Upscale slightly for small text (helps a lot, still fast)
        preprocessed = cv2.resize(sharpened, None, fx=1.5, fy=1.5, interpolation=cv2.INTER_LINEAR)

        # === RUN OCR ===
        ocr_results = reader.readtext(
            preprocessed,
            paragraph=False,           # Get individual text lines
            width_ths=0.8,             # Helps connect broken characters
            height_ths=0.8,
            text_threshold=0.6         # Lower a bit to catch faint text
        )

        # Extract text with confidence filter
        texts = [text for (_, text, prob) in ocr_results if prob > 0.5]

        # Combine into one string (you can customize this logic later)
        if texts:
            # Join all detected text — often gives good product name + price + expiry
            detected_name = " ".join(texts).strip()

            # Optional: Clean up common noise (e.g. multiple spaces, weird chars)
            import re
            detected_name = re.sub(r'\s+', ' ', detected_name).strip()
        else:
            detected_name = "No text detected"

        return jsonify({
            'name': detected_name,
            'confidence': 1.0,  # We don't have YOLO conf anymore
            'box': None         # No bounding box since no detection
        })

    except Exception as e:
        print("Detection error:", e)
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500
    try:
        from ultralytics import YOLO
        import base64
        import easyocr

        # Get base64 image from frontend (data URL format: data:image/jpeg;base64,...)
        image_data = request.json['image'].split(',')[1]
        image_bytes = base64.b64decode(image_data)
        nparr = np.frombuffer(image_bytes, np.uint8)
        frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)

        if frame is None:
            return jsonify({'error': 'Invalid image'}), 400

        # Load models once and reuse (cached on function)
        if not hasattr(detect_item, "model"):
            detect_item.model = YOLO('yolov8n.pt')  # Make sure yolov8n.pt is in the folder
            detect_item.reader = easyocr.Reader(['en'], gpu=False)  # English, CPU

        model = detect_item.model
        reader = detect_item.reader

        # Run YOLO detection
        results = model(frame, conf=0.4)[0]

        detected_name = "Unknown Item"
        confidence = 0.0
        box = None

        if len(results.boxes) > 0:
            # Get the most confident detection
            top_idx = results.boxes.conf.argmax()
            cls_id = int(results.boxes.cls[top_idx])
            confidence = float(results.boxes.conf[top_idx])
            detected_name = results.names[cls_id]

            # Get bounding box
            box_xyxy = results.boxes.xyxy[top_idx].cpu().numpy()
            x1, y1, x2, y2 = map(int, box_xyxy)

            box = [x1, y1, x2, y2]

            # Crop the detected item for better OCR on label/text
            crop = frame[y1:y2, x1:x2]

            # EasyOCR works directly with NumPy BGR arrays → no conversion needed!
            ocr_results = reader.readtext(crop, paragraph=False)

            ocr_text = " ".join([text for _, text, prob in ocr_results]) if ocr_results else ""

            # If OCR finds readable text (like product name on label), use it
            if ocr_text.strip():
                detected_name = ocr_text.strip()

        return jsonify({
            'name': detected_name,
            'confidence': round(confidence, 2),
            'box': box
        })

    except Exception as e:
        print("Detection error:", e)
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)