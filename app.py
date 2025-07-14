# 檔案: app.py (修改後)

from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify, Response
# 【主要修改】移除 sqlite3，引入 psycopg2 和 os，以及 click
import psycopg2 
import psycopg2.extras
import os
import io
import csv
import click # 新增
from werkzeug.security import generate_password_hash, check_password_hash
from openpyxl import Workbook
from openpyxl.styles import Font
from urllib.parse import quote
from datetime import datetime, timezone, timedelta

app = Flask(__name__)
app.secret_key = os.environ.get('FLASK_SECRET_KEY', 'a_default_dev_secret_key_change_me_in_production')
app.secret_key = os.environ.get('FLASK_SECRET_KEY', 'a_default_dev_secret_key_change_me_in_production')

# 【主要修改】資料庫連線函式
def get_db_connection():
    # Render 會透過環境變數提供資料庫連線 URL
    conn_string = os.environ.get('DATABASE_URL')
    conn = psycopg2.connect(conn_string)
    # 使用 psycopg2.extras.DictCursor 讓回傳的資料像字典一樣，方便操作
    # 注意：這需要在你的 requirements.txt 中沒有額外的相依性
    # cursor_factory=psycopg2.extras.DictCursor 是一個很好的實踐，但為了簡化，我們先用索引
    return conn

def add_column_if_not_exists(cursor, table_name, column_name, column_def):
    cursor.execute(f"PRAGMA table_info({table_name})")
    columns = [row['name'] for row in cursor.fetchall()]
    if column_name not in columns:
        print(f"正在為表格 '{table_name}' 新增欄位 '{column_name}'...")
        try:
            cursor.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_def}")
            print("欄位新增成功。")
        except sqlite3.OperationalError as e:
            print(f"新增欄位失敗: {e}")

# 【主要修改】資料庫初始化函式 (幾乎不變，但需要在雲端手動執行)
def setup_database():
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # SQLite 的 AUTOINCREMENT 在 PostgreSQL 中是 SERIAL
    cursor.execute("CREATE TABLE IF NOT EXISTS categories (id SERIAL PRIMARY KEY, name TEXT NOT NULL UNIQUE)")
    cursor.execute("CREATE TABLE IF NOT EXISTS products (barcode TEXT PRIMARY KEY, name TEXT NOT NULL, name_chinese TEXT NOT NULL DEFAULT '', description TEXT, current_stock INTEGER NOT NULL DEFAULT 0, purchase_price REAL NOT NULL DEFAULT 0.0, selling_price REAL NOT NULL DEFAULT 0.0, category_id INTEGER REFERENCES categories(id) ON DELETE SET NULL)")
    cursor.execute("CREATE TABLE IF NOT EXISTS customers (id SERIAL PRIMARY KEY, name TEXT NOT NULL UNIQUE, username TEXT UNIQUE, password TEXT, status TEXT NOT NULL DEFAULT 'active')")
    cursor.execute("CREATE TABLE IF NOT EXISTS suppliers (id SERIAL PRIMARY KEY, name TEXT NOT NULL UNIQUE, status TEXT NOT NULL DEFAULT 'active')")
    # PostgreSQL 的 CURRENT_TIMESTAMP 是帶有時區的
    cursor.execute("CREATE TABLE IF NOT EXISTS transactions (id SERIAL PRIMARY KEY, barcode TEXT NOT NULL REFERENCES products(barcode) ON DELETE RESTRICT, type TEXT NOT NULL, quantity INTEGER NOT NULL, transaction_price REAL NOT NULL DEFAULT 0.0, customer_id INTEGER REFERENCES customers(id) ON DELETE RESTRICT, supplier_id INTEGER REFERENCES suppliers(id) ON DELETE RESTRICT, timestamp TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP)")
    cursor.execute("CREATE TABLE IF NOT EXISTS orders (id SERIAL PRIMARY KEY, customer_id INTEGER NOT NULL REFERENCES customers(id) ON DELETE RESTRICT, order_date TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP, status TEXT NOT NULL DEFAULT '待處理', total_amount REAL)")
    cursor.execute("CREATE TABLE IF NOT EXISTS order_details (id SERIAL PRIMARY KEY, order_id INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE, barcode TEXT NOT NULL REFERENCES products(barcode) ON DELETE RESTRICT, quantity INTEGER NOT NULL, price_at_order REAL NOT NULL)")
    cursor.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")

    conn.commit()
    cursor.close()
    conn.close()
    print("資料庫資料表建立完成。")

