import pandas as pd
import pandas as pd
import google.generativeai as genai
from getpass import getpass

# ---- Step 1: API Key ----
import os
from dotenv import load_dotenv

load_dotenv()

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
genai.configure(api_key=GOOGLE_API_KEY)
print('API key set successfully!')

# ---- Step 2: Load Data from Google Sheet ----
SHEET_URL = "https://docs.google.com/spreadsheets/d/16asI7EmNgjN6QCWhXzj8pVC5ZClqtimjkP6tQqbnSqI/export?format=csv&gid=1797651716"
REFRESH_SECONDS = 30 #Refresh every 30 seconds (minimum recommended)

try:
    df = pd.read_csv(SHEET_URL)
    print(f'Loaded {len(df)} products from Google Sheet!')
except Exception as e:
    print(f'Could not load Google Sheet: {e}')
    print('Loading embedded fallback data...')
    import io
    RAW_CSV = """company,price_aed,rating_out_of_5
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
    df = pd.read_csv(io.StringIO(RAW_CSV))
    print(f'Fallback data loaded: {len(df)} products.')

# ---- Step 3: Setup Gemini ----
SYSTEM_PROMPT = f"""You are a helpful shopping assistant for an Amazon UAE product catalog.
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
    system_instruction=SYSTEM_PROMPT,
    generation_config=genai.GenerationConfig(
        temperature=0,
    )
)

chat = model.start_chat(history=[])

# ---- Step 4: Chat Loop ----
print()
print('=' * 60)
print('  Amazon Product Chatbot - Powered by Gemini')
print('=' * 60)
print()
print('Example questions:')
print('  - What is the cheapest product?')
print('  - Which Philips shavers are rated above 4.2?')
print('  - Recommend the best value shaver under 150 AED')
print('  - Which product has the highest rating?')
print('  - Compare Braun vs Philips products')
print('  - Are there any shavers for women?')
print()
print('Type "quit" to exit.')
print('-' * 60)

while True:
    user_input = input('\nYou: ').strip()

    if not user_input:
        continue

    if user_input.lower() in ['quit', 'exit', 'bye', 'stop']:
        print('\nGoodbye! Happy shopping!')
        break

    try:
        response = chat.send_message(user_input)
        print(f'\nAssistant:\n{response.text}')
        print('-' * 60)
    except Exception as e:
        print(f'\nError: {e}\nPlease try again.')
