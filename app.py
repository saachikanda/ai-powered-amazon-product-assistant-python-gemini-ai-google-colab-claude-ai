# from flask import Flask, request, jsonify, send_from_directory
# from flask_cors import CORS
# import pandas as pd
# import google.generativeai as genai
# import threading
# import time
# import os

# app = Flask(__name__, static_folder='static')
# CORS(app)

# # ---- CONFIG ----
# GOOGLE_API_KEY = 'AIzaSyA9R_MYGKdhbHBZTXjlcf226zGDWOZaa0k'  # Set via environment variable
# SHEET_URL = "https://docs.google.com/spreadsheets/d/16asI7EmNgjN6QCWhXzj8pVC5ZClqtimjkP6tQqbnSqI/export?format=csv&gid=1797651716"
# REFRESH_SECONDS = 30

# # ---- GLOBALS ----
# df = pd.DataFrame()
# chat = None
# model = None
# last_refresh = None
# product_count = 0

# FALLBACK_CSV = """company,price_aed,rating_out_of_5
# Philips Body Groomer BG3480/15 | Trim & shave body hair & balls | 100% showerproof,118.55,3.8
# Philips Shaver 1000 Series Wet & Dry Electric Shaver S1151/00,103.98,4.5
# Philips Shaver 3000 Series Wet & Dry Electric Shaver S3144/00,165.0,4.5
# WAHL Travel Shaver for Men - Wet & Dry Cordless Electric Razor,76.9,3.9
# Panasonic Pro Curve Wet & Dry Shaver,92.0,4.3
# Braun Series 5 51-B1000s Electric Shaver Wet & Dry Blue,221.4,4.0
# Panasonic 1 Blade For Men Travel Shaver Black,60.8,4.0
# Philips Body Groomer BG5480/15 Back attachment,219.0,4.4
# Philips Cordless Wet & Dry Lady Shaver BRL138/00,209.0,4.1
# Philips Series 5000 Wet and dry electric shaver S5887/10,265.0,3.8
# """


# def load_data():
#     global df, product_count, last_refresh
#     try:
#         df = pd.read_csv(SHEET_URL)
#         product_count = len(df)
#         last_refresh = time.strftime('%H:%M:%S')
#         print(f'[{last_refresh}] Refreshed: {product_count} products loaded.')
#     except Exception as e:
#         print(f'Sheet load failed: {e}. Using fallback.')
#         import io
#         df = pd.read_csv(io.StringIO(FALLBACK_CSV))
#         product_count = len(df)
#         last_refresh = time.strftime('%H:%M:%S') + ' (fallback)'


# def build_model():
#     global model, chat
#     if not GOOGLE_API_KEY:
#         return
#     genai.configure(api_key=GOOGLE_API_KEY)
#     system_prompt = f"""You are a helpful shopping assistant for an Amazon UAE product catalog.
# You have access to the following grooming product data (shavers, trimmers, groomers).
# Use ONLY this data to answer questions. Be specific, helpful, and concise.

# When recommending products:
# - Mention the product name (shortened is fine), price in AED, and rating
# - If asked for best value, consider both price and rating
# - If asked to filter by brand, price range, or rating, apply those filters accurately

# Here is the full product catalog:

# {df.to_csv(index=False)}
# """
#     model = genai.GenerativeModel(
#         model_name='gemini-2.5-flash',
#         system_instruction=system_prompt,
#         generation_config=genai.GenerationConfig(temperature=0)
#     )
#     chat = model.start_chat(history=[])
#     print('Gemini model initialized.')


# def refresh_loop():
#     while True:
#         time.sleep(REFRESH_SECONDS)
#         load_data()
#         build_model()  # Rebuild with fresh data


# # ---- ROUTES ----

# @app.route('/')
# def serve_welcome():
#     return send_from_directory('static', 'index.html')


# @app.route('/chat')
# def serve_chat():
#     return send_from_directory('static', 'chat.html')


# @app.route('/api/setup', methods=['POST'])
# def setup():
#     global GOOGLE_API_KEY
#     data = request.get_json()
#     GOOGLE_API_KEY = data.get('api_key', '').strip()
#     if not GOOGLE_API_KEY:
#         return jsonify({'error': 'No API key provided'}), 400
#     try:
#         load_data()
#         build_model()
#         threading.Thread(target=refresh_loop, daemon=True).start()
#         return jsonify({'success': True, 'product_count': product_count})
#     except Exception as e:
#         return jsonify({'error': str(e)}), 500


# @app.route('/api/chat', methods=['POST'])
# def chat_endpoint():
#     if chat is None:
#         return jsonify({'error': 'Bot not initialized. Please set up your API key first.'}), 400
#     data = request.get_json()
#     user_msg = data.get('message', '').strip()
#     if not user_msg:
#         return jsonify({'error': 'Empty message'}), 400
#     try:
#         response = chat.send_message(user_msg)
#         return jsonify({'reply': response.text, 'last_refresh': last_refresh, 'product_count': product_count})
#     except Exception as e:
#         return jsonify({'error': str(e)}), 500