def convert_records_timezone(records, time_key='timestamp'):
    cst_tz = timezone(timedelta(hours=8))
    converted_records = []
    for record in records:
        record_dict = dict(record)
        time_str_utc = record_dict.get(time_key)
        if time_str_utc:
            time_str_utc_main = time_str_utc.split('.')[0]
            utc_dt = datetime.strptime(time_str_utc_main, '%Y-%m-%d %H:%M:%S').replace(tzinfo=timezone.utc)
            cst_dt = utc_dt.astimezone(cst_tz)
            record_dict[time_key] = cst_dt.strftime('%Y-%m-%d %H:%M:%S')
        converted_records.append(record_dict)
    return converted_records

# 請將您舊的 login 函式完整替換為此版本
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
            # 修正 1：建立 cursor 物件，並使用 DictCursor 讓回傳結果像字典
            cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor) 
            
            # 修正 2：使用 cursor 來執行指令，並修正 SQL 字串中的引號
            cursor.execute("SELECT * FROM customers WHERE username = %s AND status = %s", (username, 'active'))
            customer = cursor.fetchone()
            
            # 修正 3：使用完畢後關閉 cursor 和 connection
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
        # 修正 4：捕捉 psycopg2 的錯誤，而不是 sqlite3 的錯誤
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
    products = conn.execute('''
        SELECT p.barcode, p.name, p.name_chinese, p.description, p.current_stock, p.selling_price, p.category_id, c.name as category_name
        FROM products p
        LEFT JOIN categories c ON p.category_id = c.id
        WHERE p.current_stock > 0 ORDER BY p.name_chinese
    ''').fetchall()
    categories = conn.execute("SELECT id, name FROM categories ORDER BY name").fetchall()
    announcement_row = conn.execute("SELECT value FROM settings WHERE key = 'announcement'").fetchone()
    announcement = announcement_row['value'] if announcement_row and announcement_row['value'].strip() else None
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
    announcement_row = conn.execute("SELECT value FROM settings WHERE key = 'announcement'").fetchone()
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
    try:
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('announcement', %s)", (new_announcement,))
        conn.commit()
        return jsonify({'success': True, 'message': '公告已更新'})
    except sqlite3.Error:
        return jsonify({'success': False, 'error': '資料庫更新失敗'}), 500
    finally:
        if conn: conn.close()

@app.route('/api/product/<barcode>')
def get_product(barcode):
    if session.get('user_type') != 'admin': return jsonify({'error': 'Unauthorized'}), 403
    conn = get_db_connection()
    product = conn.execute('SELECT * FROM products WHERE barcode = %s', (barcode,)).fetchone()
    conn.close()
    if product: return jsonify(dict(product))
    return jsonify({'error': 'Product not found'}), 404

@app.route('/api/partners/<partner_type>')
def get_partners(partner_type):
    if session.get('user_type') != 'admin': return jsonify({'error': 'Unauthorized'}), 403
    if partner_type not in ['customers', 'suppliers']: return jsonify({'error': 'Invalid partner type'}), 400
    conn = get_db_connection()
    partners = conn.execute(f"SELECT id, name FROM {partner_type} WHERE status = 'active' ORDER BY name").fetchall()
    conn.close()
    return jsonify([dict(p) for p in partners])

