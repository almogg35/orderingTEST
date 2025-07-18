# 檔案: app.py (雲端部署 - 臨時初始化版本)

# 1. 所有 import 語句
# ==================================
import os
import io
import csv
import click
import psycopg2
import psycopg2.extras # 為了讓回傳的資料像字典一樣
from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify, Response
from werkzeug.security import generate_password_hash, check_password_hash
from openpyxl import Workbook
from openpyxl.styles import Font
from urllib.parse import quote
from datetime import datetime, timezone, timedelta

# 2. 建立 Flask app 物件
# ==================================
app = Flask(__name__)

# 3. 定義所有自訂函式
# ==================================
def get_db_connection():
    """建立並回傳一個 PostgreSQL 資料庫連線。"""
    conn_string = os.environ.get('DATABASE_URL')
    conn = psycopg2.connect(conn_string)
    return conn

def setup_database():
    """在資料庫中建立所有必要的資料表。"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # 使用 SERIAL 作為 PostgreSQL 的自動遞增主鍵
    cursor.execute("CREATE TABLE IF NOT EXISTS categories (id SERIAL PRIMARY KEY, name TEXT NOT NULL UNIQUE)")
    cursor.execute("CREATE TABLE IF NOT EXISTS products (barcode TEXT PRIMARY KEY, name TEXT NOT NULL, name_chinese TEXT NOT NULL DEFAULT '', description TEXT, current_stock INTEGER NOT NULL DEFAULT 0, purchase_price REAL NOT NULL DEFAULT 0.0, selling_price REAL NOT NULL DEFAULT 0.0, category_id INTEGER REFERENCES categories(id) ON DELETE SET NULL)")
    cursor.execute("CREATE TABLE IF NOT EXISTS customers (id SERIAL PRIMARY KEY, name TEXT NOT NULL UNIQUE, username TEXT UNIQUE, password TEXT, status TEXT NOT NULL DEFAULT 'active')")
    cursor.execute("CREATE TABLE IF NOT EXISTS suppliers (id SERIAL PRIMARY KEY, name TEXT NOT NULL UNIQUE, status TEXT NOT NULL DEFAULT 'active')")
    # 使用 TIMESTAMPTZ (Timestamp with Time Zone) 以符合 PostgreSQL 的最佳實踐
    cursor.execute("CREATE TABLE IF NOT EXISTS transactions (id SERIAL PRIMARY KEY, barcode TEXT NOT NULL REFERENCES products(barcode) ON DELETE RESTRICT, type TEXT NOT NULL, quantity INTEGER NOT NULL, transaction_price REAL NOT NULL DEFAULT 0.0, customer_id INTEGER REFERENCES customers(id) ON DELETE RESTRICT, supplier_id INTEGER REFERENCES suppliers(id) ON DELETE RESTRICT, timestamp TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP)")
    cursor.execute("CREATE TABLE IF NOT EXISTS orders (id SERIAL PRIMARY KEY, customer_id INTEGER NOT NULL REFERENCES customers(id) ON DELETE RESTRICT, order_date TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP, status TEXT NOT NULL DEFAULT '待處理', total_amount REAL)")
    cursor.execute("CREATE TABLE IF NOT EXISTS order_details (id SERIAL PRIMARY KEY, order_id INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE, barcode TEXT NOT NULL REFERENCES products(barcode) ON DELETE RESTRICT, quantity INTEGER NOT NULL, price_at_order REAL NOT NULL)")
    cursor.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")

    conn.commit()
    cursor.close()
    conn.close()
    print("資料庫資料表建立完成。")

def convert_records_timezone(records, time_key='timestamp'):
    """將 UTC 時間轉換為 CST (Asia/Taipei) 時間。"""
    cst_tz = timezone(timedelta(hours=8))
    converted_records = []
    for record in records:
        record_dict = dict(record)
        time_utc = record_dict.get(time_key)
        if time_utc:
            if time_utc.tzinfo is None:
                time_utc = time_utc.replace(tzinfo=timezone.utc)
            cst_dt = time_utc.astimezone(cst_tz)
            record_dict[time_key] = cst_dt.strftime('%Y-%m-%d %H:%M:%S')
        converted_records.append(record_dict)
    return converted_records

# 5. 設定 app 的 secret key
# ==================================
app.secret_key = os.environ.get('FLASK_SECRET_KEY', 'a_very_secret_key_that_should_be_changed')

# 6. 所有 @app.cli 的指令
# ==================================
@app.cli.command("init-db")
def init_db_command():
    """清除並建立新的資料表。"""
    setup_database()
    click.echo("已成功初始化資料庫。")

# 7. 所有 @app.route(...) 的路由函式
# ==================================
@app.route('/', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        
        ADMIN_USERNAME = os.environ.get('ADMIN_USERNAME', 'admin')
        ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD', 'admin')
        
        if not username or not password:
            flash('使用者名稱和密碼不可為空。', 'danger')
            return redirect(url_for('login'))
            
        if username == ADMIN_USERNAME and password == ADMIN_PASSWORD:
            session.clear()
            session['user_type'] = 'admin'
            session['username'] = '管理員'
            return redirect(url_for('admin_dashboard'))
            
        try:
            conn = get_db_connection()
            cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
            cursor.execute("SELECT * FROM customers WHERE username = %s AND status = %s", (username, 'active'))
            customer = cursor.fetchone()
            cursor.close()
            conn.close()
            
            if customer and customer['password'] and check_password_hash(customer['password'], password):
                session.clear()
                session['user_type'] = 'customer'
                session['username'] = customer['name']
                session['customer_id'] = customer['id']
                return redirect(url_for('customer_portal'))
            else:
                flash('使用者名稱或密碼錯誤。', 'danger')
                return redirect(url_for('login'))
        except psycopg2.Error as e:
            print(f"Login database error: {e}")
            flash('資料庫發生錯誤，請聯繫管理員。', 'danger')
            return redirect(url_for('login'))

    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    flash('您已成功登出。', 'success')
    return redirect(url_for('login'))

@app.route('/admin')
def admin_dashboard():
    if session.get('user_type') != 'admin':
        return redirect(url_for('login'))
    return render_template('admin.html', username=session['username'])

@app.route('/db_editor')
def db_editor():
    if session.get('user_type') != 'admin':
        return redirect(url_for('login'))
    return render_template('db_editor.html', username=session['username'])

@app.route('/customer')
def customer_portal():
    if session.get('user_type') != 'customer':
        return redirect(url_for('login'))
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    cursor.execute('''
        SELECT p.barcode, p.name, p.name_chinese, p.description, p.current_stock, p.selling_price, p.category_id, c.name as category_name
        FROM products p
        LEFT JOIN categories c ON p.category_id = c.id
        WHERE p.current_stock > 0 ORDER BY p.name_chinese
    ''')
    products = cursor.fetchall()
    cursor.execute("SELECT id, name FROM categories ORDER BY name")
    categories = cursor.fetchall()
    cursor.execute("SELECT value FROM settings WHERE key = 'announcement'")
    announcement_row = cursor.fetchone()
    announcement = announcement_row['value'] if announcement_row and announcement_row['value'].strip() else None
    cursor.close()
    conn.close()
    return render_template('customer.html', username=session['username'], products=products, announcement=announcement, categories=categories)

@app.route('/reports')
def reports_page():
    if session.get('user_type') != 'admin':
        return redirect(url_for('login'))
    return render_template('reports.html')

@app.route('/order_management')
def order_management_page():
    if session.get('user_type') != 'admin':
        return redirect(url_for('login'))
    return render_template('order_management.html')

# --- ALL APIs from here ---

@app.route('/api/announcement', methods=['GET'])
def get_announcement():
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    cursor.execute("SELECT value FROM settings WHERE key = 'announcement'")
    announcement_row = cursor.fetchone()
    cursor.close()
    conn.close()
    announcement = announcement_row['value'] if announcement_row else ''
    return jsonify({'announcement': announcement})

@app.route('/api/announcement/update', methods=['POST'])
def update_announcement():
    if session.get('user_type') != 'admin':
        return jsonify({'success': False, 'error': 'Unauthorized'}), 403
    data = request.json
    new_announcement = data.get('announcement', '')
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("INSERT INTO settings (key, value) VALUES (%s, %s) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value", ('announcement', new_announcement))
        conn.commit()
        return jsonify({'success': True, 'message': '公告已更新'})
    except psycopg2.Error as e:
        print(f"DB Error: {e}")
        conn.rollback()
        return jsonify({'success': False, 'error': '資料庫更新失敗'}), 500
    finally:
        cursor.close()
        if conn: conn.close()

@app.route('/api/product/<barcode>')
def get_product(barcode):
    if session.get('user_type') != 'admin': return jsonify({'error': 'Unauthorized'}), 403
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    cursor.execute('SELECT * FROM products WHERE barcode = %s', (barcode,))
    product = cursor.fetchone()
    cursor.close()
    conn.close()
    if product: return jsonify(dict(product))
    return jsonify({'error': 'Product not found'}), 404

@app.route('/api/partners/<partner_type>')
def get_partners(partner_type):
    if session.get('user_type') != 'admin': return jsonify({'error': 'Unauthorized'}), 403
    if partner_type not in ['customers', 'suppliers']: return jsonify({'error': 'Invalid partner type'}), 400
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    cursor.execute(f"SELECT id, name FROM {partner_type} WHERE status = 'active' ORDER BY name")
    partners = cursor.fetchall()
    cursor.close()
    conn.close()
    return jsonify([dict(p) for p in partners])

@app.route('/api/product/add', methods=['POST'])
def add_product():
    if session.get('user_type') != 'admin': return jsonify({'success': False, 'error': 'Unauthorized'}), 403
    data = request.json
    category_id = data.get('category_id') if data.get('category_id') else None
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("INSERT INTO products (barcode, name, name_chinese, purchase_price, selling_price, current_stock, category_id) VALUES (%s, %s, %s, %s, %s, 0, %s)",
                     (data['barcode'], data['name'], data['name_chinese'], data['purchase_price'], data['selling_price'], category_id))
        conn.commit()
    except psycopg2.IntegrityError:
        conn.rollback()
        return jsonify({'success': False, 'error': '此條碼已存在'}), 409
    finally:
        cursor.close()
        if conn: conn.close()
    return jsonify({'success': True})

@app.route('/api/transaction/batch', methods=['POST'])
def batch_transaction():
    if session.get('user_type') != 'admin': return jsonify({'success': False, 'error': 'Unauthorized'}), 403
    data = request.json
    trans_type, items, partner_id = data.get('type'), data.get('items'), data.get('partner_id')
    if not all([trans_type, items, partner_id]) or trans_type not in ['IN', 'OUT'] or not isinstance(items, list) or len(items) == 0:
        return jsonify({'success': False, 'error': '請求資料不完整或無效'}), 400
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    try:
        for item in items:
            cursor.execute('UPDATE products SET current_stock = current_stock + %s WHERE barcode = %s',
                         (item['quantity'] if trans_type == 'IN' else -item['quantity'], item['barcode']))
            cursor.execute('SELECT purchase_price, selling_price FROM products WHERE barcode = %s', (item['barcode'],))
            product_info = cursor.fetchone()
            price = product_info['selling_price'] if trans_type == 'OUT' else product_info['purchase_price']
            customer_id, supplier_id = (partner_id, None) if trans_type == 'OUT' else (None, partner_id)
            cursor.execute("INSERT INTO transactions (barcode, type, quantity, transaction_price, customer_id, supplier_id) VALUES (%s, %s, %s, %s, %s, %s)",
                         (item['barcode'], trans_type, item['quantity'], price, customer_id, supplier_id))
        conn.commit()
        return jsonify({'success': True, 'message': f'批次{trans_type}操作成功，共處理 {len(items)} 項商品。'}), 200
    except psycopg2.Error as e:
        print(f"DB Error: {e}")
        conn.rollback()
        return jsonify({'success': False, 'error': '資料庫操作失敗，所有操作已復原。'}), 500
    finally:
        cursor.close()
        if conn: conn.close()

@app.route('/api/order/submit', methods=['POST'])
def submit_order():
    if session.get('user_type') != 'customer': return jsonify({'success': False, 'error': 'Unauthorized'}), 403
    data = request.json
    cart, customer_id = data.get('cart'), session.get('customer_id')
    if not cart or not isinstance(cart, list) or not customer_id: return jsonify({'success': False, 'error': '訂單資料不完整'}), 400
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    try:
        total_amount = 0
        products_to_update = []
        for item in cart:
            cursor.execute("SELECT name_chinese, selling_price, current_stock FROM products WHERE barcode = %s FOR UPDATE", (item['barcode'],))
            product = cursor.fetchone()
            if not product:
                raise ValueError(f"商品條碼 '{item['barcode']}' 不存在")
            if product['current_stock'] < item['quantity']:
                raise ValueError(f"商品 '{product['name_chinese']}' 庫存不足")
            total_amount += product['selling_price'] * item['quantity']
            products_to_update.append({'barcode': item['barcode'], 'quantity': item['quantity'], 'price': product['selling_price']})

        cursor.execute("INSERT INTO orders (customer_id, status, total_amount) VALUES (%s, '待處理', %s) RETURNING id", (customer_id, total_amount))
        order_id = cursor.fetchone()['id']
        
        for p in products_to_update:
            cursor.execute("UPDATE products SET current_stock = current_stock - %s WHERE barcode = %s", (p['quantity'], p['barcode']))
            cursor.execute("INSERT INTO order_details (order_id, barcode, quantity, price_at_order) VALUES (%s, %s, %s, %s)", (order_id, p['barcode'], p['quantity'], p['price']))
        
        conn.commit()
        return jsonify({'success': True, 'message': '訂單已成功送出！', 'order_id': order_id})
    except (psycopg2.Error, ValueError) as e:
        print(f"DB Error: {e}")
        conn.rollback()
        error_message = str(e) if isinstance(e, ValueError) else '建立訂單時發生資料庫錯誤'
        return jsonify({'success': False, 'error': error_message}), 500
    finally:
        cursor.close()
        if conn: conn.close()

# 請將您舊的 get_table_data 函式完整替換為此版本
@app.route('/api/db/<table>')
def get_table_data(table):
    if session.get('user_type') != 'admin': return jsonify({'error': 'Unauthorized'}), 403
    if table not in ['products', 'customers', 'suppliers', 'categories']: return jsonify({'error': 'Invalid table'}), 400
    
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    try:
        if table == 'products':
            # 【主要修改】使用更安全的排序方法，將非數字條碼排在後面
            # 語法解釋：CASE WHEN barcode ~ '^[0-9]+$' THEN 1 ELSE 2 END 會讓純數字的排在第一組
            # THEN CAST(barcode AS BIGINT) 會對純數字的進行數字排序
            cursor.execute(f"SELECT * FROM {table} ORDER BY CASE WHEN barcode ~ '^[0-9]+$' THEN 1 ELSE 2 END, CASE WHEN barcode ~ '^[0-9]+$' THEN CAST(barcode AS BIGINT) ELSE 0 END")
        else:
            cursor.execute(f'SELECT * FROM {table} ORDER BY id')
        
        data = cursor.fetchall()
        return jsonify([dict(row) for row in data])
        
    except psycopg2.Error as e:
        print(f"Error fetching table {table}: {e}")
        return jsonify({'error': f'讀取資料表 {table} 失敗'}), 500
    finally:
        cursor.close()
        if conn: conn.close()

@app.route('/api/db/product/update', methods=['POST'])
def update_product():
    if session.get('user_type') != 'admin': return jsonify({'success': False, 'error': 'Unauthorized'}), 403
    data = request.json
    category_id = data.get('category_id') if data.get('category_id') else None
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("UPDATE products SET name=%s, name_chinese=%s, description=%s, current_stock=%s, purchase_price=%s, selling_price=%s, category_id=%s WHERE barcode=%s",
                     (data['name'], data['name_chinese'], data.get('description', ''), data['current_stock'], data['purchase_price'], data['selling_price'], category_id, data['barcode']))
        conn.commit()
        return jsonify({'success': True})
    except psycopg2.Error as e:
        print(f"DB Error: {e}")
        conn.rollback()
        return jsonify({'success': False, 'error': '資料庫更新失敗'})
    finally:
        cursor.close()
        if conn: conn.close()

@app.route('/api/db/product/delete', methods=['POST'])
def delete_product():
    if session.get('user_type') != 'admin': return jsonify({'success': False, 'error': 'Unauthorized'}), 403
    barcode = request.json.get('barcode')
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("DELETE FROM products WHERE barcode = %s", (barcode,))
        conn.commit()
        return jsonify({'success': True})
    except psycopg2.IntegrityError:
        conn.rollback()
        return jsonify({'success': False, 'error': '刪除失敗：此商品可能已被交易或訂單引用。'})
    finally:
        cursor.close()
        if conn: conn.close()

@app.route('/api/partner/add', methods=['POST'])
def add_partner():
    if session.get('user_type') != 'admin': return jsonify({'success': False, 'error': 'Unauthorized'}), 403
    data = request.json
    partner_type, name = data.get('type'), data.get('name')
    if partner_type not in ['customers', 'suppliers'] or not name: return jsonify({'success': False, 'error': '資料無效'}), 400
    table_name = "customers" if partner_type == 'customers' else "suppliers"
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        if table_name == "customers":
             username, password = data.get('username'), data.get('password')
             if not username or not password: return jsonify({'success': False, 'error': '帳號和密碼為必填項'}), 400
             hashed_password = generate_password_hash(password)
             cursor.execute("INSERT INTO customers (name, username, password, status) VALUES (%s, %s, %s, 'active')", (name, username, hashed_password))
        else:
             cursor.execute(f"INSERT INTO {table_name} (name, status) VALUES (%s, 'active')", (name,))
        conn.commit()
        return jsonify({'success': True})
    except psycopg2.IntegrityError:
        conn.rollback()
        return jsonify({'success': False, 'error': '名稱或帳號已存在'})
    finally:
        cursor.close()
        if conn: conn.close()

@app.route('/api/partner/update', methods=['POST'])
def update_partner():
    if session.get('user_type') != 'admin': return jsonify({'success': False, 'error': 'Unauthorized'}), 403
    data = request.json
    ptype, pid, name = data.get('type'), data.get('id'), data.get('name')
    if ptype not in ['customers', 'suppliers'] or not pid or not name: return jsonify({'success': False, 'error': '無效的請求'}), 400
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        if ptype == 'customers':
            username = data.get('username')
            if not username: return jsonify({'success': False, 'error': '客戶帳號不得為空'}), 400
            cursor.execute("UPDATE customers SET name=%s, username=%s WHERE id=%s", (name, username, pid))
        else: # suppliers
            cursor.execute("UPDATE suppliers SET name=%s WHERE id=%s", (name, pid))
        conn.commit()
        return jsonify({'success': True})
    except psycopg2.IntegrityError:
        conn.rollback()
        return jsonify({'success': False, 'error': '名稱或帳號已存在'})
    except psycopg2.Error as e:
        print(f"API Error (update_partner): {e}")
        conn.rollback()
        return jsonify({'success': False, 'error': '資料庫更新失敗'})
    finally:
        cursor.close()
        if conn: conn.close()

@app.route('/api/partner/delete', methods=['POST'])
def delete_partner():
    if session.get('user_type') != 'admin': return jsonify({'success': False, 'error': 'Unauthorized'}), 403
    data = request.json
    partner_type, partner_id = data.get('type'), data.get('id')
    if partner_type not in ['customers', 'suppliers'] or not partner_id: return jsonify({'success': False, 'error': '資料無效'}), 400
    table_name = "customers" if partner_type == 'customers' else "suppliers"
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(f"DELETE FROM {table_name} WHERE id = %s", (partner_id,))
        conn.commit()
        return jsonify({'success': True})
    except psycopg2.IntegrityError:
        conn.rollback()
        return jsonify({'success': False, 'error': '刪除失敗：此項目可能已被交易或訂單引用。'})
    finally:
        cursor.close()
        if conn: conn.close()

@app.route('/api/partner/toggle_status', methods=['POST'])
def toggle_partner_status():
    if session.get('user_type') != 'admin': return jsonify({'success': False, 'error': 'Unauthorized'}), 403
    data = request.json
    partner_type, partner_id = data.get('type'), data.get('id')
    if partner_type not in ['customers', 'suppliers'] or not partner_id: return jsonify({'success': False, 'error': '資料無效'}), 400
    table_name = "customers" if partner_type == 'customers' else "suppliers"
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    try:
        cursor.execute(f"SELECT status FROM {table_name} WHERE id = %s", (partner_id,))
        current = cursor.fetchone()
        if not current: return jsonify({'success': False, 'error': '找不到該項目'}), 404
        new_status = 'inactive' if current['status'] == 'active' else 'active'
        cursor.execute(f"UPDATE {table_name} SET status = %s WHERE id = %s", (new_status, partner_id))
        conn.commit()
        return jsonify({'success': True, 'new_status': new_status})
    except psycopg2.Error as e:
        print(f"API Error (toggle_status): {e}")
        conn.rollback()
        return jsonify({'success': False, 'error': '操作失敗'})
    finally:
        cursor.close()
        if conn: conn.close()

@app.route('/api/db/customer/reset_password', methods=['POST'])
def reset_customer_password():
    if session.get('user_type') != 'admin': return jsonify({'success': False, 'error': 'Unauthorized'}), 403
    data = request.json
    password, customer_id = data.get('password'), data.get('id')
    if not password or not customer_id: return jsonify({'success': False, 'error': '資料不完整'}), 400
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        hashed_password = generate_password_hash(password)
        cursor.execute("UPDATE customers SET password=%s WHERE id=%s", (hashed_password, customer_id))
        conn.commit()
        return jsonify({'success': True})
    except psycopg2.Error as e:
        print(f"API Error (reset_password): {e}")
        conn.rollback()
        return jsonify({'success': False, 'error': '密碼重設失敗'})
    finally:
        cursor.close()
        if conn: conn.close()

@app.route('/api/db/category/add', methods=['POST'])
def add_category():
    if session.get('user_type') != 'admin': return jsonify({'success': False, 'error': 'Unauthorized'}), 403
    name = request.json.get('name')
    if not name: return jsonify({'success': False, 'error': '類別名稱不得為空'}), 400
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("INSERT INTO categories (name) VALUES (%s)", (name,))
        conn.commit()
        return jsonify({'success': True})
    except psycopg2.IntegrityError:
        conn.rollback()
        return jsonify({'success': False, 'error': '該類別名稱已存在'})
    finally:
        cursor.close()
        if conn: conn.close()

@app.route('/api/db/category/update', methods=['POST'])
def update_category():
    if session.get('user_type') != 'admin': return jsonify({'success': False, 'error': 'Unauthorized'}), 403
    data = request.json
    cid, name = data.get('id'), data.get('name')
    if not cid or not name: return jsonify({'success': False, 'error': '無效的請求'}), 400
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("UPDATE categories SET name = %s WHERE id = %s", (name, cid))
        conn.commit()
        return jsonify({'success': True})
    except psycopg2.IntegrityError:
        conn.rollback()
        return jsonify({'success': False, 'error': '該類別名稱已存在'})
    finally:
        cursor.close()
        if conn: conn.close()

@app.route('/api/db/category/delete', methods=['POST'])
def delete_category():
    if session.get('user_type') != 'admin': return jsonify({'success': False, 'error': 'Unauthorized'}), 403
    cid = request.json.get('id')
    if not cid: return jsonify({'success': False, 'error': '無效的請求'}), 400
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("DELETE FROM categories WHERE id = %s", (cid,))
        conn.commit()
        return jsonify({'success': True})
    except psycopg2.Error as e:
        print(f"DB Error: {e}")
        conn.rollback()
        return jsonify({'success': False, 'error': '刪除失敗'})
    finally:
        cursor.close()
        if conn: conn.close()

@app.route('/api/orders', methods=['GET'])
def get_orders():
    if session.get('user_type') != 'admin': return jsonify({'error': 'Unauthorized'}), 403
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    query = "SELECT o.id, o.order_date, o.status, o.total_amount, c.name as customer_name FROM orders o JOIN customers c ON o.customer_id = c.id ORDER BY o.order_date DESC"
    cursor.execute(query)
    orders = cursor.fetchall()
    cursor.close()
    conn.close()
    orders_data = convert_records_timezone(orders, time_key='order_date')
    return jsonify(orders_data)

@app.route('/api/orders/<int:order_id>', methods=['GET'])
def get_order_details(order_id):
    if session.get('user_type') != 'admin': return jsonify({'error': 'Unauthorized'}), 403
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    query = "SELECT od.quantity, od.price_at_order, p.name, p.name_chinese FROM order_details od JOIN products p ON od.barcode = p.barcode WHERE od.order_id = %s"
    cursor.execute(query, (order_id,))
    details = cursor.fetchall()
    cursor.close()
    conn.close()
    details_data = [{'product_name': item['name_chinese'] or item['name'], **dict(item)} for item in details]
    return jsonify(details_data)

@app.route('/api/orders/update_status', methods=['POST'])
def update_order_status():
    if session.get('user_type') != 'admin': return jsonify({'success': False, 'error': 'Unauthorized'}), 403
    data = request.json
    order_id, new_status = data.get('order_id'), data.get('status')
    if not order_id or not new_status: return jsonify({'success': False, 'error': '缺少訂單ID或新狀態'}), 400

    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    try:
        if new_status == '已取消':
            cursor.execute("SELECT status FROM orders WHERE id = %s", (order_id,))
            current_order = cursor.fetchone()
            if current_order and current_order['status'] != '已取消':
                cursor.execute("SELECT barcode, quantity FROM order_details WHERE order_id = %s", (order_id,))
                order_items = cursor.fetchall()
                for item in order_items:
                    cursor.execute("UPDATE products SET current_stock = current_stock + %s WHERE barcode = %s", (item['quantity'], item['barcode']))
        cursor.execute("UPDATE orders SET status = %s WHERE id = %s", (new_status, order_id))
        conn.commit()
        return jsonify({'success': True})
    except psycopg2.Error as e:
        print(f"DB Error: {e}")
        conn.rollback()
        return jsonify({'success': False, 'error': '資料庫更新失敗'}), 500
    finally:
        cursor.close()
        if conn: conn.close()

@app.route('/api/customer/orders', methods=['GET'])
def get_customer_orders():
    if session.get('user_type') != 'customer' or 'customer_id' not in session: return jsonify({'error': 'Unauthorized'}), 403
    customer_id = session['customer_id']
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    query = "SELECT id, order_date, status, total_amount FROM orders WHERE customer_id = %s ORDER BY order_date DESC"
    cursor.execute(query, (customer_id,))
    orders = cursor.fetchall()
    cursor.close()
    conn.close()
    orders_data = convert_records_timezone(orders, time_key='order_date')
    return jsonify(orders_data)

@app.route('/api/customer/order_details/<int:order_id>', methods=['GET'])
def get_customer_order_details(order_id):
    if session.get('user_type') != 'customer' or 'customer_id' not in session: return jsonify({'error': 'Unauthorized'}), 403
    customer_id = session['customer_id']
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    cursor.execute("SELECT customer_id FROM orders WHERE id = %s", (order_id,))
    order_owner = cursor.fetchone()
    if not order_owner or order_owner['customer_id'] != customer_id:
        cursor.close()
        conn.close()
        return jsonify({'error': 'Access Denied'}), 403
    query = "SELECT od.quantity, od.price_at_order, p.name, p.name_chinese FROM order_details od JOIN products p ON od.barcode = p.barcode WHERE od.order_id = %s"
    cursor.execute(query, (order_id,))
    details = cursor.fetchall()
    cursor.close()
    conn.close()
    details_data = [{'product_name': item['name_chinese'] or item['name'], **dict(item)} for item in details]
    return jsonify(details_data)

# 請將您舊的 get_pending_orders 函式完整替換為此版本
@app.route('/api/pending_orders', methods=['GET'])
def get_pending_orders():
    if session.get('user_type') != 'admin': return jsonify({'error': 'Unauthorized'}), 403
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    # 【主要修改】在 SELECT 中新增 o.total_amount
    query = """
        SELECT o.id, o.order_date, o.total_amount, c.name as customer_name, c.id as customer_id, 
               (SELECT COUNT(*) FROM order_details od WHERE od.order_id = o.id) as item_count, 
               (SELECT SUM(od.quantity) FROM order_details od WHERE od.order_id = o.id) as total_quantity 
        FROM orders o JOIN customers c ON o.customer_id = c.id 
        WHERE o.status = '待處理' ORDER BY o.order_date ASC
    """
    cursor.execute(query)
    orders = cursor.fetchall()
    cursor.close()
    conn.close()
    orders_data = convert_records_timezone(orders, time_key='order_date')
    return jsonify(orders_data)

# 請將您舊的 get_order_fulfillment_details 函式完整替換為此版本
@app.route('/api/order_fulfillment_details/<int:order_id>', methods=['GET'])
def get_order_fulfillment_details(order_id):
    if session.get('user_type') != 'admin': return jsonify({'error': 'Unauthorized'}), 403
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    # 【主要修改】在 SELECT 中新增 od.price_at_order
    query = """
        SELECT od.barcode, od.quantity as required_quantity, od.price_at_order, 
               p.name, p.name_chinese, p.current_stock 
        FROM order_details od JOIN products p ON od.barcode = p.barcode 
        WHERE od.order_id = %s
    """
    cursor.execute(query, (order_id,))
    details = cursor.fetchall()
    cursor.close()
    conn.close()
    return jsonify([dict(row) for row in details])

# 請將您舊的 fulfill_order 函式完整替換為此版本
@app.route('/api/fulfill_order', methods=['POST'])
def fulfill_order():
    if session.get('user_type') != 'admin': return jsonify({'success': False, 'error': 'Unauthorized'}), 403
    data = request.json
    order_id, customer_id, fulfilled_items = data.get('order_id'), data.get('customer_id'), data.get('fulfilled_items')
    
    # 【主要修改】增加對 fulfilled_items 內容的檢查
    if not all([order_id, customer_id, fulfilled_items and isinstance(fulfilled_items, list)]): 
        return jsonify({'success': False, 'error': '請求資料不完整'}), 400

    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    try:
        for item in fulfilled_items:
            # 【主要修改】檢查每項商品是否包含 barcode, quantity, 和 adjusted_price
            if not all(k in item for k in ['barcode', 'quantity', 'adjusted_price']):
                return jsonify({'success': False, 'error': '單項出貨商品資料不完整'}), 400
            
            # 【主要修改】直接使用前端傳來的 adjusted_price 作為交易價格
            transaction_price = item['adjusted_price']
            
            cursor.execute("INSERT INTO transactions (barcode, type, quantity, transaction_price, customer_id) VALUES (%s, 'OUT', %s, %s, %s)",
                           (item['barcode'], item['quantity'], transaction_price, customer_id))

        cursor.execute("UPDATE orders SET status = '已出貨' WHERE id = %s", (order_id,))
        conn.commit()
        return jsonify({'success': True, 'message': f'訂單 #{order_id} 已成功出貨！'})
    except psycopg2.Error as e:
        print(f"DB Error: {e}")
        conn.rollback()
        return jsonify({'success': False, 'error': '處理出貨時發生未知錯誤'}), 500
    finally:
        cursor.close()
        if conn: conn.close()

# 請將您舊的 get_transaction_report 函式完整替換為此版本
@app.route('/api/reports/transactions', methods=['POST'])
def get_transaction_report():
    if session.get('user_type') != 'admin': 
        return jsonify({'error': 'Unauthorized'}), 403
    
    try:
        data = request.json
        start_date_str = data.get('start_date')
        end_date_str = data.get('end_date')
        
        if not start_date_str or not end_date_str: 
            return jsonify({'error': '請提供開始與結束日期'}), 400
        
        # 【主要修改】將日期字串轉換為日期物件，並計算結束日期的隔天
        start_date = datetime.strptime(start_date_str, '%Y-%m-%d').date()
        end_date = datetime.strptime(end_date_str, '%Y-%m-%d').date()
        end_date_exclusive = end_date + timedelta(days=1)
            
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        
        query = """
            SELECT
                t.id,
                t.timestamp,
                t.type,
                t.quantity,
                p.name_chinese,
                p.name,
                t.barcode,
                COALESCE(c.name, s.name, 'N/A') as partner_name,
                CASE WHEN t.type = 'IN' THEN t.transaction_price ELSE 0 END AS purchase_price,
                CASE WHEN t.type = 'OUT' THEN t.transaction_price ELSE 0 END AS selling_price,
                CASE WHEN t.type = 'OUT' THEN (t.transaction_price - p.purchase_price) * t.quantity ELSE 0 END AS net_profit
            FROM transactions t
            LEFT JOIN products p ON t.barcode = p.barcode
            LEFT JOIN customers c ON t.customer_id = c.id
            LEFT JOIN suppliers s ON t.supplier_id = s.id
            -- 【主要修改】使用 >= 和 < 來定義時間範圍，並讓資料庫處理時區轉換
            WHERE t.timestamp AT TIME ZONE 'Asia/Taipei' >= %s 
              AND t.timestamp AT TIME ZONE 'Asia/Taipei' < %s 
            ORDER BY t.timestamp DESC
        """
        
        cursor.execute(query, (start_date, end_date_exclusive))
        transactions = cursor.fetchall()
        cursor.close()
        conn.close()
        
        report_data = convert_records_timezone(transactions, time_key='timestamp')
        
        for row in report_data:
            row['product_name'] = row.get('name_chinese') or row.get('name') or 'N/A'
        
        return jsonify(report_data)
        
    except Exception as e:
        print(f"Report generation error: {e}")
        return jsonify({'error': '產生報表時發生未知伺服器錯誤'}), 500

@app.route('/api/reports/export_xlsx', methods=['POST'])
def export_xlsx_report():
    if session.get('user_type') != 'admin': return Response("Unauthorized", status=403)
    try:
        data = request.json
        start_date = data.get('start_date')
        end_date = data.get('end_date')
        if not start_date or not end_date: return Response("Missing date range", status=400)
        end_date_full = f"{end_date} 23:59:59"
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        query = """
            SELECT
                t.timestamp, t.type, p.name_chinese, p.name, t.barcode,
                t.quantity, t.transaction_price as price,
                p.purchase_price as product_purchase_price,
                COALESCE(c.name, s.name, 'N/A') as partner_name
            FROM transactions t
            LEFT JOIN products p ON t.barcode = p.barcode
            LEFT JOIN customers c ON t.customer_id = c.id
            LEFT JOIN suppliers s ON t.supplier_id = s.id
            WHERE t.timestamp BETWEEN %s AND %s ORDER BY t.timestamp DESC
        """
        cursor.execute(query, (start_date, end_date_full))
        transactions = cursor.fetchall()
        cursor.close()
        conn.close()

        wb = Workbook()
        ws = wb.active
        ws.title = "交易明細"
        headers = ["日期時間", "類型", "品名", "條碼", "數量", "進貨價", "出貨價", "淨利", "廠商/店家"]
        ws.append(headers)
        for cell in ws[1]: cell.font = Font(bold=True)

        cst_tz = timezone(timedelta(hours=8))
        for row in transactions:
            purchase_price, selling_price, net_profit = 0.0, 0.0, 0.0
            if row['type'] == 'IN':
                purchase_price = row['price']
            else:
                purchase_price = row['product_purchase_price'] or 0.0
                selling_price = row['price']
                net_profit = (selling_price - purchase_price) * row['quantity']
            
            utc_dt = row['timestamp']
            if utc_dt.tzinfo is None:
                utc_dt = utc_dt.replace(tzinfo=timezone.utc)
            cst_dt_aware = utc_dt.astimezone(cst_tz)
            cst_dt_naive = cst_dt_aware.replace(tzinfo=None)

            ws.append([
                cst_dt_naive,
                '進貨' if row['type'] == 'IN' else '出貨',
                row['name_chinese'] or row['name'], row['barcode'],
                row['quantity'], purchase_price, selling_price, net_profit,
                row['partner_name']
            ])

        mem_file = io.BytesIO()
        wb.save(mem_file)
        mem_file.seek(0)
        filename = f'交易明細_{start_date}_to_{end_date}.xlsx'
        encoded_filename = quote(filename)
        
        return Response(
            mem_file,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            headers={'Content-Disposition': f"attachment; filename*=UTF-8''{encoded_filename}"}
        )
    except Exception as e:
        print(f"Excel export error: {e}")
        return Response("伺服器匯出錯誤", status=500)

# 請將此函式新增到 app.py 的最下方
@app.route('/api/transaction/delete', methods=['POST'])
def delete_transaction():
    if session.get('user_type') != 'admin':
        return jsonify({'success': False, 'error': 'Unauthorized'}), 403

    data = request.json
    transaction_id = data.get('transaction_id')
    if not transaction_id:
        return jsonify({'success': False, 'error': '缺少交易ID'}), 400

    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    try:
        # 步驟 1: 找出這筆交易的詳細資訊 (類型、數量、條碼)
        cursor.execute("SELECT type, quantity, barcode FROM transactions WHERE id = %s", (transaction_id,))
        transaction = cursor.fetchone()

        if not transaction:
            return jsonify({'success': False, 'error': '找不到該筆交易紀錄'}), 404

        # 步驟 2: 根據交易類型，計算要還原的庫存數量
        # 如果是進貨('IN')，刪除時就要減少庫存
        # 如果是出貨('OUT')，刪除時就要加回庫存
        stock_adjustment = -transaction['quantity'] if transaction['type'] == 'IN' else transaction['quantity']
        
        # 步驟 3: 更新 products 資料表的庫存
        cursor.execute("UPDATE products SET current_stock = current_stock + %s WHERE barcode = %s",
                       (stock_adjustment, transaction['barcode']))
        
        # 步驟 4: 從 transactions 資料表刪除這筆紀錄
        cursor.execute("DELETE FROM transactions WHERE id = %s", (transaction_id,))
        
        # 步驟 5: 提交事務，讓以上所有變更生效
        conn.commit()
        
        return jsonify({'success': True, 'message': f'交易 #{transaction_id} 已成功刪除並還原庫存。'})

    except psycopg2.Error as e:
        # 如果任何一步出錯，就回滾所有操作
        conn.rollback()
        print(f"Delete transaction error: {e}")
        return jsonify({'success': False, 'error': '刪除操作時發生資料庫錯誤'}), 500
    finally:
        cursor.close()
        if conn: conn.close()

# 請將此函式新增到 app.py 的最下方
@app.route('/api/product/history/<barcode>', methods=['GET'])
def get_product_history(barcode):
    if session.get('user_type') != 'admin':
        return jsonify({'error': 'Unauthorized'}), 403

    if not barcode:
        return jsonify({'error': '缺少商品條碼'}), 400

    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    try:
        query = """
            SELECT
                t.id,
                t.timestamp,
                t.type,
                t.quantity,
                t.transaction_price,
                COALESCE(c.name, s.name, 'N/A') as partner_name
            FROM transactions t
            LEFT JOIN customers c ON t.customer_id = c.id
            LEFT JOIN suppliers s ON t.supplier_id = s.id
            WHERE t.barcode = %s
            ORDER BY t.timestamp DESC
        """
        cursor.execute(query, (barcode,))
        history = cursor.fetchall()
        
        # 轉換時區
        history_data = convert_records_timezone(history, time_key='timestamp')

        return jsonify(history_data)

    except psycopg2.Error as e:
        print(f"Product history fetch error: {e}")
        return jsonify({'error': '查詢歷史紀錄時發生資料庫錯誤'}), 500
    finally:
        cursor.close()
        if conn: conn.close()