# @app.route('/api/status')
# def status():
#     return jsonify({
#         'initialized': chat is not None,
#         'product_count': product_count,
#         'last_refresh': last_refresh
#     })


# if __name__ == '__main__':
#     load_data()
#     print('Starting server on http://localhost:5000')
#     app.run(debug=True, port=5000, use_reloader=False)
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
import pandas as pd
import google.generativeai as genai
import threading
import time
import os

app = Flask(__name__, static_folder='static')
CORS(app)

# ---- CONFIG ----
from dotenv import load_dotenv
import os

load_dotenv()

api_key = os.getenv("GEMINI_API_KEY")
SHEET_URL = "https://docs.google.com/spreadsheets/d/16asI7EmNgjN6QCWhXzj8pVC5ZClqtimjkP6tQqbnSqI/export?format=csv&gid=1797651716"
REFRESH_SECONDS = 30

# ---- GLOBALS ----
df = pd.DataFrame()
chat = None
model = None
last_refresh = None
product_count = 0
refresh_thread_started = False

FALLBACK_CSV = """company,price_aed,rating_out_of_5
Philips Body Groomer BG3480/15 | Trim & shave body hair & balls | 100% showerproof,118.55,3.8
Philips Shaver 1000 Series Wet & Dry Electric Shaver S1151/00,103.98,4.5
Philips Shaver 3000 Series Wet & Dry Electric Shaver S3144/00,165.0,4.5
WAHL Travel Shaver for Men - Wet & Dry Cordless Electric Razor,76.9,3.9
Panasonic Pro Curve Wet & Dry Shaver,92.0,4.3
Braun Series 5 51-B1000s Electric Shaver Wet & Dry Blue,221.4,4.0
Panasonic 1 Blade For Men Travel Shaver Black,60.8,4.0
Philips Body Groomer BG5480/15 Back attachment,219.0,4.4
Philips Cordless Wet & Dry Lady Shaver BRL138/00,209.0,4.1
Philips Series 5000 Wet and dry electric shaver S5887/10,265.0,3.8
"""


def load_data():
    global df, product_count, last_refresh
    try:
        df = pd.read_csv(SHEET_URL)
        product_count = len(df)
        last_refresh = time.strftime('%H:%M:%S')
        print(f'[{last_refresh}] Refreshed: {product_count} products loaded.')
    except Exception as e:
        print(f'Sheet load failed: {e}. Using fallback.')
        import io
        df = pd.read_csv(io.StringIO(FALLBACK_CSV))
        product_count = len(df)
        last_refresh = time.strftime('%H:%M:%S') + ' (fallback)'


def build_model():
    global model, chat
    genai.configure(api_key=os.getenv("GOOGLE_API_KEY"))
    system_prompt = f"""You are a helpful shopping assistant for an Amazon UAE product catalog.
You have access to the following grooming product data (shavers, trimmers, groomers).
Use ONLY this data to answer questions. Be specific, helpful, and concise.

When recommending products:
- Mention the product name (shortened is fine), price in AED, and rating
- If asked for best value, consider both price and rating
- If asked to filter by brand, price range, or rating, apply those filters accurately

Here is the full product catalog:

{df.to_csv(index=False)}
"""
    model = genai.GenerativeModel(
        model_name='gemini-2.5-flash',
        system_instruction=system_prompt,
        generation_config=genai.GenerationConfig(temperature=0)
    )
    chat = model.start_chat(history=[])
    print('Gemini model initialized.')


def refresh_loop():
    while True:
        time.sleep(REFRESH_SECONDS)
        load_data()
        build_model()


# ---- ROUTES ----

@app.route('/')
def serve_welcome():
    return send_from_directory('static', 'index.html')


@app.route('/chat')
def serve_chat():
    return send_from_directory('static', 'chat.html')


@app.route('/api/setup', methods=['POST'])
def setup():
    global refresh_thread_started
    try:
        load_data()
        build_model()
        if not refresh_thread_started:
            threading.Thread(target=refresh_loop, daemon=True).start()
            refresh_thread_started = True
        return jsonify({'success': True, 'product_count': product_count})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/chat', methods=['POST'])
def chat_endpoint():
    if chat is None:
        return jsonify({'error': 'Bot not initialized.'}), 400
    data = request.get_json()
    user_msg = data.get('message', '').strip()
    if not user_msg:
        return jsonify({'error': 'Empty message'}), 400
    try:
        response = chat.send_message(user_msg)
        return jsonify({'reply': response.text, 'last_refresh': last_refresh, 'product_count': product_count})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/status')
def status():
    return jsonify({
        'initialized': chat is not None,
        'product_count': product_count,
        'last_refresh': last_refresh
    })


if __name__ == '__main__':
    load_data()
    build_model()
    threading.Thread(target=refresh_loop, daemon=True).start()
    refresh_thread_started = True
    print('Starting server on http://localhost:5000')
    app.run(debug=True, port=5000, use_reloader=False)