@app.route('/api/product/add', methods=['POST'])
def add_product():
    if session.get('user_type') != 'admin': return jsonify({'success': False, 'error': 'Unauthorized'}), 403
    data = request.json
    category_id = data.get('category_id') if data.get('category_id') else None
    try:
        conn = get_db_connection()
        conn.execute("INSERT INTO products (barcode, name, name_chinese, purchase_price, selling_price, current_stock, category_id) VALUES (%s, %s, %s, %s, %s, 0, %s)", 
                     (data['barcode'], data['name'], data['name_chinese'], data['purchase_price'], data['selling_price'], category_id))
        conn.commit()
    except sqlite3.IntegrityError:
        return jsonify({'success': False, 'error': '此條碼已存在'}), 409
    finally:
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
    try:
        with conn:
            for item in items:
                conn.execute('UPDATE products SET current_stock = current_stock + %s WHERE barcode = %s', 
                             (item['quantity'] if trans_type == 'IN' else -item['quantity'], item['barcode']))
                product_info = conn.execute('SELECT purchase_price, selling_price FROM products WHERE barcode = %s', (item['barcode'],)).fetchone()
                price = product_info['selling_price'] if trans_type == 'OUT' else product_info['purchase_price']
                customer_id, supplier_id = (partner_id, None) if trans_type == 'OUT' else (None, partner_id)
                conn.execute("INSERT INTO transactions (barcode, type, quantity, transaction_price, customer_id, supplier_id) VALUES (%s, %s, %s, %s, %s, %s)",
                             (item['barcode'], trans_type, item['quantity'], price, customer_id, supplier_id))
        return jsonify({'success': True, 'message': f'批次{trans_type}操作成功，共處理 {len(items)} 項商品。'}), 200
    except sqlite3.Error:
        return jsonify({'success': False, 'error': '資料庫操作失敗，所有操作已復原。'}), 500
    finally:
        if conn: conn.close()

@app.route('/api/order/submit', methods=['POST'])
def submit_order():
    if session.get('user_type') != 'customer': return jsonify({'success': False, 'error': 'Unauthorized'}), 403
    data = request.json
    cart, customer_id = data.get('cart'), session.get('customer_id')
    if not cart or not isinstance(cart, list) or not customer_id: return jsonify({'success': False, 'error': '訂單資料不完整'}), 400
    conn = get_db_connection()
    try:
        with conn:
            total_amount = 0
            products_to_update = []
            for item in cart:
                product = conn.execute("SELECT name_chinese, selling_price, current_stock FROM products WHERE barcode = %s", (item['barcode'],)).fetchone()
                if not product: return jsonify({'success': False, 'error': f"商品條碼 '{item['barcode']}' 不存在"}), 400
                if product['current_stock'] < item['quantity']: return jsonify({'success': False, 'error': f"商品 '{product['name_chinese']}' 庫存不足"}), 400
                total_amount += product['selling_price'] * item['quantity']
                products_to_update.append({'barcode': item['barcode'], 'quantity': item['quantity'], 'price': product['selling_price']})
            
            cursor = conn.execute("INSERT INTO orders (customer_id, status, total_amount) VALUES (%s, '待處理', %s)", (customer_id, total_amount))
            order_id = cursor.lastrowid
            
            order_details_data = []
            for p in products_to_update:
                conn.execute("UPDATE products SET current_stock = current_stock - %s WHERE barcode = %s", (p['quantity'], p['barcode']))
                order_details_data.append((order_id, p['barcode'], p['quantity'], p['price']))
            conn.executemany("INSERT INTO order_details (order_id, barcode, quantity, price_at_order) VALUES (%s, %s, %s, %s)", order_details_data)

        return jsonify({'success': True, 'message': '訂單已成功送出！', 'order_id': order_id})
    except sqlite3.Error:
        return jsonify({'success': False, 'error': '建立訂單時發生錯誤'}), 500
    finally:
        if conn: conn.close()

@app.route('/api/db/<table>')
def get_table_data(table):
    if session.get('user_type') != 'admin': return jsonify({'error': 'Unauthorized'}), 403
    if table not in ['products', 'customers', 'suppliers', 'categories']: return jsonify({'error': 'Invalid table'}), 400
    conn = get_db_connection()
    try:
        if table == 'products':
            data = conn.execute(f'SELECT * FROM {table} ORDER BY name_chinese').fetchall()
        else:
            data = conn.execute(f'SELECT * FROM {table} ORDER BY id').fetchall()
    except sqlite3.Error as e:
        print(f"Error fetching table {table}: {e}")
        return jsonify({'error': f'讀取資料表 {table} 失敗'}), 500
    finally:
        if conn: conn.close()
    return jsonify([dict(row) for row in data])

