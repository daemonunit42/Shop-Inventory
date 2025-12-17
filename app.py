from flask import Flask, render_template, request, redirect, url_for, jsonify, flash
from datetime import datetime, timedelta
import json
import os
import requests
from pathlib import Path
from twilio.rest import Client

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

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)