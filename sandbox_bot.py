import pandas as pd
from flask import Flask, request, jsonify
from dhanhq import dhanhq

app = Flask(__name__)

# --- CONFIG ---
CLIENT_ID = "2604143923"
ACCESS_TOKEN = "eyJhbGciOiJIUzUxMiIsInR5cCI6IkpXVCJ9.eyJ0b2tlbkNvbnN1bWVyVHlwZSI6IlNFTEYiLCJwYXJ0bmVySWQiOiIiLCJkaGFuQ2xpZW50SWQiOiIyNjA0MTQzOTIzIiwid2ViaG9va1VybCI6IiIsImlzcyI6ImRoYW4iLCJleHAiOjE3Nzg3NDQ2ODl9.KJ2EQAJDz9cdModWhSD6Ux3MwTsRJcAy15bZHfZxmGq4xGXfazX3KGJDNCIBbxHk2xJ8gi1yquHpW6q8ZP73ag"
SECRET_TOKEN = "JunnarTrader2026"

dhan = dhanhq(CLIENT_ID, ACCESS_TOKEN)
# Override the default live URL to the Sandbox URL
dhan.base_url = "https://sandbox.dhan.co/v2"

print("Syncing with Dhan Master Scrip List...")
SCRIP_URL = "https://images.dhan.co/api-data/api-scrip-master.csv"

# Added low_memory=False to fix the DtypeWarning
df = pd.read_csv(SCRIP_URL, low_memory=False)

def get_security_id(symbol):
    """Updated for 2026 Column Names"""
    try:
        # Dhan uses 'SEM_TRADING_SYMBOL' for the ticker and 'SEM_EXM_EXCH_ID' for Exchange
        match = df[(df['SEM_TRADING_SYMBOL'] == symbol.upper()) & (df['SEM_EXM_EXCH_ID'] == 'NSE')]
        
        if not match.empty:
            return str(match.iloc[0]['SEM_SMST_SECURITY_ID'])
        else:
            print(f"Symbol {symbol} not found in NSE segment.")
            return None
    except Exception as e:
        print(f"Lookup Error: {e}")
        return None

@app.route('/webhook', methods=['POST'])
def webhook():
    data = request.json
    if not data or data.get('secret') != SECRET_TOKEN:
        return jsonify({"error": "Unauthorized"}), 403

    symbol = data.get('symbol')
    sec_id = get_security_id(symbol)

    if not sec_id:
        return jsonify({"error": f"Symbol {symbol} not found"}), 400

    # Placing the order
    response = dhan.place_order(
        security_id=sec_id,
        exchange_segment=dhan.NSE,
        transaction_type=dhan.BUY if data['side'].lower() == 'buy' else dhan.SELL,
        quantity=int(data['quantity']),
        order_type=dhan.MARKET,
        product_type=dhan.INTRA,
        price=0,
        after_market_order=False 
    )
    
    return jsonify(response), 200

if __name__ == '__main__':
    app.run(port=5000)