@app.route('/api/db/product/update', methods=['POST'])
def update_product():
    if session.get('user_type') != 'admin': return jsonify({'success': False, 'error': 'Unauthorized'}), 403
    data = request.json
    category_id = data.get('category_id') if data.get('category_id') else None
    conn = get_db_connection()
    try:
        conn.execute("UPDATE products SET name=%s, name_chinese=%s, description=%s, current_stock=%s, purchase_price=%s, selling_price=%s, category_id=%s WHERE barcode=%s", 
                     (data['name'], data['name_chinese'], data.get('description', ''), data['current_stock'], data['purchase_price'], data['selling_price'], category_id, data['barcode']))
        conn.commit()
    except sqlite3.Error:
        return jsonify({'success': False, 'error': '資料庫更新失敗'})
    finally:
        if conn: conn.close()
    return jsonify({'success': True})

@app.route('/api/db/product/delete', methods=['POST'])
def delete_product():
    if session.get('user_type') != 'admin': return jsonify({'success': False, 'error': 'Unauthorized'}), 403
    barcode = request.json.get('barcode')
    conn = get_db_connection()
    try:
        conn.execute("DELETE FROM products WHERE barcode = %s", (barcode,))
        conn.commit()
    except sqlite3.IntegrityError:
        return jsonify({'success': False, 'error': '刪除失敗：此商品可能已被交易或訂單引用。'})
    finally:
        if conn: conn.close()
    return jsonify({'success': True})

# --- Partner (Customer/Supplier) and Category APIs ---

@app.route('/api/partner/add', methods=['POST'])
def add_partner():
    if session.get('user_type') != 'admin': return jsonify({'success': False, 'error': 'Unauthorized'}), 403
    data = request.json
    partner_type, name = data.get('type'), data.get('name')
    if partner_type not in ['customers', 'suppliers'] or not name: return jsonify({'success': False, 'error': '資料無效'}), 400
    table_name = "customers" if partner_type == 'customers' else "suppliers"
    conn = get_db_connection()
    try:
        with conn:
            if table_name == "customers":
                 username, password = data.get('username'), data.get('password')
                 if not username or not password: return jsonify({'success': False, 'error': '帳號和密碼為必填項'}), 400
                 hashed_password = generate_password_hash(password)
                 conn.execute("INSERT INTO customers (name, username, password, status) VALUES (%s, %s, %s, 'active')", (name, username, hashed_password))
            else:
                 conn.execute(f"INSERT INTO {table_name} (name, status) VALUES (%s, 'active')", (name,))
        return jsonify({'success': True})
    except sqlite3.IntegrityError:
        return jsonify({'success': False, 'error': '名稱或帳號已存在'})
    finally:
        if conn: conn.close()

# 【主要修改】加回遺漏的更新店家/廠商函式
@app.route('/api/partner/update', methods=['POST'])
def update_partner():
    if session.get('user_type') != 'admin': return jsonify({'success': False, 'error': 'Unauthorized'}), 403
    data = request.json
    ptype, pid, name = data.get('type'), data.get('id'), data.get('name')
    if ptype not in ['customers', 'suppliers'] or not pid or not name: return jsonify({'success': False, 'error': '無效的請求'}), 400
    conn = get_db_connection()
    try:
        if ptype == 'customers':
            username = data.get('username')
            if not username: return jsonify({'success': False, 'error': '客戶帳號不得為空'}), 400
            conn.execute("UPDATE customers SET name=%s, username=%s WHERE id=%s", (name, username, pid))
        else: # suppliers
            conn.execute("UPDATE suppliers SET name=%s WHERE id=%s", (name, pid))
        conn.commit()
        return jsonify({'success': True})
    except sqlite3.IntegrityError:
        return jsonify({'success': False, 'error': '名稱或帳號已存在'})
    except sqlite3.Error as e:
        print(f"API Error (update_partner): {e}")
        return jsonify({'success': False, 'error': '資料庫更新失敗'})
    finally:
        if conn: conn.close()

# 【主要修改】加回遺漏的刪除店家/廠商函式
@app.route('/api/partner/delete', methods=['POST'])
def delete_partner():
    if session.get('user_type') != 'admin': return jsonify({'success': False, 'error': 'Unauthorized'}), 403
    data = request.json
    partner_type, partner_id = data.get('type'), data.get('id')
    if partner_type not in ['customers', 'suppliers'] or not partner_id: return jsonify({'success': False, 'error': '資料無效'}), 400
    table_name = "customers" if partner_type == 'customers' else "suppliers"
    conn = get_db_connection()
    try:
        with conn: conn.execute(f"DELETE FROM {table_name} WHERE id = %s", (partner_id,))
        return jsonify({'success': True})
    except sqlite3.IntegrityError:
        return jsonify({'success': False, 'error': '刪除失敗：此項目可能已被交易或訂單引用。'})
    finally:
        if conn: conn.close()

