import pandas as pd
from flask import Flask, request, jsonify
from dhanhq import dhanhq

app = Flask(__name__)

# --- LIVE CONFIG ---
# Replace these with your exact Client ID and Access Token from your main LIVE PORTAL!
CLIENT_ID = "1100819221"
ACCESS_TOKEN = "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzUxMiJ9.eyJpc3MiOiJkaGFuIiwicGFydG5lcklkIjoiIiwiZXhwIjoxNzc2MzI5OTMxLCJpYXQiOjE3NzYyNDM1MzEsInRva2VuQ29uc3VtZXJUeXBlIjoiU0VMRiIsIndlYmhvb2tVcmwiOiIiLCJkaGFuQ2xpZW50SWQiOiIxMTAwODE5MjIxIn0.0NguK60rKoPFW3tVZw9whFGnPF7-9VNb-doHit01lWAq0IPF3ac-lxO8qfVM73o5qn487DjQKoBKF-kAA7wLEw"
SECRET_TOKEN = "JunnarTrader2026"

# Connect to the Live Trading System
dhan = dhanhq(CLIENT_ID, ACCESS_TOKEN)

# Notice here that we REMOVED the `dhan.base_url = ...` line!
# The default library URL points directly to the live environment.

print("Syncing with Dhan Master Scrip List...")
SCRIP_URL = "https://images.dhan.co/api-data/api-scrip-master.csv"

# Added low_memory=False to fix the DtypeWarning
df = pd.read_csv(SCRIP_URL, low_memory=False)

def get_security_id(symbol):
    try:
        # FNO symbols have mixed case (e.g. Jun2024), so making the search entirely case-insensitive
        match = df[(df['SEM_TRADING_SYMBOL'].str.upper() == symbol.upper()) & (df['SEM_EXM_EXCH_ID'] == 'NSE')]
        
        if not match.empty:
            sec_id = str(match.iloc[0]['SEM_SMST_SECURITY_ID'])
            inst_name = str(match.iloc[0]['SEM_INSTRUMENT_NAME'])
            return sec_id, inst_name
        else:
            print(f"Symbol {symbol} not found in NSE segment.")
            return None, None
    except Exception as e:
        print(f"Lookup Error: {e}")
        return None, None

@app.route('/webhook', methods=['POST'])
def webhook():
    # force=True ignores the Content-Type header which TradingView sometimes misses
    data = request.get_json(force=True, silent=True)
    
    if not data or data.get('secret') != SECRET_TOKEN:
        print(f"🔴 403 ERROR! Received Data: {data}")
        print(f"🔴 Raw Payload: {request.data}")
        return jsonify({"error": "Unauthorized"}), 403

    symbol = data.get('symbol')
    sec_id, inst_name = get_security_id(symbol)

    if not sec_id:
        return jsonify({"error": f"Symbol {symbol} not found"}), 400

    # Auto-detect if it's an Option/Future or Equity
    exch_seg = dhan.NSE_FNO if inst_name in ['OPTIDX', 'OPTSTK', 'FUTIDX', 'FUTSTK'] else dhan.NSE

    order_type = data.get('order_type', 'MARKET')
    dhan_order_type = dhan.MARKET if order_type.upper() == 'MARKET' else dhan.LIMIT
    price = data.get('price', 0)

    # Placing the order LIVE
    response = dhan.place_order(
        security_id=sec_id,
        exchange_segment=exch_seg,
        transaction_type=dhan.BUY if data['side'].lower() == 'buy' else dhan.SELL,
        quantity=int(data['quantity']),
        order_type=dhan_order_type,
        product_type=dhan.INTRA,
        price=float(price),
        after_market_order=False 
    )
    
    return jsonify(response), 200

if __name__ == '__main__':
    # Listen on all public IPs on port 80 (standard HTTP port)
    app.run(host='0.0.0.0', port=80)