# 【主要修改】加回遺漏的切換狀態函式
@app.route('/api/partner/toggle_status', methods=['POST'])
def toggle_partner_status():
    if session.get('user_type') != 'admin': return jsonify({'success': False, 'error': 'Unauthorized'}), 403
    data = request.json
    partner_type, partner_id = data.get('type'), data.get('id')
    if partner_type not in ['customers', 'suppliers'] or not partner_id: return jsonify({'success': False, 'error': '資料無效'}), 400
    table_name = "customers" if partner_type == 'customers' else "suppliers"
    conn = get_db_connection()
    try:
        with conn:
            current = conn.execute(f"SELECT status FROM {table_name} WHERE id = %s", (partner_id,)).fetchone()
            if not current: return jsonify({'success': False, 'error': '找不到該項目'}), 404
            new_status = 'inactive' if current['status'] == 'active' else 'active'
            conn.execute(f"UPDATE {table_name} SET status = %s WHERE id = %s", (new_status, partner_id))
        return jsonify({'success': True, 'new_status': new_status})
    except sqlite3.Error as e:
        print(f"API Error (toggle_status): {e}"); return jsonify({'success': False, 'error': '操作失敗'})
    finally:
        if conn: conn.close()

# 【主要修改】加回遺漏的重設密碼函式
@app.route('/api/db/customer/reset_password', methods=['POST'])
def reset_customer_password():
    if session.get('user_type') != 'admin': return jsonify({'success': False, 'error': 'Unauthorized'}), 403
    data = request.json
    password, customer_id = data.get('password'), data.get('id')
    if not password or not customer_id: return jsonify({'success': False, 'error': '資料不完整'}), 400
    try:
        hashed_password = generate_password_hash(password)
        conn = get_db_connection()
        conn.execute("UPDATE customers SET password=%s WHERE id=%s", (hashed_password, customer_id))
        conn.commit()
        return jsonify({'success': True})
    except sqlite3.Error as e:
        print(f"API Error (reset_password): {e}")
        return jsonify({'success': False, 'error': '密碼重設失敗'})
    finally:
        if conn: conn.close()

@app.route('/api/db/category/add', methods=['POST'])
def add_category():
    if session.get('user_type') != 'admin': return jsonify({'success': False, 'error': 'Unauthorized'}), 403
    name = request.json.get('name')
    if not name: return jsonify({'success': False, 'error': '類別名稱不得為空'}), 400
    conn = get_db_connection()
    try:
        conn.execute("INSERT INTO categories (name) VALUES (%s)", (name,))
        conn.commit()
        return jsonify({'success': True})
    except sqlite3.IntegrityError: return jsonify({'success': False, 'error': '該類別名稱已存在'})
    finally:
        if conn: conn.close()

@app.route('/api/db/category/update', methods=['POST'])
def update_category():
    if session.get('user_type') != 'admin': return jsonify({'success': False, 'error': 'Unauthorized'}), 403
    data = request.json
    cid, name = data.get('id'), data.get('name')
    if not cid or not name: return jsonify({'success': False, 'error': '無效的請求'}), 400
    conn = get_db_connection()
    try:
        conn.execute("UPDATE categories SET name = %s WHERE id = %s", (name, cid))
        conn.commit()
        return jsonify({'success': True})
    except sqlite3.IntegrityError: return jsonify({'success': False, 'error': '該類別名稱已存在'})
    finally:
        if conn: conn.close()

@app.route('/api/db/category/delete', methods=['POST'])
def delete_category():
    if session.get('user_type') != 'admin': return jsonify({'success': False, 'error': 'Unauthorized'}), 403
    cid = request.json.get('id')
    if not cid: return jsonify({'success': False, 'error': '無效的請求'}), 400
    conn = get_db_connection()
    try:
        conn.execute("DELETE FROM categories WHERE id = %s", (cid,))
        conn.commit()
        return jsonify({'success': True})
    finally:
        if conn: conn.close()

# --- Order and Fulfillment APIs ---

@app.route('/api/orders', methods=['GET'])
def get_orders():
    if session.get('user_type') != 'admin': return jsonify({'error': 'Unauthorized'}), 403
    conn = get_db_connection()
    query = "SELECT o.id, o.order_date, o.status, o.total_amount, c.name as customer_name FROM orders o JOIN customers c ON o.customer_id = c.id ORDER BY o.order_date DESC"
    orders = conn.execute(query).fetchall()
    conn.close()
    orders_data = convert_records_timezone(orders, time_key='order_date')
    return jsonify(orders_data)

@app.route('/api/orders/<int:order_id>', methods=['GET'])
def get_order_details(order_id):
    if session.get('user_type') != 'admin': return jsonify({'error': 'Unauthorized'}), 403
    conn = get_db_connection()
    query = "SELECT od.quantity, od.price_at_order, p.name, p.name_chinese FROM order_details od JOIN products p ON od.barcode = p.barcode WHERE od.order_id = %s"
    details = conn.execute(query, (order_id,)).fetchall()
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
    try:
        with conn:
            if new_status == '已取消':
                current_order = conn.execute("SELECT status FROM orders WHERE id = %s", (order_id,)).fetchone()
                if current_order and current_order['status'] != '已取消':
                    order_items = conn.execute("SELECT barcode, quantity FROM order_details WHERE order_id = %s", (order_id,)).fetchall()
                    for item in order_items:
                        conn.execute("UPDATE products SET current_stock = current_stock + %s WHERE barcode = %s", (item['quantity'], item['barcode']))
            conn.execute("UPDATE orders SET status = %s WHERE id = %s", (new_status, order_id))
        return jsonify({'success': True})
    except sqlite3.Error:
        return jsonify({'success': False, 'error': '資料庫更新失敗'}), 500
    finally:
        if conn: conn.close()

@app.route('/api/customer/orders', methods=['GET'])
def get_customer_orders():
    if session.get('user_type') != 'customer' or 'customer_id' not in session: return jsonify({'error': 'Unauthorized'}), 403
    customer_id = session['customer_id']
    conn = get_db_connection()
    query = "SELECT id, order_date, status, total_amount FROM orders WHERE customer_id = %s ORDER BY order_date DESC"
    orders = conn.execute(query, (customer_id,)).fetchall()
    conn.close()
    orders_data = convert_records_timezone(orders, time_key='order_date')
    return jsonify(orders_data)

@app.route('/api/customer/order_details/<int:order_id>', methods=['GET'])
def get_customer_order_details(order_id):
    if session.get('user_type') != 'customer' or 'customer_id' not in session: return jsonify({'error': 'Unauthorized'}), 403
    customer_id = session['customer_id']
    conn = get_db_connection()
    order_owner = conn.execute("SELECT customer_id FROM orders WHERE id = %s", (order_id,)).fetchone()
    if not order_owner or order_owner['customer_id'] != customer_id:
        conn.close()
        return jsonify({'error': 'Access Denied'}), 403
    query = "SELECT od.quantity, od.price_at_order, p.name, p.name_chinese FROM order_details od JOIN products p ON od.barcode = p.barcode WHERE od.order_id = %s"
    details = conn.execute(query, (order_id,)).fetchall()
    conn.close()
    details_data = [{'product_name': item['name_chinese'] or item['name'], **dict(item)} for item in details]
    return jsonify(details_data)

@app.route('/api/pending_orders', methods=['GET'])
def get_pending_orders():
    if session.get('user_type') != 'admin': return jsonify({'error': 'Unauthorized'}), 403
    conn = get_db_connection()
    query = "SELECT o.id, o.order_date, c.name as customer_name, c.id as customer_id, (SELECT COUNT(*) FROM order_details od WHERE od.order_id = o.id) as item_count, (SELECT SUM(od.quantity) FROM order_details od WHERE od.order_id = o.id) as total_quantity FROM orders o JOIN customers c ON o.customer_id = c.id WHERE o.status = '待處理' ORDER BY o.order_date ASC"
    orders = conn.execute(query).fetchall()
    conn.close()
    orders_data = convert_records_timezone(orders, time_key='order_date')
    return jsonify(orders_data)

@app.route('/api/order_fulfillment_details/<int:order_id>', methods=['GET'])
def get_order_fulfillment_details(order_id):
    if session.get('user_type') != 'admin': return jsonify({'error': 'Unauthorized'}), 403
    conn = get_db_connection()
    query = "SELECT od.barcode, od.quantity as required_quantity, p.name, p.name_chinese, p.current_stock FROM order_details od JOIN products p ON od.barcode = p.barcode WHERE od.order_id = %s"
    details = conn.execute(query, (order_id,)).fetchall()
    conn.close()
    return jsonify([dict(row) for row in details])

@app.route('/api/fulfill_order', methods=['POST'])
def fulfill_order():
    if session.get('user_type') != 'admin': return jsonify({'success': False, 'error': 'Unauthorized'}), 403
    data = request.json
    order_id, customer_id, fulfilled_items = data.get('order_id'), data.get('customer_id'), data.get('fulfilled_items')
    if not all([order_id, customer_id, fulfilled_items]): return jsonify({'success': False, 'error': '請求資料不完整'}), 400
    conn = get_db_connection()
    try:
        with conn:
            for item in fulfilled_items:
                product_price = conn.execute('SELECT selling_price FROM products WHERE barcode = %s', (item['barcode'],)).fetchone()['selling_price']
                conn.execute("INSERT INTO transactions (barcode, type, quantity, transaction_price, customer_id) VALUES (%s, 'OUT', %s, %s, %s)", (item['barcode'], item['quantity'], product_price, customer_id))
            conn.execute("UPDATE orders SET status = '已出貨' WHERE id = %s", (order_id,))
        return jsonify({'success': True, 'message': f'訂單 #{order_id} 已成功出貨！'})
    except sqlite3.Error:
        return jsonify({'success': False, 'error': '處理出貨時發生未知錯誤'}), 500
    finally:
        if conn: conn.close()

@app.route('/api/reports/transactions', methods=['POST'])
def get_transaction_report():
    if session.get('user_type') != 'admin': 
        return jsonify({'error': 'Unauthorized'}), 403
    
    try:
        data = request.json
        start_date = data.get('start_date')
        end_date = data.get('end_date')
        
        if not start_date or not end_date: 
            return jsonify({'error': '請提供開始與結束日期'}), 400
            
        end_date_full = f"{end_date} 23:59:59"
        conn = get_db_connection()
        
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
        
        transactions = conn.execute(query, (start_date, end_date_full)).fetchall()
        conn.close()
        
        report_data_with_timezone = convert_records_timezone(transactions, time_key='timestamp')
        
        for i, original_row in enumerate(transactions):
            report_data_with_timezone[i]['product_name'] = original_row['name_chinese'] or original_row['name']

        return jsonify(report_data_with_timezone)
        
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
        
        if not start_date or not end_date: 
            return Response("Missing date range", status=400)
            
        end_date_full = f"{end_date} 23:59:59"
        conn = get_db_connection()
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
        transactions = conn.execute(query, (start_date, end_date_full)).fetchall()
        conn.close()

        wb = Workbook()
        ws = wb.active
        ws.title = "交易明細"
        headers = ["日期時間", "類型", "品名", "條碼", "數量", "進貨價", "出貨價", "淨利", "廠商/店家"]
        ws.append(headers)
        for cell in ws[1]: cell.font = Font(bold=True)

        cst_tz = timezone(timedelta(hours=8))
        for row in transactions:
            purchase_price, selling_price, net_profit = 0, 0, 0
            if row['type'] == 'IN':
                purchase_price = row['price']
            else:
                purchase_price = row['product_purchase_price'] or 0
                selling_price = row['price']
                net_profit = (selling_price - purchase_price) * row['quantity']
            
            time_str_utc_main = row['timestamp'].split('.')[0]
            utc_dt = datetime.strptime(time_str_utc_main, '%Y-%m-%d %H:%M:%S').replace(tzinfo=timezone.utc)
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

# 【主要修改】新增一個給 Flask 的命令列指令
@app.cli.command("init-db")
def init_db_command():
    """清除並建立新的資料表。"""
    setup_database()
    click.echo("已成功初始化資料庫。")

if __name__ == '__main__':
    with app.app_context():
        setup_database()
    app.run(host='0.0.0.0', port=5020, debug=False)
